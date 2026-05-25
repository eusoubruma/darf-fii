"""
UI Streamlit do calculador de DARF para FII.

Rodar com:  streamlit run app.py
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

from core.cadastro_fii import fundo_do_ticker
from core.calculadora import apurar
from core.darf import gerar_darf
from core.models import EstadoFiscal, Modalidade, Operacao, Posicao, TipoOperacao
from parsers.pdf_sinacor import converter_para_operacoes, parse_pdf
from parsers.xlsx_b3 import parse_xlsx
from storage import deserializar, serializar

from auth import autenticar

MESES_PT = ["", "jan", "fev", "mar", "abr", "mai", "jun",
            "jul", "ago", "set", "out", "nov", "dez"]


# ──────────────────────────────────────────────────────────────────────────
# Session state — sem disco. Backup/restore manual via sidebar.
# ──────────────────────────────────────────────────────────────────────────

def _init_state() -> None:
    if "operacoes" not in st.session_state:
        st.session_state.operacoes = []
        st.session_state.estado_inicial = EstadoFiscal()


def _persistir() -> None:
    """No-op: persistência manual via download/upload de JSON na sidebar."""
    return


def _formatar_brl(v: Decimal) -> str:
    s = f"{v:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


# ──────────────────────────────────────────────────────────────────────────
# Páginas
# ──────────────────────────────────────────────────────────────────────────

def pagina_adicionar() -> None:
    st.subheader("Registrar operação")

    with st.form("nova_op", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            data_op = st.date_input("Data", value=date.today(), format="DD/MM/YYYY")
            ticker = st.text_input("Ticker", placeholder="HGLG11").strip().upper()
        with c2:
            tipo = st.selectbox("Tipo", ["Compra", "Venda"])
            quantidade = st.number_input("Quantidade (cotas)", min_value=1, step=1, value=1)
        with c3:
            preco = st.number_input("Preço unitário (R$)", min_value=0.0, step=0.01, format="%.2f")
            cnpj = st.text_input("CNPJ do fundo", help="Opcional — se vazio, usa o ticker como chave.")

        c4, c5 = st.columns(2)
        with c4:
            custos = st.number_input("Custos (corretagem + emolumentos, R$)",
                                     min_value=0.0, step=0.01, format="%.2f")
        with c5:
            irrf = st.number_input("IRRF retido (R$)", min_value=0.0, step=0.01, format="%.4f",
                                   help="'Dedo-duro': 0,005% (swing) ou 1% (day trade) do valor.")

        submitted = st.form_submit_button("Adicionar operação", type="primary")
        if submitted:
            if not ticker or preco == 0:
                st.error("Informe ao menos ticker e preço.")
                return
            op = Operacao(
                data=data_op,
                ticker=ticker,
                cnpj=cnpj.strip() or f"TICKER-{ticker}",
                tipo=TipoOperacao.COMPRA if tipo == "Compra" else TipoOperacao.VENDA,
                quantidade=int(quantidade),
                preco_unitario=Decimal(str(preco)),
                custos=Decimal(str(custos)),
                irrf=Decimal(str(irrf)),
            )
            st.session_state.operacoes.append(op)
            _persistir()
            st.success(f"Registrada: {tipo} de {quantidade} {ticker} a {_formatar_brl(Decimal(str(preco)))}.")


def pagina_operacoes() -> None:
    st.subheader("Operações registradas")
    ops = st.session_state.operacoes
    if not ops:
        st.info("Nenhuma operação registrada. Use a aba **Registrar** para começar.")
        return

    df = pd.DataFrame([{
        "idx": i,
        "Data": o.data.strftime("%d/%m/%Y"),
        "Ticker": o.ticker,
        "Tipo": "Compra" if o.tipo == TipoOperacao.COMPRA else "Venda",
        "Qtd": o.quantidade,
        "Preço unit.": float(o.preco_unitario),
        "Custos": float(o.custos),
        "IRRF": float(o.irrf),
        "Total líquido": float(o.valor_liquido),
    } for i, o in enumerate(ops)])
    df_view = df.sort_values("Data").reset_index(drop=True)
    st.dataframe(
        df_view.drop(columns=["idx"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Preço unit.": st.column_config.NumberColumn(format="R$ %.2f"),
            "Custos": st.column_config.NumberColumn(format="R$ %.2f"),
            "IRRF": st.column_config.NumberColumn(format="R$ %.4f"),
            "Total líquido": st.column_config.NumberColumn(format="R$ %.2f"),
        },
    )

    st.divider()
    col1, col2 = st.columns([2, 1])
    with col1:
        idx_to_delete = st.selectbox(
            "Excluir operação",
            options=df_view["idx"].tolist(),
            format_func=lambda i: f"{ops[i].data.strftime('%d/%m/%Y')} — "
                                  f"{'Compra' if ops[i].tipo == TipoOperacao.COMPRA else 'Venda'} "
                                  f"{ops[i].quantidade} {ops[i].ticker}",
        )
    with col2:
        st.write("")
        st.write("")
        if st.button("Excluir", type="secondary"):
            st.session_state.operacoes.pop(idx_to_delete)
            _persistir()
            st.rerun()


def pagina_apuracao() -> None:
    st.subheader("Apuração mensal")
    if not st.session_state.operacoes:
        st.info("Sem operações para apurar.")
        return

    apuracoes, estado_final = apurar(
        st.session_state.operacoes,
        estado_inicial=_copiar_estado(st.session_state.estado_inicial),
    )

    linhas = []
    for a in apuracoes:
        linhas.append({
            "Mês": f"{MESES_PT[a.mes]}/{a.ano}",
            "Resultado swing": float(a.resultado_swing),
            "Resultado day": float(a.resultado_day),
            "Prej. compensado": float(a.prejuizo_swing_compensado + a.prejuizo_day_compensado),
            "Base de cálculo": float(a.base_swing + a.base_day),
            "IR devido (20%)": float(a.ir_swing + a.ir_day),
            "IRRF do mês": float(a.irrf_swing + a.irrf_day),
            "DARF a pagar": float(a.ir_a_pagar),
        })
    df = pd.DataFrame(linhas)
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={c: st.column_config.NumberColumn(format="R$ %.2f")
                       for c in df.columns if c != "Mês"},
    )

    st.divider()
    st.subheader("DARFs a recolher")
    darfs = [(a, gerar_darf(a)) for a in apuracoes]
    darfs = [(a, d) for a, d in darfs if d is not None]
    if not darfs:
        st.success("Nenhum DARF a recolher (sem lucro tributável ou abaixo do mínimo de R$10).")
    else:
        for a, d in darfs:
            st.markdown(f"""
<div style="background:#FFFFFF;border:1px solid #E1E5EB;border-left:4px solid #002859;
            border-radius:6px;padding:1rem 1.2rem;margin-bottom:0.8rem;
            box-shadow:0 1px 2px rgba(0,40,89,0.04);">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem;">
    <div>
      <div style="font-size:0.72rem;text-transform:uppercase;color:#5A6B82;letter-spacing:0.5px;">DARF — código {d.codigo_receita}</div>
      <div style="font-size:1.15rem;font-weight:600;color:#002859;margin-top:0.2rem;">
        {MESES_PT[a.mes].capitalize()}/{a.ano} · vencimento {d.data_vencimento.strftime('%d/%m/%Y')}
      </div>
    </div>
    <div style="text-align:right;">
      <div style="font-size:0.72rem;text-transform:uppercase;color:#5A6B82;letter-spacing:0.5px;">Valor a recolher</div>
      <div style="font-size:1.6rem;font-weight:700;color:#002859;">{_formatar_brl(d.valor_principal)}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

    st.divider()
    st.subheader("Saldos no fim do período")
    c1, c2, c3 = st.columns(3)
    c1.metric("Prejuízo acumulado (swing)",
              _formatar_brl(estado_final.prejuizo_acumulado_swing))
    c2.metric("Prejuízo acumulado (day)",
              _formatar_brl(estado_final.prejuizo_acumulado_day))
    c3.metric("IR < R$10 acumulado",
              _formatar_brl(estado_final.darf_acumulado))


def pagina_posicao() -> None:
    st.subheader("Posição atual em custódia")
    _, estado_final = apurar(
        st.session_state.operacoes,
        estado_inicial=_copiar_estado(st.session_state.estado_inicial),
    )
    pos_ativas = [p for p in estado_final.posicoes.values() if p.quantidade > 0]
    if not pos_ativas:
        st.info("Nenhuma posição em custódia.")
        return

    df = pd.DataFrame([{
        "Ticker": p.ticker,
        "Fundo": (fundo_do_ticker(p.ticker).fundo if fundo_do_ticker(p.ticker) else ""),
        "Quantidade": p.quantidade,
        "Custo médio": float(p.custo_medio),
        "Custo total": float(p.custo_total),
    } for p in pos_ativas])
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "Custo médio": st.column_config.NumberColumn(format="R$ %.4f"),
            "Custo total": st.column_config.NumberColumn(format="R$ %.2f"),
        },
    )


def pagina_importar_pdf() -> None:
    st.subheader("Importar nota de corretagem (PDF Sinacor)")
    st.caption(
        "Faça upload de uma ou mais notas (formato Sinacor — XP, Clear, Rico, BTG etc.). "
        "Vamos extrair as operações de FII, ratear taxas e IRRF, e mostrar para revisão "
        "antes de incorporar à apuração."
    )

    uploads = st.file_uploader(
        "Arquivos PDF", type=["pdf"], accept_multiple_files=True, key="pdf_uploader",
    )
    if not uploads:
        return

    import tempfile
    from pathlib import Path as _P

    operacoes_extraidas: list[Operacao] = []
    resumos = []
    for up in uploads:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(up.read())
                tmp_path = _P(tmp.name)
            nota = parse_pdf(tmp_path)
            ops = converter_para_operacoes(nota, apenas_fii=True)
            operacoes_extraidas.extend(ops)
            resumos.append({
                "Arquivo": up.name,
                "Nº nota": nota.numero or "?",
                "Pregão": nota.data_pregao.strftime("%d/%m/%Y") if nota.data_pregao.year > 1 else "?",
                "Ops na nota": len(nota.operacoes),
                "Ops FII": len(ops),
                "Taxas (R$)": float(nota.taxas_total),
                "IRRF (R$)": float(nota.irrf_total),
            })
        except Exception as exc:
            st.error(f"Falha ao processar `{up.name}`: {exc}")

    if resumos:
        st.markdown("**Resumo das notas processadas**")
        st.dataframe(pd.DataFrame(resumos), hide_index=True, use_container_width=True)

    if not operacoes_extraidas:
        st.warning("Nenhuma operação de FII encontrada nas notas enviadas.")
        return

    st.markdown("**Operações de FII extraídas (preview)**")
    df = pd.DataFrame([{
        "Data": o.data.strftime("%d/%m/%Y"),
        "Ticker": o.ticker,
        "Tipo": "Compra" if o.tipo == TipoOperacao.COMPRA else "Venda",
        "Qtd": o.quantidade,
        "Preço": float(o.preco_unitario),
        "Custos rateados": float(o.custos),
        "IRRF rateado": float(o.irrf),
    } for o in operacoes_extraidas])
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={
            "Preço": st.column_config.NumberColumn(format="R$ %.2f"),
            "Custos rateados": st.column_config.NumberColumn(format="R$ %.4f"),
            "IRRF rateado": st.column_config.NumberColumn(format="R$ %.4f"),
        },
    )

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Importar para a apuração", type="primary"):
            st.session_state.operacoes.extend(operacoes_extraidas)
            _persistir()
            st.success(f"{len(operacoes_extraidas)} operação(ões) importada(s).")
            st.rerun()


def pagina_importar_xlsx() -> None:
    st.subheader("Importar extrato XLSX (B3 — Canal Eletrônico do Investidor)")
    st.caption(
        "Aceita o extrato de **Negociação** baixado da Área do Investidor da B3. "
        "Atenção: o extrato da B3 **não traz taxas nem IRRF** — para precisão fiscal, "
        "use as notas PDF da corretora ou registre os custos manualmente depois."
    )

    uploads = st.file_uploader(
        "Arquivos XLSX", type=["xlsx", "xls"], accept_multiple_files=True, key="xlsx_uploader",
    )
    if not uploads:
        return

    import tempfile
    from pathlib import Path as _P

    operacoes_extraidas: list[Operacao] = []
    resumos = []
    for up in uploads:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                tmp.write(up.read())
                tmp_path = _P(tmp.name)
            ops = parse_xlsx(tmp_path, apenas_fii=True)
            operacoes_extraidas.extend(ops)
            resumos.append({"Arquivo": up.name, "Operações FII": len(ops)})
        except Exception as exc:
            st.error(f"Falha ao processar `{up.name}`: {exc}")

    if resumos:
        st.dataframe(pd.DataFrame(resumos), hide_index=True, use_container_width=True)

    if not operacoes_extraidas:
        st.warning("Nenhuma operação de FII encontrada nos arquivos enviados.")
        return

    st.markdown("**Operações extraídas (preview)**")
    df = pd.DataFrame([{
        "Data": o.data.strftime("%d/%m/%Y"),
        "Ticker": o.ticker,
        "Tipo": "Compra" if o.tipo == TipoOperacao.COMPRA else "Venda",
        "Qtd": o.quantidade,
        "Preço": float(o.preco_unitario),
    } for o in operacoes_extraidas])
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={"Preço": st.column_config.NumberColumn(format="R$ %.2f")},
    )

    st.caption(
        "Filtro feito contra o cadastro oficial da B3 — ETFs (BOVA11, IVVB11 etc.) "
        "já foram descartados automaticamente."
    )

    if st.button("Importar para a apuração", type="primary"):
        st.session_state.operacoes.extend(operacoes_extraidas)
        _persistir()
        st.success(f"{len(operacoes_extraidas)} operação(ões) importada(s).")
        st.rerun()


def pagina_estado_inicial() -> None:
    st.subheader("Posição inicial (antes de usar a app)")
    st.caption(
        "Se você já tinha FIIs em custódia antes de começar a usar este sistema, "
        "informe aqui a posição em custo médio e prejuízos já declarados em anos anteriores."
    )

    est = st.session_state.estado_inicial

    with st.expander("Posições iniciais", expanded=True):
        if est.posicoes:
            df = pd.DataFrame([{
                "Ticker": p.ticker, "CNPJ": p.cnpj,
                "Quantidade": p.quantidade,
                "Custo total": float(p.custo_total),
                "Custo médio": float(p.custo_medio),
            } for p in est.posicoes.values()])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.write("_Nenhuma posição inicial registrada._")

        with st.form("nova_pos_inicial", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                ticker = st.text_input("Ticker").strip().upper()
            with c2:
                cnpj = st.text_input("CNPJ (opcional)").strip()
            with c3:
                qtd = st.number_input("Quantidade", min_value=1, step=1, value=1)
            with c4:
                custo_medio = st.number_input(
                    "Custo médio (R$)", min_value=0.0, step=0.01, format="%.4f")
            if st.form_submit_button("Adicionar posição inicial"):
                if ticker and custo_medio > 0:
                    chave = cnpj or f"TICKER-{ticker}"
                    est.posicoes[chave] = Posicao(
                        cnpj=chave, ticker=ticker,
                        quantidade=int(qtd),
                        custo_total=Decimal(str(custo_medio)) * int(qtd),
                    )
                    _persistir()
                    st.rerun()

    with st.expander("Prejuízos acumulados de períodos anteriores"):
        c1, c2 = st.columns(2)
        with c1:
            prej_sw = st.number_input(
                "Prejuízo acumulado — swing trade (R$)",
                min_value=0.0, step=0.01, format="%.2f",
                value=float(est.prejuizo_acumulado_swing),
            )
        with c2:
            prej_dt = st.number_input(
                "Prejuízo acumulado — day trade (R$)",
                min_value=0.0, step=0.01, format="%.2f",
                value=float(est.prejuizo_acumulado_day),
            )
        if st.button("Salvar prejuízos"):
            est.prejuizo_acumulado_swing = Decimal(str(prej_sw))
            est.prejuizo_acumulado_day = Decimal(str(prej_dt))
            _persistir()
            st.success("Prejuízos atualizados.")


def _copiar_estado(e: EstadoFiscal) -> EstadoFiscal:
    """Cópia rasa — evita mutar o estado_inicial ao apurar."""
    return EstadoFiscal(
        posicoes={k: Posicao(cnpj=p.cnpj, ticker=p.ticker,
                             quantidade=p.quantidade, custo_total=p.custo_total)
                  for k, p in e.posicoes.items()},
        prejuizo_acumulado_swing=e.prejuizo_acumulado_swing,
        prejuizo_acumulado_day=e.prejuizo_acumulado_day,
        irrf_acumulado_swing=e.irrf_acumulado_swing,
        irrf_acumulado_day=e.irrf_acumulado_day,
        darf_acumulado=e.darf_acumulado,
    )


# ──────────────────────────────────────────────────────────────────────────
# Layout
# ──────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="DARF FII", layout="wide", page_icon="📊")

# ── Gate de autenticação ────────────────────────────────────────────────
username, authenticator = autenticar()
if not username:
    st.stop()

_init_state()


# ──────────────────────────────────────────────────────────────────────────
# Identidade visual inspirada na B3
# ──────────────────────────────────────────────────────────────────────────

_B3_NAVY = "#002859"
_B3_NAVY_DARK = "#001A3A"
_B3_CYAN = "#0086C9"
_B3_CYAN_LIGHT = "#E6F4FB"
_B3_GRAY_LINE = "#E1E5EB"

st.markdown(f"""
<style>
/* Faixa superior navy ao estilo B3 */
.b3-header {{
    background: linear-gradient(90deg, {_B3_NAVY_DARK} 0%, {_B3_NAVY} 60%, {_B3_NAVY} 100%);
    color: #FFFFFF;
    padding: 1.4rem 1.8rem;
    border-radius: 6px;
    margin-bottom: 1.5rem;
    box-shadow: 0 2px 10px rgba(0, 40, 89, 0.18);
    display: flex;
    align-items: center;
    justify-content: space-between;
}}
.b3-header h1 {{
    color: #FFFFFF; margin: 0;
    font-size: 1.55rem; font-weight: 600; letter-spacing: 0.2px;
}}
.b3-header .b3-subtitle {{
    color: #B8D4EA; font-size: 0.92rem; margin-top: 0.35rem; font-weight: 400;
}}
.b3-header .b3-badge {{
    background: {_B3_CYAN}; color: #fff;
    padding: 0.32rem 0.85rem; border-radius: 999px;
    font-size: 0.78rem; font-weight: 600; letter-spacing: 0.5px;
    text-transform: uppercase;
}}

/* Abas no estilo segmentado da B3 */
div[data-baseweb="tab-list"] {{
    background: #FFFFFF;
    border: 1px solid {_B3_GRAY_LINE};
    border-radius: 6px;
    padding: 4px;
    gap: 2px;
}}
button[data-baseweb="tab"] {{
    background: transparent !important;
    color: {_B3_NAVY} !important;
    font-weight: 500;
    border-radius: 4px;
    padding: 0.55rem 1.1rem !important;
}}
button[data-baseweb="tab"][aria-selected="true"] {{
    background: {_B3_NAVY} !important;
    color: #FFFFFF !important;
}}
div[data-baseweb="tab-highlight"] {{ display: none; }}

/* Métricas: cards brancos com borda fina */
div[data-testid="stMetric"] {{
    background: #FFFFFF;
    border: 1px solid {_B3_GRAY_LINE};
    border-left: 4px solid {_B3_CYAN};
    border-radius: 6px;
    padding: 0.9rem 1.1rem;
    box-shadow: 0 1px 2px rgba(0, 40, 89, 0.04);
}}
div[data-testid="stMetricLabel"] {{
    color: #5A6B82 !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}}
div[data-testid="stMetricValue"] {{
    color: {_B3_NAVY} !important;
    font-size: 1.35rem !important;
    font-weight: 600 !important;
}}

/* Botões primários: navy */
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{
    background: {_B3_NAVY};
    color: #FFFFFF;
    border: none;
    font-weight: 600;
    border-radius: 4px;
    letter-spacing: 0.3px;
}}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {{
    background: {_B3_NAVY_DARK};
}}

/* Containers/forms: linha lateral discreta */
div[data-testid="stForm"] {{
    background: #FFFFFF;
    border: 1px solid {_B3_GRAY_LINE};
    border-radius: 6px;
    padding: 1.2rem;
}}

/* Containers com borda (st.container(border=True)) */
div[data-testid="stVerticalBlockBorderWrapper"] {{
    border-radius: 6px;
    border-color: {_B3_GRAY_LINE} !important;
}}

/* Tabelas: cabeçalho discreto navy */
div[data-testid="stDataFrame"] thead tr th {{
    background: {_B3_CYAN_LIGHT} !important;
    color: {_B3_NAVY} !important;
    font-weight: 600 !important;
}}

/* Sidebar: tom levemente mais escuro */
section[data-testid="stSidebar"] {{
    background: #FAFBFC;
    border-right: 1px solid {_B3_GRAY_LINE};
}}
section[data-testid="stSidebar"] h2 {{
    color: {_B3_NAVY};
    font-size: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}}

/* Caption e divider mais sutis */
hr {{ border-color: {_B3_GRAY_LINE}; }}
.stCaption, [data-testid="stCaptionContainer"] {{
    color: #5A6B82;
}}

/* Subheaders em navy */
h2, h3 {{ color: {_B3_NAVY}; }}
</style>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="b3-header">
    <div>
        <h1>Calculadora de DARF — Fundos Imobiliários</h1>
        <div class="b3-subtitle">Apuração mensal de IR sobre ganhos líquidos em FII (PF) · Alíquota 20% · Código 6015</div>
    </div>
    <div class="b3-badge">Pessoa Física</div>
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown(f"**Conectado como:** `{username}`")
    authenticator.logout("Sair", location="sidebar")
    st.divider()

    st.header("Resumo")
    n_ops = len(st.session_state.operacoes)
    st.metric("Operações registradas", n_ops)

    st.divider()
    st.subheader("Backup")
    st.caption(
        "Os dados ficam apenas na sua sessão (não são salvos no servidor). "
        "Baixe um backup periodicamente e suba-o quando voltar."
    )
    st.download_button(
        "Baixar backup (JSON)",
        data=serializar(st.session_state.operacoes, st.session_state.estado_inicial),
        file_name=f"darf_fii_{username}.json",
        mime="application/json",
        use_container_width=True,
    )
    upload = st.file_uploader("Restaurar backup", type=["json"], key="upload_backup",
                              label_visibility="collapsed")
    if upload is not None and st.button("Restaurar backup", use_container_width=True):
        try:
            ops, est = deserializar(upload.read())
            st.session_state.operacoes = ops
            st.session_state.estado_inicial = est
            st.success(f"{len(ops)} operação(ões) restauradas.")
            st.rerun()
        except Exception as exc:
            st.error(f"Arquivo inválido: {exc}")

    st.divider()
    if st.button("Limpar tudo", type="secondary", use_container_width=True):
        st.session_state.operacoes = []
        st.session_state.estado_inicial = EstadoFiscal()
        st.rerun()

tab_add, tab_pdf, tab_xlsx, tab_ops, tab_apur, tab_pos, tab_inic = st.tabs(
    ["Registrar", "Importar PDF", "Importar XLSX",
     "Operações", "Apuração e DARF", "Posição atual", "Estado inicial"]
)
with tab_add:
    pagina_adicionar()
with tab_pdf:
    pagina_importar_pdf()
with tab_xlsx:
    pagina_importar_xlsx()
with tab_ops:
    pagina_operacoes()
with tab_apur:
    pagina_apuracao()
with tab_pos:
    pagina_posicao()
with tab_inic:
    pagina_estado_inicial()
