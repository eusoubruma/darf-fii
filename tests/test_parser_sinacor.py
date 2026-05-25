"""
Testes do parser de Sinacor usando texto sintético que reproduz o layout
real de notas de corretagem da B3.
"""

from datetime import date
from decimal import Decimal

import pytest

from core.models import TipoOperacao
from parsers.pdf_sinacor import (
    NotaCorretagem,
    converter_para_operacoes,
    parse_texto,
)

D = Decimal


# Texto sintético reproduzindo uma nota Sinacor típica
NOTA_SIMPLES = """
NOTA DE NEGOCIAÇÃO
Nr. nota   Folha   Data pregão
   12345     1     15/01/2025

Negócios realizados
Q Negociação  C/V  Tipo mercado  Prazo Especificação do título     Obs. Quantidade  Preço/Ajuste  Valor Operação/Ajuste D/C
1-BOVESPA     C    VISTA               HGLG11 CI  FII CSHG LOG          100         100,00            10.000,00         D
1-BOVESPA     C    VISTA               XPLG11 CI  FII XP LOG            50          200,00            10.000,00         D

Resumo dos Negócios                  Resumo Financeiro
Debêntures              0,00         Clearing
Vendas à vista          0,00          Taxa de liquidação            5,00 D
Compras à vista     20.000,00         Taxa de Registro              1,00 D
Opções compras          0,00         Bovespa / Soma
Opções vendas           0,00          Emolumentos                   1,40 D
Operações a termo       0,00         Corretagem / Despesas
Valor das oper. c/títu  0,00          Corretagem                   10,00 D
Valor oper. com ouro    0,00          ISS                           0,50 D
Valor dos negócios  20.000,00          I.R.R.F. s/operações         0,00
                                      Outras                        0,00
                                     Líquido para 16/01/2025    20.017,90 D
"""


def test_parse_extrai_metadados():
    nota = parse_texto(NOTA_SIMPLES)
    assert nota.numero == "12345"
    assert nota.data_pregao == date(2025, 1, 15)


def test_parse_extrai_duas_operacoes_de_compra():
    nota = parse_texto(NOTA_SIMPLES)
    assert len(nota.operacoes) == 2
    op1, op2 = nota.operacoes
    assert op1.ticker == "HGLG11"
    assert op1.tipo == TipoOperacao.COMPRA
    assert op1.quantidade == 100
    assert op1.preco_unitario == D("100.00")
    assert op2.ticker == "XPLG11"
    assert op2.quantidade == 50
    assert op2.preco_unitario == D("200.00")


def test_parse_soma_taxas_do_resumo_financeiro():
    nota = parse_texto(NOTA_SIMPLES)
    # 5,00 + 1,00 + 1,40 + 10,00 + 0,50 = 17,90
    assert nota.taxas_total == D("17.90")
    assert nota.irrf_total == D("0.00")


def test_converter_rateia_custos_proporcionalmente():
    """Cada operação recebe parte dos R$17,90 proporcional ao seu valor."""
    nota = parse_texto(NOTA_SIMPLES)
    ops = converter_para_operacoes(nota)
    assert len(ops) == 2
    # Como as duas operações têm o mesmo valor (R$10.000), custos devem ser iguais
    assert ops[0].custos == D("8.95")
    assert ops[1].custos == D("8.95")
    # Soma deve fechar com o total da nota (regra do resíduo na última op)
    assert ops[0].custos + ops[1].custos == nota.taxas_total


def test_converter_filtra_apenas_fii_quando_solicitado():
    nota_mista = """
Nr. nota 99   Folha   Data pregão
                       10/02/2025
Negócios realizados
1-BOVESPA C VISTA   PETR4 ON N1                    100  30,00      3.000,00 D
1-BOVESPA C VISTA   HGLG11 CI FII CSHG LOG         100  100,00    10.000,00 D
1-BOVESPA C VISTA   BOVA11 CI ISHARES IBOV          10  120,00     1.200,00 D
Resumo Financeiro
 Taxa de liquidação                                  1,00 D
"""
    nota = parse_texto(nota_mista)
    assert len(nota.operacoes) == 3
    ops = converter_para_operacoes(nota, apenas_fii=True)
    # Só HGLG11 é FII (PETR4 é ação; BOVA11 é ETF, não tem "FII"/"IMOB" na spec)
    assert len(ops) == 1
    assert ops[0].ticker == "HGLG11"


def test_converter_irrf_so_em_vendas():
    nota_com_venda = """
Nr. nota 100 Folha Data pregão
                   20/03/2025
Negócios realizados
1-BOVESPA C VISTA   HGLG11 CI FII CSHG LOG    100  100,00   10.000,00 D
1-BOVESPA V VISTA   XPLG11 CI FII XP LOG       50  220,00   11.000,00 C
Resumo Financeiro
 Taxa de liquidação                                  2,00 D
 I.R.R.F. s/operações                                0,55
"""
    nota = parse_texto(nota_com_venda)
    ops = converter_para_operacoes(nota)
    compra = next(o for o in ops if o.tipo == TipoOperacao.COMPRA)
    venda = next(o for o in ops if o.tipo == TipoOperacao.VENDA)
    assert compra.irrf == D("0.0000")     # IRRF não rateia em compras
    assert venda.irrf == D("0.5500")      # rateia tudo na única venda


def test_cnpj_resolvido_de_tabela_quando_fornecida():
    nota = parse_texto(NOTA_SIMPLES)
    tabela = {"HGLG11": "11.728.688/0001-47", "XPLG11": "26.502.794/0001-85"}
    ops = converter_para_operacoes(nota, cnpj_por_ticker=tabela)
    assert ops[0].cnpj == "11.728.688/0001-47"
    assert ops[1].cnpj == "26.502.794/0001-85"


def test_nota_sem_fii_retorna_lista_vazia():
    nota = """
Nr. nota 1 Folha Data pregão
                 01/04/2025
Negócios realizados
1-BOVESPA C VISTA   PETR4 ON N1     100  30,00  3.000,00 D
Resumo Financeiro
 Taxa de liquidação                                  1,00 D
"""
    parsed = parse_texto(nota)
    assert converter_para_operacoes(parsed, apenas_fii=True) == []
