"""
Cadastro de FIIs listados na B3.

Fonte: arquivo `data/fiis_b3.csv` (lista oficial publicada pela B3).
Permite diferenciar FII de ETF (BOVA11 não está na lista) e exibir o nome
do fundo a partir do ticker.

A B3 publica códigos de 4 letras; o ticker é "<código>11" (ou eventualmente
"<código>12", "<código>13" para classes diferentes do mesmo fundo).

A B3 não publica o CNPJ neste arquivo — para CNPJ é preciso outra fonte (CVM).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ARQUIVO_CADASTRO = Path(__file__).resolve().parent.parent / "data" / "fiis_b3.csv"


@dataclass(frozen=True)
class FundoListado:
    codigo: str          # 4 letras, ex.: "HGLG"
    fundo: str           # nome curto, ex.: "FII CSHG LOG"
    razao_social: str    # razão social completa


@lru_cache(maxsize=1)
def carregar_cadastro() -> dict[str, FundoListado]:
    """Carrega o cadastro indexado por código (4 letras, uppercase)."""
    if not ARQUIVO_CADASTRO.exists():
        return {}
    cadastro: dict[str, FundoListado] = {}
    with open(ARQUIVO_CADASTRO, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            codigo = row["codigo"].strip().upper()
            if codigo:
                cadastro[codigo] = FundoListado(
                    codigo=codigo,
                    fundo=row["fundo"].strip(),
                    razao_social=row["razao_social"].strip(),
                )
    return cadastro


def _raiz(ticker: str) -> str:
    """Devolve as 4 primeiras letras do ticker em uppercase."""
    return ticker.strip().upper()[:4]


def eh_fii_listado(ticker: str) -> bool:
    """True se o ticker corresponde a um FII listado (descarta ETFs, ações)."""
    return _raiz(ticker) in carregar_cadastro()


def fundo_do_ticker(ticker: str) -> FundoListado | None:
    """Retorna metadados do fundo ou None se não encontrado."""
    return carregar_cadastro().get(_raiz(ticker))
