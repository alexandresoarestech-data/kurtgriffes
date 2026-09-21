import json
import logging
from pathlib import Path

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    Request = None
    Credentials = None
    InstalledAppFlow = None
    build = None
    MediaFileUpload = None

LOGGER = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/drive"]
MIME_FOLDER = "application/vnd.google-apps.folder"
_service = None


def drive_configurado(caminho_client_secret, caminho_token, pasta_raiz_id):
    return bool(
        caminho_client_secret
        and caminho_token
        and pasta_raiz_id
        and Path(caminho_client_secret).is_file()
        and Request
        and Credentials
        and InstalledAppFlow
        and build
        and MediaFileUpload
    )


def _servico_drive(caminho_client_secret, caminho_token):
    global _service
    if _service is not None:
        return _service

    token = Path(caminho_token)
    token.parent.mkdir(parents=True, exist_ok=True)
    credenciais = Credentials.from_authorized_user_file(str(token), SCOPES) if token.is_file() else None

    if credenciais and credenciais.expired and credenciais.refresh_token:
        credenciais.refresh(Request())
    if not credenciais or not credenciais.valid:
        fluxo = InstalledAppFlow.from_client_secrets_file(caminho_client_secret, SCOPES)
        credenciais = fluxo.run_local_server(port=0, access_type="offline", prompt="consent")
        token.write_text(credenciais.to_json(), encoding="utf-8")

    _service = build("drive", "v3", credentials=credenciais, cache_discovery=False)
    return _service


def autorizar_drive(caminho_client_secret, caminho_token):
    if not caminho_client_secret or not Path(caminho_client_secret).is_file():
        raise FileNotFoundError("Credencial OAuth do Google Drive não encontrada.")
    _servico_drive(caminho_client_secret, caminho_token)


def _escapar_nome(nome):
    return str(nome).replace("'", "\\'")


def _buscar_pasta(servico, nome, pasta_pai_id):
    consulta = (
        f"name = '{_escapar_nome(nome)}' and '{pasta_pai_id}' in parents "
        f"and mimeType = '{MIME_FOLDER}' and trashed = false"
    )
    resposta = servico.files().list(q=consulta, spaces="drive", fields="files(id, name)", pageSize=1).execute()
    arquivos = resposta.get("files", [])
    return arquivos[0]["id"] if arquivos else None


def _obter_ou_criar_pasta(servico, nome, pasta_pai_id):
    pasta_id = _buscar_pasta(servico, nome, pasta_pai_id)
    if pasta_id:
        return pasta_id
    metadados = {"name": nome, "mimeType": MIME_FOLDER, "parents": [pasta_pai_id]}
    pasta = servico.files().create(body=metadados, fields="id").execute()
    return pasta["id"]


def obter_pasta_produto(categoria, produto, caminho_client_secret, caminho_token, pasta_raiz_id):
    if not drive_configurado(caminho_client_secret, caminho_token, pasta_raiz_id):
        return ""
    servico = _servico_drive(caminho_client_secret, caminho_token)
    pasta_categoria = _obter_ou_criar_pasta(servico, categoria, pasta_raiz_id)
    return _obter_ou_criar_pasta(servico, produto, pasta_categoria)


def enviar_imagem(caminho, categoria, produto, caminho_client_secret, caminho_token, pasta_raiz_id):
    if not drive_configurado(caminho_client_secret, caminho_token, pasta_raiz_id):
        return ""
    try:
        servico = _servico_drive(caminho_client_secret, caminho_token)
        pasta_produto = obter_pasta_produto(categoria, produto, caminho_client_secret, caminho_token, pasta_raiz_id)
        metadados = {"name": Path(caminho).name, "parents": [pasta_produto]}
        midia = MediaFileUpload(str(caminho), resumable=True)
        arquivo = servico.files().create(body=metadados, media_body=midia, fields="id").execute()
        return arquivo["id"]
    except Exception:
        LOGGER.exception("Não foi possível enviar %s para o Google Drive", caminho)
        return ""


def remover_imagem(file_id, caminho_client_secret, caminho_token):
    if not file_id or not caminho_client_secret or not Path(caminho_client_secret).is_file():
        return
    try:
        _servico_drive(caminho_client_secret, caminho_token).files().delete(fileId=file_id).execute()
    except Exception:
        LOGGER.exception("Não foi possível remover o arquivo %s do Google Drive", file_id)


def ids_para_json(ids):
    return json.dumps(ids, ensure_ascii=True)


def ids_de_json(valor, quantidade=0):
    try:
        ids = json.loads(valor or "[]")
        if not isinstance(ids, list):
            ids = []
    except (TypeError, ValueError):
        ids = []
    return (ids + [""] * quantidade)[:quantidade] if quantidade else ids
