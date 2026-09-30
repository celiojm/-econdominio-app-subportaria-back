# ==========================================================================================
# send_grouped_whatsapp.py
# Worker de envio agrupado WhatsApp
# Versao: 3.2.0 - 2026-06-18
#
# Novidades v3.2.0:
#   - Template trocado de chegada_encomenda_v3 para aviso_chegada_encomenda_v5.
#   - {{4}} simplificado: apenas "*XXXX*" (so o codigo entre asteriscos),
#     pois o template v5 ja tem o texto "Informe na portaria:" fixo no corpo.
#
# Novidades v3.1.1 (FIX CRITICO):
#   - Removido schema hardcoded "AdmGeral." de todas as queries SQL deste arquivo.
#     Antes: FROM AdmGeral.whatsapp_message_queue (fixo, ignorava o banco da conexao)
#     Agora: FROM whatsapp_message_queue (usa o schema definido em DATABASE_URL)
#     Motivo: ao rodar este worker a partir de ~/desenvolvimento (DATABASE_URL
#     apontando para AdmGeral_dev), as queries continuavam lendo/escrevendo em
#     AdmGeral (producao) por causa do prefixo fixo no SQL. Isso fazia o worker
#     de dev ignorar a fila de teste e, potencialmente, processar registros reais
#     de producao quando rodado manualmente fora do cron oficial.
#     Locais corrigidos: fetch_next_phone, fetch_all_pending_for_phone,
#     mark_as_processing, mark_as_sent, mark_as_failed, recover_stale_processing.
#
# Novidades v3.0.0:
#   - Template ENCOMENDA_RECEBIDA migrado de encomenda_na_portaria_v2 para
#     chegada_encomenda_v3 (4 parametros + 3 botoes: VER / CONFIRMAR / QUEM_RETIROU).
#   - {{1}} = nome  {{2}} = unidade  {{3}} = codigo_rastreio  {{4}} = *Cod. Retirada: XXXX*
#   - Botao QUEM_RETIROU_{id} adicionado ao template de encomenda recebida.
#   - META_TEMPLATE_ENCOMENDA_V3 adicionado como constante separada.
#   - build_meta_template_params: novo bloco para chegada_encomenda_v3 (4 params).
#   - build_meta_button_components: terceiro botao QUEM_RETIROU_{id} adicionado.
#
# Versoes anteriores:
#   v2.9.0 - SIMPLIFICACAO CRITICA: Z-API removida do fluxo de moradores
#   v2.8.0 - ENCOMENDA_ENTREGUE e CONFIRMACAO_WHATSAPP adicionados a META_ONLY_EVENTS
#   v2.7.0 - FIX CRITICO: ENCOMENDA_RECEBIDA e CADASTRO_MORADOR nunca agrupados
#   v2.6.0 - NOVO EVENTO CADASTRO_MORADOR com template proprio
#   v2.5.0 - Roteamento por tipo de evento / META_ONLY_EVENTS
#   v2.4.0 - SQLAlchemy substituindo pymysql
# ==========================================================================================

import json
import os
import re
import time
import random
import logging
from typing import Optional

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

load_dotenv("/home/visionlpr/app_subportaria_back/.env.worker")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("whatsapp_worker")

# ------------------------------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------------------------------

DELAY_MIN        = int(os.getenv("WHATSAPP_DELAY_MIN_SECONDS", "3"))
DELAY_MAX        = int(os.getenv("WHATSAPP_DELAY_MAX_SECONDS", "8"))
MAX_ATTEMPTS     = int(os.getenv("WHATSAPP_MAX_ATTEMPTS", "5"))
WHATSAPP_ENABLED = os.getenv("WHATSAPP_ENABLED", "true").lower() in ("1", "true")

# Z-API / server.js — usado apenas para mensagens administrativas
ZAPI_ENABLED      = False  # v2.9.1 — Z-API desabilitada, tudo via Meta
ZAPI_INSTANCE_ID  = os.getenv("ZAPI_INSTANCE_ID") or os.getenv("WHATSAPP_ZAPI_INSTANCE_ID", "")
ZAPI_TOKEN        = os.getenv("ZAPI_TOKEN") or os.getenv("WHATSAPP_ZAPI_TOKEN", "")
ZAPI_API_URL      = os.getenv("ZAPI_API_URL", "http://191.252.221.192:8080")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN") or os.getenv("WHATSAPP_ZAPI_CLIENT_TOKEN", "")
ZAPI_MAX_SENDS    = 0  # v2.9.1 — garantia adicional: nenhum envio via Z-API

# Meta API — usado para TODOS os eventos de moradores
META_ENABLED            = os.getenv("META_WHATSAPP_ENABLED", "true").lower() in ("1", "true")
META_ACCESS_TOKEN       = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_ID           = os.getenv("META_PHONE_NUMBER_ID", "1002371749631777")
# v3.0.0 — template principal de encomenda agora e chegada_encomenda_v3
# META_TEMPLATE_NAME mantido como fallback para ENCOMENDA_ENTREGUE e CONFIRMACAO_WHATSAPP
META_TEMPLATE_NAME      = os.getenv("META_TEMPLATE_NAME", "encomenda_na_portaria_v2")
META_TEMPLATE_ENCOMENDA_V3 = os.getenv("META_TEMPLATE_ENCOMENDA_V3", "aviso_chegada_encomenda_v5")
META_TEMPLATE_LANG      = os.getenv("META_TEMPLATE_LANG", "pt_BR")
META_API_VERSION        = os.getenv("META_API_VERSION", "v25.0")
META_API_URL            = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_ID}/messages"
META_TEMPLATE_NOVO_COND = os.getenv("META_TEMPLATE_CONFIRMACAO", "novo_condominio")
META_TEMPLATE_CADASTRO  = os.getenv("META_TEMPLATE_CADASTRO", "cadastro_e_condominio_morador")
META_TEMPLATE_LEMBRETE  = os.getenv("META_TEMPLATE_LEMBRETE", "reaviso_encomenda")

PROCESSING_TIMEOUT_MINUTES = int(os.getenv("WHATSAPP_PROCESSING_TIMEOUT_MINUTES", "15"))

# ------------------------------------------------------------------------------------------
# TIPOS DE EVENTO
# ------------------------------------------------------------------------------------------

EVENTO_ENCOMENDA_RECEBIDA   = "ENCOMENDA_RECEBIDA"
EVENTO_ENCOMENDA_ENTREGUE   = "ENCOMENDA_ENTREGUE"
EVENTO_MENSAGEM_GENERICA    = "MENSAGEM_GENERICA"
EVENTO_CONFIRMACAO_WHATSAPP = "CONFIRMACAO_WHATSAPP"
EVENTO_NOVO_CONDOMINIO      = "NOVO_CONDOMINIO"
EVENTO_ALERTA_SISTEMA       = "ALERTA_SISTEMA"
EVENTO_ALERTA_MYSQL         = "ALERTA_MYSQL"
EVENTO_CADASTRO_MORADOR     = "CADASTRO_MORADOR"
EVENTO_LEMBRETE_ENCOMENDA_24H = "LEMBRETE_ENCOMENDA_24H"
EVENTO_LEMBRETE_ENCOMENDA_48H = "LEMBRETE_ENCOMENDA_48H"
EVENTO_LEMBRETE_ENCOMENDA_72H = "LEMBRETE_ENCOMENDA_72H"

META_ONLY_EVENTS = {
    EVENTO_ENCOMENDA_RECEBIDA,
    EVENTO_ENCOMENDA_ENTREGUE,
    EVENTO_CADASTRO_MORADOR,
    EVENTO_CONFIRMACAO_WHATSAPP,
    EVENTO_LEMBRETE_ENCOMENDA_24H,
    EVENTO_LEMBRETE_ENCOMENDA_48H,
    EVENTO_LEMBRETE_ENCOMENDA_72H,
}

LEMBRETE_HORAS_POR_EVENTO = {
    EVENTO_LEMBRETE_ENCOMENDA_24H: "24",
    EVENTO_LEMBRETE_ENCOMENDA_48H: "48",
    EVENTO_LEMBRETE_ENCOMENDA_72H: "72",
}

ZAPI_PREFERRED_EVENTS = {
    EVENTO_NOVO_CONDOMINIO,
    EVENTO_ALERTA_SISTEMA,
    EVENTO_ALERTA_MYSQL,
    EVENTO_MENSAGEM_GENERICA,
}

# ------------------------------------------------------------------------------------------
# DATABASE — SQLAlchemy
# ------------------------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL nao configurado no .env")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=2,
    max_overflow=2,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

logger.info(
    "Banco configurado | url=%s",
    DATABASE_URL.split("@")[-1],
)

# ------------------------------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------------------------------

def normalize_phone(phone: str) -> str:
    """
    O telefone gravado na fila (whatsapp_message_queue.telefone) ja chega
    normalizado por normalize_phone_br() em app/services/whatsapp.py, no
    momento do enfileiramento (queue_whatsapp_message). NAO reaplicar
    heuristica de DDI 55 aqui — isso quebra numeros ja corretos de
    moradores estrangeiros (Chile, Russia, Emirados, etc), colando um
    '55' extra na frente de um numero que ja estava certo.
    """
    if not phone:
        return ""
    return "".join(filter(str.isdigit, str(phone)))



def _extract_payload(m: dict) -> dict:
    payload = m.get("payload_json")
    if not payload:
        return {}
    if isinstance(payload, dict):
        return payload
    try:
        return json.loads(payload)
    except Exception:
        return {}


def _get_encomenda_id(msgs: list) -> Optional[int]:
    for m in msgs:
        enc_id = m.get("encomenda_id")
        if enc_id:
            return int(enc_id)
        payload = _extract_payload(m)
        enc_id = payload.get("id") or payload.get("encomenda_id")
        if enc_id:
            return int(enc_id)
    return None


def is_meta_only_event(tipo: str) -> bool:
    return tipo in META_ONLY_EVENTS

# ------------------------------------------------------------------------------------------
# VERIFICACAO DE STATUS DO Z-API
# ------------------------------------------------------------------------------------------

def check_zapi_connected() -> bool:
    if not ZAPI_ENABLED:
        logger.info("Z-API desabilitada por config.")
        return False
    if not ZAPI_INSTANCE_ID or not ZAPI_TOKEN:
        logger.warning("Z-API sem INSTANCE_ID ou TOKEN.")
        return False
    try:
        r = requests.get(f"{ZAPI_API_URL}/status", timeout=5)
        if r.status_code == 200:
            ready = r.json().get("whatsappReady", False)
            if ready:
                logger.info("Z-API OK | whatsappReady=true | url=%s", ZAPI_API_URL)
                return True
            logger.warning("Z-API online mas WhatsApp NAO conectado | whatsappReady=%s", ready)
        else:
            logger.warning("Z-API /status HTTP %s", r.status_code)
    except Exception as exc:
        logger.warning("Z-API indisponivel | erro=%s", exc)
    return False

# ------------------------------------------------------------------------------------------
# DATABASE READ
# ------------------------------------------------------------------------------------------

def fetch_next_phone(db: Session) -> Optional[str]:
    row = db.execute(text("""
        SELECT telefone
        FROM whatsapp_message_queue
        WHERE status = 'pending'
        ORDER BY criado_em ASC
        LIMIT 1
    """)).mappings().first()
    return row["telefone"] if row else None


def fetch_all_pending_for_phone(db: Session, phone: str) -> list:
    rows = db.execute(text("""
        SELECT
            q.id, q.morador_id, q.condominio_id, q.encomenda_id,
            q.telefone, q.nome_morador, q.tipo_evento,
            q.mensagem_original, q.payload_json, q.status,
            q.tentativas, q.processar_apos, q.enviado_em,
            q.erro, q.criado_em, q.atualizado_em,
            COALESCE(c.nome, '') AS nome_condominio_resolvido,
            m.nome        AS nome_morador_resolvido,
            m.apartamento AS apartamento_morador
        FROM whatsapp_message_queue q
        LEFT JOIN moradores m ON m.id = q.morador_id AND m.ativo = 1
        LEFT JOIN condominios c ON c.id = q.condominio_id AND c.ativo = 1
        WHERE q.telefone = :phone AND q.status = 'pending'
        ORDER BY q.criado_em ASC
    """), {"phone": phone}).mappings().all()
    return [dict(r) for r in rows]

# ------------------------------------------------------------------------------------------
# GET NEXT GROUP
# ------------------------------------------------------------------------------------------

def get_next_group():
    try:
        db = SessionLocal()
        try:
            phone = fetch_next_phone(db)
            if not phone:
                return None, None
            msgs = fetch_all_pending_for_phone(db, phone)
            if not msgs:
                return None, None
            logger.info("Proximo morador | telefone=%s | pendentes=%s", phone, len(msgs))
            return phone, msgs
        finally:
            db.close()
    except Exception as exc:
        logger.exception("Erro buscando grupo | %s", exc)
        return None, None

# ------------------------------------------------------------------------------------------
# BUILD MESSAGE DATA
# ------------------------------------------------------------------------------------------

def detect_event_type(msgs: list) -> str:
    tipos = {m.get("tipo_evento") for m in msgs if m.get("tipo_evento")}
    if len(tipos) == 1:
        return next(iter(tipos))
    return "MISTO"


def extract_message_data(msgs: list) -> dict:
    nome            = "Morador"
    codigos         = []
    textos          = []
    nome_condominio = ""
    apartamento     = ""
    codigo_retirada_global = ""
    local_armazenamento_global = ""
    for m in msgs:
        if not nome or nome == "Morador":
            nome = (
                m.get("nome_morador_resolvido")
                or m.get("nome_morador")
                or _extract_payload(m).get("nome_morador")
                or "Morador"
            )
        if not nome_condominio:
            payload = _extract_payload(m)
            nome_condominio = (
                m.get("nome_condominio_resolvido")
                or m.get("nome_condominio")
                or payload.get("nome_condominio")
                or ""
            )
        if not apartamento:
            payload = _extract_payload(m)
            apto  = payload.get("apartamento") or m.get("apartamento_morador") or ""
            bloco = payload.get("bloco") or ""
            if apto:
                apartamento = f"Apt {apto}{f' Bloco {bloco}' if bloco else ''}"

        payload = _extract_payload(m)
        codigo  = (
            payload.get("codigo_rastreio")
            or payload.get("codigo")
            or m.get("codigo_rastreio")
            or m.get("codigo")
        )

        codigo_retirada = (
            payload.get("codigo_retirada")
            or m.get("codigo_retirada")
        )
        local_armazenamento = (
            payload.get("local_armazenamento")
            or m.get("local_armazenamento")
        )

        if codigo:
            texto_codigo = str(codigo).strip()
            if codigo_retirada:
                texto_codigo += f"\nCod.Retirada: {codigo_retirada}"
            codigos.append(texto_codigo)

        # Guarda o codigo_retirada para uso nos params do template v3
        if codigo_retirada and not codigo_retirada_global:
            codigo_retirada_global = str(codigo_retirada).strip()

        # Guarda o local_armazenamento para uso nos params do template v3
        if local_armazenamento and not local_armazenamento_global:
            local_armazenamento_global = str(local_armazenamento).strip()

    codigos = list(dict.fromkeys(codigos))
    textos  = list(dict.fromkeys(textos))

    return {
        "nome":             nome,
        "codigos":          codigos,
        "textos":           textos,
        "nome_condominio":  nome_condominio,
        "apartamento":      apartamento,
        "tipo_evento":      detect_event_type(msgs),
        "total_msgs":       len(msgs),
        "codigo_retirada":  codigo_retirada_global,
        "local_armazenamento": local_armazenamento_global,
    }


def build_text_message(data: dict) -> str:
    nome            = data["nome"]
    codigos         = data["codigos"]
    textos          = data["textos"]
    tipo            = data["tipo_evento"]
    nome_condominio = data["nome_condominio"]
    total           = len(codigos) if codigos else len(textos)

    if tipo == EVENTO_ENCOMENDA_RECEBIDA:
        cab = (
            f"Ola, {nome}! Voce tem uma encomenda aguardando retirada:"
            if total == 1
            else f"Ola, {nome}! Voce tem {total} encomendas aguardando retirada:"
        )
    elif tipo == EVENTO_ENCOMENDA_ENTREGUE:
        cab = (
            f"Ola, {nome}! Sua encomenda foi retirada:"
            if total == 1
            else f"Ola, {nome}! Suas {total} encomendas foram retiradas:"
        )
    elif tipo == EVENTO_CONFIRMACAO_WHATSAPP:
        cab = f"Ola, {nome}! Confirmacao:"
    elif tipo == EVENTO_NOVO_CONDOMINIO:
        cab = f"Ola, {nome}! Bem-vindo ao eCondominio!"
    elif tipo in (EVENTO_ALERTA_SISTEMA, EVENTO_ALERTA_MYSQL):
        cab = f"[ALERTA] {nome}:"
    elif tipo == EVENTO_CADASTRO_MORADOR:
        cab = f"Ola, {nome}! Bem-vindo ao e-Condominio."
    else:
        cab = f"Ola, {nome}."

    if codigos:
        corpo = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(codigos))
    elif textos:
        corpo = "\n".join(textos)
    else:
        corpo = "(sem detalhes)"

    rodape = f"\n-- {nome_condominio}" if nome_condominio else ""
    return f"{cab}\n{corpo}{rodape}"


def _sanitize_meta_param(text: str, max_len: int = 1024) -> str:
    if not text:
        return ""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'[^\x00-\x7F\u00C0-\u024F]', '', text)
    text = re.sub(r'[*_~]', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text[:max_len] if len(text) > max_len else text


def _sanitize_meta_param_allow_bold(text: str, max_len: int = 1024) -> str:
    """
    Igual ao _sanitize_meta_param mas PRESERVA asteriscos para negrito WhatsApp.
    Usado exclusivamente no {{4}} do chegada_encomenda_v3.
    """
    if not text:
        return ""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'[^\x00-\x7F\u00C0-\u024F*]', '', text)  # mantem *
    text = re.sub(r'[_~]', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text[:max_len] if len(text) > max_len else text


def build_meta_template_params(data: dict, template_name: Optional[str] = None) -> list:
    """
    Monta os parametros do template Meta conforme o template usado.

    chegada_encomenda_v3 (ENCOMENDA_RECEBIDA):
      {{1}} = nome
      {{2}} = unidade (apartamento)
      {{3}} = codigo_rastreio
      {{4}} = *Cod. Retirada: XXXX* (negrito — preserva asteriscos)

    cadastro_e_condominio_morador (CADASTRO_MORADOR):
      {{1}} = nome
      {{2}} = nome_condominio
      {{3}} = apartamento

    encomenda_na_portaria_v2 (fallback / entregue / confirmacao):
      {{1}} = nome
      {{2}} = apartamento
      {{3}} = codigos/lista
    """
    nome        = _sanitize_meta_param(data["nome"]) or "Morador"
    codigos     = data["codigos"]
    textos      = data["textos"]
    tipo        = data["tipo_evento"]
    apartamento = _sanitize_meta_param(data.get("apartamento", "")) or "sua unidade"
    nome_cond   = _sanitize_meta_param(data.get("nome_condominio", "")) or "seu condominio"

    # --- Template chegada_encomenda_v3 (4 parametros) ---

    # --- Template chegada_encomenda_v3 (5 parametros) ---
    if template_name == META_TEMPLATE_ENCOMENDA_V3:
        # {{3}} codigo_rastreio (apenas o codigo, sem Cod.Retirada)
        if codigos:
            # codigos podem conter "SD123\nCod.Retirada: 1234" — pega so a primeira linha
            codigo_rastreio = _sanitize_meta_param(codigos[0].split("\n")[0])
        elif textos:
            codigo_rastreio = _sanitize_meta_param(textos[0])
        else:
            codigo_rastreio = "encomenda aguardando retirada"
        # {{4}} local de armazenamento — fallback "portaria" se nao informado
        local_arm = _sanitize_meta_param(data.get("local_armazenamento", "")) or "portaria"
        # {{5}} codigo de retirada — SEM asteriscos, o template ja tem **{{5}}** fixo no corpo
        cod_retirada = data.get("codigo_retirada", "")
        param5 = _sanitize_meta_param(cod_retirada) if cod_retirada else "----"  
        return [
            {"type": "text", "text": nome},
            {"type": "text", "text": apartamento},
            {"type": "text", "text": codigo_rastreio},
            {"type": "text", "text": local_arm},
            {"type": "text", "text": param5},
        ]
    # --- Template reaviso_encomenda (2 parametros) ---
    # {{1}} = nome  {{2}} = horas (24/48/72), aprovado no Meta em 2026-09
    if template_name == META_TEMPLATE_LEMBRETE:
        horas = LEMBRETE_HORAS_POR_EVENTO.get(tipo, "")
        return [
            {"type": "text", "text": nome},
            {"type": "text", "text": horas},
        ]
    # --- Template aviso_cadastro_novo_morador (4 parametros) ---
    if tipo == EVENTO_CADASTRO_MORADOR:
        cod_retirada_cadastro = data.get("codigo_retirada", "")
        param4_cadastro = (
            _sanitize_meta_param(cod_retirada_cadastro)
            if cod_retirada_cadastro else "----"
        )

        return [
            {"type": "text", "text": nome},
            {"type": "text", "text": nome_cond},
            {"type": "text", "text": apartamento},
            {"type": "text", "text": param4_cadastro},
        ]
    # --- Template encomenda_na_portaria_v2 fallback (3 parametros) ---
    if codigos:
        codigos_clean = [_sanitize_meta_param(c) for c in codigos if c]
        lista = codigos_clean[0] if len(codigos_clean) == 1 else ", ".join(codigos_clean)
    elif textos:
        lista = _sanitize_meta_param(" | ".join(textos))
    else:
        if tipo == EVENTO_ENCOMENDA_RECEBIDA:
            lista = "encomenda aguardando retirada na portaria"
        elif tipo == EVENTO_ENCOMENDA_ENTREGUE:
            lista = "encomenda retirada"
        elif tipo == EVENTO_NOVO_CONDOMINIO:
            lista = "novo condominio cadastrado"
        elif tipo in LEMBRETE_HORAS_POR_EVENTO:
            lista = f"encomenda ainda aguardando retirada ha mais de {LEMBRETE_HORAS_POR_EVENTO[tipo]}h"
        else:
            lista = "notificacao do condominio"

    return [
        {"type": "text", "text": nome},
        {"type": "text", "text": apartamento},
        {"type": "text", "text": lista},
    ]


def build_meta_button_components(
    encomenda_id: Optional[int] = None,
    button_payloads: Optional[list] = None,
    include_quem_retirou: bool = False,
) -> list:
    """
    Monta os componentes de botao Quick Reply.

    include_quem_retirou=True: adiciona terceiro botao QUEM_RETIROU_{id}
    Usado exclusivamente com o template chegada_encomenda_v3.
    """
    if button_payloads:
        return [
            {
                "type": "button",
                "sub_type": "quick_reply",
                "index": str(i),
                "parameters": [{"type": "payload", "payload": p}],
            }
            for i, p in enumerate(button_payloads)
        ]

    if not encomenda_id:
        botoes = [
            {"type": "button", "sub_type": "quick_reply", "index": "0",
             "parameters": [{"type": "payload", "payload": "VER_ENCOMENDA"}]},
            {"type": "button", "sub_type": "quick_reply", "index": "1",
             "parameters": [{"type": "payload", "payload": "CONFIRMAR_RETIRADA"}]},
        ]
        if include_quem_retirou:
            botoes.append(
                {"type": "button", "sub_type": "quick_reply", "index": "2",
                 "parameters": [{"type": "payload", "payload": "QUEM_RETIROU"}]}
            )
        return botoes

    botoes = [
        {"type": "button", "sub_type": "quick_reply", "index": "0",
         "parameters": [{"type": "payload", "payload": f"VER_ENCOMENDA_{encomenda_id}"}]},
        {"type": "button", "sub_type": "quick_reply", "index": "1",
         "parameters": [{"type": "payload", "payload": f"CONFIRMAR_RETIRADA_{encomenda_id}"}]},
    ]
    if include_quem_retirou:
        botoes.append(
            {"type": "button", "sub_type": "quick_reply", "index": "2",
             "parameters": [{"type": "payload", "payload": f"QUEM_RETIROU_{encomenda_id}"}]}
        )
    return botoes

# ------------------------------------------------------------------------------------------
# Z-API SEND — usado apenas para mensagens administrativas
# ------------------------------------------------------------------------------------------

def send_text_zapi(phone: str, message: str) -> dict:
    phone = normalize_phone(phone)
    if not WHATSAPP_ENABLED:
        logger.info("SIMULACAO Z-API | telefone=%s", phone)
        return {"success": True, "provider": "simulado"}

    url     = f"{ZAPI_API_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
    headers = {"Content-Type": "application/json"}
    if ZAPI_CLIENT_TOKEN:
        headers["Client-Token"] = ZAPI_CLIENT_TOKEN

    try:
        r    = requests.post(url, json={"phone": phone, "message": message}, headers=headers, timeout=30)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
        if r.status_code < 300:
            if body.get("error") or body.get("erro"):
                return {"success": False, "provider": "zapi", "error": str(body.get("error") or body.get("erro"))}
            mid = body.get("messageId") or body.get("zaapId")
            if mid or body.get("success") is True:
                logger.info("Z-API OK | telefone=%s | messageId=%s", phone, mid)
                return {"success": True, "provider": "zapi", "message_id": mid}
            return {"success": False, "provider": "zapi", "error": f"Resposta ambigua: {body}"}
        return {"success": False, "provider": "zapi", "error": str(body)}
    except Exception as exc:
        logger.exception("Erro HTTP Z-API | telefone=%s", phone)
        return {"success": False, "provider": "zapi", "error": str(exc)}

# ------------------------------------------------------------------------------------------
# META API SEND
# ------------------------------------------------------------------------------------------

def send_template_meta(
    phone: str,
    data: dict,
    encomenda_id: Optional[int] = None,
    template_name: Optional[str] = None,
    include_buttons: bool = True,
    button_payloads: Optional[list] = None,
    include_quem_retirou: bool = False,
) -> dict:
    phone = normalize_phone(phone)
    if not WHATSAPP_ENABLED:
        logger.info("SIMULACAO META | telefone=%s", phone)
        return {"success": True, "provider": "simulado_meta"}
    if not META_ENABLED:
        return {"success": False, "provider": "meta", "error": "Meta desabilitada"}
    if not META_ACCESS_TOKEN:
        return {"success": False, "provider": "meta", "error": "META_ACCESS_TOKEN nao configurado"}

    tpl_name = template_name or META_TEMPLATE_NAME

    components = [{"type": "body", "parameters": build_meta_template_params(data, template_name=tpl_name)}]
    if include_buttons:
        components += build_meta_button_components(
            encomenda_id,
            button_payloads,
            include_quem_retirou=include_quem_retirou,
        )

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": tpl_name,
            "language": {"code": META_TEMPLATE_LANG},
            "components": components,
        },
    }
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}

    logger.info(
        "Meta | tel=%s | template=%s | encomenda_id=%s | botoes=%s | quem_retirou=%s",
        phone, tpl_name, encomenda_id, include_buttons, include_quem_retirou,
    )

    try:
        r    = requests.post(META_API_URL, json=payload, headers=headers, timeout=30)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
        if r.status_code < 300:
            messages = body.get("messages", [])
            if messages:
                mid    = messages[0].get("id")
                status = messages[0].get("message_status", "accepted")
                logger.info("Meta OK | telefone=%s | msgId=%s | status=%s", phone, mid, status)
                return {"success": True, "provider": "meta", "message_id": mid}
            return {"success": False, "provider": "meta", "error": f"Resposta inesperada: {body}"}
        error_msg = body.get("error", {}).get("message", str(body))
        logger.warning("Meta HTTP %s | telefone=%s | erro=%s", r.status_code, phone, error_msg)
        return {"success": False, "provider": "meta", "error": error_msg}
    except Exception as exc:
        logger.exception("Erro HTTP Meta | telefone=%s", phone)
        return {"success": False, "provider": "meta", "error": str(exc)}

# ------------------------------------------------------------------------------------------
# SEND — logica principal
# v3.0.0: ENCOMENDA_RECEBIDA usa chegada_encomenda_v3 com 4 params e 3 botoes
# ------------------------------------------------------------------------------------------

def send_message(phone: str, msgs: list, zapi_connected: bool, zapi_sends_done: int) -> tuple:
    """
    Roteamento v3.0.0:
      META_ONLY_EVENTS → SEMPRE Meta, dispatch explicito por tipo:
        ENCOMENDA_RECEBIDA   → template chegada_encomenda_v3 (4 params)
                               botoes: VER / CONFIRMAR_RETIRADA / QUEM_RETIROU
        ENCOMENDA_ENTREGUE   → template encomenda_na_portaria_v2 SEM botoes
        CADASTRO_MORADOR     → template cadastro_e_condominio_morador + botoes CONFIRMAR/NEGAR
        CONFIRMACAO_WHATSAPP → template encomenda_na_portaria_v2 SEM botoes
      ZAPI_PREFERRED_EVENTS → Z-API se disponivel, fallback Meta
    """
    data         = extract_message_data(msgs)
    tipo         = data["tipo_evento"]
    encomenda_id = _get_encomenda_id(msgs)
    forcado_meta = is_meta_only_event(tipo)

    logger.info(
        "Enviando | tel=%s | tipo=%s | msgs=%s | enc_id=%s | forcado_meta=%s | zapi_ok=%s | zapi_cnt=%s/%s",
        phone, tipo, len(msgs), encomenda_id,
        forcado_meta, zapi_connected, zapi_sends_done, ZAPI_MAX_SENDS,
    )

    # ==========================================================================
    # META_ONLY: dispatch explicito por tipo
    # ==========================================================================
    if forcado_meta:
        logger.info("ROTA: Meta exclusivo | tipo=%s | tel=%s", tipo, phone)

        if tipo == EVENTO_ENCOMENDA_RECEBIDA:
            # v3.0.0 — Template chegada_encomenda_v3 com 4 params e 3 botoes
            # {{1}}=nome {{2}}=unidade {{3}}=codigo_rastreio {{4}}=*Cod. Retirada: XXXX*
            result = send_template_meta(
                phone, data,
                encomenda_id=encomenda_id,
                template_name=META_TEMPLATE_ENCOMENDA_V3,
                include_buttons=True,
                include_quem_retirou=True,   # adiciona botao QUEM_RETIROU_{id}
            )

        elif tipo == EVENTO_ENCOMENDA_ENTREGUE:
            result = send_template_meta(
                phone, data,
                encomenda_id=None,
                template_name=META_TEMPLATE_NAME,
                include_buttons=False,
            )

        elif tipo == EVENTO_CADASTRO_MORADOR:
            result = send_template_meta(
                phone, data,
                encomenda_id=None,
                template_name=META_TEMPLATE_CADASTRO,
                include_buttons=True,
                button_payloads=["CONFIRMAR_MORADOR", "NEGAR_MORADOR"],
            )

        elif tipo == EVENTO_CONFIRMACAO_WHATSAPP:
            result = send_template_meta(
                phone, data,
                encomenda_id=None,
                template_name=META_TEMPLATE_NAME,
                include_buttons=False,
            )

        elif tipo in LEMBRETE_HORAS_POR_EVENTO:
            result = send_template_meta(
                phone, data,
                encomenda_id=encomenda_id,
                template_name=META_TEMPLATE_LEMBRETE,
                include_buttons=False,
            )

        else:
            logger.warning("META_ONLY sem handler explicito | tipo=%s | usando template generico", tipo)
            result = send_template_meta(
                phone, data,
                encomenda_id=encomenda_id,
                template_name=META_TEMPLATE_NAME,
                include_buttons=False,
            )

        if result.get("success"):
            return result, False

        logger.error("Meta falhou (rota exclusiva) | tipo=%s | tel=%s | erro=%s",
                     tipo, phone, result.get("error"))
        return result, False

    # ==========================================================================
    # ZAPI_PREFERRED: Z-API com fallback Meta — apenas para administrativo
    # ==========================================================================
    usar_zapi = zapi_connected and (zapi_sends_done < ZAPI_MAX_SENDS)

    if usar_zapi:
        logger.info("ROTA: Z-API preferencial | tipo=%s | tel=%s", tipo, phone)
        result = send_text_zapi(phone, build_text_message(data))
        if result.get("success"):
            return result, True
        logger.warning(
            "Z-API falhou | tipo=%s | tel=%s | erro=%s | acionando Meta...",
            tipo, phone, result.get("error"),
        )
    else:
        if zapi_connected:
            logger.info("Limite Z-API atingido (%s/%s) | Meta | tipo=%s | tel=%s",
                        zapi_sends_done, ZAPI_MAX_SENDS, tipo, phone)
        else:
            logger.info("ROTA: Meta (Z-API offline) | tipo=%s | tel=%s", tipo, phone)

    if META_ENABLED and META_ACCESS_TOKEN:
        if tipo == EVENTO_NOVO_CONDOMINIO:
            result = send_template_meta(
                phone, data,
                encomenda_id=None,
                template_name=META_TEMPLATE_NOVO_COND,
                include_buttons=False,
            )
        else:
            result = send_template_meta(
                phone, data,
                encomenda_id=None,
                template_name=META_TEMPLATE_NAME,
                include_buttons=False,
            )

        if result.get("success"):
            return result, False
        logger.error("Meta falhou (fallback admin) | tipo=%s | tel=%s | erro=%s",
                     tipo, phone, result.get("error"))
        return result, False

    return {"success": False, "provider": "nenhum", "error": "Z-API e Meta indisponiveis"}, False

# ------------------------------------------------------------------------------------------
# UPDATE STATUS
# ------------------------------------------------------------------------------------------

def mark_as_processing(db: Session, ids: list) -> int:
    ph     = ",".join([f":id{i}" for i in range(len(ids))])
    params = {f"id{i}": ids[i] for i in range(len(ids))}
    result = db.execute(
        text(f"UPDATE whatsapp_message_queue SET status='processing' WHERE id IN ({ph})"),
        params
    )
    return result.rowcount


def mark_as_sent(db: Session, ids: list) -> None:
    ph     = ",".join([f":id{i}" for i in range(len(ids))])
    params = {f"id{i}": ids[i] for i in range(len(ids))}
    db.execute(
        text(f"UPDATE whatsapp_message_queue SET status='sent', enviado_em=NOW() WHERE id IN ({ph})"),
        params
    )


def mark_as_failed(db: Session, ids: list, error: str) -> None:
    ph     = ",".join([f":id{i}" for i in range(len(ids))])
    params = {f"id{i}": ids[i] for i in range(len(ids))}
    params["error"]        = str(error)
    params["max_attempts"] = MAX_ATTEMPTS
    db.execute(
        text(f"""UPDATE whatsapp_message_queue
                SET tentativas = tentativas + 1, erro = :error,
                    status = CASE WHEN tentativas + 1 >= :max_attempts THEN 'failed' ELSE 'pending' END
                WHERE id IN ({ph})"""),
        params
    )

# ------------------------------------------------------------------------------------------
# PROCESS GROUP
# ------------------------------------------------------------------------------------------

def process_group(phone: str, msgs: list, zapi_connected: bool, zapi_sends_done: int) -> int:
    ids = [m["id"] for m in msgs]
    db  = SessionLocal()
    try:
        updated = mark_as_processing(db, ids)
        db.commit()

        if updated == 0:
            logger.warning("Race condition | tel=%s", phone)
            return zapi_sends_done

        result, usou_zapi = send_message(phone, msgs, zapi_connected, zapi_sends_done)

        if result.get("success"):
            mark_as_sent(db, ids)
            db.commit()
            logger.info("Enviado | tel=%s | provider=%s | id=%s",
                        phone, result.get("provider"), result.get("message_id"))
            if usou_zapi:
                zapi_sends_done += 1
        else:
            mark_as_failed(db, ids, result.get("error", ""))
            db.commit()
            logger.error("Falha | tel=%s | provider=%s | erro=%s",
                         phone, result.get("provider"), result.get("error"))

    except Exception:
        logger.exception("Erro ao processar grupo | tel=%s", phone)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()

    return zapi_sends_done

# ------------------------------------------------------------------------------------------
# RECOVER STALE PROCESSING
# ------------------------------------------------------------------------------------------

def recover_stale_processing() -> None:
    db = SessionLocal()
    try:
        result = db.execute(text("""
            UPDATE whatsapp_message_queue
            SET status='pending', processar_apos=NOW()
            WHERE status='processing'
              AND criado_em <= NOW() - INTERVAL :minutes MINUTE
        """), {"minutes": PROCESSING_TIMEOUT_MINUTES})
        recovered = result.rowcount
        db.commit()
        if recovered > 0:
            logger.warning("Recuperadas %s mensagens travadas em processing", recovered)
        else:
            logger.info("Nenhuma mensagem travada em processing.")
    except Exception:
        logger.exception("Erro ao recuperar processing")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()

# ------------------------------------------------------------------------------------------
# CONFIG CHECK
# ------------------------------------------------------------------------------------------

def check_config() -> None:
    if not WHATSAPP_ENABLED:
        logger.warning("CONFIG: WHATSAPP_ENABLED=false — modo SIMULACAO")
    if not ZAPI_ENABLED:
        logger.warning("CONFIG: Z-API desabilitada")
    else:
        logger.info("CONFIG: Z-API = %s | instance = %s | limite = %s msgs/execucao (somente admin)",
                    ZAPI_API_URL, ZAPI_INSTANCE_ID, ZAPI_MAX_SENDS)
    if not META_ENABLED:
        logger.warning("CONFIG: Meta API desabilitada")
    else:
        logger.info(
            "CONFIG: Meta | phone_id=%s | template_encomenda=%s | template_cadastro=%s | lang=%s",
            META_PHONE_ID, META_TEMPLATE_ENCOMENDA_V3, META_TEMPLATE_CADASTRO, META_TEMPLATE_LANG,
        )
        if not META_ACCESS_TOKEN:
            logger.warning("CONFIG: META_ACCESS_TOKEN nao configurado!")
    logger.info(
        "CONFIG: Roteamento | Meta-exclusivo=%s | Z-API-admin=%s",
        META_ONLY_EVENTS, ZAPI_PREFERRED_EVENTS,
    )

# ------------------------------------------------------------------------------------------
# SPLIT — separa eventos individuais de agrupados
# ------------------------------------------------------------------------------------------

def split_individual_events(msgs: list) -> list:
    """
    v2.7.0+ — META_ONLY: cada mensagem vira grupo unitario.
    """
    individuais = []
    agrupados   = []

    for m in msgs:
        tipo = m.get("tipo_evento", "")
        if tipo in META_ONLY_EVENTS:
            individuais.append([m])
        else:
            agrupados.append(m)

    result = individuais
    if agrupados:
        result = result + [agrupados]

    if len(result) > 1:
        logger.info(
            "split_individual_events | total=%s | individuais=%s | agrupados=%s",
            len(msgs), len(individuais), len(agrupados),
        )

    return result

# ------------------------------------------------------------------------------------------
# WORKER
# ------------------------------------------------------------------------------------------

def run_worker() -> None:
    logger.info("=================================================")
    logger.info("WHATSAPP WORKER INICIADO v3.0.0")
    logger.info("=================================================")
    check_config()
    recover_stale_processing()

    zapi_connected  = check_zapi_connected()
    zapi_sends_done = 0

    logger.info(
        "Roteamento ativo | Meta-exclusivo (moradores): %s | Z-API (admin/propaganda): %s | zapi_ok=%s",
        META_ONLY_EVENTS, ZAPI_PREFERRED_EVENTS, zapi_connected,
    )

    processed = 0
    while True:
        phone, msgs = get_next_group()
        if not phone:
            if processed == 0:
                logger.info("Nenhuma mensagem pendente.")
            else:
                logger.info(
                    "Fila finalizada | grupos=%s | zapi=%s | meta=%s",
                    processed, zapi_sends_done, processed - zapi_sends_done,
                )
            break

        sublistas = split_individual_events(msgs)

        for sublista in sublistas:
            zapi_sends_done = process_group(phone, sublista, zapi_connected, zapi_sends_done)
            processed += 1

            if len(sublistas) > 1:
                time.sleep(random.uniform(2, 5))

        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        logger.info("Aguardando %.1fs...", delay)
        time.sleep(delay)


if __name__ == "__main__":
    run_worker()
