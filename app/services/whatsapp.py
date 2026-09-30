# =============================================================================
# Arquivo  : whatsapp.py
# Projeto  : AdmGeral / e-Condominio - Modulo Mobile (Portaria)
# Caminho  : /home/visionlpr/backend/app/services/whatsapp.py
# Versao   : 6.5.0
# Data     : 2026-03-24
# Finalidade: Servico de enfileiramento de mensagens WhatsApp no MySQL.
#             NAO realiza envio imediato. Apenas registra mensagens pendentes
#             na tabela `whatsapp_message_queue` para processamento posterior
#             pelo worker `send_grouped_whatsapp.py`.
#
# Historico:
#   v5.x   - Envio via Z-API com fila em arquivo JSON (deprecated)
#   v6.0.0 - Migracao para fila MySQL + Meta Cloud API
#   v6.1.0 - Compativel com .env existente
#   v6.2.0 - Correcao de processar_apos para calculo no MySQL com NOW()
#   v6.2.1 - BLOQUEIO TEMPORARIO de imagens via BLOCK_IMAGE_MESSAGES no .env
#   v6.3.0 - Remove imagem base64 do payload antes de salvar no MySQL
#            Remove emojis das mensagens de texto
#   v6.4.0 - Bifurcacao por whats_confirmado em send_package_notification:
#   v6.5.0 - FIX: whats_confirmado e DATETIME no banco, nao VARCHAR.
#            Comparacao corrigida para bool(valor) em vez de == '1'.
#              * whats_confirmado NOT NULL (datetime) → ENCOMENDA_RECEBIDA (Meta, template padrao)
#              * whats_confirmado NULL → CADASTRO_MORADOR (Meta,
#                template cadastro_e_condominio_morador, botoes Sim/Nao sou eu)
#            Nova funcao publica send_cadastro_morador_notification()
#            Novo evento EVENTO_CADASTRO_MORADOR
#
# Arquitetura:
#   app/services/whatsapp.py              -> enfileira no MySQL (este arquivo)
#   /home/visionlpr/backend/send_grouped_whatsapp.py -> agrupa e processa a fila
#   app/services/meta_whatsapp_service.py -> envio futuro via Meta/Z-API
# =============================================================================

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

import pytz
import pymysql
import pymysql.cursors

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fuso horario
# ---------------------------------------------------------------------------
TZ_BR = pytz.timezone("America/Sao_Paulo")

# ---------------------------------------------------------------------------
# Delay padrao de enfileiramento (minutos)
# Sobrescrever via .env: WHATSAPP_QUEUE_DELAY_MINUTES=30
# ---------------------------------------------------------------------------
QUEUE_DELAY_MINUTES: int = int(os.environ.get("WHATSAPP_QUEUE_DELAY_MINUTES", "30"))

# ---------------------------------------------------------------------------
# Constantes de tipo de evento
# ---------------------------------------------------------------------------
EVENTO_ENCOMENDA_RECEBIDA   = "ENCOMENDA_RECEBIDA"
EVENTO_ENCOMENDA_ENTREGUE   = "ENCOMENDA_ENTREGUE"
EVENTO_MENSAGEM_GENERICA    = "MENSAGEM_GENERICA"
EVENTO_CONFIRMACAO_WHATSAPP = "CONFIRMACAO_WHATSAPP"
EVENTO_CADASTRO_MORADOR     = "CADASTRO_MORADOR"   # v6.4.0 — template cadastro_e_condominio_morador
EVENTO_LEMBRETE_ENCOMENDA_24H = "LEMBRETE_ENCOMENDA_24H"
EVENTO_LEMBRETE_ENCOMENDA_48H = "LEMBRETE_ENCOMENDA_48H"
EVENTO_LEMBRETE_ENCOMENDA_72H = "LEMBRETE_ENCOMENDA_72H"

# ---------------------------------------------------------------------------
# BLOQUEIO TEMPORARIO DE IMAGENS
# Enquanto True, send_image_message e send_notification ignoram qualquer
# imagem recebida e NAO enfileiram nada relacionado a ela.
# Para reativar: defina BLOCK_IMAGE_MESSAGES=false no .env
# ---------------------------------------------------------------------------
BLOCK_IMAGE_MESSAGES: bool = os.environ.get("BLOCK_IMAGE_MESSAGES", "true").lower() in ("1", "true")

# ---------------------------------------------------------------------------
# Campos de payload que contem imagem base64 — serao removidos antes de salvar
# ---------------------------------------------------------------------------
_PAYLOAD_IMAGE_KEYS = {
    "imagem_etiqueta", "imagem", "image", "foto",
    "base64", "thumbnail", "photo", "picture",
}


# ---------------------------------------------------------------------------
# Leitura de configuracao — pydantic settings ou os.environ
# ---------------------------------------------------------------------------

def _cfg(key: str, default: str = "") -> str:
    """
    Le variavel de ambiente.
    Tenta settings pydantic primeiro (app.config.settings),
    depois cai para os.environ — compativel com o .env existente.
    """
    try:
        from app.config import settings  # type: ignore
        val = getattr(settings, key, None)
        if val is not None and str(val).strip():
            return str(val)
    except Exception:
        pass
    return os.environ.get(key, default)


# ---------------------------------------------------------------------------
# Conexao MySQL — usa DB_HOST / DB_PORT / DB_USER / DB_PASSWORD do .env atual
# ---------------------------------------------------------------------------

def _get_db() -> pymysql.connections.Connection:
    """
    Abre conexao PyMySQL com as mesmas credenciais DB_* ja definidas no .env.
    Nao envia nada; apenas grava na fila.
    """
    return pymysql.connect(
        host=_cfg("DB_HOST", "localhost"),
        port=int(_cfg("DB_PORT", "3306")),
        user=_cfg("DB_USER", "root"),
        password=_cfg("DB_PASSWORD", ""),
        database=_cfg("DB_NAME", _cfg("DATABASE_NAME", "AdmGeral")),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
    )


# ---------------------------------------------------------------------------
# Normalizacao de telefone brasileiro -> E.164 sem '+'
# ---------------------------------------------------------------------------
def normalize_phone_br(phone: str) -> str:
    """
    Normaliza telefone para formato internacional sem '+'.
    Regra: o DDI internacional só é reconhecido quando o número vem com
    '+' explícito. Número BR sem '+' (10 ou 11 dígitos) SEMPRE recebe 55.
    Adivinhar DDI pelo prefixo quebra DDDs BR que colidem com DDIs
    (DDD 11=DDI 1 EUA, DDD 51=DDI 51 Peru, DDD 54=Argentina, etc).
    IMPORTANTE: morador estrangeiro precisa estar gravado COM '+'.
        (48) 99999-8888  -> 5548999998888   (BR sem DDI)
        51984249403      -> 5551984249403   (BR DDD 51 — corrigido)
        5548999998888    -> 5548999998888   (BR já com DDI)
        +56994300056     -> 56994300056     (Chile, com '+', não prefixa 55)
    """
    if not phone:
        return ""
    phone = phone.strip()
    tem_ddi_explicito = phone.startswith("+")
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    # Com '+': DDI já informado — confiar, não prefixar 55
    if tem_ddi_explicito:
        return digits
    # BR já com DDI 55 (12 díg = fixo, 13 díg = celular)
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits
    # Sem '+' e 10/11 dígitos => número BR sem DDI => prefixa 55
    if len(digits) in (10, 11):
        return "55" + digits
    # Outro tamanho: provável internacional digitado sem '+'; mantém como veio
    logger.warning("Telefone com formato inesperado: %r -> %r", phone, digits)
    return digits
# ---------------------------------------------------------------------------
# Remove imagem base64 do payload antes de salvar
# Evita payloads gigantes no MySQL que podem causar lentidao e erros
# ---------------------------------------------------------------------------

def _clean_payload(payload: Optional[dict]) -> Optional[dict]:
    """
    Remove campos de imagem base64 do payload.
    Criterios de remocao:
      - Chave esta em _PAYLOAD_IMAGE_KEYS
      - Valor e string que comeca com 'data:image'
      - Valor e string com mais de 10.000 caracteres (provavelmente base64)
    """
    if not payload:
        return None

    cleaned = {}
    for k, v in payload.items():
        key_lower = str(k).lower()

        # Remove por nome da chave
        if key_lower in _PAYLOAD_IMAGE_KEYS:
            logger.debug("_clean_payload: removendo campo '%s' (chave de imagem)", k)
            continue

        # Remove por conteudo (base64 data URI ou string gigante)
        if isinstance(v, str):
            if v.startswith("data:image"):
                logger.debug("_clean_payload: removendo campo '%s' (data:image)", k)
                continue
            if len(v) > 10000:
                logger.debug("_clean_payload: removendo campo '%s' (string muito grande: %d chars)", k, len(v))
                continue

        cleaned[k] = v

    return cleaned if cleaned else None


# ---------------------------------------------------------------------------
# Funcao central: enfileirar no MySQL
# ---------------------------------------------------------------------------

def queue_whatsapp_message(
    *,
    tipo_evento: str,
    telefone: str,
    nome_morador: Optional[str] = None,
    morador_id: Optional[int] = None,
    condominio_id: Optional[int] = None,
    encomenda_id: Optional[int] = None,
    mensagem_original: Optional[str] = None,
    payload: Optional[dict] = None,
    delay_minutes: Optional[int] = None,
) -> bool:
    """
    Insere registro na fila `whatsapp_message_queue` com status 'pending'.
    O envio real ocorre depois via send_grouped_whatsapp.py (cron).

    Regras:
    - Nao envia nada diretamente
    - processar_apos = NOW() + delay_minutes no proprio MySQL
    - payload e salvo em JSON sem imagens base64
    """
    telefone_ok = normalize_phone_br(telefone)
    if not telefone_ok:
        logger.error(
            "queue_whatsapp_message: telefone invalido | tipo=%s | raw=%r | morador_id=%s",
            tipo_evento, telefone, morador_id,
        )
        return False

    delay = delay_minutes if delay_minutes is not None else QUEUE_DELAY_MINUTES

    # Remove imagem base64 antes de serializar
    payload_clean = _clean_payload(payload)
    payload_json  = json.dumps(payload_clean, ensure_ascii=False, default=str) if payload_clean else None

    sql = """
        INSERT INTO whatsapp_message_queue
            (
                morador_id,
                condominio_id,
                encomenda_id,
                telefone,
                nome_morador,
                tipo_evento,
                mensagem_original,
                payload_json,
                status,
                tentativas,
                processar_apos
            )
        VALUES
            (
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                'pending', 0,
                DATE_ADD(NOW(), INTERVAL %s MINUTE)
            )
    """

    params = (
        morador_id,
        condominio_id,
        encomenda_id,
        telefone_ok,
        nome_morador,
        tipo_evento,
        mensagem_original,
        payload_json,
        delay,
    )

    conn = None
    try:
        conn = _get_db()
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()

        logger.info(
            "Enfileirado | tipo=%s | tel=%s | morador_id=%s | encomenda_id=%s | delay_min=%s",
            tipo_evento,
            telefone_ok,
            morador_id,
            encomenda_id,
            delay,
        )
        return True

    except pymysql.err.IntegrityError as exc:
        # v6.6.0 — Trava de deduplicacao (UNIQUE uq_wmq_encomenda_tipo).
        # Erro 1062 = Duplicate entry: outra requisicao (retry do app / outro worker)
        # ja enfileirou este (encomenda_id, tipo_evento). Isso NAO e falha —
        # a notificacao ja esta na fila. Ignora silenciosamente e segue.
        if exc.args and exc.args[0] == 1062:
            logger.info(
                "Dedup: ja enfileirado (ignorado) | tipo=%s | tel=%s | encomenda_id=%s",
                tipo_evento,
                telefone_ok,
                encomenda_id,
            )
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return True  # tratado como sucesso — nao trava o Receber.tsx

        logger.error(
            "Erro de integridade ao enfileirar | tipo=%s | tel=%s | erro=%s",
            tipo_evento,
            telefone_ok,
            exc,
        )
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return False

    except pymysql.Error as exc:
        logger.error(
            "Erro MySQL ao enfileirar | tipo=%s | tel=%s | erro=%s",
            tipo_evento,
            telefone_ok,
            exc,
        )
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return False

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# API publica — mesma interface do WhatsAppService v5.x
# Chamadas existentes em encomendas.py continuam funcionando sem alteracao
# ---------------------------------------------------------------------------

def send_package_notification(
    encomenda_data: dict,
    needs_confirmation: bool = False,
) -> tuple[bool, str]:
    """
    Chamada ao RECEBER encomenda.

    v6.4.0 — Bifurcacao por whats_confirmado:
      - whats_confirmado NOT NULL → ENCOMENDA_RECEBIDA via Meta
                                   (template encomenda_na_portaria_v2 com botoes de retirada)
      - whats_confirmado NULL     → CADASTRO_MORADOR via Meta
                                     (template cadastro_e_condominio_morador,
                                      botoes "Sim, sou eu" / "Nao sou eu")

    O campo encomenda_id e preservado no payload do CADASTRO_MORADOR para que,
    quando o webhook receber CONFIRMAR_MORADOR, possa reenviar a notificacao
    de encomenda pendente automaticamente.
    """
    telefone = (encomenda_data.get("telefone_morador") or "").strip()
    if not telefone:
        logger.warning("send_package_notification: telefone_morador ausente.")
        return False, "Telefone nao informado"

    # Verifica se o morador ja confirmou o numero de WhatsApp
    # whats_confirmado e DATETIME no banco: preenchido = confirmado, NULL = nao confirmado
    # Verifica se o morador ja confirmou o numero de WhatsApp
    # whats_confirmado e DATETIME no banco: preenchido = confirmado, NULL = nao confirmado
    morador_id = encomenda_data.get("morador_id")
    whats_confirmado_raw = encomenda_data.get("whats_confirmado")
    morador_confirmado = bool(whats_confirmado_raw)

    # Se não tem morador vinculado, não envia nada — não há como confirmar cadastro
    if not morador_id:
        logger.warning(
            "send_package_notification: morador_id ausente — notificacao ignorada | tel=%s",
            telefone,
        )
        return False, "Morador nao vinculado — notificacao ignorada"

    # needs_confirmation=True (legado) ou morador sem confirmacao → template de cadastro
    if needs_confirmation or not morador_confirmado:

        logger.info(
            "send_package_notification: morador sem whats_confirmado | "
            "enfileirando CADASTRO_MORADOR | tel=%s | morador_id=%s",
            telefone,
            encomenda_data.get("morador_id"),
        )

        nome_morador    = encomenda_data.get("nome_destinatario", "Morador")
        nome_condominio = (
            encomenda_data.get("nome_condominio")
            or _cfg("NOME_CONDOMINIO", "do condominio")
        )
        apartamento = encomenda_data.get("apartamento", "")
        bloco       = encomenda_data.get("bloco", "")
        apto_str    = f"Apt {apartamento}{f' Bloco {bloco}' if bloco else ''}"

        ok = queue_whatsapp_message(
            tipo_evento=EVENTO_CADASTRO_MORADOR,
            telefone=telefone,
            nome_morador=nome_morador,
            morador_id=encomenda_data.get("morador_id"),
            condominio_id=encomenda_data.get("condominio_id"),
            encomenda_id=encomenda_data.get("id"),   # preservar para reenvio apos confirmacao
            mensagem_original=(
                f"Cadastro e-Condominio | {nome_morador} | "
                f"{nome_condominio} | {apto_str}"
            ),

            payload={
                "nome_morador":        nome_morador,
                "nome_condominio":     nome_condominio,
                "apartamento":         apartamento,
                "bloco":               bloco,
                "encomenda_id":        encomenda_data.get("id"),
                "codigo_retirada":     encomenda_data.get("codigo_retirada", ""),
                "local_armazenamento": encomenda_data.get("local_armazenamento", ""),
            },
            delay_minutes=0,   # primeiro contato — envio imediato
        )
        if ok:
            return True, "Template de cadastro/confirmacao enfileirado com sucesso"
        return False, "Erro ao enfileirar template de cadastro"


    # Morador ja confirmou → notificacao normal de encomenda recebida
    logger.info(
        "send_package_notification: whats_confirmado preenchido | "
        "enfileirando ENCOMENDA_RECEBIDA | tel=%s",
        telefone,
    )

    ok = queue_whatsapp_message(
        tipo_evento=EVENTO_ENCOMENDA_RECEBIDA,
        telefone=telefone,
        nome_morador=encomenda_data.get("nome_destinatario", "Morador"),
        morador_id=encomenda_data.get("morador_id"),
        condominio_id=encomenda_data.get("condominio_id"),
        encomenda_id=encomenda_data.get("id"),
        mensagem_original=_build_new_package_text(encomenda_data),
        payload=encomenda_data,
    )

    if ok:
        return True, "Notificacao de recebimento enfileirada com sucesso"
    return False, "Erro ao enfileirar notificacao"


_EVENTO_LEMBRETE_POR_HORAS = {
    24: EVENTO_LEMBRETE_ENCOMENDA_24H,
    48: EVENTO_LEMBRETE_ENCOMENDA_48H,
    72: EVENTO_LEMBRETE_ENCOMENDA_72H,
}


def send_lembrete_retirada(encomenda_data: dict, horas: int) -> bool:
    """
    Enfileira lembrete de encomenda parada ha `horas` (24, 48 ou 72) sem retirada.
    Usado por monitor_encomendas_paradas.py (cron diario as 16h), so para
    condominios com condominios.lembrete_encomenda_ativo = 1.
    """
    tipo_evento = _EVENTO_LEMBRETE_POR_HORAS.get(horas)
    if not tipo_evento:
        logger.error("send_lembrete_retirada: horas invalidas | horas=%s", horas)
        return False

    telefone = encomenda_data.get("telefone_morador") or encomenda_data.get("telefone")
    if not telefone:
        logger.warning(
            "send_lembrete_retirada: telefone ausente | encomenda_id=%s",
            encomenda_data.get("id"),
        )
        return False

    nome_morador = encomenda_data.get("nome_destinatario", "Morador")

    return queue_whatsapp_message(
        tipo_evento=tipo_evento,
        telefone=telefone,
        nome_morador=nome_morador,
        morador_id=encomenda_data.get("morador_id"),
        condominio_id=encomenda_data.get("condominio_id"),
        encomenda_id=encomenda_data.get("id"),
        mensagem_original=f"Lembrete {horas}h | {nome_morador} | encomenda aguardando retirada",
        payload=encomenda_data,
        delay_minutes=0,
    )


def send_cadastro_morador_notification(
    phone: str,
    nome_morador: str,
    nome_condominio: str,
    apartamento: str,
    bloco: Optional[str] = None,
    morador_id: Optional[int] = None,
    condominio_id: Optional[int] = None,
    encomenda_id: Optional[int] = None,
) -> bool:
    """
    v6.4.0 — Enfileira template cadastro_e_condominio_morador diretamente.

    Pode ser chamado:
      - pelo endpoint de cadastro de morador (sem encomenda associada)
      - pelo webhook CONFIRMAR_MORADOR para reenvio manual se necessario

    Parametros do template Meta:
      {{1}} = nome_morador
      {{2}} = nome_condominio
      {{3}} = apartamento (formato "Apt 203 Bloco A")

    Botoes (Quick Reply):
      index 0 → payload CONFIRMAR_MORADOR
      index 1 → payload NEGAR_MORADOR
    """
    apto_str = f"Apt {apartamento}{f' Bloco {bloco}' if bloco else ''}"

    return queue_whatsapp_message(
        tipo_evento=EVENTO_CADASTRO_MORADOR,
        telefone=phone,
        nome_morador=nome_morador,
        morador_id=morador_id,
        condominio_id=condominio_id,
        encomenda_id=encomenda_id,
        mensagem_original=(
            f"Cadastro e-Condominio | {nome_morador} | "
            f"{nome_condominio} | {apto_str}"
        ),
        payload={
            "nome_morador":    nome_morador,
            "nome_condominio": nome_condominio,
            "apartamento":     apartamento,
            "bloco":           bloco or "",
            "encomenda_id":    encomenda_id,
        },
        delay_minutes=0,
    )


def send_grouped_delivery_message(
    phone: str,
    encomendas: list,
    nome_recebedor: str,
    nome_morador: Optional[str] = None,
    nome_condominio: Optional[str] = None,
    morador_id: Optional[int] = None,
    condominio_id: Optional[int] = None,
) -> bool:
    """
    Chamada ao ENTREGAR encomenda(s).
    Enfileira ENCOMENDA_ENTREGUE por encomenda.
    """
    if not phone:
        logger.error("send_grouped_delivery_message: telefone vazio")
        return False
    if not encomendas:
        logger.error("send_grouped_delivery_message: lista vazia")
        return False

    resultados = []
    for enc in encomendas:
        payload = {**enc, "nome_recebedor": nome_recebedor}
        if nome_condominio:
            payload["nome_condominio"] = nome_condominio

        ok = queue_whatsapp_message(
            tipo_evento=EVENTO_ENCOMENDA_ENTREGUE,
            telefone=phone,
            nome_morador=nome_morador or enc.get("nome_destinatario"),
            morador_id=morador_id or enc.get("morador_id"),
            condominio_id=condominio_id or enc.get("condominio_id"),
            encomenda_id=enc.get("id"),
            mensagem_original=_build_delivery_text(enc, nome_recebedor),
            payload=payload,
        )
        resultados.append(ok)

    total_ok = sum(1 for r in resultados if r)
    logger.info(
        "send_grouped_delivery_message: %s/%s enfileiradas | tel=%s",
        total_ok, len(encomendas), phone,
    )
    return total_ok == len(encomendas)


def send_text_message(phone: str, message: str) -> bool:
    """Enfileira mensagem generica."""
    return queue_whatsapp_message(
        tipo_evento=EVENTO_MENSAGEM_GENERICA,
        telefone=phone,
        mensagem_original=message,
        payload={"texto": message},
    )


def send_notification(phone: str, message: str, image_base64=None) -> bool:
    """
    Compatibilidade com chamadas antigas.
    BLOQUEIO TEMPORARIO: enquanto BLOCK_IMAGE_MESSAGES=true, a imagem e
    descartada silenciosamente e apenas o texto e enfileirado.
    """
    if BLOCK_IMAGE_MESSAGES and image_base64:
        logger.info(
            "send_notification: BLOQUEIO TEMPORARIO ativo — imagem descartada | tel=%s",
            phone,
        )

    return send_text_message(phone, message)


def send_image_message(phone: str, image_base64: str, caption: str = "", filename: str = "etiqueta.jpg") -> bool:
    """
    Compatibilidade com legado.
    BLOQUEIO TEMPORARIO: enquanto BLOCK_IMAGE_MESSAGES=true, a imagem e
    completamente ignorada — nada e enfileirado.
    Retorna True para nao gerar erro nas chamadas existentes.
    """
    if BLOCK_IMAGE_MESSAGES:
        logger.info(
            "send_image_message: BLOQUEIO TEMPORARIO ativo — imagem ignorada | tel=%s | filename=%s",
            phone,
            filename,
        )
        return True

    if caption:
        return send_text_message(phone, caption)

    logger.warning("send_image_message: sem caption, nada enfileirado.")
    return False


def send_confirmation_request_message(
    phone: str,
    nome_morador: str,
    nome_condominio: Optional[str] = None,
) -> bool:
    cond = nome_condominio or _cfg("NOME_CONDOMINIO", "do condominio")
    msg  = (
        f"Prezado morador {nome_morador}\n\n"
        f"{cond}\n\n"
        f"Ha encomenda(s) sua na portaria.\n\n"
        f"e-Condominio\nhttps://econdominio.com.br"
    )
    return queue_whatsapp_message(
        tipo_evento=EVENTO_CONFIRMACAO_WHATSAPP,
        telefone=phone,
        nome_morador=nome_morador,
        mensagem_original=msg,
        payload={
            "nome_morador":    nome_morador,
            "nome_condominio": cond,
            "tipo":            "confirmacao_whatsapp",
        },
    )


# ---------------------------------------------------------------------------
# Formatadores de texto (fallback / mensagem_original)
# Sem emojis — texto limpo para compatibilidade com template Meta
# ---------------------------------------------------------------------------

def _build_new_package_text(data: dict) -> str:
    nome             = data.get("nome_destinatario", "N/A")
    apto             = data.get("apartamento", "N/A")
    bloco            = data.get("bloco", "")
    codigo           = data.get("codigo_rastreio") or "Sem codigo"
    remetente        = (data.get("remetente") or "").strip()
    cond             = (data.get("nome_condominio") or _cfg("NOME_CONDOMINIO", "")).strip()
    codigo_retirada  = (data.get("codigo_retirada") or "").strip()
    agora            = datetime.now(TZ_BR).strftime("%d/%m/%Y as %H:%M")
    msg = (
        f"NOVA ENCOMENDA RECEBIDA\n\n"
        f"Destinatario: {nome}\n"
        f"Apartamento: {apto}{f'/{bloco}' if bloco else ''}\n"
        f"Codigo: {codigo}"
    )
    if remetente and remetente.lower() not in ("none", "n/a", "", "null"):
        msg += f"\nRemetente: {remetente}"
    msg += f"\nRecebido em: {agora}\n\nDisponivel para retirada na portaria."
    if codigo_retirada:
        msg += f"\n\n*Codigo de Retirada: {codigo_retirada}*"
    if cond:
        msg += f"\n\n{cond.title()}"
    msg += "\n\ne-Condominio\nhttps://econdominio.com.br"
    return msg

def _build_delivery_text(data: dict, nome_recebedor: str = "") -> str:
    nome    = data.get("nome_destinatario", "N/A")
    codigo  = data.get("codigo_rastreio") or "Sem codigo"
    cond    = (data.get("nome_condominio") or _cfg("NOME_CONDOMINIO", "")).strip()
    primeiro = nome.split()[0] if nome != "N/A" else "Morador"

    msg = f"ENCOMENDA ENTREGUE\n\nOla {primeiro}!\n\nSua encomenda {codigo} foi entregue."
    if nome_recebedor:
        msg += f"\nRecebido por: {nome_recebedor}"
    if cond:
        msg += f"\n\n{cond.title()}"
    msg += "\n\ne-Condominio\nhttps://econdominio.com.br"
    return msg


# ---------------------------------------------------------------------------
# Shim de compatibilidade com importacoes legadas
# ---------------------------------------------------------------------------

class _WhatsAppServiceCompat:
    """Delega chamadas legadas para as funcoes de fila acima."""

    def send_package_notification(self, encomenda_data: dict, needs_confirmation: bool = False):
        return send_package_notification(encomenda_data, needs_confirmation)

    def send_cadastro_morador_notification(
        self,
        phone: str,
        nome_morador: str,
        nome_condominio: str,
        apartamento: str,
        bloco: Optional[str] = None,
        morador_id: Optional[int] = None,
        condominio_id: Optional[int] = None,
        encomenda_id: Optional[int] = None,
    ) -> bool:
        return send_cadastro_morador_notification(
            phone=phone,
            nome_morador=nome_morador,
            nome_condominio=nome_condominio,
            apartamento=apartamento,
            bloco=bloco,
            morador_id=morador_id,
            condominio_id=condominio_id,
            encomenda_id=encomenda_id,
        )

    def send_grouped_delivery_message(
        self,
        phone,
        encomendas,
        nome_recebedor,
        nome_morador=None,
        nome_condominio=None,
        morador_id=None,
        condominio_id=None,
    ):
        return send_grouped_delivery_message(
            phone=phone,
            encomendas=encomendas,
            nome_recebedor=nome_recebedor,
            nome_morador=nome_morador,
            nome_condominio=nome_condominio,
            morador_id=morador_id,
            condominio_id=condominio_id,
        )

    def send_text_message(self, phone: str, message: str) -> bool:
        return send_text_message(phone, message)

    def send_notification(self, phone: str, message: str, image_base64=None) -> bool:
        return send_notification(phone, message, image_base64)

    def send_image_message(self, phone, image_base64, caption="", filename="etiqueta.jpg"):
        return send_image_message(phone, image_base64, caption, filename)

    def send_confirmation_request_message(self, phone, nome_morador, nome_condominio=None):
        return send_confirmation_request_message(phone, nome_morador, nome_condominio)

    def format_new_package_message(self, encomenda_data: dict) -> str:
        return _build_new_package_text(encomenda_data)

    def format_delivery_message(self, encomenda_data: dict) -> str:
        return _build_delivery_text(encomenda_data)

    def queue_status(self) -> dict:
        return {"info": "Fila migrada para MySQL — consulte whatsapp_message_queue."}


whatsapp_service = _WhatsAppServiceCompat()
