"""
Parser do extrato de **Negociação** da B3 (Canal Eletrônico do Investidor).

O arquivo XLSX da B3 tem schema estável (colunas com nomes em português)
e é uma fonte oficial — mais confiável do que parsing de PDF.

Colunas esperadas (B3, 2024-2025):
    Entrada/Saída | Data do Negócio | Movimentação | Mercado |
    Prazo/Vencimento | Instituição | Código de Negociação |
    Quantidade | Preço | Valor

Observações:
- O extrato da B3 NÃO traz taxas/corretagem nem IRRF — esses dados só estão
  nas notas de corretagem (PDF). O usuário precisa lançar manualmente se
  quiser refletir custos com precisão; do contrário, é uma aproximação razoável
  (taxas da B3 são, na ordem de grandeza, < 0,03% do volume).
- A B3 ainda não fornece CNPJ do fundo — uso "TICKER-XXX" como chave por padrão.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from core.cadastro_fii import eh_fii_listado
from core.models import Modalidade, Operacao, TipoOperacao

ZERO = Decimal("0")


def _normalizar(s: str) -> str:
    """minúsculas, sem acento, sem espaços extras — para matching de colunas."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


# Mapeamento tolerante: aceita pequenas variações no nome da coluna
_COLUNAS = {
    "tipo": ["entrada/saida", "entrada saida", "movimentacao", "tipo de movimentacao", "tipo"],
    "data": ["data do negocio", "data negocio", "data"],
    "ticker": ["codigo de negociacao", "ticker", "papel", "ativo", "codigo"],
    "quantidade": ["quantidade", "qtd", "qtde"],
    "preco": ["preco", "preco unitario", "preco/ajuste"],
    "valor": ["valor", "valor operacao", "valor da operacao"],
    "instituicao": ["instituicao", "corretora"],
}



def _mapear_colunas(df: pd.DataFrame) -> dict[str, str]:
    """Identifica qual coluna do DataFrame corresponde a cada campo lógico."""
    normalizadas = {_normalizar(c): c for c in df.columns}
    resultado: dict[str, str] = {}
    for chave_logica, candidatos in _COLUNAS.items():
        for cand in candidatos:
            if cand in normalizadas:
                resultado[chave_logica] = normalizadas[cand]
                break
    return resultado


def _interpretar_tipo(valor: str) -> TipoOperacao | None:
    """
    A B3 usa diferentes convenções:
    - 'Credito'/'Debito' (do ponto de vista da custódia: crédito = recebeu = compra)
    - 'Compra'/'Venda'
    """
    v = _normalizar(valor)
    if v in ("compra", "credito", "c"):
        return TipoOperacao.COMPRA
    if v in ("venda", "debito", "v"):
        return TipoOperacao.VENDA
    return None


def _para_decimal(v) -> Decimal:
    if pd.isna(v):
        return ZERO
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = str(v).strip().replace("R$", "").replace(".", "").replace(",", ".").strip()
    return Decimal(s) if s else ZERO


def _para_data(v) -> date | None:
    if pd.isna(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _eh_ticker_fii(ticker: str) -> bool:
    """Usa o cadastro oficial da B3 — descarta ETFs como BOVA11."""
    return eh_fii_listado(ticker)


def parse_xlsx(
    caminho: Path | str,
    *,
    apenas_fii: bool = True,
    cnpj_por_ticker: dict[str, str] | None = None,
    sheet_name: int | str = 0,
) -> list[Operacao]:
    """
    Lê o XLSX e devolve operações no formato do motor fiscal.

    `apenas_fii`: filtra para apenas tickers no padrão FII (XXXX11).
                  Para refinar contra ETFs, prefira casar com tabela de CNPJs.
    `cnpj_por_ticker`: tabela opcional para preencher o CNPJ real do fundo.
    """
    df = pd.read_excel(caminho, sheet_name=sheet_name)
    return parse_dataframe(df, apenas_fii=apenas_fii, cnpj_por_ticker=cnpj_por_ticker)


def parse_dataframe(
    df: pd.DataFrame,
    *,
    apenas_fii: bool = True,
    cnpj_por_ticker: dict[str, str] | None = None,
) -> list[Operacao]:
    """
    Versão direta sobre DataFrame — útil para testes e para reuso quando a
    leitura do XLSX já foi feita em outro lugar (ex.: skip de linhas de cabeçalho).
    """
    mapa = _mapear_colunas(df)
    obrigatorias = {"tipo", "data", "ticker", "quantidade", "preco"}
    faltando = obrigatorias - mapa.keys()
    if faltando:
        raise ValueError(
            f"Colunas obrigatórias não encontradas no XLSX: {sorted(faltando)}. "
            f"Colunas presentes: {list(df.columns)}"
        )

    cnpj_por_ticker = cnpj_por_ticker or {}
    operacoes: list[Operacao] = []
    for _, row in df.iterrows():
        tipo = _interpretar_tipo(row[mapa["tipo"]])
        if tipo is None:
            continue
        data_op = _para_data(row[mapa["data"]])
        if data_op is None:
            continue
        ticker = str(row[mapa["ticker"]]).strip().upper()
        if not ticker or ticker == "NAN":
            continue
        if apenas_fii and not _eh_ticker_fii(ticker):
            continue

        qtd_raw = row[mapa["quantidade"]]
        try:
            quantidade = int(qtd_raw) if not pd.isna(qtd_raw) else 0
        except (ValueError, TypeError):
            quantidade = int(_para_decimal(qtd_raw))
        if quantidade <= 0:
            continue

        preco = _para_decimal(row[mapa["preco"]])
        if preco <= 0:
            continue

        operacoes.append(Operacao(
            data=data_op,
            ticker=ticker,
            cnpj=cnpj_por_ticker.get(ticker, f"TICKER-{ticker}"),
            tipo=tipo,
            quantidade=quantidade,
            preco_unitario=preco,
            custos=ZERO,        # B3 extrato não traz custos
            modalidade=Modalidade.SWING,
            irrf=ZERO,          # nem IRRF
        ))
    return operacoes
