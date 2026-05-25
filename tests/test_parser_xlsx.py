"""
Testes do parser de XLSX (extrato de Negociação da B3).

Trabalhamos com DataFrames sintéticos via `parse_dataframe` — não dependemos
de criar arquivos XLSX no disco.
"""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from core.models import TipoOperacao
from parsers.xlsx_b3 import parse_dataframe

D = Decimal


def _df_b3(linhas):
    """Cria DataFrame com as colunas padrão do extrato B3."""
    return pd.DataFrame(linhas, columns=[
        "Entrada/Saída", "Data do Negócio", "Movimentação", "Mercado",
        "Prazo/Vencimento", "Instituição", "Código de Negociação",
        "Quantidade", "Preço", "Valor",
    ])


def test_parse_b3_compra_e_venda():
    df = _df_b3([
        ["Credito", "10/01/2025", "Compra", "Mercado à Vista", "-", "XP", "HGLG11", 100, 100.00, 10000.00],
        ["Debito",  "15/02/2025", "Venda",  "Mercado à Vista", "-", "XP", "HGLG11",  50, 120.00,  6000.00],
    ])
    ops = parse_dataframe(df)
    assert len(ops) == 2
    assert ops[0].tipo == TipoOperacao.COMPRA
    assert ops[0].ticker == "HGLG11"
    assert ops[0].data == date(2025, 1, 10)
    assert ops[0].quantidade == 100
    assert ops[0].preco_unitario == D("100.00")
    assert ops[1].tipo == TipoOperacao.VENDA
    assert ops[1].quantidade == 50


def test_filtra_apenas_fii_por_padrao():
    df = _df_b3([
        ["Credito", "10/01/2025", "Compra", "Vista", "-", "XP", "PETR4",   100, 30.00,  3000.00],  # ação
        ["Credito", "10/01/2025", "Compra", "Vista", "-", "XP", "HGLG11",  100, 100.00, 10000.00], # FII
        ["Credito", "10/01/2025", "Compra", "Vista", "-", "XP", "BOVA11",   10, 120.00,  1200.00], # ETF (passa pelo filtro XXXX11)
    ])
    ops = parse_dataframe(df, apenas_fii=True)
    tickers = [o.ticker for o in ops]
    assert "PETR4" not in tickers           # ação — não é FII
    assert "HGLG11" in tickers              # FII listado
    assert "BOVA11" not in tickers          # ETF — cadastro oficial descarta


def test_tolera_variacao_nome_colunas():
    df = pd.DataFrame([
        {"Movimentação": "Compra", "Data": "10/01/2025", "Ticker": "HGLG11",
         "Qtd": 100, "Preço": 100.00},
    ])
    ops = parse_dataframe(df)
    assert len(ops) == 1
    assert ops[0].ticker == "HGLG11"


def test_tolera_data_como_datetime():
    df = _df_b3([
        ["Credito", pd.Timestamp("2025-01-10"), "Compra", "Vista", "-", "XP",
         "HGLG11", 100, 100.00, 10000.00],
    ])
    ops = parse_dataframe(df)
    assert ops[0].data == date(2025, 1, 10)


def test_tolera_credito_debito_em_vez_de_compra_venda():
    """Perspectiva de custódia: Credito=ativos entraram=compra, Debito=ativos saíram=venda."""
    df = pd.DataFrame([
        {"Entrada/Saída": "Credito", "Data do Negócio": "10/01/2025",
         "Código de Negociação": "HGLG11", "Quantidade": 100, "Preço": 100.00},
        {"Entrada/Saída": "Debito", "Data do Negócio": "15/02/2025",
         "Código de Negociação": "HGLG11", "Quantidade": 50, "Preço": 110.00},
    ])
    ops = parse_dataframe(df)
    assert ops[0].tipo == TipoOperacao.COMPRA   # Credito = ativos creditados = compra
    assert ops[1].tipo == TipoOperacao.VENDA    # Debito = ativos debitados = venda


def test_falha_se_colunas_obrigatorias_ausentes():
    df = pd.DataFrame([{"Foo": 1, "Bar": 2}])
    with pytest.raises(ValueError, match="Colunas obrigatórias"):
        parse_dataframe(df)


def test_cnpj_resolvido_via_tabela():
    df = _df_b3([
        ["Credito", "10/01/2025", "Compra", "Vista", "-", "XP", "HGLG11", 100, 100.00, 10000.00],
    ])
    tabela = {"HGLG11": "11.728.688/0001-47"}
    ops = parse_dataframe(df, cnpj_por_ticker=tabela)
    assert ops[0].cnpj == "11.728.688/0001-47"


def test_ignora_linhas_sem_tipo_valido():
    df = _df_b3([
        ["Credito", "10/01/2025", "Compra", "Vista", "-", "XP", "HGLG11", 100, 100.00, 10000.00],
        ["Algumacoisa", "10/01/2025", "?", "Vista", "-", "XP", "HGLG11", 100, 100.00, 10000.00],
    ])
    ops = parse_dataframe(df)
    assert len(ops) == 1


def test_movimentacao_b3_atual_extrai_ticker_de_produto():
    """Formato real do extrato Movimentação (2025+): coluna 'Produto' tem 'TICKER - NOME'."""
    df = pd.DataFrame([
        {"Entrada/Saída": "Credito", "Data": "23/12/2025",
         "Movimentação": "Transferência - Liquidação",
         "Produto": "IRIM11 - IRIDIUM FUNDO DE INVESTIMENTO IMOBILIÁRIO",
         "Instituição": "NU INVEST", "Quantidade": 1,
         "Preço unitário": 65.00, "Valor da Operação": 65.00},
        {"Entrada/Saída": "Debito", "Data": "08/04/2026",
         "Movimentação": "Transferência - Liquidação",
         "Produto": "HGLG11 - CSHG LOGÍSTICA",
         "Instituição": "NU INVEST", "Quantidade": 10,
         "Preço unitário": 150.00, "Valor da Operação": 1500.00},
    ])
    ops = parse_dataframe(df)
    assert len(ops) == 2
    assert ops[0].ticker == "IRIM11"
    assert ops[0].tipo == TipoOperacao.COMPRA   # Credito = ativos creditados = compra
    assert ops[0].data == date(2025, 12, 23)
    assert ops[0].preco_unitario == D("65.00")
    assert ops[1].ticker == "HGLG11"
    assert ops[1].tipo == TipoOperacao.VENDA    # Debito = ativos debitados = venda


def test_movimentacao_b3_filtra_rendimentos_e_jcp():
    """Eventos como Rendimento, JCP, Bonificação não são trades — devem ser ignorados."""
    df = pd.DataFrame([
        # Trade real
        {"Entrada/Saída": "Credito", "Data": "10/01/2025",
         "Movimentação": "Transferência - Liquidação",
         "Produto": "HGLG11 - CSHG", "Quantidade": 10, "Preço unitário": 100.00},
        # Rendimento de FII (dividendo) — não é operação tributável
        {"Entrada/Saída": "Credito", "Data": "15/01/2025",
         "Movimentação": "Rendimento",
         "Produto": "HGLG11 - CSHG", "Quantidade": 10, "Preço unitário": 0.85},
        # JCP de ação
        {"Entrada/Saída": "Credito", "Data": "20/01/2025",
         "Movimentação": "Juros sobre Capital Próprio",
         "Produto": "ITSA4 - ITAUSA", "Quantidade": 100, "Preço unitário": 0.20},
    ])
    ops = parse_dataframe(df, apenas_fii=False)
    assert len(ops) == 1
    assert ops[0].ticker == "HGLG11"


def test_extrato_b3_nao_traz_custos_nem_irrf():
    """Documenta a limitação: B3 extrato não tem taxas/IRRF → vêm zerados."""
    df = _df_b3([
        ["Credito", "10/01/2025", "Compra", "Vista", "-", "XP", "HGLG11", 100, 100.00, 10000.00],
    ])
    ops = parse_dataframe(df)
    assert ops[0].custos == D("0")
    assert ops[0].irrf == D("0")
