# DARF FII — Calculadora de IR sobre Fundos Imobiliários

App em Streamlit que apura mensalmente o IR sobre operações com FII (PF) e gera o valor do DARF (código 6015) a recolher.

## Funcionalidades

- Registro manual de operações + importação de notas Sinacor (PDF) e extratos B3 (XLSX).
- Custo médio ponderado por CNPJ, segregação swing × day trade, compensação de prejuízos, IRRF, regra do DARF mínimo de R$ 10.
- Cadastro embarcado com ~515 FIIs listados (descarta ETFs automaticamente).
- Autenticação multi-usuário (família/amigos) com isolamento de dados por sessão.
- Backup/restauração de dados via JSON manual (download/upload).

## Rodando local

```bash
pip install -r requirements.txt

# 1. Crie a configuração de autenticação
cp auth_config.yaml.example auth_config.yaml

# 2. Gere um hash para a sua senha
python gerar_hash.py minha_senha_aqui
# Cole o hash retornado no campo `password:` do auth_config.yaml

# 3. Gere uma chave aleatória para o cookie
python -c "import secrets; print(secrets.token_hex(32))"
# Cole no campo `cookie.key:` do auth_config.yaml

# 4. Suba a app
streamlit run app.py
```

Acesse <http://localhost:8501> e faça login.

## Hospedagem grátis (Streamlit Community Cloud)

1. **Crie um repositório no GitHub** com este projeto. O `.gitignore` já exclui `auth_config.yaml` e `estado.json` — segredos NÃO vão para o repositório.

2. **Crie conta em** <https://streamlit.io/cloud> e conecte ao GitHub.

3. **Aponte a app para o repositório**: branch `main`, arquivo `app.py`.

4. **Configure os secrets** (no painel da app, em *Settings → Secrets*):

   ```toml
   [auth.credentials.usernames.bruno]
   name = "Bruno"
   email = "bruno@example.com"
   password = "$2b$12$cole_aqui_o_hash"

   [auth.credentials.usernames.conjuge]
   name = "Cônjuge"
   email = "conjuge@example.com"
   password = "$2b$12$cole_aqui_o_hash"

   [auth.cookie]
   name = "darf_fii_auth"
   key = "cole_aqui_64_hex_aleatorios"
   expiry_days = 7

   [auth.preauthorized]
   emails = []
   ```

5. **Deploy** — a app fica em `<seu-usuario>-darf-fii.streamlit.app`.

## Adicionando novos usuários

- **Local**: edite `auth_config.yaml`, adicione um novo bloco em `credentials.usernames` com hash gerado por `python gerar_hash.py`.
- **Cloud**: edite os secrets pelo painel, adicione `[auth.credentials.usernames.<novo>]`.

## Importante sobre persistência

Os dados financeiros **NÃO são salvos no servidor**. A app é stateless — vivem apenas na sessão do navegador. Para não perder:

- Use **"Baixar backup (JSON)"** na sidebar antes de fechar o navegador.
- Use **"Restaurar backup"** ao voltar.

Isso evita qualquer risco de vazamento entre usuários e de perda quando o servidor reinicia.

## Testes

```bash
python -m pytest tests/ -v
```

37 testes cobrindo motor fiscal, parser de PDF, parser de XLSX e cadastro de FIIs.

## Estrutura

```
fii-darf/
├── app.py              # UI Streamlit (7 abas + auth + backup)
├── auth.py             # Carregamento de config + login
├── gerar_hash.py       # CLI para gerar hash bcrypt de senha
├── storage.py          # Serialização/deserialização JSON
├── core/               # Motor fiscal
├── parsers/            # PDF Sinacor + XLSX B3
├── data/fiis_b3.csv    # Cadastro de FIIs listados
└── tests/              # 37 testes
```
