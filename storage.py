"""
Persistência via export/import manual de JSON.

A app hospedada não grava em disco (disco efêmero no Streamlit Cloud).
O estado vive em `st.session_state` durante a sessão; o usuário baixa
um backup quando quiser e sobe quando voltar.

Para desenvolvimento local também usamos o mesmo fluxo — uniforme entre dev e prod.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from core.models import EstadoFiscal, Modalidade, Operacao, Posicao, TipoOperacao


def _default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, (TipoOperacao, Modalidade)):
        return o.value
    raise TypeError(f"Não serializável: {type(o)}")


def serializar(operacoes: list[Operacao], estado_inicial: EstadoFiscal) -> bytes:
    payload = {
        "versao": 1,
        "operacoes": [asdict(o) for o in operacoes],
        "estado_inicial": {
            "posicoes": {k: asdict(v) for k, v in estado_inicial.posicoes.items()},
            "prejuizo_acumulado_swing": str(estado_inicial.prejuizo_acumulado_swing),
            "prejuizo_acumulado_day": str(estado_inicial.prejuizo_acumulado_day),
            "irrf_acumulado_swing": str(estado_inicial.irrf_acumulado_swing),
            "irrf_acumulado_day": str(estado_inicial.irrf_acumulado_day),
            "darf_acumulado": str(estado_inicial.darf_acumulado),
        },
    }
    return json.dumps(payload, default=_default, indent=2, ensure_ascii=False).encode("utf-8")


def deserializar(blob: bytes) -> tuple[list[Operacao], EstadoFiscal]:
    data = json.loads(blob.decode("utf-8"))

    operacoes = [
        Operacao(
            data=date.fromisoformat(o["data"]),
            ticker=o["ticker"],
            cnpj=o["cnpj"],
            tipo=TipoOperacao(o["tipo"]),
            quantidade=int(o["quantidade"]),
            preco_unitario=Decimal(o["preco_unitario"]),
            custos=Decimal(o["custos"]),
            modalidade=Modalidade(o["modalidade"]),
            irrf=Decimal(o["irrf"]),
        )
        for o in data.get("operacoes", [])
    ]

    e = data.get("estado_inicial", {})
    estado = EstadoFiscal(
        posicoes={
            k: Posicao(
                cnpj=v["cnpj"], ticker=v["ticker"],
                quantidade=int(v["quantidade"]),
                custo_total=Decimal(v["custo_total"]),
            )
            for k, v in e.get("posicoes", {}).items()
        },
        prejuizo_acumulado_swing=Decimal(e.get("prejuizo_acumulado_swing", "0")),
        prejuizo_acumulado_day=Decimal(e.get("prejuizo_acumulado_day", "0")),
        irrf_acumulado_swing=Decimal(e.get("irrf_acumulado_swing", "0")),
        irrf_acumulado_day=Decimal(e.get("irrf_acumulado_day", "0")),
        darf_acumulado=Decimal(e.get("darf_acumulado", "0")),
    )
    return operacoes, estado
