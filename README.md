# Kurt Griffes Loja

Catalogo digital da Kurt Griffes, desenvolvido com Flask, SQLite, Jinja2 e Pillow. A loja publica produtos, permite escolher variacoes, monta o carrinho no navegador e encaminha o pedido para o WhatsApp. A area administrativa exige senha e permite cadastrar, editar, excluir e pesquisar produtos.

## Requisitos

- Windows, macOS ou Linux
- Python 3.11 ou superior
- Internet para a carga inicial da planilha, fontes externas e WhatsApp

## Estrutura do projeto

```text
kurtgriffes_loja/
|-- app.py                         Aplicacao Flask, rotas e regras
|-- loja.sqlite3                   Banco principal local, ignorado pelo Git
|-- loja.db                        Banco legado usado na migracao inicial
|-- requirements.txt               Dependencias Python
|-- .env.example                   Modelo de configuracao sem segredos
|-- .gitignore                     Arquivos que nao devem ser publicados
|-- templates/                     Paginas HTML Jinja2
|-- static/style.css               Estilos da loja e do painel
|-- static/imagens/icons/          Icones e identidade visual
|-- static/imagens/produtos/       Fotos do catalogo
|-- doc/                            Anotacoes complementares
```

## Instalar no Windows

No PowerShell, dentro da pasta do projeto:

```powershell
py -3 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Configuracao segura

O sistema nao possui senha administrativa, chave de sessao ou chave Pix real gravada no codigo. Configure as variaveis antes de iniciar:

```powershell
$env:SECRET_KEY = 'gere-uma-chave-aleatoria-grande-e-privada'
$env:ADMIN_PASSWORD = 'use-uma-senha-forte-e-unica'
$env:PIX_KEY = ''
$env:FLASK_DEBUG = '0'
$env:HOST = '127.0.0.1'
$env:PORT = '5000'
```

Gere uma chave forte com:

```powershell
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

O arquivo `.env.example` e apenas um modelo. O aplicativo carrega `.env` automaticamente, e `.env` esta no `.gitignore`. Em producao, use o gerenciador de segredos do servidor.

## Rodar a aplicacao

```powershell
.\venv\Scripts\python.exe app.py
```

Abra a loja em http://127.0.0.1:5000/ e o painel em http://127.0.0.1:5000/admin/login.

O servidor escuta apenas `127.0.0.1` por padrao. Nao use `HOST=0.0.0.0` sem firewall, HTTPS e servidor de producao configurados.

## Acesso administrativo

1. Configure `ADMIN_PASSWORD`.
2. Inicie a aplicacao.
3. Acesse `/admin/login`.
4. Entre com a senha configurada.

Nao existe mais a senha padrao `admin123`. Sem `ADMIN_PASSWORD`, o login fica bloqueado.

## Banco de dados

O banco principal fica na raiz:

```text
loja.sqlite3
```

O arquivo `loja.db` e legado e so e lido se o banco principal estiver vazio. Arquivos `-journal`, `-wal` e `-shm` sao temporarios do SQLite e nao devem ser publicados.

Para consultar tabelas e estrutura, use:

```powershell
sqlite3 loja.sqlite3 ".tables"
sqlite3 loja.sqlite3 ".schema produtos"
sqlite3 loja.sqlite3 "PRAGMA table_info(produtos);"
```

Sem o executavel `sqlite3`, use Python:

```powershell
.\venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('loja.sqlite3'); print(c.execute('PRAGMA table_info(produtos)').fetchall()); c.close()"
```

Para consultar produtos:

```powershell
sqlite3 -header -column loja.sqlite3 "SELECT id, nome, categoria, preco, estoque, data_criacao, hora_criacao FROM produtos ORDER BY id DESC;"
```

Antes de editar ou copiar o banco, pare o Flask e faca backup:

```powershell
Copy-Item loja.sqlite3 "backup-loja-$(Get-Date -Format yyyyMMdd-HHmmss).sqlite3"
```

## Imagens

As fotos ficam em `static/imagens/produtos/`. Cada produto pode ter sua propria pasta. O painel converte novas fotos para WebP quando o Pillow esta instalado e grava no banco somente o caminho relativo. Os icones ficam em `static/imagens/icons/`.

## Fluxo principal

1. `app.py` inicializa o banco e migra data e hora quando necessario.
2. `/` consulta produtos ativos.
3. O navegador monta o carrinho no `localStorage`.
4. O cliente escolhe Pix, cartao ou boleto.
5. O JavaScript cria uma mensagem curta com itens e apenas a forma de pagamento.
6. O pedido abre no WhatsApp.
7. O administrador gerencia o catalogo em `/admin`.

## Seguranca aplicada

- Chave de sessao nao e fixa nem publicada no codigo.
- Senha administrativa nao possui valor padrao.
- Comparacao da senha usa `hmac.compare_digest`.
- Cookie de sessao usa `HttpOnly` e `SameSite=Lax`.
- Debug fica desligado por padrao.
- Servidor local escuta `127.0.0.1` por padrao.
- Upload limita o tamanho total a 8 MB e usa nomes seguros.
- Consultas SQL usam parametros.
- Banco, `.env`, sessoes SQLite e uploads administrativos ficam fora do Git.

## Checklist antes de publicar

- Definir `SECRET_KEY` forte e privado.
- Definir `ADMIN_PASSWORD` forte e unica.
- Manter `FLASK_DEBUG=0`.
- Usar HTTPS com servidor WSGI, como Waitress ou Gunicorn.
- Manter `HOST=127.0.0.1` quando houver proxy reverso.
- Fazer backup de `loja.sqlite3` e `static/imagens/produtos/`.
- Nunca publicar `.env`, banco, backups, senha ou chave Pix.
- Trocar todos os valores de teste.

## Diagnostico rapido

```powershell
.\venv\Scripts\python.exe -m py_compile app.py
```

```powershell
.\venv\Scripts\python.exe -c "import app; app.inicializar_banco(); print(app.DATABASE); print(app.conectar_banco().execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall())"
```

Se a porta 5000 estiver ocupada:

```powershell
$env:PORT = '5001'
.\venv\Scripts\python.exe app.py
```

## Limites atuais

O pagamento nao e processado automaticamente; o WhatsApp apenas encaminha o pedido. Para producao, ainda e recomendado adicionar HTTPS, servidor WSGI, protecao CSRF para formularios administrativos, limite de tentativas de login, logs centralizados e backup externo.
