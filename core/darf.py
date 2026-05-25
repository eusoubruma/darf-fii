"""
Geração dos dados do DARF a partir da apuração mensal.

Não gera o boleto/código de barras (isso depende de integração com Sicalc).
Produz os campos que o contribuinte preenche manualmente no Sicalc/portal e-CAC.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .models import ApuracaoMensal

CODIGO_RECEITA_FII = "6015"  # Ganhos líquidos em renda variável — PF


@dataclass
class DARF:
    codigo_receita: str
    periodo_apuracao: date    # último dia do mês de apuração
    data_vencimento: date     # último dia útil do mês seguinte
    valor_principal: Decimal


def _ultimo_dia_util(d: date) -> date:
    """Recua até cair em dia útil (seg-sex). Não considera feriados nacionais."""
    while d.weekday() >= 5:  # 5=sáb, 6=dom
        d -= timedelta(days=1)
    return d


def gerar_darf(apuracao: ApuracaoMensal) -> DARF | None:
    """Retorna o DARF do mês ou None se não há valor a pagar (R$0 ou abaixo do mínimo)."""
    if apuracao.ir_a_pagar <= 0:
        return None

    ultimo_dia_apuracao = date(
        apuracao.ano, apuracao.mes,
        calendar.monthrange(apuracao.ano, apuracao.mes)[1],
    )
    # Vencimento: último dia útil do mês SEGUINTE à apuração
    if apuracao.mes == 12:
        ano_venc, mes_venc = apuracao.ano + 1, 1
    else:
        ano_venc, mes_venc = apuracao.ano, apuracao.mes + 1
    ultimo_dia_venc = date(ano_venc, mes_venc, calendar.monthrange(ano_venc, mes_venc)[1])

    return DARF(
        codigo_receita=CODIGO_RECEITA_FII,
        periodo_apuracao=ultimo_dia_apuracao,
        data_vencimento=_ultimo_dia_util(ultimo_dia_venc),
        valor_principal=apuracao.ir_a_pagar,
    )
