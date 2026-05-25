"""
Cenários fiscais que o motor precisa acertar.

Cada teste é um cenário do mundo real — não apenas unit tests de função,
mas validação de regra fiscal ponta a ponta.
"""

from datetime import date
from decimal import Decimal

import pytest

from core.calculadora import apurar
from core.darf import gerar_darf
from core.models import EstadoFiscal, Modalidade, Operacao, TipoOperacao

D = Decimal


def op(d, ticker, tipo, qtd, preco, custos="0", irrf="0", cnpj=None):
    return Operacao(
        data=d,
        ticker=ticker,
        cnpj=cnpj or f"CNPJ-{ticker}",
        tipo=TipoOperacao(tipo),
        quantidade=qtd,
        preco_unitario=D(str(preco)),
        custos=D(str(custos)),
        irrf=D(str(irrf)),
    )


# ──────────────────────────────────────────────────────────────────────────
# 1. Custo médio ponderado
# ──────────────────────────────────────────────────────────────────────────

def test_custo_medio_ponderado_em_duas_compras():
    """100 cotas a R$100 + 100 a R$120 → custo médio R$110. Venda a R$130 → lucro R$2000."""
    ops = [
        op(date(2025, 1, 10), "HGLG11", "C", 100, "100.00"),
        op(date(2025, 2, 10), "HGLG11", "C", 100, "120.00"),
        op(date(2025, 3, 10), "HGLG11", "V", 100, "130.00"),
    ]
    apuracoes, estado = apurar(ops)
    mar = apuracoes[-1]
    assert mar.resultado_swing == D("2000.00")
    assert mar.ir_swing == D("400.00")  # 20% de 2000
    assert estado.posicoes["CNPJ-HGLG11"].quantidade == 100
    assert estado.posicoes["CNPJ-HGLG11"].custo_medio == D("110.00")


def test_custos_de_corretagem_entram_no_custo_medio():
    """Corretagem na compra aumenta o custo de aquisição. Corretagem na venda reduz a receita."""
    ops = [
        op(date(2025, 1, 10), "HGLG11", "C", 100, "100.00", custos="50.00"),  # custo total 10050
        op(date(2025, 2, 10), "HGLG11", "V", 100, "110.00", custos="30.00"),  # receita líquida 10970
    ]
    apuracoes, _ = apurar(ops)
    # resultado = 10970 - 10050 = 920
    assert apuracoes[-1].resultado_swing == D("920.00")


# ──────────────────────────────────────────────────────────────────────────
# 2. NÃO há isenção de R$ 20 mil (diferente de ações)
# ──────────────────────────────────────────────────────────────────────────

def test_venda_pequena_ainda_tributa():
    """FII tributa qualquer lucro — não há mínimo de isenção."""
    ops = [
        op(date(2025, 1, 10), "XPLG11", "C", 10, "100.00"),
        op(date(2025, 2, 10), "XPLG11", "V", 10, "150.00"),  # lucro R$500, valor R$1500
    ]
    apuracoes, _ = apurar(ops)
    assert apuracoes[-1].resultado_swing == D("500.00")
    assert apuracoes[-1].ir_swing == D("100.00")  # 20% de 500 — tributado mesmo abaixo de R$20k


# ──────────────────────────────────────────────────────────────────────────
# 3. Compensação de prejuízos
# ──────────────────────────────────────────────────────────────────────────

def test_prejuizo_de_um_mes_compensa_lucro_do_seguinte():
    ops = [
        op(date(2025, 1, 5), "HGLG11", "C", 100, "100.00"),
        op(date(2025, 1, 20), "HGLG11", "V", 100, "80.00"),   # prejuízo 2000
        op(date(2025, 2, 5), "XPLG11", "C", 100, "100.00"),
        op(date(2025, 2, 20), "XPLG11", "V", 100, "150.00"),  # lucro 5000
    ]
    apuracoes, estado = apurar(ops)
    fev = apuracoes[1]
    assert fev.resultado_swing == D("5000.00")
    assert fev.prejuizo_swing_compensado == D("2000.00")
    assert fev.base_swing == D("3000.00")
    assert fev.ir_swing == D("600.00")  # 20% de 3000
    assert estado.prejuizo_acumulado_swing == D("0.00")


def test_prejuizo_swing_nao_compensa_lucro_day_trade():
    """Regra crítica: swing e day trade têm buckets separados de prejuízo."""
    ops = [
        # Janeiro: prejuízo em swing
        op(date(2025, 1, 5), "HGLG11", "C", 100, "100.00"),
        op(date(2025, 1, 20), "HGLG11", "V", 100, "80.00"),  # prejuízo swing 2000
        # Fevereiro: lucro em day trade (mesma data: compra + venda)
        op(date(2025, 2, 10), "XPLG11", "C", 100, "100.00"),
        op(date(2025, 2, 10), "XPLG11", "V", 100, "110.00"),  # lucro day trade 1000
    ]
    apuracoes, estado = apurar(ops)
    fev = apuracoes[1]
    assert fev.resultado_day == D("1000.00")
    assert fev.prejuizo_day_compensado == D("0.00")  # não usa prejuízo de swing
    assert fev.ir_day == D("200.00")
    assert estado.prejuizo_acumulado_swing == D("2000.00")  # continua intacto


# ──────────────────────────────────────────────────────────────────────────
# 4. Day trade
# ──────────────────────────────────────────────────────────────────────────

def test_day_trade_nao_altera_custo_medio_da_posicao():
    """
    Posição inicial de 100 cotas a R$100. No dia 10, day trade de 50 cotas
    (compra a R$105, venda a R$108). Custo médio do estoque NÃO muda.
    """
    ops = [
        op(date(2025, 1, 5), "HGLG11", "C", 100, "100.00"),
        op(date(2025, 1, 10), "HGLG11", "C", 50, "105.00"),  # vira day trade
        op(date(2025, 1, 10), "HGLG11", "V", 50, "108.00"),  # vira day trade
    ]
    apuracoes, estado = apurar(ops)
    pos = estado.posicoes["CNPJ-HGLG11"]
    assert pos.quantidade == 100
    assert pos.custo_medio == D("100.00")  # inalterado
    jan = apuracoes[0]
    assert jan.resultado_day == D("150.00")  # (108-105)*50
    assert jan.resultado_swing == D("0.00")


def test_day_trade_parcial_o_excedente_vira_swing():
    """
    Compra 100 e vende 60 no mesmo dia: 60 viram day trade, 40 ficam em estoque.
    """
    ops = [
        op(date(2025, 1, 10), "HGLG11", "C", 100, "100.00"),
        op(date(2025, 1, 10), "HGLG11", "V", 60, "105.00"),
    ]
    apuracoes, estado = apurar(ops)
    jan = apuracoes[0]
    assert jan.resultado_day == D("300.00")  # (105-100)*60
    assert estado.posicoes["CNPJ-HGLG11"].quantidade == 40
    assert estado.posicoes["CNPJ-HGLG11"].custo_medio == D("100.00")


# ──────────────────────────────────────────────────────────────────────────
# 5. IRRF (dedo-duro)
# ──────────────────────────────────────────────────────────────────────────

def test_irrf_swing_abate_do_ir_devido():
    ops = [
        op(date(2025, 1, 5), "HGLG11", "C", 100, "100.00"),
        op(date(2025, 1, 20), "HGLG11", "V", 100, "150.00", irrf="0.75"),  # 0,005% de 15000
    ]
    apuracoes, _ = apurar(ops)
    jan = apuracoes[0]
    assert jan.ir_swing == D("1000.00")     # 20% de 5000
    assert jan.irrf_swing == D("0.75")
    # ir_a_pagar = 1000 - 0.75
    assert jan.ir_a_pagar == D("999.25")


def test_irrf_excedente_acumula_para_meses_seguintes():
    """Se o IRRF retido > IR devido, o saldo é compensável no futuro."""
    ops = [
        # Janeiro: prejuízo, mas IRRF retido em venda com lucro pontual
        op(date(2025, 1, 5), "HGLG11", "C", 100, "100.00"),
        op(date(2025, 1, 20), "HGLG11", "V", 100, "80.00", irrf="0.40"),  # prejuízo, mas IRRF retido
        # Fevereiro: lucro, IR devido absorve o IRRF acumulado
        op(date(2025, 2, 5), "XPLG11", "C", 100, "100.00"),
        op(date(2025, 2, 20), "XPLG11", "V", 100, "150.00"),  # lucro 5000, prejuízo já compensa 2000
    ]
    apuracoes, estado = apurar(ops)
    # Fev: base = 5000 - 2000 = 3000; IR = 600; IRRF disponível = 0.40 → ir_a_pagar = 599.60
    assert apuracoes[1].ir_a_pagar == D("599.60")
    assert estado.irrf_acumulado_swing == D("0.00")


# ──────────────────────────────────────────────────────────────────────────
# 6. Regra do DARF mínimo (R$ 10)
# ──────────────────────────────────────────────────────────────────────────

def test_darf_abaixo_de_10_reais_acumula_para_o_mes_seguinte():
    """Lucro de R$40 → IR de R$8 < R$10. Não paga, acumula."""
    ops = [
        op(date(2025, 1, 5), "HGLG11", "C", 10, "100.00"),
        op(date(2025, 1, 20), "HGLG11", "V", 10, "104.00"),  # lucro 40, IR 8
    ]
    apuracoes, estado = apurar(ops)
    assert apuracoes[0].ir_swing == D("8.00")
    assert apuracoes[0].ir_a_pagar == D("0.00")  # abaixo de R$10
    assert estado.darf_acumulado == D("8.00")


def test_darf_acumulado_some_quando_atinge_o_minimo():
    ops = [
        # Janeiro: lucro pequeno, IR R$8 → acumula
        op(date(2025, 1, 5), "HGLG11", "C", 10, "100.00"),
        op(date(2025, 1, 20), "HGLG11", "V", 10, "104.00"),
        # Fevereiro: outro lucro pequeno, IR R$8 → total R$16, paga
        op(date(2025, 2, 5), "XPLG11", "C", 10, "100.00"),
        op(date(2025, 2, 20), "XPLG11", "V", 10, "104.00"),
    ]
    apuracoes, estado = apurar(ops)
    assert apuracoes[1].ir_a_pagar == D("16.00")
    assert estado.darf_acumulado == D("0.00")


# ──────────────────────────────────────────────────────────────────────────
# 7. Geração do DARF
# ──────────────────────────────────────────────────────────────────────────

def test_darf_gerado_com_codigo_e_vencimento_corretos():
    ops = [
        op(date(2025, 1, 5), "HGLG11", "C", 100, "100.00"),
        op(date(2025, 1, 20), "HGLG11", "V", 100, "150.00"),
    ]
    apuracoes, _ = apurar(ops)
    darf = gerar_darf(apuracoes[0])
    assert darf is not None
    assert darf.codigo_receita == "6015"
    assert darf.periodo_apuracao == date(2025, 1, 31)
    # Último dia útil de fevereiro/2025 = 28/02 (sexta)
    assert darf.data_vencimento == date(2025, 2, 28)
    assert darf.valor_principal == D("1000.00")


def test_darf_de_dezembro_vence_em_janeiro():
    ops = [
        op(date(2025, 11, 5), "HGLG11", "C", 100, "100.00"),
        op(date(2025, 12, 20), "HGLG11", "V", 100, "150.00"),
    ]
    apuracoes, _ = apurar(ops)
    darf = gerar_darf(apuracoes[-1])
    assert darf is not None
    # Último dia útil de janeiro/2026 = 30/01 (sexta) — 31 é sábado
    assert darf.data_vencimento == date(2026, 1, 30)


# ──────────────────────────────────────────────────────────────────────────
# 8. Estado inicial (posição custodiada antes de começar a usar a app)
# ──────────────────────────────────────────────────────────────────────────

def test_estado_inicial_permite_continuar_de_uma_posicao_antiga():
    from core.models import Posicao
    estado = EstadoFiscal(
        posicoes={"CNPJ-HGLG11": Posicao(
            cnpj="CNPJ-HGLG11", ticker="HGLG11",
            quantidade=200, custo_total=D("20000.00"),
        )},
        prejuizo_acumulado_swing=D("500.00"),
    )
    ops = [op(date(2025, 6, 10), "HGLG11", "V", 100, "120.00")]
    apuracoes, estado_final = apurar(ops, estado_inicial=estado)
    # custo médio era 100; vende a 120 → lucro 2000; compensa 500 de prejuízo → base 1500
    assert apuracoes[0].resultado_swing == D("2000.00")
    assert apuracoes[0].prejuizo_swing_compensado == D("500.00")
    assert apuracoes[0].ir_a_pagar == D("300.00")  # 20% de 1500
    assert estado_final.posicoes["CNPJ-HGLG11"].quantidade == 100
