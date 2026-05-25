"""
Parser de notas de corretagem padrão Sinacor (B3).

Estratégia em duas etapas:
  1. `extrair_nota` — lê o PDF e devolve uma `NotaCorretagem` com operações brutas
     (sem rateio de custos) e o resumo financeiro.
  2. `converter_para_operacoes` — rateia taxas/IRRF por valor entre as operações
     e devolve `Operacao` no formato do motor fiscal. Permite filtrar só FIIs.

A separação é proposital: a etapa 1 trabalha em "modo leitor de PDF" e nunca
inventa dados; a etapa 2 aplica decisões fiscais (rateio, classificação FII).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from core.cadastro_fii import eh_fii_listado
from core.models import Modalidade, Operacao, TipoOperacao

ZERO = Decimal("0")


# ──────────────────────────────────────────────────────────────────────────
# Estruturas intermediárias (antes do rateio de custos)
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class OperacaoBruta:
    """Uma linha da seção 'Negócios realizados', sem custos rateados ainda."""
    tipo: TipoOperacao
    mercado: str                  # "VISTA", "FRACIONARIO", "OPCAO", ...
    ticker: str
    especificacao: str            # texto bruto da coluna "Especificação do título"
    quantidade: int
    preco_unitario: Decimal
    valor_total: Decimal          # qtd * preço
    debito_credito: str           # "D" (compra) ou "C" (venda)


@dataclass
class NotaCorretagem:
    numero: str
    data_pregao: date
    operacoes: list[OperacaoBruta] = field(default_factory=list)
    valor_compras: Decimal = ZERO
    valor_vendas: Decimal = ZERO

    # Resumo financeiro — taxas todas somadas no `taxas_total` (rateáveis por valor).
    taxas_total: Decimal = ZERO
    irrf_total: Decimal = ZERO          # rateável só nas vendas

    # Mantemos as taxas individuais por transparência/auditoria
    detalhamento_taxas: dict[str, Decimal] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# Parsing de baixo nível
# ──────────────────────────────────────────────────────────────────────────

def _br_decimal(s: str) -> Decimal:
    """Converte número no formato brasileiro ('1.234,56') para Decimal."""
    s = s.strip().replace(".", "").replace(",", ".")
    return Decimal(s) if s else ZERO


_RE_DATA_PREGAO = re.compile(
    r"Data\s+preg[ãa]o.*?(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE | re.DOTALL,
)
_RE_NR_NOTA = re.compile(r"Nr\.\s*nota[^\d]*(\d+)", re.IGNORECASE)

# Operação: 1-BOVESPA C VISTA HGLG11 CI ... 100 100,00 10.000,00 D
# Tolerante a espaços e à ausência da coluna "Prazo" / "Obs".
_RE_OPERACAO = re.compile(
    r"""
    ^\s*\d+-?\s*\S+\s+              # "1-BOVESPA" (ou "1- BOVESPA")
    (?P<cv>[CV])\s+                 # C ou V
    (?P<mercado>VISTA|FRACIONARIO|FRACIONÁRIO|OPCAO|OPÇÃO\sCOMPRA|OPÇÃO\sVENDA|TERMO)\b
    \s*(?:\#\d+)?\s*                # eventual prazo / obs
    (?P<spec>.+?)\s+                # especificação do título (greedy mínimo)
    (?P<qtd>[\d.]+)\s+
    (?P<preco>[\d.]+,\d{2,6})\s+
    (?P<valor>[\d.]+,\d{2})\s+
    (?P<dc>[DC])\s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Ticker da B3: 4 letras + 1 ou 2 dígitos. Para FII costuma terminar em "11".
_RE_TICKER = re.compile(r"\b([A-Z]{4}\d{1,2})\b")

# Linhas do "Resumo Financeiro" — captura "Descrição  ... valor D/C"
_TAXAS_PADRAO = {
    "taxa de liquidação": "taxa_liquidacao",
    "taxa de liquidacao": "taxa_liquidacao",
    "taxa de registro": "taxa_registro",
    "emolumentos": "emolumentos",
    "corretagem": "corretagem",
    "iss": "iss",
    "outras": "outras",
    "taxa a.n.a.": "taxa_ana",
    "taxa ana": "taxa_ana",
    "taxa operacional": "taxa_operacional",
}
_RE_IRRF = re.compile(
    r"I\.?R\.?R\.?F\.?[^\n]*?([\d.]+,\d{2})",
    re.IGNORECASE,
)


def _extrair_texto(pdf_path: Path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def _identificar_ticker(especificacao: str) -> str | None:
    m = _RE_TICKER.search(especificacao.upper())
    return m.group(1) if m else None


def _eh_fii(ticker: str, especificacao: str) -> bool:
    """
    Identifica FII via cadastro oficial da B3 (data/fiis_b3.csv).

    Fallback heurístico (caso o cadastro esteja vazio ou o ticker novo ainda
    não tenha sido capturado): exige XXXX11 + marcador textual de FII.
    """
    if eh_fii_listado(ticker):
        return True
    if not re.fullmatch(r"[A-Z]{4}\d{1,2}", ticker):
        return False
    spec = especificacao.upper()
    marcadores = ("FII", "FDO INV IMOB", "FDO IMOB", "FUNDO INV IMOB", "FUNDO IMOB", "FI IMOB")
    return any(m in spec for m in marcadores)


def parse_texto(texto: str) -> NotaCorretagem:
    """
    Faz o parsing direto do texto extraído do PDF.

    Exposta separadamente do `parse_pdf` para facilitar testes com texto sintético
    e para depuração quando uma corretora tem layout ligeiramente diferente.
    """
    nota = NotaCorretagem(numero="", data_pregao=date.min)

    m_data = _RE_DATA_PREGAO.search(texto)
    if m_data:
        d, mes, a = m_data.group(1).split("/")
        nota.data_pregao = date(int(a), int(mes), int(d))

    m_nr = _RE_NR_NOTA.search(texto)
    if m_nr:
        nota.numero = m_nr.group(1)

    for linha in texto.splitlines():
        m = _RE_OPERACAO.match(linha)
        if not m:
            continue
        ticker = _identificar_ticker(m.group("spec"))
        if not ticker:
            continue
        qtd = int(m.group("qtd").replace(".", ""))
        preco = _br_decimal(m.group("preco"))
        valor = _br_decimal(m.group("valor"))
        op = OperacaoBruta(
            tipo=TipoOperacao.COMPRA if m.group("cv").upper() == "C" else TipoOperacao.VENDA,
            mercado=m.group("mercado").upper(),
            ticker=ticker,
            especificacao=m.group("spec").strip(),
            quantidade=qtd,
            preco_unitario=preco,
            valor_total=valor,
            debito_credito=m.group("dc").upper(),
        )
        nota.operacoes.append(op)
        if op.tipo == TipoOperacao.COMPRA:
            nota.valor_compras += op.valor_total
        else:
            nota.valor_vendas += op.valor_total

    # Resumo financeiro — taxas
    texto_lower = texto.lower()
    for label, chave in _TAXAS_PADRAO.items():
        # Padrão: "label ... 1,50 D"  ou só "label ... 1,50"
        padrao = re.compile(
            re.escape(label) + r"\s*[^\n\d]{0,40}?([\d.]+,\d{2})\s*[DC]?",
            re.IGNORECASE,
        )
        m = padrao.search(texto_lower)
        if m:
            valor = _br_decimal(m.group(1))
            nota.detalhamento_taxas[chave] = nota.detalhamento_taxas.get(chave, ZERO) + valor
            nota.taxas_total += valor

    m_irrf = _RE_IRRF.search(texto)
    if m_irrf:
        nota.irrf_total = _br_decimal(m_irrf.group(1))

    return nota


def parse_pdf(pdf_path: Path | str) -> NotaCorretagem:
    """Lê uma nota de corretagem em PDF e devolve a `NotaCorretagem`."""
    return parse_texto(_extrair_texto(Path(pdf_path)))


# ──────────────────────────────────────────────────────────────────────────
# Conversão para o modelo do motor (rateio de custos + filtro FII)
# ──────────────────────────────────────────────────────────────────────────

def converter_para_operacoes(
    nota: NotaCorretagem,
    *,
    apenas_fii: bool = True,
    cnpj_por_ticker: dict[str, str] | None = None,
) -> list[Operacao]:
    """
    Rateia taxas (proporcional ao valor) e IRRF (proporcional ao valor entre vendas)
    e devolve uma lista de `Operacao` pronta para alimentar o motor fiscal.

    `cnpj_por_ticker`: tabela opcional para preencher o CNPJ real do fundo.
    Se ausente, usa "TICKER-XXXXXX" como chave (funciona, mas perde robustez
    para casos de mudança de ticker).
    """
    if apenas_fii:
        elegiveis = [o for o in nota.operacoes if _eh_fii(o.ticker, o.especificacao)]
    else:
        elegiveis = list(nota.operacoes)

    if not elegiveis:
        return []

    valor_total_nota = sum((o.valor_total for o in nota.operacoes), ZERO)
    valor_vendas_nota = sum(
        (o.valor_total for o in nota.operacoes if o.tipo == TipoOperacao.VENDA), ZERO
    )

    cnpj_por_ticker = cnpj_por_ticker or {}
    operacoes: list[Operacao] = []
    custos_acumulado = ZERO
    irrf_acumulado = ZERO

    # Rateamos por todas as operações *elegíveis* exceto a última, que recebe o
    # resíduo — isso evita perda/ganho de centavos por arredondamento.
    for idx, ob in enumerate(elegiveis):
        ultima = idx == len(elegiveis) - 1

        if ultima:
            custos = nota.taxas_total * (
                sum((o.valor_total for o in elegiveis), ZERO) / valor_total_nota
                if valor_total_nota else ZERO
            ) - custos_acumulado
            irrf = (
                nota.irrf_total * (
                    sum((o.valor_total for o in elegiveis
                         if o.tipo == TipoOperacao.VENDA), ZERO) / valor_vendas_nota
                    if valor_vendas_nota else ZERO
                ) - irrf_acumulado
            ) if ob.tipo == TipoOperacao.VENDA else ZERO
        else:
            custos = (
                nota.taxas_total * ob.valor_total / valor_total_nota
                if valor_total_nota else ZERO
            )
            irrf = (
                nota.irrf_total * ob.valor_total / valor_vendas_nota
                if (ob.tipo == TipoOperacao.VENDA and valor_vendas_nota) else ZERO
            )
            custos_acumulado += custos
            irrf_acumulado += irrf

        custos = custos.quantize(Decimal("0.01"))
        irrf = irrf.quantize(Decimal("0.0001"))  # IRRF é minúsculo, mais casas

        operacoes.append(Operacao(
            data=nota.data_pregao,
            ticker=ob.ticker,
            cnpj=cnpj_por_ticker.get(ob.ticker, f"TICKER-{ob.ticker}"),
            tipo=ob.tipo,
            quantidade=ob.quantidade,
            preco_unitario=ob.preco_unitario,
            custos=max(custos, ZERO),
            modalidade=Modalidade.SWING,   # classificação de day trade é feita pelo motor
            irrf=max(irrf, ZERO),
        ))
    return operacoes
