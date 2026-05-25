"""
Gera hash bcrypt para uma senha — utilitário para popular `auth_config.yaml`.

Uso:
    python gerar_hash.py minha_senha_aqui

Cole o hash gerado no campo `password:` do usuário correspondente.
"""

import sys

import streamlit_authenticator as stauth


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python gerar_hash.py <senha>", file=sys.stderr)
        return 1
    senha = sys.argv[1]
    hashes = stauth.Hasher().hash_list([senha])
    print(hashes[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
