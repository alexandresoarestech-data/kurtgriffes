import csv
import hmac
import os
import secrets
import sqlite3
from io import StringIO
from pathlib import Path

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None


import requests
from flask import Flask, flash, redirect, render_template, request, session, url_for
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

from drive_storage import enviar_imagem, ids_de_json, ids_para_json, remover_imagem

try:
    from PIL import Image
except ImportError:
    Image = None


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)
DATABASE = BASE_DIR / "loja.sqlite3"
LEGACY_DATABASE = BASE_DIR / "loja.db"
UPLOAD_DIR = BASE_DIR / "static" / "imagens" / "produtos"
URL_DA_PLANILHA = os.environ.get(
    "URL_DA_PLANILHA",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vRAqJPe8vNW6_ZAx6IFCF0z2OMScIKbJREo3iwBX8QKHkJ5dD8BpQ82dxSFyDRXWYwqmVPB8K41Rjxe/pub?output=csv",
)
CATEGORIAS = {
    "calca": ("Calças", "Alfaiataria e estilo"),
    "polo": ("Polos", "Conforto e elegância"),
    "camisa": ("Camisas", "Casual e social"),
    "calcado": ("Calçados", "Tênis e sapatos"),
}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "")
app.config["PIX_KEY"] = os.environ.get("PIX_KEY", "")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
app.config["GOOGLE_DRIVE_CLIENT_SECRET"] = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "")
app.config["GOOGLE_DRIVE_TOKEN"] = os.environ.get("GOOGLE_DRIVE_TOKEN", "")
app.config["GOOGLE_DRIVE_ROOT_FOLDER_ID"] = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


class ConexaoCompat:
    def __init__(self, conexao, postgres=False):
        self._conexao = conexao
        self._postgres = postgres

    def _sql(self, consulta):
        return consulta.replace("?", "%s") if self._postgres else consulta

    def execute(self, consulta, parametros=()):
        return self._conexao.execute(self._sql(consulta), parametros)

    def executemany(self, consulta, parametros):
        return self._conexao.executemany(self._sql(consulta), parametros)

    def commit(self):
        return self._conexao.commit()

    def rollback(self):
        return self._conexao.rollback()

    def close(self):
        return self._conexao.close()

    def __enter__(self):
        self._conexao.__enter__()
        return self

    def __exit__(self, tipo, valor, traceback):
        return self._conexao.__exit__(tipo, valor, traceback)


def conectar_banco():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("Instale psycopg[binary] para usar DATABASE_URL.")
        return ConexaoCompat(psycopg.connect(DATABASE_URL, row_factory=dict_row), postgres=True)
    conexao = sqlite3.connect(DATABASE, timeout=30)
    conexao.execute("PRAGMA busy_timeout = 30000")
    conexao.row_factory = sqlite3.Row
    return ConexaoCompat(conexao)


def inicializar_banco():
    with conectar_banco() as banco:
        tipo_id = "BIGSERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
        banco.execute(
            f"""CREATE TABLE IF NOT EXISTS produtos (
                id {tipo_id},
                nome TEXT NOT NULL,
                categoria TEXT NOT NULL,
                preco REAL NOT NULL DEFAULT 0,
                cores TEXT DEFAULT '',
                estoque INTEGER NOT NULL DEFAULT 0,
                imagens TEXT DEFAULT '',
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )

        if DATABASE_URL:
            colunas = {linha["column_name"] for linha in banco.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'produtos'").fetchall()}
        else:
            colunas = {linha[1] for linha in banco.execute("PRAGMA table_info(produtos)").fetchall()}
        if "data_criacao" not in colunas:
            banco.execute("ALTER TABLE produtos ADD COLUMN data_criacao TEXT")
        if "hora_criacao" not in colunas:
            banco.execute("ALTER TABLE produtos ADD COLUMN hora_criacao TEXT")
        if "drive_imagens" not in colunas:
            banco.execute("ALTER TABLE produtos ADD COLUMN drive_imagens TEXT DEFAULT '[]'")

        valores_data = "CURRENT_DATE::text, CURRENT_TIME::text" if DATABASE_URL else "CURRENT_DATE, CURRENT_TIME"
        banco.execute(
            f"""UPDATE produtos
               SET data_criacao = COALESCE(data_criacao, {valores_data.split(', ')[0]}),
                   hora_criacao = COALESCE(hora_criacao, {valores_data.split(', ')[1]})
               WHERE data_criacao IS NULL OR hora_criacao IS NULL"""
        )

        quantidade = banco.execute("SELECT COUNT(*) AS quantidade FROM produtos").fetchone()["quantidade"] if DATABASE_URL else banco.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        if quantidade == 0 and os.environ.get("IMPORTAR_DADOS_INICIAIS", "1") == "1":
            migrar_banco_antigo(banco)
            quantidade = banco.execute("SELECT COUNT(*) AS quantidade FROM produtos").fetchone()["quantidade"] if DATABASE_URL else banco.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
            if quantidade == 0:
                migrar_planilha(banco)


def migrar_banco_antigo(banco):
    if not LEGACY_DATABASE.exists():
        return
    try:
        antigo = sqlite3.connect(LEGACY_DATABASE, timeout=5)
        antigo.row_factory = sqlite3.Row
        produtos = antigo.execute("SELECT nome, categoria, preco, cores, estoque, imagens, ativo FROM produtos").fetchall()
        antigo.close()
        banco.executemany(
            "INSERT INTO produtos (nome, categoria, preco, cores, estoque, imagens, ativo, data_criacao, hora_criacao) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_DATE, CURRENT_TIME)",
            [tuple(produto) for produto in produtos],
        )
        banco.commit()
    except sqlite3.Error as erro:
        app.logger.warning("Não foi possível migrar o banco antigo: %s", erro)


def converter_preco(valor):
    texto = str(valor or "0").replace("R$", "").replace(" ", "").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


app.config["DELIVERY_FEE"] = converter_preco(os.environ.get("DELIVERY_FEE", "0"))


def listar_imagens_pasta(nome_pasta):
    pasta = UPLOAD_DIR / nome_pasta
    if not nome_pasta or not pasta.exists():
        return []
    return [
        f"imagens/produtos/{nome_pasta}/{arquivo.name}"
        for arquivo in sorted(pasta.iterdir())
        if arquivo.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]


def enviar_imagem_para_drive(caminho, categoria, nome_produto):
    return enviar_imagem(
        caminho,
        CATEGORIAS.get(categoria, (categoria, ""))[0],
        nome_produto,
        app.config["GOOGLE_DRIVE_CLIENT_SECRET"],
        app.config["GOOGLE_DRIVE_TOKEN"],
        app.config["GOOGLE_DRIVE_ROOT_FOLDER_ID"],
    )


def remover_imagem_do_drive(file_id):
    remover_imagem(file_id, app.config["GOOGLE_DRIVE_CLIENT_SECRET"], app.config["GOOGLE_DRIVE_TOKEN"])


def drive_esta_configurado():
    return bool(
        app.config["GOOGLE_DRIVE_CLIENT_SECRET"]
        and Path(app.config["GOOGLE_DRIVE_CLIENT_SECRET"]).is_file()
        and app.config["GOOGLE_DRIVE_ROOT_FOLDER_ID"]
    )


def migrar_planilha(banco):
    try:
        resposta = requests.get(URL_DA_PLANILHA, timeout=10)
        resposta.raise_for_status()
        leitor = csv.DictReader(StringIO(resposta.content.decode("utf-8-sig")))
        for linha in leitor:
            linha = {str(chave).strip().lower(): valor or "" for chave, valor in linha.items() if chave}
            pasta = linha.get("pasta", "").strip()
            imagens = listar_imagens_pasta(pasta)
            categoria = normalizar_categoria(linha.get("categoria", ""))
            banco.execute(
                "INSERT INTO produtos (nome, categoria, preco, cores, estoque, imagens, data_criacao, hora_criacao) VALUES (?, ?, ?, ?, ?, ?, CURRENT_DATE, CURRENT_TIME)",
                (linha.get("nome", "Produto"), categoria, converter_preco(linha.get("preco")), linha.get("cor", ""), 10, "|".join(imagens)),
            )
        banco.commit()
    except (requests.RequestException, csv.Error) as erro:
        app.logger.warning("Não foi possível migrar a planilha: %s", erro)


def normalizar_categoria(categoria):
    texto = str(categoria or "").strip().lower()
    aliases = {"calças": "calca", "calca": "calca", "polos": "polo", "camisas": "camisa", "calçados": "calcado", "calcados": "calcado"}
    return aliases.get(texto, texto if texto in CATEGORIAS else "camisa")


def produto_para_template(produto):
    item = dict(produto)
    imagens = [imagem for imagem in (item.get("imagens") or "").split("|") if imagem]
    if not imagens:
        imagens = ["imagens/placeholder.svg"]
    item["todas_imagens"] = imagens
    item["imagem_capa"] = imagens[0]
    item["lista_cores"] = [cor.strip() for cor in (item.get("cores") or "").split(",") if cor.strip()]
    item["parcelas"] = f"ou 12x de R$ {item['preco'] / 12:.2f}".replace(".", ",") + " sem juros"
    return item


def obter_produtos():
    with conectar_banco() as banco:
        produtos = banco.execute("SELECT * FROM produtos WHERE ativo = 1 ORDER BY criado_em DESC, id DESC").fetchall()
    return [produto_para_template(produto) for produto in produtos]


def admin_obrigatorio():
    if not session.get("admin_logado"):
        return redirect(url_for("admin_login"))
    return None


@app.route("/")
def home():
    produtos = obter_produtos()
    categorias = {}
    for chave, (titulo, subtitulo) in CATEGORIAS.items():
        itens = [produto for produto in produtos if produto["categoria"] == chave]
        if itens:
            categorias[chave] = {"titulo": titulo, "subtitulo": subtitulo, "itens": itens}
    return render_template("index.html", novidades={"titulo": "Novidades", "subtitulo": "Chegou agora na loja", "itens": produtos}, categorias=categorias)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        senha_configurada = app.config["ADMIN_PASSWORD"]
        senha_informada = request.form.get("senha", "")
        if senha_configurada and hmac.compare_digest(senha_informada, senha_configurada):
            session["admin_logado"] = True
            return redirect(url_for("admin_produtos"))
        flash("Senha inválida ou acesso administrativo não configurado.", "erro")
    return render_template("admin_login.html")


@app.route("/admin/sair")
def admin_sair():
    session.pop("admin_logado", None)
    return redirect(url_for("home"))


@app.route("/admin")
def admin_produtos():
    bloqueio = admin_obrigatorio()
    if bloqueio:
        return bloqueio
    with conectar_banco() as banco:
        produtos = banco.execute("SELECT * FROM produtos ORDER BY id DESC").fetchall()
    resumo = {
        "total": len(produtos),
        "estoque_baixo": sum(produto["estoque"] < 3 for produto in produtos),
        "categorias": len({produto["categoria"] for produto in produtos}),
    }
    return render_template("admin_produtos.html", produtos=produtos, categorias=CATEGORIAS, resumo=resumo)


@app.route("/admin/produtos/novo", methods=["GET", "POST"])
def admin_novo_produto():
    bloqueio = admin_obrigatorio()
    if bloqueio:
        return bloqueio
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        categoria = normalizar_categoria(request.form.get("categoria"))
        preco = converter_preco(request.form.get("preco"))
        cores = request.form.get("cores", "").strip()
        try:
            estoque = max(0, int(request.form.get("estoque", 0) or 0))
        except (TypeError, ValueError):
            flash("O estoque deve ser um número inteiro maior ou igual a zero.", "erro")
            return render_template("admin_novo.html", categorias=CATEGORIAS)
        if not nome:
            flash("Informe o nome do produto.", "erro")
            return render_template("admin_novo.html", categorias=CATEGORIAS)

        slug = secure_filename(nome.lower().replace(" ", "-")) or "produto"
        pasta = UPLOAD_DIR / f"admin-{slug}-{os.urandom(3).hex()}"
        pasta.mkdir(parents=True, exist_ok=True)
        imagens = []
        for arquivo in request.files.getlist("fotos"):
            if not arquivo or not arquivo.filename:
                continue
            nome_seguro = secure_filename(Path(arquivo.filename).stem) or "foto"
            destino = pasta / f"{nome_seguro}.webp"
            try:
                if Image:
                    imagem = Image.open(arquivo.stream).convert("RGB")
                    imagem.thumbnail((1600, 1600))
                    imagem.save(destino, "WEBP", quality=84, method=6)
                else:
                    destino = pasta / f"{nome_seguro}{Path(arquivo.filename).suffix.lower()}"
                    arquivo.save(destino)
                imagens.append(destino.relative_to(BASE_DIR / "static").as_posix())
            except Exception:
                flash("Uma das imagens não pôde ser processada.", "erro")

        drive_ids = []
        for imagem in imagens:
            file_id = enviar_imagem_para_drive(BASE_DIR / "static" / imagem, categoria, nome)
            drive_ids.append(file_id)
        if drive_esta_configurado() and imagens and not any(drive_ids):
            flash("Produto salvo localmente, mas não foi possível enviar as fotos ao Google Drive.", "erro")

        with conectar_banco() as banco:
            banco.execute(
                "INSERT INTO produtos (nome, categoria, preco, cores, estoque, imagens, drive_imagens, data_criacao, hora_criacao) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_DATE, CURRENT_TIME)",
                (nome, categoria, preco, cores, estoque, "|".join(imagens), ids_para_json(drive_ids)),
            )
            banco.commit()
        flash("Produto criado com sucesso.", "sucesso")
        return redirect(url_for("admin_produtos"))
    return render_template("admin_novo.html", categorias=CATEGORIAS, produto=None, modo_edicao=False)


@app.route("/admin/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
def admin_editar_produto(produto_id):
    bloqueio = admin_obrigatorio()
    if bloqueio:
        return bloqueio
    with conectar_banco() as banco:
        produto = banco.execute("SELECT * FROM produtos WHERE id = ?", (produto_id,)).fetchone()
    if produto is None:
        flash("Produto não encontrado.", "erro")
        return redirect(url_for("admin_produtos"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        categoria = normalizar_categoria(request.form.get("categoria"))
        preco = converter_preco(request.form.get("preco"))
        cores = request.form.get("cores", "").strip()
        try:
            estoque = max(0, int(request.form.get("estoque", 0) or 0))
        except (TypeError, ValueError):
            flash("O estoque deve ser um número inteiro maior ou igual a zero.", "erro")
            return render_template("admin_novo.html", categorias=CATEGORIAS, produto=produto, modo_edicao=True)
        if not nome:
            flash("Informe o nome do produto.", "erro")
            return render_template("admin_novo.html", categorias=CATEGORIAS, produto=produto, modo_edicao=True)

        imagens_atuais = [imagem for imagem in (produto["imagens"] or "").split("|") if imagem]
        drive_ids_atuais = ids_de_json(produto["drive_imagens"], len(imagens_atuais))
        imagens_mantidas = [imagem for imagem in request.form.getlist("imagens_mantidas") if imagem in imagens_atuais]
        imagens_removidas = set(imagens_atuais) - set(imagens_mantidas)
        imagens = list(imagens_mantidas)
        drive_ids = [drive_ids_atuais[imagens_atuais.index(imagem)] for imagem in imagens_mantidas]

        for caminho_relativo in imagens_removidas:
            indice = imagens_atuais.index(caminho_relativo)
            remover_imagem_do_drive(drive_ids_atuais[indice])
            caminho = (BASE_DIR / "static" / caminho_relativo).resolve()
            static_dir = (BASE_DIR / "static").resolve()
            if static_dir in caminho.parents and caminho.is_file():
                try:
                    caminho.unlink()
                except OSError as erro:
                    app.logger.warning("Não foi possível remover a imagem %s: %s", caminho, erro)

        pasta = UPLOAD_DIR / f"admin-produto-{produto_id}"
        pasta.mkdir(parents=True, exist_ok=True)
        for arquivo in request.files.getlist("fotos"):
            if not arquivo or not arquivo.filename:
                continue
            nome_seguro = secure_filename(Path(arquivo.filename).stem) or "foto"
            destino = pasta / f"{nome_seguro}-{os.urandom(2).hex()}.webp"
            try:
                if Image:
                    imagem = Image.open(arquivo.stream).convert("RGB")
                    imagem.thumbnail((1600, 1600))
                    imagem.save(destino, "WEBP", quality=84, method=6)
                else:
                    destino = pasta / f"{nome_seguro}-{os.urandom(2).hex()}{Path(arquivo.filename).suffix.lower()}"
                    arquivo.save(destino)
                imagem_relativa = destino.relative_to(BASE_DIR / "static").as_posix()
                imagens.append(imagem_relativa)
                drive_ids.append(enviar_imagem_para_drive(destino, categoria, nome))
            except Exception:
                flash("Uma das imagens não pôde ser processada.", "erro")

        if drive_esta_configurado() and request.files.getlist("fotos") and imagens and not any(drive_ids):
            flash("Produto atualizado localmente, mas não foi possível sincronizar as fotos com o Google Drive.", "erro")

        with conectar_banco() as banco:
            banco.execute(
                "UPDATE produtos SET nome = ?, categoria = ?, preco = ?, cores = ?, estoque = ?, imagens = ?, drive_imagens = ?, data_criacao = COALESCE(data_criacao, CURRENT_DATE), hora_criacao = COALESCE(hora_criacao, CURRENT_TIME) WHERE id = ?",
                (nome, categoria, preco, cores, estoque, "|".join(imagens), ids_para_json(drive_ids), produto_id),
            )
            banco.commit()
        flash("Produto atualizado com sucesso.", "sucesso")
        return redirect(url_for("admin_produtos"))

    return render_template("admin_novo.html", categorias=CATEGORIAS, produto=produto, modo_edicao=True)


@app.post("/admin/produtos/<int:produto_id>/excluir")
def admin_excluir_produto(produto_id):
    bloqueio = admin_obrigatorio()
    if bloqueio:
        return bloqueio
    with conectar_banco() as banco:
        produto = banco.execute("SELECT imagens, drive_imagens FROM produtos WHERE id = ?", (produto_id,)).fetchone()
        if produto:
            for file_id in ids_de_json(produto["drive_imagens"]):
                remover_imagem_do_drive(file_id)
        banco.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))
        banco.commit()
    flash("Produto removido.", "sucesso")
    return redirect(url_for("admin_produtos"))


@app.errorhandler(413)
def arquivo_muito_grande(_erro):
    flash("As imagens ultrapassam o limite de 8 MB.", "erro")
    return redirect(url_for("admin_novo_produto")), 413


inicializar_banco()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
