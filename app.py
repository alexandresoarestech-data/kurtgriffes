from flask import Flask, render_template
import requests
import csv
import os
from io import StringIO
import os

import sqlite3
from io import StringIO
from pathlib import Path

import requests
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

try:
    from PIL import Image
except ImportError:
    Image = None


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "loja.db"
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
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "chave-de-desenvolvimento-altere-em-producao")
app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "admin123")
app.config["PIX_KEY"] = os.environ.get("PIX_KEY", "chave-pix-nao-configurada")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


def conectar_banco():
    conexao = sqlite3.connect(DATABASE)
    conexao.row_factory = sqlite3.Row
    return conexao


def inicializar_banco():
    with conectar_banco() as banco:
        banco.execute(
            """CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        quantidade = banco.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        if quantidade == 0:
            migrar_planilha(banco)


def converter_preco(valor):
    texto = str(valor or "0").replace("R$", "").replace(" ", "").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def listar_imagens_pasta(nome_pasta):
    pasta = UPLOAD_DIR / nome_pasta
    if not nome_pasta or not pasta.exists():
        return []
    return [
        f"imagens/produtos/{nome_pasta}/{arquivo.name}"
        for arquivo in sorted(pasta.iterdir())
        if arquivo.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]


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
                "INSERT INTO produtos (nome, categoria, preco, cores, estoque, imagens) VALUES (?, ?, ?, ?, ?, ?)",
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
        imagens = ["imagens/placeholder.jpg"]
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
        if request.form.get("senha") == app.config["ADMIN_PASSWORD"]:
            session["admin_logado"] = True
            return redirect(url_for("admin_produtos"))
        flash("Senha inválida.", "erro")
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
    return render_template("admin_produtos.html", produtos=produtos, categorias=CATEGORIAS)


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
        estoque = max(0, int(request.form.get("estoque", 0) or 0))
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

        with conectar_banco() as banco:
            banco.execute("INSERT INTO produtos (nome, categoria, preco, cores, estoque, imagens) VALUES (?, ?, ?, ?, ?, ?)", (nome, categoria, preco, cores, estoque, "|".join(imagens)))
            banco.commit()
        flash("Produto criado com sucesso.", "sucesso")
        return redirect(url_for("admin_produtos"))
    return render_template("admin_novo.html", categorias=CATEGORIAS)


@app.post("/admin/produtos/<int:produto_id>/excluir")
def admin_excluir_produto(produto_id):
    bloqueio = admin_obrigatorio()
    if bloqueio:
        return bloqueio
    with conectar_banco() as banco:
        banco.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))
        banco.commit()
    flash("Produto removido.", "sucesso")
    return redirect(url_for("admin_produtos"))


inicializar_banco()

if __name__ == "__main__":
    app.run(debug=True)
