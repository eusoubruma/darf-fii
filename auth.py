"""
Autenticação leve via streamlit-authenticator.

Configuração:
- Em desenvolvimento local: lê `auth_config.yaml` no diretório do projeto.
- Em produção (Streamlit Cloud): lê `st.secrets["auth"]` (TOML).

A estrutura é a mesma em ambos os formatos:
    credentials.usernames.<user>:
        name: "Nome"
        email: "email@..."
        password: "<bcrypt-hash>"
    cookie:
        name: "darf_fii_auth"
        key: "<chave-aleatoria-para-assinar-cookies>"
        expiry_days: 7

Senhas SEMPRE em hash bcrypt — gere com `python gerar_hash.py senha123`.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit_authenticator as stauth
import yaml
from yaml import SafeLoader

ARQUIVO_LOCAL = Path(__file__).resolve().parent / "auth_config.yaml"


def _carregar_config() -> dict:
    """Carrega config de `st.secrets` (produção) ou do YAML local (dev)."""
    try:
        secrets_auth = st.secrets.get("auth")
    except Exception:
        secrets_auth = None
    if secrets_auth:
        return _to_dict(secrets_auth)
    if ARQUIVO_LOCAL.exists():
        return yaml.load(ARQUIVO_LOCAL.read_text(encoding="utf-8"), Loader=SafeLoader)
    raise RuntimeError(
        "Configuração de autenticação não encontrada. "
        f"Crie `{ARQUIVO_LOCAL.name}` (veja `auth_config.yaml.example`) "
        "ou configure secrets no Streamlit Cloud."
    )


def _to_dict(obj):
    """Converte recursivamente st.secrets Mapping → dict (necessário para stauth)."""
    if hasattr(obj, "items"):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dict(v) for v in obj]
    return obj


def autenticar() -> tuple[str | None, "stauth.Authenticate | None"]:
    """
    Renderiza o formulário de login (se necessário) e devolve `(username, authenticator)`.

    Retorna `(None, None)` se o usuário não está autenticado — a página chamadora
    deve interromper a renderização do conteúdo protegido nesse caso.
    """
    config = _carregar_config()
    authenticator = stauth.Authenticate(
        config["credentials"],
        config["cookie"]["name"],
        config["cookie"]["key"],
        config["cookie"]["expiry_days"],
    )
    authenticator.login(location="main")
    status = st.session_state.get("authentication_status")
    if status is True:
        return st.session_state.get("username"), authenticator
    if status is False:
        st.error("Usuário ou senha incorretos.")
    elif status is None:
        st.info("Faça login para acessar a calculadora.")
    return None, None
