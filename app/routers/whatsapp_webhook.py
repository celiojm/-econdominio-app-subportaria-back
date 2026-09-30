# =============================================================================
# Arquivo  : whatsapp_webhook.py
# Caminho  : /home/visionlpr/app_subportaria_back/app/routers/whatsapp_webhook.py
# Versao   : 2.11.0
# Data     : 2026-09-02
#
# Novidades v2.11.0:
#   - FIX: find_morador_by_phone_e_condominio (LIMIT 1) podia escolher morador
#     errado quando o mesmo telefone/condominio tem varios moradores (caso
#     esperado: familia inteira recebendo encomenda em nome proprio, so um
#     WhatsApp cadastrado). Renomeada pra find_moradores_by_phone_e_condominio
#     (retorna lista). No gatilho CADASTRO_MORADOR, se vier >1, pergunta qual
#     morador via botoes (nova etapa aguardando_escolha_morador_multiplo).
#   - FIX (achado durante a implementacao acima, corrigido de brinde):
#     "Corrigir" tambem re-consultava por telefone (mesmo LIMIT 1 arbitrario)
#     em vez de usar o morador ja identificado. morador_id_editando agora e
#     populado assim que entra em aguardando_decisao_ja_cadastrado (nao so
#     quando clica Corrigir), e todo acesso downstream (Corrigir, Adicionar,
#     reshow pos-cancelamento) usa find_morador_by_phone_id por esse id —
#     nunca mais re-consulta por telefone.
#   - NOVO: "Adiciona Pessoa" (JA_ADICIONAR) funcional (antes era stub "em
#     breve"). Lista moradores ja cadastrados no mesmo apto/bloco, confirma,
#     pergunta so o nome (resto copiado do morador de referencia), INSERT,
#     e pergunta de novo se quer adicionar mais alguem (loop, mesmo padrao
#     do fluxo de Corrigir) — casos novas etapas: adicionando_confirmar,
#     adicionando_aguardando_nome.
#   - Sem colunas novas em whatsapp_auto_cadastro_estado — reaproveita
#     morador_id_editando (ja existe) pros dois fluxos novos. ENUM da coluna
#     etapa precisa de MODIFY COLUMN com os 3 valores novos (ver CLAUDE.md
#     15-O — nao e so ADD COLUMN).
#
# Novidades v2.10.0:
#   - FIX: TEXTO_JA_CADASTRADO_MESMO agora inclui bloco e whatsapp (faltavam
#     do template) e lista os dados em linhas separadas.
#   - NOVO: botao "Adiciona Pessoa" (JA_ADICIONAR) no fluxo de "ja cadastrado"
#     - por ora so responde texto de "em breve", sem fluxo completo (depende
#     de correcao em find_morador_by_phone, fora do escopo desta versao).
#   - NOVO: fluxo completo de "Corrigir" (etapas corrigindo_escolher_campo /
#     corrigindo_aguardando_valor / corrigindo_aguardando_confirmacao) —
#     permite corrigir nome/apartamento/bloco do proprio cadastro via
#     WhatsApp, com UPDATE em moradores apos confirmacao.
#     * Novas colunas em whatsapp_auto_cadastro_estado: morador_id_editando,
#       campo_editando, valor_novo (ver ALTER TABLE em anexo, nao aplicado
#       por este commit).
#   - FIX: send_whatsapp_interactive_buttons agora trunca defensivamente pra
#     3 botoes (limite da API da Meta), logando erro se vier mais que isso.
#   - Texto do "Confirmar" do fluxo "ja cadastrado" trocado para "Obrigado
#     por confirmar!".
#
# Novidades v2.9.0:
#   - NOVO: botoes interativos (send_whatsapp_interactive_buttons) nos 3
#     pontos de decisao sim/nao do fluxo de auto-cadastro (Fase B), com
#     fallback pra texto digitado mantido em todos
# Novidades v2.8.0:
#   - NOVO: fluxo de auto-cadastro de morador via WhatsApp (Fase B)
#     * handle_auto_cadastro, find_estado_auto_cadastro,
#       find_morador_by_phone_e_condominio, upsert_estado_auto_cadastro
#     * gatilho: mensagem de texto CADASTRO_MORADOR|<condominio_id>
#     * estado da conversa em whatsapp_auto_cadastro_estado (AdmGeral)
# Novidades v2.7.0:
#   - NOVO: handler handle_quem_retirou — responde ao botao "Quem Retirou?"
#     do template chegada_encomenda_v3.
#     * Se encomenda entregue: informa nr da encomenda, data/hora chegada,
#       data/hora retirada e nome de quem retirou.
#     * Se ainda pendente: informa que a retirada ainda nao foi registrada.
#   - NOVO: _is_quem_retirou — detecta payload QUEM_RETIROU_{id}
#   - NOVO: get_encomenda_completa — query com data_entrega e nome_retirou
#   - Dispatch no receive_webhook atualizado para tratar QUEM_RETIROU
#     antes dos demais handlers (ordem correta).
#
# Correcoes v2.6.0:
#   - FIX CRITICO 1: button_id agora extrai corretamente o campo "payload"
#   - FIX CRITICO 2: fallback de telefone para encomenda_id divergente
#   - NOVO: find_morador_by_phone_id
#
# Correcoes v2.5.0:
#   - FIX: _is_confirmar_morador e _is_negar_morador detectam payload e texto
# =============================================================================

import asyncio
import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import aiohttp
import requests
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import text

from app.database import SessionLocal

router = APIRouter()
logger = logging.getLogger("app.webhook.whatsapp")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VERIFY_TOKEN         = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_TOKEN       = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID    = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
META_ACCESS_TOKEN    = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_ID        = os.getenv("META_PHONE_NUMBER_ID", "1002371749631777")
IMAGE_STORAGE_URL    = os.getenv("IMAGE_STORAGE_BASE_URL", "http://10.3.1.7:4000")
IMAGE_STORAGE_KEY    = os.getenv("IMAGE_STORAGE_API_KEY", "")
META_API_VERSION     = os.getenv("META_API_VERSION", "v25.0")

def _normalizar_bloco_wa(db, condominio_id, bloco):  # 2026-09-30: bloco padronizado
    try:
        from app.services.blocos import normalizar_bloco
        return normalizar_bloco(db, condominio_id, bloco)
    except Exception:
        return bloco


def _send_token():
    return META_ACCESS_TOKEN or WHATSAPP_TOKEN

def _send_phone_id():
    return META_PHONE_ID or WHATSAPP_PHONE_ID

# ---------------------------------------------------------------------------
# Helpers de telefone
# ---------------------------------------------------------------------------

def normalize_digits(phone: Optional[str]) -> str:
    if not phone:
        return ""
    return re.sub(r"\D", "", phone)


def generate_phone_candidates(phone: str) -> List[str]:
    digits = normalize_digits(phone)
    if not digits:
        return []
    candidates = {digits}
    if digits.startswith("55") and len(digits) > 2:
        local = digits[2:]
        candidates.add(local)
    else:
        local = digits
        candidates.add("55" + digits)
    if len(local) == 11 and local[2] == "9":
        without_9 = local[:2] + local[3:]
        candidates.add(without_9)
        candidates.add("55" + without_9)
    if len(local) == 10:
        with_9 = local[:2] + "9" + local[2:]
        candidates.add(with_9)
        candidates.add("55" + with_9)
    return list(candidates)


def formatar_telefone_exibicao(wa_id: str) -> str:
    """Mascara o telefone pra exibir em mensagem ao morador e pra salvar em
    moradores.telefone. BR de 11 digitos locais vira (XX) XXXXX-XXXX;
    qualquer outro formato (numero internacional com DDI diferente de 55,
    por exemplo) cai pro numero cru com "+" — NUNCA tenta reformatar isso
    como se fosse BR.

    Celular BR as vezes chega com 10 digitos locais (sem o 9 — comum em
    numeros antigos ou alguns registros do Meta). So nesse caso, quando o
    numero ja foi identificado como BR (com ou sem o prefixo 55, ambos os
    casos ja eram tratados como BR antes desta mudanca — nenhum numero
    internacional passa por aqui, ver acima), insere o 9 se o primeiro
    digito do numero local for 6-9 (padrao de celular)."""
    digits = normalize_digits(wa_id)
    if not digits:
        return wa_id or ""
    local = digits[2:] if digits.startswith("55") and len(digits) > 2 else digits
    if len(local) == 10 and local[2] in "6789":
        local = local[:2] + "9" + local[2:]
    if len(local) == 11:
        return f"({local[:2]}) {local[2:7]}-{local[7:]}"
    if len(local) == 10:
        return f"({local[:2]}) {local[2:6]}-{local[6:]}"
    return f"+{digits}"

# ---------------------------------------------------------------------------
# Envio Meta API
# ---------------------------------------------------------------------------

def _meta_headers():
    return {
        "Authorization": f"Bearer {_send_token()}",
        "Content-Type": "application/json",
    }

def _meta_url():
    return f"https://graph.facebook.com/{META_API_VERSION}/{_send_phone_id()}/messages"


def send_whatsapp_interactive_buttons(to_number: str, body: str, buttons: list) -> bool:
    if len(buttons) > 3:
        logger.error("Tentativa de enviar %d botoes, maximo permitido e 3", len(buttons))
        buttons = buttons[:3]

    token    = _send_token()
    phone_id = _send_phone_id()
    if not token or not phone_id:
        logger.warning("Token ou Phone ID nao configurados para envio interativo.")
        return False

    to_number = normalize_digits(to_number)
    if not to_number:
        return False

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": btn["id"], "title": btn["title"]}}
                    for btn in buttons
                ]
            },
        },
    }
    try:
        resp = requests.post(_meta_url(), headers=_meta_headers(), json=payload, timeout=30)
        if 200 <= resp.status_code < 300:
            logger.info("Interativo enviado para %s", to_number)
            return True
        logger.error("Erro interativo %s | %s | %s", to_number, resp.status_code, resp.text)
        return False
    except Exception as exc:
        logger.exception("Excecao interativo %s | %s", to_number, exc)
        return False


def send_whatsapp_text(to_number: str, body: str) -> bool:
    token    = _send_token()
    phone_id = _send_phone_id()
    if not token or not phone_id:
        logger.warning("Token ou Phone ID nao configurados para envio.")
        return False

    to_number = normalize_digits(to_number)
    if not to_number:
        return False

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": body},
    }
    try:
        resp = requests.post(_meta_url(), headers=_meta_headers(), json=payload, timeout=30)
        if 200 <= resp.status_code < 300:
            logger.info("Texto enviado para %s", to_number)
            return True
        logger.error("Erro texto %s | %s | %s", to_number, resp.status_code, resp.text)
        return False
    except Exception as exc:
        logger.exception("Excecao texto %s | %s", to_number, exc)
        return False


def send_whatsapp_image_base64(to_number: str, image_base64: str, caption: str = "") -> bool:
    token    = _send_token()
    phone_id = _send_phone_id()
    if not token or not phone_id:
        logger.warning("Token ou Phone ID nao configurados para envio de imagem.")
        return False

    to_number = normalize_digits(to_number)
    if not to_number or not image_base64:
        return False

    try:
        image_bytes = base64.b64decode(image_base64)
        upload_url  = f"https://graph.facebook.com/{META_API_VERSION}/{phone_id}/media"
        upload_resp = requests.post(
            upload_url,
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file":               ("etiqueta.jpg", image_bytes, "image/jpeg"),
                "type":               (None, "image/jpeg"),
                "messaging_product":  (None, "whatsapp"),
            },
            timeout=60,
        )
        if upload_resp.status_code not in (200, 201):
            logger.error("Erro upload imagem Meta | %s | %s", upload_resp.status_code, upload_resp.text)
            return False

        media_id = upload_resp.json().get("id")
        if not media_id:
            logger.error("media_id nao retornado pelo upload")
            return False

        logger.info("Imagem uploaded | media_id=%s", media_id)

        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "image",
            "image": {
                "id":      media_id,
                "caption": caption,
            },
        }
        msg_resp = requests.post(_meta_url(), headers=_meta_headers(), json=payload, timeout=30)
        if 200 <= msg_resp.status_code < 300:
            logger.info("Imagem enviada para %s", to_number)
            return True

        logger.error("Erro envio imagem %s | %s | %s", to_number, msg_resp.status_code, msg_resp.text)
        return False

    except Exception as exc:
        logger.exception("Excecao envio imagem %s | %s", to_number, exc)
        return False

# ---------------------------------------------------------------------------
# Storage interno — busca imagem
# ---------------------------------------------------------------------------

async def fetch_image_base64_from_storage(filename: str) -> Optional[str]:
    if not filename:
        return None

    filename = filename.lstrip("/")
    url = f"{IMAGE_STORAGE_URL}/storage/image/{filename}"

    headers = {}
    if IMAGE_STORAGE_KEY:
        headers["X-API-Key"] = IMAGE_STORAGE_KEY

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    logger.info("Imagem buscada do storage | filename=%s | bytes=%d", filename, len(content))
                    return base64.b64encode(content).decode("utf-8")
                logger.warning("Storage retornou %s para %s", resp.status, url)
                return None
    except Exception as exc:
        logger.exception("Erro buscando imagem do storage | %s | %s", url, exc)
        return None

# ---------------------------------------------------------------------------
# Queries banco
# ---------------------------------------------------------------------------

def find_morador_by_phone(db, wa_id: str) -> Optional[Dict]:
    candidates = generate_phone_candidates(wa_id)
    if not candidates:
        return None
    phone_expr = """
        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(telefone,''), ' ', ''), '-', ''), '(', ''), ')', ''), '+', '')
    """
    placeholders = ", ".join([f":p{i}" for i in range(len(candidates))])
    sql = text(f"""
        SELECT id, nome, telefone, condominio_id, apartamento, whats_confirmado
        FROM moradores
        WHERE {phone_expr} IN ({placeholders})
        LIMIT 1
    """)
    params = {f"p{i}": candidates[i] for i in range(len(candidates))}
    result = db.execute(sql, params).mappings().first()
    return dict(result) if result else None


def find_morador_by_phone_id(db, morador_id: int) -> Optional[Dict]:
    sql = text("""
        SELECT id, nome, telefone, condominio_id, apartamento, bloco, whats_confirmado
        FROM moradores
        WHERE id = :mid
        LIMIT 1
    """)
    result = db.execute(sql, {"mid": morador_id}).mappings().first()
    return dict(result) if result else None



# ---------------------------------------------------------------------------
# Auto-cadastro de morador via WhatsApp (Fase B)
# ---------------------------------------------------------------------------

def find_estado_auto_cadastro(db, wa_id: str) -> Optional[Dict]:
    sql = text("SELECT * FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id LIMIT 1")
    estado = db.execute(sql, {"wa_id": wa_id}).mappings().first()
    if not estado:
        return None

    if datetime.now() - estado["atualizado_em"] > timedelta(hours=24):
        db.execute(
            text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"),
            {"wa_id": wa_id},
        )
        return None

    return dict(estado)


def find_moradores_by_phone_e_condominio(db, wa_id: str, condominio_id: int) -> List[Dict]:
    """Retorna TODOS os moradores com esse telefone+condominio (nao so 1) —
    caso esperado: familia inteira recebendo encomenda em nome proprio, so
    um numero de WhatsApp cadastrado. Chamador decide o que fazer com >1."""
    candidates = generate_phone_candidates(wa_id)
    if not candidates:
        return []
    phone_expr = """
        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(telefone,''), ' ', ''), '-', ''), '(', ''), ')', ''), '+', '')
    """
    placeholders = ", ".join([f":p{i}" for i in range(len(candidates))])
    sql = text(f"""
        SELECT id, nome, telefone, condominio_id, apartamento, bloco, whats_confirmado
        FROM moradores
        WHERE {phone_expr} IN ({placeholders}) AND condominio_id = :condominio_id
        ORDER BY id
    """)
    params = {f"p{i}": candidates[i] for i in range(len(candidates))}
    params["condominio_id"] = condominio_id
    result = db.execute(sql, params).mappings().all()
    return [dict(r) for r in result]


def find_moradores_por_apartamento(db, condominio_id: int, apartamento: str, bloco: Optional[str]) -> List[Dict]:
    """Moradores da MESMA unidade (apto+bloco), independente de telefone —
    usado pelo fluxo 'Adiciona Pessoa' pra listar quem ja mora ali."""
    if bloco:
        sql = text("""
            SELECT id, nome FROM moradores
            WHERE condominio_id = :condominio_id AND apartamento = :apartamento AND bloco = :bloco
            ORDER BY nome
        """)
        params = {"condominio_id": condominio_id, "apartamento": apartamento, "bloco": bloco}
    else:
        sql = text("""
            SELECT id, nome FROM moradores
            WHERE condominio_id = :condominio_id AND apartamento = :apartamento
              AND (bloco IS NULL OR bloco = '')
            ORDER BY nome
        """)
        params = {"condominio_id": condominio_id, "apartamento": apartamento}
    result = db.execute(sql, params).mappings().all()
    return [dict(r) for r in result]


def upsert_estado_auto_cadastro(
    db,
    wa_id: str,
    condominio_id: int,
    etapa: str,
    nome_parcial: Optional[str] = None,
    apartamento_parcial: Optional[str] = None,
    bloco_parcial: Optional[str] = None,
) -> None:
    sql = text("""
        INSERT INTO whatsapp_auto_cadastro_estado
            (wa_id, condominio_id, etapa, nome_parcial, apartamento_parcial, bloco_parcial)
        VALUES
            (:wa_id, :condominio_id, :etapa, :nome_parcial, :apartamento_parcial, :bloco_parcial)
        ON DUPLICATE KEY UPDATE
            condominio_id       = VALUES(condominio_id),
            etapa               = VALUES(etapa),
            nome_parcial        = VALUES(nome_parcial),
            apartamento_parcial = VALUES(apartamento_parcial),
            bloco_parcial       = VALUES(bloco_parcial)
    """)
    db.execute(sql, {
        "wa_id": wa_id,
        "condominio_id": condominio_id,
        "etapa": etapa,
        "nome_parcial": nome_parcial,
        "apartamento_parcial": apartamento_parcial,
        "bloco_parcial": bloco_parcial,
    })
    db.commit()


def set_estado_correcao(
    db,
    wa_id: str,
    etapa: str,
    morador_id_editando: Optional[int],
    campo_editando: Optional[str],
    valor_novo: Optional[str],
) -> None:
    """Atualiza etapa + colunas do fluxo de correcao (morador_id_editando,
    campo_editando, valor_novo) num estado que ja existe — o fluxo de
    correcao so roda depois de aguardando_decisao_ja_cadastrado, que ja
    upsertou a linha pra esse wa_id via upsert_estado_auto_cadastro."""
    db.execute(text("""
        UPDATE whatsapp_auto_cadastro_estado
        SET etapa = :etapa,
            morador_id_editando = :morador_id_editando,
            campo_editando = :campo_editando,
            valor_novo = :valor_novo
        WHERE wa_id = :wa_id
    """), {
        "etapa": etapa,
        "wa_id": wa_id,
        "morador_id_editando": morador_id_editando,
        "campo_editando": campo_editando,
        "valor_novo": valor_novo,
    })
    db.commit()


TEXTO_INICIO = (
    "Olá! Vamos concluir seu cadastro como morador do {condominio_nome}. "
    "Qual é o seu nome completo?"
)
TEXTO_PERGUNTA_APTO = (
    "Obrigado, {nome}! Agora me informe o número do seu apartamento/unidade."
)
TEXTO_PERGUNTA_BLOCO = (
    "Seu condomínio tem bloco ou torre? Se sim, informe (ex: Bloco A). "
    "Se não tiver, responda 'sem bloco'."
)
TEXTO_CONFIRMACAO = (
    "Confirme os dados:\n"
    "Nome: {nome}\n"
    "Apartamento: {apto}\n"
    "Bloco: {bloco}\n"
    "Está correto? Responda SIM para confirmar ou NAO para recomeçar."
)
TEXTO_SUCESSO = "Cadastro concluído com sucesso! Bem-vindo(a) ao {condominio_nome}."
TEXTO_CANCELADO = "Cadastro cancelado. Se quiser recomeçar, clique no link novamente."
TEXTO_JA_CADASTRADO_MESMO = (
    "Você já está cadastrado(a) aqui no {condominio_nome}. Confira seus dados:\n"
    "Nome: {nome}\n"
    "Apto: {apartamento}\n"
    "Bloco: {bloco}\n"
    "WhatsApp: {whatsapp}\n\n"
    "Você pode confirmar seus dados, editar caso algo esteja errado, ou adicionar uma "
    "pessoa que mora com você (que também vai receber avisos por este mesmo número)."
)
TEXTO_JA_CADASTRADO_OUTRO = (
    "Identifiquei que seu número já está cadastrado em outro condomínio. "
    "Deseja fazer um cadastro novo também no {condominio_nome}? "
    "Responda SIM para continuar ou NAO para cancelar."
)


def enviar_ja_cadastrado_mesmo(wa_id: str, condominio: Dict, morador: Dict) -> None:
    """Reenvia a mensagem/menu de 'ja cadastrado' com os dados atuais do
    morador — usado no gatilho inicial e ao cancelar uma correcao em
    andamento (pra reexibir os dados sem o valor errado digitado)."""
    send_whatsapp_interactive_buttons(wa_id, TEXTO_JA_CADASTRADO_MESMO.format(
        condominio_nome=condominio["nome"], nome=morador["nome"],
        apartamento=morador["apartamento"], bloco=morador.get("bloco") or "não informado",
        whatsapp=formatar_telefone_exibicao(wa_id)),
        buttons=[
            {"id": "JA_CONFIRMAR", "title": "Confirmar Cadastro"},
            {"id": "JA_CORRIGIR", "title": "Corrigir"},
            {"id": "JA_ADICIONAR", "title": "Adiciona Pessoa"},
        ])


def enviar_escolha_campo_corrigir(wa_id: str) -> None:
    send_whatsapp_interactive_buttons(
        wa_id, "Qual dado você quer corrigir?",
        buttons=[
            {"id": "CORRIGIR_NOME", "title": "Nome"},
            {"id": "CORRIGIR_APTO", "title": "Apto"},
            {"id": "CORRIGIR_BLOCO", "title": "Bloco"},
        ])


def enviar_escolha_morador_multiplo(wa_id: str, candidatos: List[Dict]) -> None:
    """Mesmo telefone com >1 morador no condominio (ex: familia inteira, so
    um WhatsApp cadastrado) — pergunta sobre qual morador a conversa e.
    Nomes vao no corpo (podem passar do limite de ~20 caracteres de titulo
    de botao da Meta); botao so leva o numero da opcao."""
    mostrar = candidatos[:3]
    linhas = "\n".join(
        f"{i+1}. {m['nome']} — apto {m['apartamento']}" for i, m in enumerate(mostrar)
    )
    aviso = ""
    if len(candidatos) > 3:
        aviso = f"\n\n(mostrando as 3 primeiras de {len(candidatos)} encontradas)"
    send_whatsapp_interactive_buttons(
        wa_id,
        f"Encontrei mais de uma pessoa cadastrada com este WhatsApp:\n{linhas}{aviso}\n\n"
        "Sobre qual morador você quer falar?",
        buttons=[
            {"id": f"ESCOLHER_MORADOR_{m['id']}", "title": f"Opção {i + 1}"}
            for i, m in enumerate(mostrar)
        ])


def enviar_lista_e_confirmar_adicionar(db, wa_id: str, morador_ref: Dict) -> None:
    """Lista quem ja mora na mesma unidade (apto+bloco) do morador de
    referencia e pergunta se confirma adicionar mais uma pessoa. Chamada no
    clique inicial de 'Adiciona Pessoa' E de novo apos cada insercao (loop),
    pra lista sempre refletir quem ja foi adicionado nesta conversa."""
    existentes = find_moradores_por_apartamento(
        db, morador_ref["condominio_id"], morador_ref["apartamento"], morador_ref.get("bloco")
    )
    nomes = "\n".join(f"- {m['nome']}" for m in existentes) or "(nenhum encontrado)"
    send_whatsapp_interactive_buttons(
        wa_id,
        f"Moradores já cadastrados no seu apartamento:\n{nomes}\n\n"
        "Deseja adicionar mais uma pessoa?",
        buttons=[
            {"id": "ADICIONAR_CONFIRMAR", "title": "Sim, adicionar"},
            {"id": "ADICIONAR_CANCELAR", "title": "Não, voltar"},
        ])


def handle_auto_cadastro(db, wa_id: str, message_text: str, message_text_norm: str, button_id: Optional[str]) -> bool:
    match = re.match(r'^CADASTRO_MORADOR\|(\d{1,6})$', (message_text or "").strip())
    if match:
        condominio_id = int(match.group(1))
        condominio = get_condominio(db, condominio_id)
        if not condominio:
            return False
        if not condominio.get('permite_auto_cadastro'):
            return False

        candidatos_neste = find_moradores_by_phone_e_condominio(db, wa_id, condominio_id)
        if len(candidatos_neste) == 1:
            ja_neste = candidatos_neste[0]
            upsert_estado_auto_cadastro(db, wa_id, condominio_id, "aguardando_decisao_ja_cadastrado")
            set_estado_correcao(db, wa_id, "aguardando_decisao_ja_cadastrado",
                                 morador_id_editando=ja_neste["id"], campo_editando=None, valor_novo=None)
            enviar_ja_cadastrado_mesmo(wa_id, condominio, ja_neste)
            return True
        elif len(candidatos_neste) > 1:
            upsert_estado_auto_cadastro(db, wa_id, condominio_id, "aguardando_escolha_morador_multiplo")
            enviar_escolha_morador_multiplo(wa_id, candidatos_neste)
            return True

        em_outro = find_morador_by_phone(db, wa_id)
        if em_outro:
            upsert_estado_auto_cadastro(db, wa_id, condominio_id, "aguardando_decisao_outro_condominio")
            send_whatsapp_interactive_buttons(wa_id, TEXTO_JA_CADASTRADO_OUTRO.format(condominio_nome=condominio["nome"]),
                buttons=[
                    {"id": "SIM_OUTRO_COND", "title": "Sim, cadastrar"},
                    {"id": "NAO_OUTRO_COND", "title": "Não, obrigado"},
                ])
            return True

        upsert_estado_auto_cadastro(db, wa_id, condominio_id, "aguardando_nome")
        send_whatsapp_text(wa_id, TEXTO_INICIO.format(condominio_nome=condominio["nome"]))
        return True

    estado = find_estado_auto_cadastro(db, wa_id)
    if not estado:
        return False

    etapa = estado["etapa"]

    if etapa == "aguardando_nome":
        nome = (message_text or "").strip()
        if not nome:
            send_whatsapp_text(wa_id, "Não entendi. Qual é o seu nome completo?")
            return True
        upsert_estado_auto_cadastro(db, wa_id, estado["condominio_id"], "aguardando_apto",
                                     nome_parcial=nome)
        send_whatsapp_text(wa_id, TEXTO_PERGUNTA_APTO.format(nome=nome))

    elif etapa == "aguardando_apto":
        apto = (message_text or "").strip()
        if not apto:
            send_whatsapp_text(wa_id, "Não entendi. Qual o número do seu apartamento?")
            return True
        upsert_estado_auto_cadastro(db, wa_id, estado["condominio_id"], "aguardando_bloco",
                                     nome_parcial=estado["nome_parcial"], apartamento_parcial=apto)
        send_whatsapp_text(wa_id, TEXTO_PERGUNTA_BLOCO)

    elif etapa == "aguardando_bloco":
        bloco = None if message_text_norm in ("sem bloco", "nao tenho", "não tenho", "-", "n/a") \
            else (message_text or "").strip()
        upsert_estado_auto_cadastro(db, wa_id, estado["condominio_id"], "aguardando_confirmacao",
                                     nome_parcial=estado["nome_parcial"],
                                     apartamento_parcial=estado["apartamento_parcial"],
                                     bloco_parcial=bloco)
        send_whatsapp_interactive_buttons(wa_id, TEXTO_CONFIRMACAO.format(
            nome=estado["nome_parcial"], apto=estado["apartamento_parcial"],
            bloco=bloco or "não informado"),
            buttons=[
                {"id": "SIM_CONFIRMAR", "title": "Sim, confirmar"},
                {"id": "NAO_CONFIRMAR", "title": "Não, corrigir"},
            ])

    elif etapa == "aguardando_confirmacao":
        if _is_sim_confirmar_cadastro(button_id, message_text_norm):
            condominio = get_condominio(db, estado["condominio_id"])
            condominio_nome = condominio.get("nome") if condominio else None

            sql = text("""
                INSERT INTO moradores
                    (nome, apartamento, bloco, telefone, condominio_id, condominio_nome,
                     whats_confirmado, obs)
                VALUES
                    (:nome, :apartamento, :bloco, :telefone, :condominio_id, :condominio_nome,
                     NOW(), :obs)
            """)
            db.execute(sql, {
                "nome":            estado["nome_parcial"],
                "apartamento":     estado["apartamento_parcial"],
                "bloco":           _normalizar_bloco_wa(db, estado["condominio_id"], estado["bloco_parcial"]),
                "telefone":        formatar_telefone_exibicao(wa_id),
                "condominio_id":   estado["condominio_id"],
                "condominio_nome": condominio_nome,
                "obs":             f"Cadastro via auto-cadastro WhatsApp em {datetime.now():%d/%m/%Y %H:%M}",
            })
            db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
            send_whatsapp_text(wa_id, TEXTO_SUCESSO.format(condominio_nome=condominio_nome))

        elif _is_nao_confirmar_cadastro(button_id, message_text_norm):
            db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
            send_whatsapp_text(wa_id, TEXTO_CANCELADO)

        else:
            send_whatsapp_text(wa_id, "Não entendi. Toque em um dos botões ou responda SIM ou NAO.")

    elif etapa == "aguardando_decisao_ja_cadastrado":
        if _is_ja_confirmar(button_id, message_text_norm):
            send_whatsapp_text(wa_id, "Obrigado por confirmar!")
            db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
        elif _is_ja_corrigir(button_id, message_text_norm):
            morador = find_morador_by_phone_id(db, estado["morador_id_editando"])
            if not morador:
                send_whatsapp_text(wa_id, "Não encontrei mais seu cadastro. Entre em contato com a portaria.")
                db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
            else:
                set_estado_correcao(db, wa_id, "corrigindo_escolher_campo",
                                     morador_id_editando=morador["id"], campo_editando=None, valor_novo=None)
                enviar_escolha_campo_corrigir(wa_id)
        elif _is_ja_adicionar(button_id, message_text_norm):
            morador_ref = find_morador_by_phone_id(db, estado["morador_id_editando"])
            if not morador_ref:
                send_whatsapp_text(wa_id, "Não encontrei mais seu cadastro. Entre em contato com a portaria.")
                db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
            else:
                set_estado_correcao(db, wa_id, "adicionando_confirmar",
                                     morador_id_editando=estado["morador_id_editando"],
                                     campo_editando=None, valor_novo=None)
                enviar_lista_e_confirmar_adicionar(db, wa_id, morador_ref)
        else:
            send_whatsapp_text(wa_id, "Não entendi. Toque em um dos botões ou responda CONFIRMAR, CORRIGIR ou ADICIONAR.")

    elif etapa == "aguardando_escolha_morador_multiplo":
        candidatos = find_moradores_by_phone_e_condominio(db, wa_id, estado["condominio_id"])
        escolhido = _escolher_morador_por_resposta(button_id, message_text_norm, candidatos[:3])
        if not escolhido:
            send_whatsapp_text(wa_id, "Não entendi. Toque em uma das opções.")
        else:
            set_estado_correcao(db, wa_id, "aguardando_decisao_ja_cadastrado",
                                 morador_id_editando=escolhido["id"], campo_editando=None, valor_novo=None)
            condominio = get_condominio(db, estado["condominio_id"])
            enviar_ja_cadastrado_mesmo(wa_id, condominio, escolhido)

    elif etapa == "adicionando_confirmar":
        if _is_adicionar_confirmar(button_id, message_text_norm):
            set_estado_correcao(db, wa_id, "adicionando_aguardando_nome",
                                 morador_id_editando=estado["morador_id_editando"],
                                 campo_editando=None, valor_novo=None)
            send_whatsapp_text(wa_id, "Qual é o nome completo da nova pessoa?")
        elif _is_adicionar_cancelar(button_id, message_text_norm):
            morador = find_morador_by_phone_id(db, estado["morador_id_editando"])
            condominio = get_condominio(db, estado["condominio_id"])
            if morador and condominio:
                set_estado_correcao(db, wa_id, "aguardando_decisao_ja_cadastrado",
                                     morador_id_editando=estado["morador_id_editando"],
                                     campo_editando=None, valor_novo=None)
                enviar_ja_cadastrado_mesmo(wa_id, condominio, morador)
            else:
                db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
                send_whatsapp_text(wa_id, "Tudo bem, nada foi alterado.")
        else:
            send_whatsapp_text(wa_id, "Não entendi. Toque em Sim ou Não.")

    elif etapa == "adicionando_aguardando_nome":
        nome_novo = (message_text or "").strip()
        if not nome_novo:
            send_whatsapp_text(wa_id, "Não entendi. Qual é o nome completo da nova pessoa?")
        elif len(nome_novo) > 200:
            # moradores.nome e VARCHAR(200) — rejeita em vez de truncar
            # silenciosamente (mesma categoria de erro do ENUM, seção 15-O).
            send_whatsapp_text(wa_id, "Nome muito longo, tente novamente (máximo 200 caracteres).")
        else:
            morador_ref = find_morador_by_phone_id(db, estado["morador_id_editando"])
            if not morador_ref:
                send_whatsapp_text(wa_id, "Não encontrei o cadastro de referência. Entre em contato com a portaria.")
                db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
            else:
                condominio_ref = get_condominio(db, morador_ref["condominio_id"])
                db.execute(text("""
                    INSERT INTO moradores
                        (nome, apartamento, bloco, telefone, condominio_id, condominio_nome,
                         whats_confirmado, obs)
                    VALUES
                        (:nome, :apartamento, :bloco, :telefone, :condominio_id, :condominio_nome,
                         NOW(), :obs)
                """), {
                    "nome":            nome_novo,
                    "apartamento":     morador_ref["apartamento"],
                    "bloco":           morador_ref.get("bloco"),
                    "telefone":        formatar_telefone_exibicao(wa_id),
                    "condominio_id":   morador_ref["condominio_id"],
                    "condominio_nome": condominio_ref.get("nome") if condominio_ref else None,
                    "obs":             f"Adicionado via WhatsApp em {datetime.now():%d/%m/%Y %H:%M} "
                                       f"(mesmo telefone de {morador_ref['nome']})",
                })
                send_whatsapp_text(wa_id, f"{nome_novo} foi adicionado(a) com sucesso!")
                set_estado_correcao(db, wa_id, "adicionando_confirmar",
                                     morador_id_editando=estado["morador_id_editando"],
                                     campo_editando=None, valor_novo=None)
                enviar_lista_e_confirmar_adicionar(db, wa_id, morador_ref)

    elif etapa == "corrigindo_escolher_campo":
        campo = _campo_por_escolha_corrigir(button_id, message_text_norm)
        if not campo:
            send_whatsapp_text(wa_id, "Não entendi. Toque em Nome, Apto ou Bloco.")
        else:
            set_estado_correcao(db, wa_id, "corrigindo_aguardando_valor",
                                 morador_id_editando=estado["morador_id_editando"],
                                 campo_editando=campo, valor_novo=None)
            send_whatsapp_text(wa_id, f"Qual é o novo {campo}? (responda com o valor)")

    elif etapa == "corrigindo_aguardando_valor":
        valor_novo = (message_text or "").strip()
        campo_editando = estado["campo_editando"]
        if not valor_novo:
            send_whatsapp_text(wa_id, f"Não entendi. Qual é o novo {campo_editando}?")
        else:
            morador_atual = find_morador_by_phone_id(db, estado["morador_id_editando"])
            valor_atual = (morador_atual or {}).get(campo_editando) or "não informado"
            set_estado_correcao(db, wa_id, "corrigindo_aguardando_confirmacao",
                                 morador_id_editando=estado["morador_id_editando"],
                                 campo_editando=campo_editando, valor_novo=valor_novo)
            send_whatsapp_interactive_buttons(
                wa_id,
                f"Atual — {campo_editando}: {valor_atual}\n"
                f"Novo — {campo_editando}: {valor_novo}\n\n"
                "Confirma a alteração?",
                buttons=[
                    {"id": "CORRIGIR_CONFIRMAR", "title": "Confirmar"},
                    {"id": "CORRIGIR_CANCELAR", "title": "Cancelar"},
                ])

    elif etapa == "corrigindo_aguardando_confirmacao":
        if _is_corrigir_confirmar_valor(button_id, message_text_norm):
            campo_editando = estado["campo_editando"]
            if campo_editando not in ("nome", "apartamento", "bloco"):
                logger.error("corrigindo_aguardando_confirmacao: campo_editando invalido %r | wa_id=%s",
                             campo_editando, wa_id)
                send_whatsapp_text(wa_id, "Erro interno ao identificar o campo. Entre em contato com a portaria.")
                db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
            else:
                db.execute(text(f"UPDATE moradores SET {campo_editando} = :valor WHERE id = :id"),
                           {"valor": estado["valor_novo"], "id": estado["morador_id_editando"]})
                set_estado_correcao(db, wa_id, "corrigindo_mais_ou_finalizar",
                                     morador_id_editando=estado["morador_id_editando"],
                                     campo_editando=None, valor_novo=None)
                send_whatsapp_interactive_buttons(
                    wa_id, "Dado atualizado com sucesso! Quer corrigir mais algum dado?",
                    buttons=[
                        {"id": "CORRIGIR_MAIS", "title": "Sim, corrigir"},
                        {"id": "CORRIGIR_FINALIZAR", "title": "Não, finalizar"},
                    ])
        elif _is_corrigir_cancelar_valor(button_id, message_text_norm):
            morador = find_morador_by_phone_id(db, estado["morador_id_editando"])
            condominio = get_condominio(db, estado["condominio_id"])
            if morador and condominio:
                set_estado_correcao(db, wa_id, "aguardando_decisao_ja_cadastrado",
                                     morador_id_editando=None, campo_editando=None, valor_novo=None)
                enviar_ja_cadastrado_mesmo(wa_id, condominio, morador)
            else:
                db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
                send_whatsapp_text(wa_id, "Alteração cancelada.")
        else:
            send_whatsapp_text(wa_id, "Não entendi. Toque em Confirmar ou Cancelar.")

    elif etapa == "corrigindo_mais_ou_finalizar":
        if _is_corrigir_mais(button_id, message_text_norm):
            set_estado_correcao(db, wa_id, "corrigindo_escolher_campo",
                                 morador_id_editando=estado["morador_id_editando"],
                                 campo_editando=None, valor_novo=None)
            enviar_escolha_campo_corrigir(wa_id)
        elif _is_corrigir_finalizar(button_id, message_text_norm):
            db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
            send_whatsapp_text(wa_id, "Tudo certo! Se precisar de algo mais, é só chamar.")
        else:
            send_whatsapp_text(wa_id, "Não entendi. Toque em Sim ou Não.")

    elif etapa == "aguardando_decisao_outro_condominio":
        if _is_sim_outro_condominio(button_id, message_text_norm):
            upsert_estado_auto_cadastro(db, wa_id, estado["condominio_id"], "aguardando_nome")
            condominio = get_condominio(db, estado["condominio_id"])
            send_whatsapp_text(wa_id, TEXTO_INICIO.format(
                condominio_nome=condominio.get("nome") if condominio else ""))
        elif _is_nao_outro_condominio(button_id, message_text_norm):
            db.execute(text("DELETE FROM whatsapp_auto_cadastro_estado WHERE wa_id = :wa_id"), {"wa_id": wa_id})
            send_whatsapp_text(wa_id, "Tudo bem, cadastro não realizado.")
        else:
            send_whatsapp_text(wa_id, "Não entendi. Toque em um dos botões ou responda SIM ou NAO.")

    db.commit()
    return True


def get_condominio(db, condominio_id: int) -> Optional[Dict]:
    sql = text("SELECT id, nome, telefone, permite_auto_cadastro FROM condominios WHERE id = :cid LIMIT 1")
    result = db.execute(sql, {"cid": condominio_id}).mappings().first()
    return dict(result) if result else None


def get_encomendas_pendentes(db, morador_id: int) -> List[Dict]:
    sql = text("""
        SELECT
            id, codigo_rastreio, apartamento, bloco,
            data_recebimento, img_etiqueta_server, status,
            nome_destinatario, condominio_id, morador_id
        FROM encomendas
        WHERE morador_id = :morador_id
          AND status = 'pendente'
        ORDER BY data_recebimento DESC
        LIMIT 5
    """)
    rows = db.execute(sql, {"morador_id": morador_id}).mappings().all()
    return [dict(r) for r in rows]


def get_encomenda_por_id_e_morador(db, encomenda_id: int, morador_id: int) -> Optional[Dict]:
    sql = text("""
        SELECT
            id, codigo_rastreio, apartamento, bloco,
            data_recebimento, img_etiqueta_server, status,
            nome_destinatario, condominio_id, morador_id
        FROM encomendas
        WHERE id        = :id
          AND morador_id = :morador_id
        LIMIT 1
    """)
    result = db.execute(sql, {"id": encomenda_id, "morador_id": morador_id}).mappings().first()
    return dict(result) if result else None


def get_encomenda_por_id(db, encomenda_id: int) -> Optional[Dict]:
    sql = text("""
        SELECT
            id, codigo_rastreio, apartamento, bloco,
            data_recebimento, img_etiqueta_server, status,
            nome_destinatario, condominio_id, morador_id
        FROM encomendas
        WHERE id = :id
        LIMIT 1
    """)
    result = db.execute(sql, {"id": encomenda_id}).mappings().first()
    return dict(result) if result else None


def get_encomenda_completa(db, encomenda_id: int) -> Optional[Dict]:
    """
    v2.7.0 — Busca encomenda com todos os campos de entrega.
    Usada pelo handler QUEM_RETIROU para informar data/hora e nome de quem retirou.
    """
    sql = text("""
        SELECT
            id, codigo_rastreio, apartamento, bloco,
            data_recebimento, data_entrega, img_etiqueta_server,
            status, nome_destinatario, nome_retirou,
            condominio_id, morador_id
        FROM encomendas
        WHERE id = :id
        LIMIT 1
    """)
    result = db.execute(sql, {"id": encomenda_id}).mappings().first()
    return dict(result) if result else None


def confirmar_retirada_encomenda(db, encomenda_id: int, morador_id: int, nome_retirou: str, wa_number: str = "") -> bool:
    try:
        db.execute(text("""
            UPDATE encomendas
            SET status = 'entregue',
                data_entrega = NOW(),
                nome_retirou = :nome_retirou,
                observacoes = CONCAT(
                    IFNULL(observacoes, ''),
                    '\n[', DATE_FORMAT(NOW(), '%d/%m/%Y %H:%i'),
                    '] Entrega confirmada pelo morador via WhatsApp ',
                    :wa_number
                )
            WHERE id        = :id
              AND morador_id = :morador_id
              AND status    = 'pendente'
        """), {"id": encomenda_id, "morador_id": morador_id, "nome_retirou": nome_retirou, "wa_number": wa_number})
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.exception("Erro ao confirmar retirada encomenda_id=%s | %s", encomenda_id, exc)
        return False


def update_whats_confirmado(db, morador_id: int) -> bool:
    try:
        db.execute(text("""
            UPDATE moradores
            SET whats_confirmado = NOW()
            WHERE id = :id
        """), {"id": morador_id})
        db.commit()
        logger.info("whats_confirmado=NOW() | morador_id=%s", morador_id)
        return True
    except Exception as exc:
        db.rollback()
        logger.exception("Erro ao atualizar whats_confirmado | morador_id=%s | %s", morador_id, exc)
        return False


def reenviar_encomenda_pendente_apos_confirmacao(db, morador_id: int, encomenda_id: Optional[int]) -> bool:
    try:
        from app.services.whatsapp import queue_whatsapp_message, EVENTO_ENCOMENDA_RECEBIDA

        enc = None
        if encomenda_id:
            enc = get_encomenda_por_id_e_morador(db, encomenda_id, morador_id)

        if not enc:
            pendentes = get_encomendas_pendentes(db, morador_id)
            if pendentes:
                enc = pendentes[0]

        if not enc:
            logger.info(
                "Sem encomenda pendente para reenviar apos confirmacao | morador_id=%s",
                morador_id,
            )
            return False

        row = db.execute(
            text("SELECT telefone, nome, condominio_id FROM moradores WHERE id = :id LIMIT 1"),
            {"id": morador_id},
        ).mappings().first()
        if not row or not row["telefone"]:
            return False

        nome_cond = ""
        if row["condominio_id"]:
            cond = get_condominio(db, row["condominio_id"])
            if cond:
                nome_cond = cond.get("nome", "")

        ok = queue_whatsapp_message(
            tipo_evento=EVENTO_ENCOMENDA_RECEBIDA,
            telefone=row["telefone"],
            nome_morador=row["nome"],
            morador_id=morador_id,
            condominio_id=row["condominio_id"],
            encomenda_id=enc["id"],
            mensagem_original=(
                f"ENCOMENDA NA PORTARIA | {enc.get('codigo_rastreio','S/N')} | "
                f"{enc.get('apartamento','')} | reenviado apos confirmacao WhatsApp"
            ),
            payload={
                "nome_destinatario": enc.get("nome_destinatario", row["nome"]),
                "apartamento":       enc.get("apartamento", ""),
                "bloco":             enc.get("bloco", ""),
                "codigo_rastreio":   enc.get("codigo_rastreio", ""),
                "nome_condominio":   nome_cond,
                "id":                enc["id"],
            },
            delay_minutes=0,
        )
        if ok:
            logger.info(
                "ENCOMENDA_RECEBIDA reenfileirada apos confirmacao | morador_id=%s | enc_id=%s",
                morador_id, enc["id"],
            )
        return ok

    except Exception as exc:
        logger.exception(
            "Erro ao reenfileirar encomenda apos confirmacao | morador_id=%s | %s",
            morador_id, exc,
        )
        return False


def clear_morador_phone(db, morador_id: int) -> None:
    db.execute(text("UPDATE moradores SET telefone = NULL WHERE id = :id"), {"id": morador_id})


def save_inbound_message(db, **kwargs) -> None:
    sql = text("""
        INSERT INTO whatsapp_inbound_messages (
            wa_id, profile_name, condominio_id, morador_id,
            matched_phone, action_taken, message_id,
            message_type, message_text, raw_payload
        ) VALUES (
            :wa_id, :profile_name, :condominio_id, :morador_id,
            :matched_phone, :action_taken, :message_id,
            :message_type, :message_text, :raw_payload
        )
    """)
    db.execute(sql, {**kwargs, "raw_payload": json.dumps(kwargs.get("raw_payload", {}), ensure_ascii=False)})

# ---------------------------------------------------------------------------
# Helpers de deteccao de botao
# ---------------------------------------------------------------------------

def _extrair_id_do_payload(button_id: Optional[str], prefixo: str) -> Optional[int]:
    if not button_id:
        return None
    upper = button_id.upper()
    if upper.startswith(prefixo):
        parte = upper[len(prefixo):]
        if parte.isdigit():
            return int(parte)
    return None


def _is_ver_encomenda(button_id: Optional[str], message_text_norm: str) -> bool:
    if not button_id and not message_text_norm:
        return False
    bid_upper = (button_id or "").upper()
    return (
        bid_upper in ("VER ENCOMENDA", "VER_ENCOMENDA")
        or bid_upper.startswith("VER_ENCOMENDA_")
        or message_text_norm in ("ver encomenda", "ver encomendas")
    )


def _is_confirmar_retirada(button_id: Optional[str], message_text_norm: str) -> bool:
    if not button_id and not message_text_norm:
        return False
    bid_upper = (button_id or "").upper()
    return (
        bid_upper in ("CONFIRMAR RETIRADA", "CONFIRMAR_RETIRADA")
        or bid_upper.startswith("CONFIRMAR_RETIRADA_")
        or message_text_norm in ("confirmar retirada", "confirmar")
    )


def _is_retirada_confirmada(button_id: Optional[str], message_text_norm: str) -> bool:
    if not button_id and not message_text_norm:
        return False
    bid_upper = (button_id or "").upper()
    return (
        bid_upper.startswith("RETIRADA_CONFIRMADA_")
        or message_text_norm in ("sim, ja retirei", "ja retirei", "sim ja retirei", "retirei", "sim, retirei")
    )


def _is_retirada_negada(button_id: Optional[str], message_text_norm: str) -> bool:
    if not button_id and not message_text_norm:
        return False
    bid_upper = (button_id or "").upper()
    return (
        bid_upper.startswith("RETIRADA_NEGADA_")
        or message_text_norm in ("nao retirei", "não retirei", "ainda nao retirei")
    )


def _is_quem_retirou(button_id: Optional[str], message_text_norm: str) -> bool:
    """
    v2.7.0 — Detecta clique no botao "Quem Retirou?" do template chegada_encomenda_v3.
    Payload: QUEM_RETIROU_{encomenda_id}
    """
    if not button_id and not message_text_norm:
        return False
    bid_upper = (button_id or "").upper()
    return (
        bid_upper in ("QUEM RETIROU", "QUEM_RETIROU", "QUEM RETIROU?", "QUEM_RETIROU?")
        or bid_upper.startswith("QUEM_RETIROU_")
        or message_text_norm in ("quem retirou", "quem retirou?")
    )


def _is_confirmar_morador(button_id: Optional[str], message_text_norm: str = "") -> bool:
    bid = (button_id or "").upper().strip()
    txt = message_text_norm.strip()
    return (
        bid == "CONFIRMAR_MORADOR"
        or bid in ("SIM, SOU EU", "SIM SOU EU", "SIM")
        or txt in ("sim, sou eu", "sim sou eu", "sou eu", "sim")
    )


def _is_negar_morador(button_id: Optional[str], message_text_norm: str = "") -> bool:
    bid = (button_id or "").upper().strip()
    txt = message_text_norm.strip()
    return (
        bid == "NEGAR_MORADOR"
        or bid in ("NAO SOU EU", "NÃO SOU EU", "NAO", "NÃO")
        or txt in ("nao sou eu", "não sou eu", "nao sou", "nao")
    )

# ---------------------------------------------------------------------------
# Helpers de auto-cadastro (Fase B)
# ---------------------------------------------------------------------------

def _is_sim_outro_condominio(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "SIM_OUTRO_COND" or message_text_norm in ("sim", "s")


def _is_nao_outro_condominio(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "NAO_OUTRO_COND" or message_text_norm in ("nao", "não", "n")


def _is_sim_confirmar_cadastro(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "SIM_CONFIRMAR" or message_text_norm in ("sim", "s", "confirmar")


def _is_nao_confirmar_cadastro(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "NAO_CONFIRMAR" or message_text_norm in ("nao", "não", "n", "cancelar")


def _is_ja_confirmar(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "JA_CONFIRMAR" or message_text_norm in ("confirmar", "sim", "s")


def _is_ja_corrigir(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "JA_CORRIGIR" or message_text_norm in ("corrigir", "nao", "não", "n")


def _is_ja_adicionar(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "JA_ADICIONAR" or message_text_norm in ("adicionar", "adicionar pessoa")


def _escolher_morador_por_resposta(
    button_id: Optional[str], message_text_norm: str, candidatos: List[Dict]
) -> Optional[Dict]:
    """Resolve a escolha entre varios moradores com o mesmo telefone: por
    botao (id do morador embutido, via _extrair_id_do_payload — mesmo
    padrao ja usado em outros fluxos do arquivo, ex. CONFIRMAR_RETIRADA_{id})
    ou, sem clique, por nome digitado (contido no nome de algum candidato).
    Nao persiste a lista de candidatos em lugar nenhum — sempre recebida
    fresca de quem chama."""
    escolhido_id = _extrair_id_do_payload(button_id, "ESCOLHER_MORADOR_")
    if escolhido_id is not None:
        for m in candidatos:
            if m["id"] == escolhido_id:
                return m
        return None
    texto = (message_text_norm or "").strip()
    if not texto:
        return None
    for m in candidatos:
        if texto in m["nome"].lower():
            return m
    return None


def _is_adicionar_confirmar(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "ADICIONAR_CONFIRMAR" or message_text_norm in ("sim", "s", "confirmar", "adicionar")


def _is_adicionar_cancelar(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "ADICIONAR_CANCELAR" or message_text_norm in ("nao", "não", "n", "cancelar", "voltar")


def _campo_por_escolha_corrigir(button_id: Optional[str], message_text_norm: str) -> Optional[str]:
    """Mapeia o botao/texto da escolha de campo pro nome da coluna real em
    moradores. Retorna None se nao reconhecer a escolha."""
    mapa_botao = {"CORRIGIR_NOME": "nome", "CORRIGIR_APTO": "apartamento", "CORRIGIR_BLOCO": "bloco"}
    bid_upper = (button_id or "").upper()
    if bid_upper in mapa_botao:
        return mapa_botao[bid_upper]
    mapa_texto = {"nome": "nome", "apto": "apartamento", "apartamento": "apartamento", "bloco": "bloco"}
    return mapa_texto.get(message_text_norm)


def _is_corrigir_confirmar_valor(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "CORRIGIR_CONFIRMAR" or message_text_norm in ("confirmar", "sim", "s")


def _is_corrigir_cancelar_valor(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "CORRIGIR_CANCELAR" or message_text_norm in ("cancelar", "nao", "não", "n")


def _is_corrigir_mais(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "CORRIGIR_MAIS" or message_text_norm in ("sim", "s")


def _is_corrigir_finalizar(button_id: Optional[str], message_text_norm: str) -> bool:
    bid_upper = (button_id or "").upper()
    return bid_upper == "CORRIGIR_FINALIZAR" or message_text_norm in ("nao", "não", "n", "finalizar")

# ---------------------------------------------------------------------------
# Helpers de prospeccao
# ---------------------------------------------------------------------------

def _is_prospeccao_interesse(button_id, message_text_norm: str = "") -> bool:
    import unicodedata
    def norm(s): return unicodedata.normalize('NFKD', s or '').encode('ascii','ignore').decode().lower().strip()
    bid = norm(button_id); txt = norm(message_text_norm)
    return bid in ("quero saber mais", "quero_saber_mais") or "quero saber mais" in txt

def _is_prospeccao_sem_interesse(button_id, message_text_norm: str = "") -> bool:
    import unicodedata
    def norm(s): return unicodedata.normalize('NFKD', s or '').encode('ascii','ignore').decode().lower().strip()
    bid = norm(button_id); txt = norm(message_text_norm)
    return bid in ("nao tenho interesse", "nao_tenho_interesse") or "nao tenho interesse" in txt

def _handle_prospeccao_resposta(db, wa_id: str, button_id, message_text_norm: str) -> bool:
    from sqlalchemy import text as sql_text
    import unicodedata
    def norm(s): return unicodedata.normalize('NFKD', s or '').encode('ascii','ignore').decode().lower().strip()
    sufixo = wa_id[-8:] if len(wa_id) >= 8 else wa_id
    row = db.execute(sql_text(
        "SELECT id, nome FROM marketing_leads "
        "WHERE REPLACE(REPLACE(REPLACE(whatsapp,'+',''),'-',''),' ','') LIKE :suf "
        "AND whatsapp IS NOT NULL "
        "ORDER BY prospeccao_enviada_em DESC LIMIT 1"
    ), {"suf": f"%{sufixo}"}).fetchone()
    if not row:
        logger.info("Prospeccao: lead nao encontrado para wa_id=%s", wa_id)
        return False
    lead_id, lead_nome = row[0], (row[1] or "Lead")
    interesse = _is_prospeccao_interesse(button_id, message_text_norm)
    if interesse:
        db.execute(sql_text(
            "UPDATE marketing_leads SET status='interessado', temperatura='quente', "
            "resposta_prospeccao='Quero saber mais', updated_at=NOW() WHERE id=:id"
        ), {"id": lead_id})
        db.commit()
        msg = (
            "Olá! Que ótimo que você quer saber mais! 👋\n\n"
            "Um dos maiores desafios no condomínio é o controle de encomendas. "
            "O *eCondomínio* resolve isso de forma simples:\n\n"
            "📸 O porteiro tira a foto da etiqueta\n"
            "⚡ O sistema registra automaticamente\n"
            "📲 O morador recebe aviso imediato no WhatsApp\n\n"
            "*Resultados para o condomínio:*\n"
            "✔ Até 92% menos tempo gasto na portaria\n"
            "✔ Mais organização e segurança\n"
            "✔ Histórico completo com foto e retirada registrada\n\n"
            "🏢 *eCondomínio*\n"
            "🌐 https://econdominio.com.br\n\n"
            "📹 *Veja como funciona em 1 minuto:*\n"
            "▶️ Receber encomenda:\nhttps://youtube.com/shorts/pYLUcnBBeq0\n"
            "▶️ Entregar encomenda:\nhttps://youtube.com/shorts/nLF0VGxyx1s\n"
            "▶️ Painel administrativo:\nhttps://youtu.be/jaB0AHgi4eQ\n\n"
            "Nossa equipe de vendas entrará em contato pelo WhatsApp:\n"
            "📞 (48) 3035-1252"
        )
        try:
            send_whatsapp_text(wa_id, msg)
        except Exception as e:
            logger.warning("Erro ao enviar apresentacao prospeccao: %s", e)
        logger.info("Prospeccao: lead %s (%s) INTERESSADO", lead_id, lead_nome)
    else:
        db.execute(sql_text(
            "UPDATE marketing_leads SET bloquear_prospeccao=1, status='descartado', "
            "resposta_prospeccao='Nao tenho interesse', updated_at=NOW() WHERE id=:id"
        ), {"id": lead_id})
        db.commit()
        logger.info("Prospeccao: lead %s (%s) SEM INTERESSE", lead_id, lead_nome)
    return True

# ---------------------------------------------------------------------------
# Handlers dos botoes
# ---------------------------------------------------------------------------

async def handle_quem_retirou(
    db,
    wa_id: str,
    morador: Dict,
    button_id: Optional[str] = None,
) -> str:
    """
    v2.7.0 — Responde ao botao "Quem Retirou?" do template chegada_encomenda_v3.

    Busca a encomenda pelo ID do payload (QUEM_RETIROU_{id}).

    Se encomenda entregue:
      Informa nr da encomenda, data/hora chegada, data/hora retirada e quem retirou.

    Se encomenda ainda pendente:
      Informa que a retirada ainda nao foi registrada.

    Se nao encontrar a encomenda:
      Mensagem generica orientando contato com a portaria.
    """
    enc_id = _extrair_id_do_payload(button_id, "QUEM_RETIROU_")

    logger.info(
        "handle_quem_retirou | wa_id=%s | button_id=%s | enc_id=%s",
        wa_id, button_id, enc_id,
    )

    if not enc_id:
        # Payload sem ID — nao temos como identificar a encomenda especifica
        send_whatsapp_text(
            wa_id,
            "Para verificar quem retirou uma encomenda especifica, "
            "entre em contato com a portaria informando o numero da encomenda.\n\n"
            "e-Condominio"
        )
        return "quem_retirou_sem_id"

    enc = get_encomenda_completa(db, enc_id)

    if not enc:
        logger.warning("handle_quem_retirou: enc_id=%s nao encontrado | wa_id=%s", enc_id, wa_id)
        send_whatsapp_text(
            wa_id,
            "Nao foi possivel localizar esta encomenda. "
            "Entre em contato com a portaria.\n\n"
            "e-Condominio"
        )
        return "quem_retirou_enc_nao_encontrada"

    codigo    = enc.get("codigo_rastreio") or f"ID {enc_id}"
    apto      = enc.get("apartamento") or ""
    bloco     = enc.get("bloco") or ""
    apto_str  = f"{apto}/{bloco}" if bloco else apto
    status    = enc.get("status", "")

    # Formatar data/hora de chegada
    dt_chegada = enc.get("data_recebimento")
    if dt_chegada:
        dt_chegada_str = dt_chegada.strftime("%d/%m/%Y as %H:%M")
    else:
        dt_chegada_str = "nao registrada"

    if status == "entregue":
        # Encomenda ja foi retirada — informa todos os dados
        dt_entrega = enc.get("data_entrega")
        if dt_entrega:
            dt_entrega_str = dt_entrega.strftime("%d/%m/%Y as %H:%M")
        else:
            dt_entrega_str = "nao registrada"

        nome_retirou = enc.get("nome_retirou") or "nao informado"

        msg = (
            f"Informacoes da encomenda:\n\n"
            f"Numero: {codigo}\n"
            f"Unidade: {apto_str}\n"
            f"Chegada: {dt_chegada_str}\n\n"
            f"Retirada: {dt_entrega_str}\n"
            f"Retirado por: {nome_retirou}\n\n"
            f"e-Condominio"
        )
        send_whatsapp_text(wa_id, msg)
        logger.info(
            "quem_retirou: encomenda entregue | enc_id=%s | retirado_por=%s | wa_id=%s",
            enc_id, nome_retirou, wa_id,
        )
        return "quem_retirou_entregue"

    else:
        # Encomenda ainda pendente — ainda nao foi retirada
        msg = (
            f"A encomenda ainda nao foi retirada.\n\n"
            f"Numero: {codigo}\n"
            f"Unidade: {apto_str}\n"
            f"Chegada: {dt_chegada_str}\n\n"
            f"O item aguarda retirada na portaria.\n\n"
            f"e-Condominio"
        )
        send_whatsapp_text(wa_id, msg)
        logger.info(
            "quem_retirou: encomenda ainda pendente | enc_id=%s | wa_id=%s",
            enc_id, wa_id,
        )
        return "quem_retirou_pendente"


async def handle_ver_encomenda(
    db,
    wa_id: str,
    morador: Dict,
    encomendas: List[Dict],
    button_id: Optional[str] = None,
) -> str:
    morador_id        = morador.get("id")
    nome              = morador.get("nome", "Morador").split()[0]
    enc_id_especifico = _extrair_id_do_payload(button_id, "VER_ENCOMENDA_")
    if enc_id_especifico:
        # v2.8.0 — usa get_encomenda_completa para ter data_entrega e nome_retirou
        # disponiveis caso a encomenda ja tenha sido retirada
        enc = get_encomenda_completa(db, enc_id_especifico)
        if not enc:
            logger.warning(
                "VER_ENCOMENDA: enc_id=%s nao encontrado no banco | wa_id=%s",
                enc_id_especifico, wa_id,
            )
            send_whatsapp_text(
                wa_id,
                "Nao foi possivel localizar esta encomenda. "
                "Verifique com a portaria."
            )
            return "ver_encomenda_id_invalido"

        enc_morador_id = enc.get("morador_id")
        morador_da_enc = find_morador_by_phone_id(db, enc_morador_id) if enc_morador_id else None

        acesso_liberado = False
        if morador_da_enc:
            tel_morador   = normalize_digits(morador_da_enc.get("telefone", ""))
            candidates_wa = generate_phone_candidates(wa_id)
            if tel_morador and tel_morador in candidates_wa:
                acesso_liberado = True
                morador    = morador_da_enc
                morador_id = morador_da_enc["id"]
                logger.info(
                    "VER_ENCOMENDA OK | enc_id=%s | morador_id=%s | wa_id=%s",
                    enc_id_especifico, morador_id, wa_id,
                )
            else:
                logger.warning(
                    "VER_ENCOMENDA BLOQUEADO | enc_id=%s | enc_morador_id=%s | "
                    "tel_morador=%s | wa_id=%s | candidates=%s",
                    enc_id_especifico, enc_morador_id,
                    tel_morador, wa_id, candidates_wa,
                )
        else:
            acesso_liberado = True
            logger.warning(
                "VER_ENCOMENDA: morador_id=%s nao encontrado, liberando sem validacao | "
                "enc_id=%s | wa_id=%s",
                enc_morador_id, enc_id_especifico, wa_id,
            )

        if not acesso_liberado:
            send_whatsapp_text(
                wa_id,
                "Nao foi possivel localizar esta encomenda para seu numero. "
                "Verifique com a portaria."
            )
            return "ver_encomenda_bloqueado_telefone_divergente"

        nome_dest = enc.get("nome_destinatario", "Morador").split()[0]

        logger.info(
            "Ver encomenda por ID direto | enc_id=%s | destinatario=%s | wa_id=%s",
            enc_id_especifico, enc.get("nome_destinatario"), wa_id,
        )

        codigo   = enc.get("codigo_rastreio") or "S/N"
        apto     = enc.get("apartamento") or ""
        bloco    = enc.get("bloco") or ""
        apto_str = f"{apto}/{bloco}" if bloco else apto
        dt       = enc.get("data_recebimento")
        dt_str   = dt.strftime("%d/%m/%Y as %H:%M") if dt else ""

        # v2.8.0 — diferencia mensagem conforme status: pendente x entregue.
        # Em ambos os casos a imagem da etiqueta continua sendo enviada normalmente.
        if enc.get("status") == "entregue":
            dt_entrega     = enc.get("data_entrega")
            dt_entrega_str = dt_entrega.strftime("%d/%m/%Y as %H:%M") if dt_entrega else "nao registrada"
            nome_retirou   = enc.get("nome_retirou") or "nao informado"
            texto = (
                f"Ola {nome_dest}! Esta encomenda ja foi retirada:\n\n"
                f"Codigo: {codigo}\n"
                f"Unidade: {apto_str}\n"
                f"Chegada: {dt_str}\n\n"
                f"Retirada: {dt_entrega_str}\n"
                f"Retirado por: {nome_retirou}"
            )
            action = "ver_encomenda_texto_entregue"
        else:
            texto = (
                f"Ola {nome_dest}! Sua encomenda na portaria:\n\n"
                f"Codigo: {codigo}\n"
                f"Unidade: {apto_str}\n"
                f"Recebida em: {dt_str}\n\n"
                f"Para confirmar a retirada, responda 'Confirmar Retirada'."
            )
            action = "ver_encomenda_texto"

        img_filename = enc.get("img_etiqueta_server")
        if img_filename:
            image_b64 = await fetch_image_base64_from_storage(img_filename)
            if image_b64:
                enviado = await asyncio.to_thread(
                    send_whatsapp_image_base64, wa_id, image_b64,
                    f"Etiqueta da encomenda {codigo}"
                )
                if enviado:
                    action = action.replace("texto", "imagem_enviada") if "imagem" not in action else action
                    logger.info(
                        "Imagem enviada | enc_id=%s | filename=%s | wa_id=%s",
                        enc_id_especifico, img_filename, wa_id,
                    )
            else:
                logger.warning(
                    "Imagem nao encontrada no storage | filename=%s | enc_id=%s",
                    img_filename, enc_id_especifico,
                )

        send_whatsapp_text(wa_id, texto)
        return action

    if not encomendas:


        send_whatsapp_text(
            wa_id,
            "Nao encontramos encomendas pendentes para seu apartamento no momento."
        )
        return "ver_encomenda_sem_pendentes"

    logger.info(
        "Ver encomenda sem ID especifico | morador_id=%s | pendentes=%s",
        morador_id, len(encomendas),
    )

    imagens_enviadas = 0
    for enc in encomendas:
        img_filename = enc.get("img_etiqueta_server")
        if not img_filename:
            continue
        image_b64 = await fetch_image_base64_from_storage(img_filename)
        if image_b64:
            codigo  = enc.get("codigo_rastreio") or "S/N"
            enviado = await asyncio.to_thread(
                send_whatsapp_image_base64, wa_id, image_b64,
                f"Etiqueta da encomenda {codigo}"
            )
            if enviado:
                imagens_enviadas += 1
                logger.info(
                    "Imagem enviada (sem ID) | enc_id=%s | morador_id=%s | filename=%s",
                    enc.get("id"), morador_id, img_filename,
                )

    if len(encomendas) == 1:
        enc      = encomendas[0]
        codigo   = enc.get("codigo_rastreio") or "S/N"
        apto     = enc.get("apartamento") or ""
        bloco    = enc.get("bloco") or ""
        apto_str = f"{apto}/{bloco}" if bloco else apto
        dt       = enc.get("data_recebimento")
        dt_str   = dt.strftime("%d/%m/%Y as %H:%M") if dt else ""
        texto = (
            f"Ola {nome}! Sua encomenda na portaria:\n\n"
            f"Codigo: {codigo}\n"
            f"Unidade: {apto_str}\n"
            f"Recebida em: {dt_str}\n\n"
            f"Para confirmar a retirada, responda 'Confirmar Retirada'."
        )
    else:
        texto = f"Ola {nome}! Suas {len(encomendas)} encomendas pendentes:\n\n"
        for i, enc in enumerate(encomendas, 1):
            codigo = enc.get("codigo_rastreio") or "S/N"
            dt     = enc.get("data_recebimento")
            dt_str = dt.strftime("%d/%m") if dt else ""
            texto += f"{i}. Cod: {codigo} | Recebida: {dt_str}\n"
        texto += "\nPara confirmar a retirada de todas, responda 'Confirmar Retirada'."

    send_whatsapp_text(wa_id, texto)
    return f"ver_encomenda_imagens_{imagens_enviadas}"


async def handle_confirmar_retirada(
    db,
    wa_id: str,
    morador: Dict,
    encomendas: List[Dict],
    button_id: Optional[str] = None,
) -> str:
    morador_id        = morador.get("id")
    enc_id_especifico = _extrair_id_do_payload(button_id, "CONFIRMAR_RETIRADA_")

    if enc_id_especifico:
        enc_especifica = get_encomenda_por_id(db, enc_id_especifico)

        if enc_especifica:
            enc_morador_id = enc_especifica.get("morador_id")
            morador_da_enc = find_morador_by_phone_id(db, enc_morador_id) if enc_morador_id else None

            acesso_liberado = False
            if morador_da_enc:
                tel_morador   = normalize_digits(morador_da_enc.get("telefone", ""))
                candidates_wa = generate_phone_candidates(wa_id)
                if tel_morador and tel_morador in candidates_wa:
                    acesso_liberado = True
                    morador    = morador_da_enc
                    morador_id = morador_da_enc["id"]
                    logger.info(
                        "CONFIRMAR_RETIRADA OK | enc_id=%s | morador_id=%s | wa_id=%s",
                        enc_id_especifico, morador_id, wa_id,
                    )
                else:
                    logger.warning(
                        "CONFIRMAR_RETIRADA BLOQUEADO | enc_id=%s | enc_morador_id=%s | "
                        "tel_morador=%s | wa_id=%s | candidates=%s",
                        enc_id_especifico, enc_morador_id,
                        tel_morador, wa_id, candidates_wa,
                    )
            else:
                acesso_liberado = True
                logger.warning(
                    "CONFIRMAR_RETIRADA: morador_id=%s nao encontrado, liberando | "
                    "enc_id=%s | wa_id=%s",
                    enc_morador_id, enc_id_especifico, wa_id,
                )

            if not acesso_liberado:
                send_whatsapp_text(
                    wa_id,
                    "Nao foi possivel localizar esta encomenda para seu numero. "
                    "Verifique com a portaria."
                )
                return "confirmar_retirada_bloqueado_telefone_divergente"

            if enc_especifica.get("status") != "pendente":
                send_whatsapp_text(
                    wa_id,
                    f"Esta encomenda ja foi retirada anteriormente. "
                    f"Obrigado, {morador.get('nome', 'Morador').split()[0]}!"
                )
                return "retirada_ja_confirmada"

            enc        = enc_especifica
            nome_dest  = enc.get("nome_destinatario") or morador.get("nome", "Morador")
            nome_curto = nome_dest.split()[0]
            codigo     = enc.get("codigo_rastreio") or "S/N"
            apto       = enc.get("apartamento") or ""
            bloco      = enc.get("bloco") or ""
            apto_str   = f"{apto}/{bloco}" if bloco else apto
            dt         = enc.get("data_recebimento")
            dt_str     = dt.strftime("%d/%m/%Y as %H:%M") if dt else ""

            corpo = (
                f"Ola {nome_curto}! Esta encomenda esta registrada como aguardando retirada na portaria:\n\n"
                f"Codigo: {codigo}\n"
                f"Unidade: {apto_str}\n"
                f"Recebida em: {dt_str}\n\n"
                f"Voce ja retirou esta encomenda?"
            )

            img_filename = enc.get("img_etiqueta_server")
            acao = "confirmar_retirada_aguardando_2etapa"
            if img_filename:
                image_b64 = await fetch_image_base64_from_storage(img_filename)
                if image_b64:
                    enviado = await asyncio.to_thread(
                        send_whatsapp_image_base64, wa_id, image_b64,
                        f"Etiqueta da encomenda {codigo}"
                    )
                    if enviado:
                        acao = "confirmar_retirada_foto_enviada_aguardando_2etapa"
                        logger.info(
                            "Foto enviada na etapa 1 | enc_id=%s | wa_id=%s",
                            enc_id_especifico, wa_id,
                        )

            botoes = [
                {"id": f"RETIRADA_CONFIRMADA_{enc['id']}", "title": "Sim, ja retirei"},
                {"id": f"RETIRADA_NEGADA_{enc['id']}",    "title": "Nao retirei"},
            ]
            enviado_interativo = await asyncio.to_thread(
                send_whatsapp_interactive_buttons, wa_id, corpo, botoes
            )
            if not enviado_interativo:
                send_whatsapp_text(wa_id, corpo + "\n\nResponda Sim, ja retirei ou Nao retirei.")

            logger.info(
                "Etapa 1 CONFIRMAR_RETIRADA com ID | enc_id=%s | morador_id=%s | wa_id=%s",
                enc_id_especifico, morador_id, wa_id,
            )
            return acao

        else:
            logger.warning(
                "CONFIRMAR_RETIRADA: enc_id=%s nao encontrado no banco | wa_id=%s",
                enc_id_especifico, wa_id,
            )

    if not encomendas:
        send_whatsapp_text(
            wa_id,
            "Nao encontramos encomendas pendentes para confirmar no momento."
        )
        return "confirmar_retirada_sem_pendentes"

    enc        = encomendas[0]
    enc_id     = enc["id"]
    nome_dest  = enc.get("nome_destinatario") or morador.get("nome", "Morador")
    nome_curto = nome_dest.split()[0]
    codigo     = enc.get("codigo_rastreio") or "S/N"
    apto       = enc.get("apartamento") or ""
    bloco      = enc.get("bloco") or ""
    apto_str   = f"{apto}/{bloco}" if bloco else apto
    dt         = enc.get("data_recebimento")
    dt_str     = dt.strftime("%d/%m/%Y as %H:%M") if dt else ""

    corpo = (
        f"Ola {nome_curto}! Esta encomenda esta registrada como aguardando retirada na portaria:\n\n"
        f"Codigo: {codigo}\n"
        f"Unidade: {apto_str}\n"
        f"Recebida em: {dt_str}\n\n"
        f"Voce ja retirou esta encomenda?"
    )

    img_filename = enc.get("img_etiqueta_server")
    acao = "confirmar_retirada_aguardando_2etapa"
    if img_filename:
        image_b64 = await fetch_image_base64_from_storage(img_filename)
        if image_b64:
            enviado = await asyncio.to_thread(
                send_whatsapp_image_base64, wa_id, image_b64,
                f"Etiqueta da encomenda {codigo}"
            )
            if enviado:
                acao = "confirmar_retirada_foto_enviada_aguardando_2etapa"

    botoes = [
        {"id": f"RETIRADA_CONFIRMADA_{enc['id']}", "title": "Sim, ja retirei"},
        {"id": f"RETIRADA_NEGADA_{enc['id']}",    "title": "Nao retirei"},
    ]
    enviado_interativo = await asyncio.to_thread(
        send_whatsapp_interactive_buttons, wa_id, corpo, botoes
    )
    if not enviado_interativo:
        send_whatsapp_text(wa_id, corpo + "\n\nResponda Sim, ja retirei ou Nao retirei.")

    logger.info(
        "Etapa 1 CONFIRMAR_RETIRADA sem ID | enc_id=%s | morador_id=%s | wa_id=%s",
        enc_id, morador_id, wa_id,
    )
    return acao


async def handle_retirada_confirmada(
    db,
    wa_id: str,
    morador: Dict,
    encomendas: List[Dict],
    button_id: Optional[str] = None,
) -> str:
    morador_id = morador.get("id")
    nome       = morador.get("nome", "Morador")
    nome_curto = nome.split()[0]

    # NAO grava mais no banco a partir desta confirmacao via WhatsApp (decisao
    # de 2026-09-22) — o morador so avisa que ja retirou, quem registra de
    # fato a retirada continua sendo a portaria (ModalRegistrarEntrega no
    # admin). Ver docs/01_BUGS_E_DECISOES.md pra detalhes da mudanca.
    enc_id_especifico = _extrair_id_do_payload(button_id, "RETIRADA_CONFIRMADA_")
    if enc_id_especifico:
        enc = get_encomenda_por_id(db, enc_id_especifico)
        if enc and enc.get("status") == "pendente":
            enc_nome = enc.get("nome_destinatario") or nome
            enc_nome_curto = enc_nome.split()[0]
            codigo = enc.get("codigo_rastreio") or f"ID {enc_id_especifico}"
            send_whatsapp_text(
                wa_id,
                f"Obrigado pela informacao, {enc_nome_curto}!\n\n"
                f"Encomenda {codigo}: por favor, informe a portaria pessoalmente "
                f"para confirmar a retirada."
            )
            logger.info(
                "Retirada informada pelo morador (sem gravar no banco) | "
                "enc_id=%s | wa_id=%s",
                enc_id_especifico, wa_id,
            )
            return "retirada_2etapa_informado_portaria_por_id"
        elif enc and enc.get("status") != "pendente":
            send_whatsapp_text(
                wa_id,
                f"Esta encomenda ja foi registrada como retirada. Obrigado!"
            )
            return "retirada_2etapa_ja_confirmada"

    if not encomendas:
        send_whatsapp_text(
            wa_id,
            "Nao encontramos encomendas pendentes para registrar a retirada."
        )
        return "retirada_2etapa_sem_pendentes"

    codigos = [enc.get("codigo_rastreio") or f"ID {enc['id']}" for enc in encomendas]

    if len(codigos) == 1:
        msg = (
            f"Obrigado pela informacao, {nome_curto}!\n\n"
            f"Encomenda {codigos[0]}: por favor, informe a portaria pessoalmente "
            f"para confirmar a retirada."
        )
    else:
        lista = "\n".join(f"- {c}" for c in codigos)
        msg = (
            f"Obrigado pela informacao, {nome_curto}!\n\n"
            f"Por favor, informe a portaria pessoalmente para confirmar a retirada "
            f"das encomendas:\n{lista}"
        )
    send_whatsapp_text(wa_id, msg)
    logger.info(
        "Retirada informada pelo morador (sem gravar no banco) | qtd=%s | morador_id=%s",
        len(codigos), morador_id,
    )
    return f"retirada_2etapa_informado_portaria_{len(codigos)}"


async def handle_confirmar_morador(
    db,
    wa_id: str,
    morador: Dict,
    button_id: Optional[str],
    encomenda_id_payload: Optional[int] = None,
) -> str:
    morador_id = morador.get("id")
    nome_curto = morador.get("nome", "Morador").split()[0]

    confirmado = update_whats_confirmado(db, morador_id)
    if not confirmado:
        send_whatsapp_text(
            wa_id,
            "Tivemos um problema ao confirmar seu cadastro. Tente novamente ou contate a portaria."
        )
        return "confirmar_morador_falha_db"

    reenviar_encomenda_pendente_apos_confirmacao(db, morador_id, encomenda_id_payload)

    send_whatsapp_text(
        wa_id,
        f"Perfeito, {nome_curto}! Seu WhatsApp foi confirmado com sucesso.\n\n"
        f"A partir de agora voce recebera as notificacoes de encomendas diretamente aqui.\n\n"
        f"Voce tem uma encomenda aguardando retirada na portaria. "
        f"Em instantes enviaremos os detalhes.\n\n"
        f"e-Condominio"
    )
    logger.info(
        "Morador confirmado via WhatsApp | morador_id=%s | wa_id=%s",
        morador_id, wa_id,
    )
    return "confirmar_morador_ok"


async def handle_negar_morador(
    db,
    wa_id: str,
    morador: Dict,
) -> str:
    morador_id    = morador.get("id")
    nome_morador  = morador.get("nome", "Morador")
    condominio_id = morador.get("condominio_id")

    clear_morador_phone(db, morador_id)
    db.commit()
    logger.info(
        "Telefone removido — morador negou identidade | morador_id=%s | wa_id=%s",
        morador_id, wa_id,
    )

    if condominio_id:
        condominio = get_condominio(db, condominio_id)
        if condominio and condominio.get("telefone"):
            aviso = (
                f"ATENCAO: O numero {wa_id} informou que NAO e o morador "
                f"{nome_morador} (ID {morador_id}).\n"
                f"Por favor, atualize o telefone no cadastro do morador.\n\n"
                f"e-Condominio"
            )
            send_whatsapp_text(condominio["telefone"], aviso)

    send_whatsapp_text(
        wa_id,
        "Entendido! Removemos este numero do cadastro.\n\n"
        "A portaria sera notificada para corrigir o cadastro. "
        "Desculpe qualquer inconveniente.\n\n"
        "e-Condominio"
    )
    return "negar_morador_ok"

# ---------------------------------------------------------------------------
# Webhook endpoints
# ---------------------------------------------------------------------------

@router.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    params    = request.query_params
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(content=challenge, status_code=200)

    return JSONResponse({"error": "verification failed"}, status_code=403)


@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    data = await request.json()
    logger.info("=== WEBHOOK WHATSAPP RECEBIDO ===")
    logger.debug(json.dumps(data, ensure_ascii=False)[:500])

    db = SessionLocal()

    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                field = change.get("field")
                value = change.get("value", {})

                if field != "messages":
                    logger.info("Webhook ignorado | field=%s", field)
                    continue

                contacts = value.get("contacts", [])
                messages = value.get("messages", [])
                statuses = value.get("statuses", [])

                if statuses:
                    logger.info("Statuses recebidos: %s", statuses)
                    for st in statuses:
                        try:
                            pricing = st.get("pricing", {}) or {}
                            db.execute(text("""
                                INSERT IGNORE INTO AdmGeral.whatsapp_status_entregas
                                    (message_id, recipient_id, status, billable, category, pricing_model, tipo)
                                VALUES
                                    (:mid, :rid, :status, :billable, :category, :pricing_model, :tipo)
                            """), {
                                "mid":           st.get("id", ""),
                                "rid":           st.get("recipient_id", ""),
                                "status":        st.get("status", ""),
                                "billable":      1 if pricing.get("billable") else 0,
                                "category":      pricing.get("category", ""),
                                "pricing_model": pricing.get("pricing_model", ""),
                                "tipo":          pricing.get("type", ""),
                            })
                        except Exception as e_st:
                            logger.debug("Erro ao salvar status billing: %s", e_st)

                if not messages:
                    continue

                profile_name = None
                if contacts:
                    profile_name = contacts[0].get("profile", {}).get("name")

                processed_ids = set()
                for msg in messages:
                    wa_id      = msg.get("from")
                    message_id = msg.get("id")

                    if message_id and message_id in processed_ids:
                        logger.info("Webhook duplicado ignorado | message_id=%s", message_id)
                        continue
                    if message_id:
                        processed_ids.add(message_id)
                        try:
                            existe = db.execute(text(
                                "SELECT id FROM AdmGeral.whatsapp_inbound_messages WHERE message_id = :mid LIMIT 1"
                            ), {"mid": message_id}).first()
                            if existe:
                                logger.info("Webhook ja processado (banco) | message_id=%s", message_id)
                                continue
                        except Exception:
                            pass

                    message_type = msg.get("type")

                    message_text = ""
                    button_id    = None

                    if message_type == "text":
                        message_text = (msg.get("text", {}) or {}).get("body", "")

                    elif message_type == "button":
                        btn          = msg.get("button", {}) or {}
                        message_text = btn.get("text", "")
                        raw_payload  = btn.get("payload", "")
                        button_id    = raw_payload if raw_payload else None
                        logger.info(
                            "Botao | wa_id=%s | text=%s | payload=%s | button_id=%s",
                            wa_id, message_text, raw_payload, button_id,
                        )

                    elif message_type == "interactive":
                        interactive  = msg.get("interactive", {}) or {}
                        btn_reply    = interactive.get("button_reply", {}) or {}
                        message_text = btn_reply.get("title", "")
                        button_id    = btn_reply.get("id", message_text)

                    message_text_norm = (message_text or "").strip().lower()

                    logger.info(
                        "Mensagem | wa_id=%s | nome=%s | tipo=%s | texto=%s | button_id=%s",
                        wa_id, profile_name, message_type, message_text, button_id,
                    )

                    if handle_auto_cadastro(db, wa_id, message_text, message_text_norm, button_id):
                        action_taken  = "auto_cadastro_processado"
                        condominio_id = None
                        morador_id    = None
                        matched_phone = None
                    else:
                        morador       = find_morador_by_phone(db, wa_id)
                        action_taken  = "only_logged"
                        condominio_id = None
                        morador_id    = None
                        matched_phone = None

                        if morador:
                            morador_id    = morador.get("id")
                            condominio_id = morador.get("condominio_id")
                            matched_phone = morador.get("telefone")

                            logger.info(
                                "Morador identificado | id=%s | nome=%s | cond=%s | whats_confirmado=%s",
                                morador_id, morador.get("nome"), condominio_id,
                                morador.get("whats_confirmado"),
                            )

                            encomendas_pendentes = get_encomendas_pendentes(db, morador_id)

                            # ------------------------------------------------------------------
                            # ORDEM DE DISPATCH — importante: QUEM_RETIROU antes de VER_ENCOMENDA
                            # para evitar falso positivo (ambos tem prefixo com encomenda_id)
                            # ------------------------------------------------------------------

                            if _is_confirmar_morador(button_id, message_text_norm):
                                enc_id_ref = None
                                if encomendas_pendentes:
                                    enc_id_ref = encomendas_pendentes[0].get("id")
                                action_taken = await handle_confirmar_morador(
                                    db, wa_id, morador, button_id, enc_id_ref
                                )

                            elif _is_negar_morador(button_id, message_text_norm):
                                action_taken = await handle_negar_morador(db, wa_id, morador)

                            elif _is_quem_retirou(button_id, message_text_norm):
                                # v2.7.0 — handler do botao "Quem Retirou?"
                                action_taken = await handle_quem_retirou(
                                    db, wa_id, morador, button_id
                                )

                            elif _is_ver_encomenda(button_id, message_text_norm):
                                action_taken = await handle_ver_encomenda(
                                    db, wa_id, morador, encomendas_pendentes, button_id
                                )

                            elif _is_retirada_confirmada(button_id, message_text_norm):
                                action_taken = await handle_retirada_confirmada(
                                    db, wa_id, morador, encomendas_pendentes, button_id
                                )

                            elif _is_retirada_negada(button_id, message_text_norm):
                                send_whatsapp_text(
                                    wa_id,
                                    f"Tudo bem, {morador.get('nome','Morador').split()[0]}! "
                                    f"Sua encomenda continua aguardando na portaria.\n\n"
                                    f"Quando retirar, clique em 'Confirmar Retirada' na mensagem anterior.\n\n"
                                    f"e-Condominio"
                                )
                                action_taken = "retirada_negada_pelo_morador"

                            elif _is_confirmar_retirada(button_id, message_text_norm):
                                action_taken = await handle_confirmar_retirada(
                                    db, wa_id, morador, encomendas_pendentes, button_id
                                )

                            elif message_text_norm == "numero errado":
                                clear_morador_phone(db, morador_id)
                                db.commit()
                                action_taken = "morador_phone_cleared"
                                condominio   = get_condominio(db, condominio_id) if condominio_id else None
                                if condominio and condominio.get("telefone"):
                                    aviso = (
                                        f"Atencao: o morador {morador.get('nome')} informou que o numero "
                                        f"de WhatsApp cadastrado esta errado. Favor atualizar o cadastro."
                                    )
                                    sent = send_whatsapp_text(condominio["telefone"], aviso)
                                    action_taken = (
                                        "morador_phone_cleared_and_condominio_notified"
                                        if sent else "morador_phone_cleared_condominio_notify_failed"
                                    )

                            elif _is_prospeccao_interesse(button_id, message_text_norm) or _is_prospeccao_sem_interesse(button_id, message_text_norm):
                                handled = _handle_prospeccao_resposta(db, wa_id, button_id, message_text_norm)
                                action_taken = "prospeccao_tratada" if handled else "prospeccao_lead_nao_encontrado"

                            else:
                                nome_morador = morador.get("nome", "").split()[0] if morador else ""
                                cond_id = morador.get("condominio_id") if morador else None
                                cond_nome = ""
                                if cond_id:
                                    try:
                                        cond = get_condominio(db, cond_id)
                                        cond_nome = cond.get("nome", "") if cond else ""
                                    except Exception:
                                        pass
                                if nome_morador and cond_nome:
                                    resposta = (
                                        f"Prezado(a) {nome_morador}, sobre sua encomenda, "
                                        f"entre em contato com a portaria do {cond_nome}. "
                                        f"Este WhatsApp e somente para notificacoes automaticas. "
                                        f"Grato. Econdominio.com.br"
                                    )
                                elif nome_morador:
                                    resposta = (
                                        f"Prezado(a) {nome_morador}, "
                                        f"este WhatsApp e somente para notificacoes automaticas de encomendas. "
                                        f"Para informacoes, entre em contato com a portaria do seu condominio. "
                                        f"Grato. Econdominio.com.br"
                                    )
                                else:
                                    resposta = (
                                        "Entre em contato pelo numero de WhatsApp do seu condominio. "
                                        "Este WhatsApp e somente para notificacao de recebimento e entrega de encomendas. "
                                        "Grato. Econdominio.com.br"
                                    )
                                sent = send_whatsapp_text(wa_id, resposta)
                                action_taken = "auto_reply_sent" if sent else "auto_reply_failed"

                        else:
                            logger.info("Nenhum morador encontrado para wa_id=%s", wa_id)
                            if _is_prospeccao_interesse(button_id, message_text_norm) or _is_prospeccao_sem_interesse(button_id, message_text_norm):
                                handled = _handle_prospeccao_resposta(db, wa_id, button_id, message_text_norm)
                                action_taken = "prospeccao_tratada" if handled else "prospeccao_lead_nao_encontrado"

                    save_inbound_message(
                        db=db,
                        wa_id=wa_id,
                        profile_name=profile_name,
                        condominio_id=condominio_id,
                        morador_id=morador_id,
                        matched_phone=matched_phone,
                        action_taken=action_taken,
                        message_id=message_id,
                        message_type=message_type,
                        message_text=message_text,
                        raw_payload=data,
                    )

                db.commit()

        return {"status": "ok"}

    except Exception as exc:
        db.rollback()
        logger.exception("Erro ao processar webhook WhatsApp: %s", exc)
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)

    finally:
        db.close()
