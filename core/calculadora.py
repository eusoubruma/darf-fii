"""
Motor fiscal de apuração de IR sobre operações com FII (PF).

Regras implementadas:
1. Custo médio ponderado por CNPJ do fundo.
2. Segregação swing trade vs day trade (mesmo CNPJ, mesma data → day trade).
3. Resultado por venda = (preço * qtd - custos) - (custo_médio * qtd).
4. Compensação de prejuízos: swing só com swing, day só com day. Sem prazo.
5. Alíquota de 20% sobre o ganho líquido (após compensação).
6. IRRF abate IR devido; sobra acumula para meses seguintes.
7. Regra dos R$ 10: DARF < R$10 acumula para o mês seguinte (mesmo código 6015).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from itertools import groupby

from .models import (
    ZERO,
    ApuracaoMensal,
    EstadoFiscal,
    Modalidade,
    Operacao,
    Posicao,
    ResultadoVenda,
    TipoOperacao,
)

ALIQUOTA_FII = Decimal("0.20")
DARF_MINIMO = Decimal("10.00")


def _q(v: Decimal) -> Decimal:
    """Arredonda para 2 casas (centavos), modo bancário fiscal (half-up)."""
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _classificar_modalidade(operacoes_do_dia: list[Operacao]) -> list[Operacao]:
    """
    Reclassifica operações de um único dia/CNPJ identificando day trade.

    Day trade = quantidade comprada e vendida do mesmo CNPJ no mesmo dia.
    O menor dos dois lados (compra/venda) é day trade; o excedente é swing.
    """
    qtd_compra = sum(o.quantidade for o in operacoes_do_dia if o.tipo == TipoOperacao.COMPRA)
    qtd_venda = sum(o.quantidade for o in operacoes_do_dia if o.tipo == TipoOperacao.VENDA)
    qtd_day = min(qtd_compra, qtd_venda)

    if qtd_day == 0:
        return operacoes_do_dia  # tudo swing

    reclassificadas: list[Operacao] = []
    restante_day = {TipoOperacao.COMPRA: qtd_day, TipoOperacao.VENDA: qtd_day}

    for op in operacoes_do_dia:
        if restante_day[op.tipo] == 0:
            reclassificadas.append(op)
            continue
        qtd_dt = min(op.quantidade, restante_day[op.tipo])
        restante_day[op.tipo] -= qtd_dt
        qtd_st = op.quantidade - qtd_dt

        # Rateio proporcional de custos e IRRF entre as duas partes
        proporcao_dt = Decimal(qtd_dt) / Decimal(op.quantidade)
        custos_dt = _q(op.custos * proporcao_dt)
        irrf_dt = _q(op.irrf * proporcao_dt)

        from dataclasses import replace
        reclassificadas.append(replace(
            op, quantidade=qtd_dt, custos=custos_dt, irrf=irrf_dt,
            modalidade=Modalidade.DAY_TRADE,
        ))
        if qtd_st > 0:
            reclassificadas.append(replace(
                op, quantidade=qtd_st,
                custos=op.custos - custos_dt, irrf=op.irrf - irrf_dt,
                modalidade=Modalidade.SWING,
            ))
    return reclassificadas


def classificar_operacoes(operacoes: list[Operacao]) -> list[Operacao]:
    """Aplica classificação de day trade em todas as operações."""
    chave = lambda o: (o.data, o.cnpj)
    ops_ordenadas = sorted(operacoes, key=chave)
    resultado: list[Operacao] = []
    for _, grupo in groupby(ops_ordenadas, key=chave):
        resultado.extend(_classificar_modalidade(list(grupo)))
    return resultado


def _processar_compra(estado: EstadoFiscal, op: Operacao) -> None:
    """
    Atualiza custo médio. Day trade NÃO afeta o custo médio do estoque
    (a posição comprada é integralmente liquidada no mesmo dia).
    """
    if op.modalidade == Modalidade.DAY_TRADE:
        return
    pos = estado.posicoes.setdefault(op.cnpj, Posicao(cnpj=op.cnpj, ticker=op.ticker))
    pos.quantidade += op.quantidade
    pos.custo_total += op.valor_liquido  # preço * qtd + custos
    pos.ticker = op.ticker  # mantém ticker atualizado (raro mudar, mas acontece)


def _processar_venda(
    estado: EstadoFiscal, op: Operacao
) -> ResultadoVenda:
    """
    Calcula resultado da venda. Para day trade, o "custo" é o preço médio
    das compras do mesmo dia/CNPJ — calculado pelo chamador via pareamento.
    Aqui assumimos que op.modalidade=DAY_TRADE já vem com qtd casada.
    """
    if op.modalidade == Modalidade.DAY_TRADE:
        # Para day trade, o custo é apurado contra as compras do mesmo dia.
        # Tratado em _apurar_day_trade_do_dia para garantir o pareamento.
        raise RuntimeError("Day trade deve ser apurado em bloco — não chamar aqui.")

    pos = estado.posicoes.get(op.cnpj)
    if pos is None or pos.quantidade < op.quantidade:
        raise ValueError(
            f"Venda de {op.quantidade} cotas de {op.ticker} em {op.data} "
            f"sem posição suficiente (atual: {pos.quantidade if pos else 0})."
        )

    custo_medio = pos.custo_medio
    custo_baixa = custo_medio * op.quantidade
    resultado = op.valor_liquido - custo_baixa  # valor_liquido já desconta custos da venda

    pos.quantidade -= op.quantidade
    pos.custo_total -= custo_baixa
    if pos.quantidade == 0:
        pos.custo_total = ZERO  # evita resíduo de arredondamento

    return ResultadoVenda(
        operacao=op,
        custo_medio_aplicado=_q(custo_medio),
        resultado=_q(resultado),
        modalidade=Modalidade.SWING,
    )


def _apurar_day_trade_do_dia(
    operacoes_dt: list[Operacao],
) -> list[ResultadoVenda]:
    """
    Apura day trade de um único dia/CNPJ.

    Custo médio do day trade = média ponderada das compras do dia.
    Resultado = soma(vendas líquidas) - custo_medio * qtd_vendida.
    Retorna um ResultadoVenda consolidado por dia/CNPJ (suficiente para apuração mensal).
    """
    compras = [o for o in operacoes_dt if o.tipo == TipoOperacao.COMPRA]
    vendas = [o for o in operacoes_dt if o.tipo == TipoOperacao.VENDA]
    if not compras or not vendas:
        return []

    qtd_compra = sum(o.quantidade for o in compras)
    qtd_venda = sum(o.quantidade for o in vendas)
    assert qtd_compra == qtd_venda, "Day trade deve ter quantidades casadas após classificação."

    custo_total_compras = sum((o.valor_liquido for o in compras), ZERO)  # inclui custos
    receita_total_vendas = sum((o.valor_liquido for o in vendas), ZERO)  # já líquida de custos
    resultado = receita_total_vendas - custo_total_compras

    # Representamos o resultado consolidado anexado à primeira venda (apenas para histórico).
    op_representativa = vendas[0]
    custo_medio_dt = custo_total_compras / qtd_compra if qtd_compra else ZERO
    return [ResultadoVenda(
        operacao=op_representativa,
        custo_medio_aplicado=_q(custo_medio_dt),
        resultado=_q(resultado),
        modalidade=Modalidade.DAY_TRADE,
    )]


def _apurar_mes(
    estado: EstadoFiscal,
    ano: int,
    mes: int,
    operacoes_do_mes: list[Operacao],
) -> ApuracaoMensal:
    apuracao = ApuracaoMensal(ano=ano, mes=mes)

    # Ordena por data e separa por dia/CNPJ para tratar day trade em bloco
    ops_ordenadas = sorted(operacoes_do_mes, key=lambda o: (o.data, o.cnpj))

    for (_, _), grupo in groupby(ops_ordenadas, key=lambda o: (o.data, o.cnpj)):
        grupo_lista = list(grupo)
        ops_swing = [o for o in grupo_lista if o.modalidade == Modalidade.SWING]
        ops_dt = [o for o in grupo_lista if o.modalidade == Modalidade.DAY_TRADE]

        # 1) Day trade do dia: apura em bloco (compras não vão para estoque)
        for resultado in _apurar_day_trade_do_dia(ops_dt):
            apuracao.vendas.append(resultado)
            apuracao.resultado_day += resultado.resultado
            apuracao.irrf_day += sum((o.irrf for o in ops_dt if o.tipo == TipoOperacao.VENDA), ZERO)

        # 2) Swing: compras atualizam custo médio, vendas geram resultado
        for op in ops_swing:
            if op.tipo == TipoOperacao.COMPRA:
                _processar_compra(estado, op)
            else:
                resultado = _processar_venda(estado, op)
                apuracao.vendas.append(resultado)
                apuracao.resultado_swing += resultado.resultado
                apuracao.irrf_swing += op.irrf

    # 3) Compensação de prejuízos acumulados (com sinal contrário)
    if apuracao.resultado_swing > 0:
        compensa = min(apuracao.resultado_swing, estado.prejuizo_acumulado_swing)
        apuracao.prejuizo_swing_compensado = compensa
        apuracao.base_swing = apuracao.resultado_swing - compensa
        estado.prejuizo_acumulado_swing -= compensa
    else:
        # Prejuízo do mês vai para o acumulado
        estado.prejuizo_acumulado_swing += -apuracao.resultado_swing
        apuracao.base_swing = ZERO

    if apuracao.resultado_day > 0:
        compensa = min(apuracao.resultado_day, estado.prejuizo_acumulado_day)
        apuracao.prejuizo_day_compensado = compensa
        apuracao.base_day = apuracao.resultado_day - compensa
        estado.prejuizo_acumulado_day -= compensa
    else:
        estado.prejuizo_acumulado_day += -apuracao.resultado_day
        apuracao.base_day = ZERO

    # 4) IR devido (20%)
    apuracao.ir_swing = _q(apuracao.base_swing * ALIQUOTA_FII)
    apuracao.ir_day = _q(apuracao.base_day * ALIQUOTA_FII)

    # 5) IRRF acumulado + do mês abate IR devido (separado por modalidade)
    irrf_swing_disp = estado.irrf_acumulado_swing + apuracao.irrf_swing
    irrf_day_disp = estado.irrf_acumulado_day + apuracao.irrf_day

    ir_swing_liquido = max(ZERO, apuracao.ir_swing - irrf_swing_disp)
    ir_day_liquido = max(ZERO, apuracao.ir_day - irrf_day_disp)

    estado.irrf_acumulado_swing = max(ZERO, irrf_swing_disp - apuracao.ir_swing)
    estado.irrf_acumulado_day = max(ZERO, irrf_day_disp - apuracao.ir_day)

    ir_total = _q(ir_swing_liquido + ir_day_liquido)

    # 6) Regra do DARF mínimo (R$10): acumula com o saldo de meses anteriores
    ir_total_acumulado = ir_total + estado.darf_acumulado
    if ir_total_acumulado < DARF_MINIMO:
        estado.darf_acumulado = ir_total_acumulado
        apuracao.ir_a_pagar = ZERO
    else:
        apuracao.ir_a_pagar = _q(ir_total_acumulado)
        estado.darf_acumulado = ZERO

    apuracao.irrf_a_compensar = _q(
        estado.irrf_acumulado_swing + estado.irrf_acumulado_day
    )
    return apuracao


def apurar(
    operacoes: list[Operacao],
    estado_inicial: EstadoFiscal | None = None,
) -> tuple[list[ApuracaoMensal], EstadoFiscal]:
    """
    Apura todas as operações cronologicamente, retornando uma apuração por mês
    em que houve movimento e o estado final (posições + prejuízos + acumulados).

    O `estado_inicial` permite continuar a apuração a partir de uma posição
    pré-existente (custódia antiga, prejuízos declarados em anos anteriores).
    """
    estado = estado_inicial or EstadoFiscal()
    ops_classificadas = classificar_operacoes(operacoes)
    ops_classificadas.sort(key=lambda o: o.data)

    apuracoes: list[ApuracaoMensal] = []
    chave_mes = lambda o: (o.data.year, o.data.month)
    for (ano, mes), grupo in groupby(ops_classificadas, key=chave_mes):
        apuracoes.append(_apurar_mes(estado, ano, mes, list(grupo)))
    return apuracoes, estado
