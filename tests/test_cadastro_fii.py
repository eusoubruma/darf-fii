"""Testes do cadastro de FIIs listados na B3."""

from core.cadastro_fii import (
    carregar_cadastro,
    eh_fii_listado,
    fundo_do_ticker,
)


def test_cadastro_carrega_centenas_de_fundos():
    cadastro = carregar_cadastro()
    assert len(cadastro) > 400, f"Esperado >400 FIIs, obtive {len(cadastro)}"


def test_hglg11_eh_fii_conhecido():
    assert eh_fii_listado("HGLG11")
    fundo = fundo_do_ticker("HGLG11")
    assert fundo is not None
    assert fundo.codigo == "HGLG"
    assert fundo.razao_social  # algum nome
    assert fundo.fundo


def test_etfs_nao_sao_fii():
    """BOVA11, IVVB11, SMAL11 são ETFs — não devem aparecer no cadastro."""
    assert not eh_fii_listado("BOVA11")
    assert not eh_fii_listado("IVVB11")


def test_acoes_nao_sao_fii():
    assert not eh_fii_listado("PETR4")
    assert not eh_fii_listado("VALE3")


def test_aceita_ticker_lowercase():
    assert eh_fii_listado("hglg11")


def test_diferentes_classes_do_mesmo_fundo():
    """Mesmo código com sufixos diferentes (ex.: XXXX11, XXXX12) — todos válidos."""
    cadastro = carregar_cadastro()
    # Escolhe qualquer código do cadastro e verifica que reconhece com sufixo 12, 13
    primeiro_codigo = next(iter(cadastro))
    assert eh_fii_listado(primeiro_codigo + "11")
    assert eh_fii_listado(primeiro_codigo + "12")
