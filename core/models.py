"""
Modelos de dados do motor fiscal de FII.

Convenções:
- Todos os valores monetários são Decimal (evita erro de ponto flutuante).
- Quantidades são int (cotas de FII são sempre inteiras).
- Datas são `date` (não `datetime`) — o que importa é o dia da operação.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class TipoOperacao(str, Enum):
    COMPRA = "C"
    VENDA = "V"


class Modalidade(str, Enum):
    SWING = "swing"      # posição mantida por mais de um dia
    DAY_TRADE = "day"    # compra e venda do mesmo ativo no mesmo dia


ZERO = Decimal("0")


@dataclass(frozen=True)
class Operacao:
    """Uma linha de uma nota de corretagem (compra ou venda de um FII)."""
    data: date
    ticker: str          # ex.: "HGLG11"
    cnpj: str            # CNPJ do fundo — chave fiscal real (cotas mudam de ticker, CNPJ não)
    tipo: TipoOperacao
    quantidade: int
    preco_unitario: Decimal
    custos: Decimal = ZERO       # corretagem + emolumentos + taxas, rateados nesta operação
    modalidade: Modalidade = Modalidade.SWING
    irrf: Decimal = ZERO         # IRRF retido na fonte ("dedo-duro") nesta operação

    @property
    def valor_bruto(self) -> Decimal:
        return self.preco_unitario * self.quantidade

    @property
    def valor_liquido(self) -> Decimal:
        """Valor financeiro efetivo: bruto + custos na compra, bruto - custos na venda."""
        if self.tipo == TipoOperacao.COMPRA:
            return self.valor_bruto + self.custos
        return self.valor_bruto - self.custos


@dataclass
class Posicao:
    """Posição custodiada de um fundo, com custo médio ponderado."""
    cnpj: str
    ticker: str
    quantidade: int = 0
    custo_total: Decimal = ZERO   # soma de (preço * qtd + custos) das compras remanescentes

    @property
    def custo_medio(self) -> Decimal:
        if self.quantidade == 0:
            return ZERO
        return self.custo_total / self.quantidade


@dataclass
class ResultadoVenda:
    """Resultado fiscal de uma venda específica."""
    operacao: Operacao
    custo_medio_aplicado: Decimal
    resultado: Decimal           # positivo = lucro, negativo = prejuízo
    modalidade: Modalidade


@dataclass
class ApuracaoMensal:
    """Apuração de IR de um mês — separada por modalidade (swing vs day trade)."""
    ano: int
    mes: int

    # Resultados brutos do mês, por modalidade
    resultado_swing: Decimal = ZERO
    resultado_day: Decimal = ZERO

    # Prejuízos acumulados consumidos neste mês
    prejuizo_swing_compensado: Decimal = ZERO
    prejuizo_day_compensado: Decimal = ZERO

    # Base de cálculo após compensação
    base_swing: Decimal = ZERO
    base_day: Decimal = ZERO

    # IR devido bruto (20% sobre cada base)
    ir_swing: Decimal = ZERO
    ir_day: Decimal = ZERO

    # IRRF retido na fonte no mês (abate do IR devido)
    irrf_swing: Decimal = ZERO
    irrf_day: Decimal = ZERO

    # Resultado final
    ir_a_pagar: Decimal = ZERO           # antes da regra dos R$10
    irrf_a_compensar: Decimal = ZERO     # IRRF que sobrou para meses futuros

    vendas: list[ResultadoVenda] = field(default_factory=list)


@dataclass
class EstadoFiscal:
    """Estado persistente entre execuções: posições + prejuízos + DARF acumulado."""
    posicoes: dict[str, Posicao] = field(default_factory=dict)      # chave: CNPJ
    prejuizo_acumulado_swing: Decimal = ZERO
    prejuizo_acumulado_day: Decimal = ZERO
    irrf_acumulado_swing: Decimal = ZERO
    irrf_acumulado_day: Decimal = ZERO
    darf_acumulado: Decimal = ZERO          # IR < R$10 que está esperando atingir o mínimo
