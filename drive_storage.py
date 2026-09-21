import json
import logging
from pathlib import Path

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    service_account = None
    build = None
    MediaFileUpload = None

LOGGER = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/drive"]
MIME_FOLDER = "application/vnd.google-apps.folder"
_service = None


def drive_configurado(caminho_credencial, pasta_raiz_id):
    return bool(caminho_credencial and pasta_raiz_id and service_account and build and MediaFileUpload)


def _servico_drive(caminho_credencial):
    global _service
    if _service is None:
        credenciais = service_account.Credentials.from_service_account_file(caminho_credencial, scopes=SCOPES)
        _service = build("drive", "v3", credentials=credenciais, cache_discovery=False)
    return _service


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
    pasta = servico.files().create(body=metadados, fields="id", supportsAllDrives=True).execute()
    return pasta["id"]


def enviar_imagem(caminho, categoria, produto, caminho_credencial, pasta_raiz_id):
    if not drive_configurado(caminho_credencial, pasta_raiz_id):
        return ""
    try:
        servico = _servico_drive(caminho_credencial)
        pasta_categoria = _obter_ou_criar_pasta(servico, categoria, pasta_raiz_id)
        pasta_produto = _obter_ou_criar_pasta(servico, produto, pasta_categoria)
        metadados = {"name": Path(caminho).name, "parents": [pasta_produto]}
        midia = MediaFileUpload(str(caminho), resumable=True)
        arquivo = servico.files().create(
            body=metadados,
            media_body=midia,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        return arquivo["id"]
    except Exception:
        LOGGER.exception("Não foi possível enviar %s para o Google Drive", caminho)
        return ""


def remover_imagem(file_id, caminho_credencial):
    if not file_id or not service_account or not build:
        return
    try:
        _servico_drive(caminho_credencial).files().delete(fileId=file_id, supportsAllDrives=True).execute()
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
