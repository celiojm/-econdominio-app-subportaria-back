# ============================================================================
# ARQUIVO: router.py
# PASTA: /home/visionlpr/app_subportaria_back/leads/
# DESCRIÇÃO: Endpoints PÚBLICOS de captura de leads (funil /conheca do site +
#            servidor de WhatsApp externo). Nunca confia no navegador — toda
#            validação real é aqui (allowlist por campo, Pydantic extra=
#            'forbid', ORM parametrizado). Grava/atualiza a tabela `leads`
#            (AdmGeral) — tabela nova, separada de `contato_condominios`
#            (cadastro de condomínio real) de propósito, pra não confundir
#            lead de anúncio com cadastro de cliente.
# VERSÃO: 1.3.0 - formulário atualiza nome/CNPJ de lead existente; log do repasse visível
#         1.2.0 - lead do formulário também é repassado à agência (webhook n8n)
#         1.1.0 - qualificação via WhatsApp (perfil, nome, cnpj/condomínio; etapa)
# data criação: 2026-09-15   data alteração: 2026-09-15
# ============================================================================

import re  # 2026-10-02: validação do chat_id
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import text

from app.database import SessionLocal
from .validators import (
    ORIGEM_WHATSAPP,
    extrair_ip_real,
    normalizar_whatsapp_br,
    rate_limit_excedido,
    validar_cnpj,
    validar_fbclid,
    validar_mensagem,
    validar_nome,
    validar_origem_formulario,
    validar_pagina,
    validar_utm,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["Leads"])

LEADS_WHATSAPP_TOKEN = os.getenv("LEADS_WHATSAPP_TOKEN", "")
LEADS_WHATSAPP_IP_PERMITIDO = os.getenv("LEADS_WHATSAPP_IP", "191.252.221.192")

# Webhook da agência (n8n). Pra desligar o repasse: AGENCIA_WEBHOOK_URL= (vazio) no .env
AGENCIA_WEBHOOK_URL = os.getenv(
    "AGENCIA_WEBHOOK_URL",
    "https://n8n-n8n-start.t4r0vc.easypanel.host/webhook/fff55ee4-c4e6-42a5-bd84-d62a8ab00354",
)


def _enviar_lead_agencia(payload: dict) -> None:
    """Repassa o lead do formulário à agência. Roda em background, depois da
    resposta ao visitante: nunca atrasa nem derruba o cadastro; falha só vai pro log."""
    if not AGENCIA_WEBHOOK_URL:
        return
    import json
    import urllib.request

    try:
        req = urllib.request.Request(
            AGENCIA_WEBHOOK_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            print(f"Lead repassado à agência | id={payload.get('lead_id')} | HTTP {resp.status}", flush=True)
    except Exception as e:
        print(f"Falha ao repassar lead à agência | id={payload.get('lead_id')} | {e}", flush=True)

MAX_BODY_BYTES = 4096

PERFIS_VALIDOS = ("sindico", "morador", "gestor")
ETAPAS_QUALIFICACAO = ("perfil", "nome", "cnpj")


def validar_perfil(v):
    if v is None or v == "":
        return None
    if v not in PERFIS_VALIDOS:
        raise ValueError("perfil inválido")
    return v


# ─── Schemas (extra='forbid' — qualquer campo a mais é rejeitado) ──────────

class LeadFormularioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: str
    whatsapp: str
    cnpj: str
    origem: str
    consentimento: bool
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    fbclid: Optional[str] = None
    pagina: Optional[str] = None
    # honeypot — campo oculto no formulário; se vier preenchido, é bot.
    # Nome neutro de propósito (evita autofill do Chrome, que preenche
    # "website"/"url" automaticamente): esconder via CSS (não type="hidden"
    # puro), com autocomplete="off" e tabindex="-1" no input.
    hp_ref: Optional[str] = None

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        r = validar_nome(v)
        if not r:
            raise ValueError("nome obrigatório")
        return r

    @field_validator("cnpj")
    @classmethod
    def _v_cnpj(cls, v):
        r = validar_cnpj(v)
        if not r:
            raise ValueError("cnpj obrigatório")
        return r

    @field_validator("origem")
    @classmethod
    def _v_origem(cls, v):
        return validar_origem_formulario(v)

    @field_validator("consentimento")
    @classmethod
    def _v_consentimento(cls, v):
        if v is not True:
            raise ValueError("consentimento com a LGPD é obrigatório")
        return v

    @field_validator("utm_source", "utm_medium", "utm_campaign", "utm_content")
    @classmethod
    def _v_utm(cls, v, info):
        return validar_utm(v, info.field_name)

    @field_validator("fbclid")
    @classmethod
    def _v_fbclid(cls, v):
        return validar_fbclid(v)

    @field_validator("pagina")
    @classmethod
    def _v_pagina(cls, v):
        return validar_pagina(v)


class LeadWhatsAppRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    whatsapp: str
    nome: Optional[str] = None
    mensagem: Optional[str] = None
    ref: Optional[str] = None
    perfil: Optional[str] = None
    cnpj: Optional[str] = None
    etapa: Optional[str] = None
    razao_social: Optional[str] = None
    chat_id: Optional[str] = None   # 2026-10-02: id da conversa no WhatsApp (ex.: ...@lid)

    @field_validator("chat_id")
    @classmethod
    def _v_chat_id(cls, v):
        # formato inesperado não recusa o lead — só descarta o campo
        return v if v and re.fullmatch(r"[0-9]{5,25}@(c\.us|lid)", v) else None

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        return validar_nome(v)

    @field_validator("mensagem")
    @classmethod
    def _v_mensagem(cls, v):
        return validar_mensagem(v)

    @field_validator("ref")
    @classmethod
    def _v_ref(cls, v):
        return validar_utm(v, "ref")

    @field_validator("perfil")
    @classmethod
    def _v_perfil(cls, v):
        return validar_perfil(v)

    @field_validator("cnpj")
    @classmethod
    def _v_cnpj_wa(cls, v):
        return validar_cnpj(v)

    @field_validator("etapa")
    @classmethod
    def _v_etapa(cls, v):
        if v is None:
            return None
        if v not in ETAPAS_QUALIFICACAO:
            raise ValueError("etapa inválida")
        return v

    @field_validator("razao_social")
    @classmethod
    def _v_razao_social(cls, v):
        r = validar_mensagem(v)
        return r[:200] if r else None


# ─── Persistência (upsert manual — dedup por whatsapp canônico) ───────────

def _upsert_lead(
    *,
    whatsapp_canonico: str,
    origem: str,
    nome: Optional[str] = None,
    cnpj: Optional[str] = None,
    razao_social: Optional[str] = None,
    cidade: Optional[str] = None,
    utm_source: Optional[str] = None,
    utm_medium: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    utm_content: Optional[str] = None,
    fbclid: Optional[str] = None,
    pagina: Optional[str] = None,
    ref: Optional[str] = None,
    mensagem: Optional[str] = None,
    ip_origem: Optional[str] = None,
    consentimento_lgpd: bool = False,
    perfil: Optional[str] = None,
    qualificacao: Optional[str] = None,
    sobrescrever: bool = False,
) -> dict:
    db = SessionLocal()
    try:
        existente = db.execute(
            text("SELECT id, nome, cnpj, razao_social, cidade, observacao FROM leads WHERE whatsapp = :w"),
            {"w": whatsapp_canonico},
        ).fetchone()

        agora_str = datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M")
        rotulo_ocorrencia = "Retorno via" if existente else "Novo contato via"
        nova_ocorrencia = f"[{agora_str}] {rotulo_ocorrencia} {origem}"
        if mensagem:
            nova_ocorrencia += f': "{mensagem}"'
        campanha = ref or utm_campaign or utm_source  # 2026-09-30: campanha no histórico de cada contato
        if campanha and not qualificacao:
            nova_ocorrencia += f" [campanha: {campanha}]"
        if qualificacao:
            valor_q = {"perfil": perfil, "nome": nome, "cnpj": cnpj or razao_social}.get(qualificacao)
            nova_ocorrencia = f"[{agora_str}] Qualificação WhatsApp — {qualificacao}: {valor_q or '(sem resposta)'}"

        if existente:
            observacao_atual = existente.observacao or ""
            observacao_nova = (observacao_atual + "\n" + nova_ocorrencia).strip()
            db.execute(
                text(
                    """
                    UPDATE leads SET
                        total_contatos = total_contatos + :incremento,
                        ultimo_contato_em = NOW(),
                        nome = CASE WHEN :forcar = 1 AND :nome IS NOT NULL THEN :nome
                                    ELSE COALESCE(NULLIF(nome, ''), :nome) END,
                        cnpj = CASE WHEN :forcar = 1 AND :cnpj IS NOT NULL THEN :cnpj
                                    ELSE COALESCE(cnpj, :cnpj) END,
                        perfil = COALESCE(:perfil, perfil),
                        razao_social = CASE WHEN :forcar = 1 AND :razao_social IS NOT NULL THEN :razao_social
                                    ELSE COALESCE(NULLIF(razao_social, ''), :razao_social) END,
                        cidade = COALESCE(NULLIF(cidade, ''), :cidade),
                        ref = COALESCE(NULLIF(ref, ''), :ref),
                        utm_source = COALESCE(NULLIF(utm_source, ''), :utm_source),
                        utm_medium = COALESCE(NULLIF(utm_medium, ''), :utm_medium),
                        utm_campaign = COALESCE(NULLIF(utm_campaign, ''), :utm_campaign),
                        utm_content = COALESCE(NULLIF(utm_content, ''), :utm_content),
                        consentimento_em = CASE
                            WHEN consentimento_lgpd = 0 AND :consentimento_lgpd = 1 THEN NOW()
                            ELSE consentimento_em
                        END,
                        consentimento_lgpd = CASE
                            WHEN consentimento_lgpd = 0 AND :consentimento_lgpd = 1 THEN 1
                            ELSE consentimento_lgpd
                        END,
                        observacao = :observacao
                    WHERE id = :id
                    """
                ),
                {
                    "nome": nome,
                    "cnpj": cnpj,
                    "razao_social": razao_social,
                    "consentimento_lgpd": 1 if consentimento_lgpd else 0,
                    "cidade": cidade,
                    "observacao": observacao_nova,
                    "perfil": perfil,
                    "ref": ref, "utm_source": utm_source, "utm_medium": utm_medium,
                    "utm_campaign": utm_campaign, "utm_content": utm_content,
                    "forcar": 1 if (qualificacao or sobrescrever) else 0,
                    "incremento": 0 if qualificacao else 1,
                    "id": existente.id,
                },
            )
            db.commit()
            logger.info("Lead atualizado (repetido) | id=%s | whatsapp=%s... | origem=%s", existente.id, whatsapp_canonico[:6], origem)
            return {"id": existente.id, "novo": False}

        result = db.execute(
            text(
                """
                INSERT INTO leads (
                    nome, whatsapp, cnpj, razao_social, cidade, origem, status,
                    utm_source, utm_medium, utm_campaign, utm_content, fbclid, pagina, ref,
                    mensagem, ip_origem, consentimento_lgpd, consentimento_em, observacao, perfil
                ) VALUES (
                    :nome, :whatsapp, :cnpj, :razao_social, :cidade, :origem, 'novo',
                    :utm_source, :utm_medium, :utm_campaign, :utm_content, :fbclid, :pagina, :ref,
                    :mensagem, :ip_origem, :consentimento_lgpd,
                    CASE WHEN :consentimento_lgpd = 1 THEN NOW() ELSE NULL END, :observacao, :perfil
                )
                """
            ),
            {
                "nome": nome,
                "whatsapp": whatsapp_canonico,
                "cnpj": cnpj,
                "razao_social": razao_social,
                "cidade": cidade,
                "origem": origem,
                "utm_source": utm_source,
                "utm_medium": utm_medium,
                "utm_campaign": utm_campaign,
                "utm_content": utm_content,
                "fbclid": fbclid,
                "pagina": pagina,
                "ref": ref,
                "mensagem": mensagem,
                "ip_origem": ip_origem,
                "consentimento_lgpd": 1 if consentimento_lgpd else 0,
                "observacao": nova_ocorrencia,
                "perfil": perfil,
            },
        )
        db.commit()
        novo_id = result.lastrowid
        logger.info("Lead novo criado | id=%s | whatsapp=%s... | origem=%s", novo_id, whatsapp_canonico[:6], origem)
        return {"id": novo_id, "novo": True}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ─── POST /api/leads — formulário público do site ──────────────────────────

@router.post("")
async def criar_lead_formulario(data: LeadFormularioRequest, request: Request, background_tasks: BackgroundTasks):
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(status_code=400, detail="dados inválidos")

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="dados inválidos")

    # Honeypot: campo oculto preenchido = bot. Responde sucesso genérico
    # (não avisa o bot que foi pego) mas NÃO grava nada.
    if data.hp_ref:
        logger.warning("Lead descartado (honeypot) | ip=%s", extrair_ip_real(request))
        return {"ok": True}

    ip = extrair_ip_real(request)
    if rate_limit_excedido(ip):
        logger.warning("Lead descartado (rate limit) | ip=%s", ip)
        raise HTTPException(status_code=429, detail="Muitas tentativas. Tente novamente mais tarde.")

    whatsapp_canonico = normalizar_whatsapp_br(data.whatsapp)
    if not whatsapp_canonico:
        # 2026-09-30: registra o motivo (sem o número — LGPD)
        logger.warning("Lead recusado (WhatsApp fora do padrão de celular BR) | digitos=%d | ip=%s",
                       sum(c.isdigit() for c in (data.whatsapp or "")), ip)
        raise HTTPException(status_code=422, detail="dados inválidos")

    try:
        resultado = _upsert_lead(
            whatsapp_canonico=whatsapp_canonico,
            origem=data.origem,
            nome=data.nome,
            cnpj=data.cnpj,
            utm_source=data.utm_source,
            utm_medium=data.utm_medium,
            utm_campaign=data.utm_campaign,
            utm_content=data.utm_content,
            fbclid=data.fbclid,
            pagina=data.pagina,
            ip_origem=ip,
            consentimento_lgpd=True,
            sobrescrever=True,
        )
    except Exception:
        logger.exception("Erro ao gravar lead do formulario")
        raise HTTPException(status_code=500, detail="Erro ao processar. Tente novamente.")

    # Repasse à agência — mesmos nomes de campo do pacote da agência
    # (name/email/whatsapp/units) + os nossos. email/units não existem no nosso formulário.
    background_tasks.add_task(_enviar_lead_agencia, {
        "name": data.nome,
        "email": None,
        "whatsapp": whatsapp_canonico,
        "units": None,
        "cnpj": data.cnpj,
        "origem": data.origem,
        "pagina": data.pagina,
        "utm_source": data.utm_source,
        "utm_medium": data.utm_medium,
        "utm_campaign": data.utm_campaign,
        "utm_content": data.utm_content,
        "fbclid": data.fbclid,
        "consentimento_lgpd": True,
        "lead_id": resultado["id"],
        "lead_novo": resultado["novo"],
        "data_hora": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "fonte": "econdominio.com.br",
    })

    return {"ok": True, "id": resultado["id"]}


# ─── POST /api/leads/whatsapp — só o servidor do WhatsApp chama ────────────

@router.post("/whatsapp")
async def criar_lead_whatsapp(data: LeadWhatsAppRequest, request: Request):
    token_recebido = request.headers.get("x-lead-token", "")
    if not LEADS_WHATSAPP_TOKEN or not hmac.compare_digest(token_recebido, LEADS_WHATSAPP_TOKEN):
        raise HTTPException(status_code=403, detail="acesso negado")

    ip = extrair_ip_real(request)
    if ip != LEADS_WHATSAPP_IP_PERMITIDO:
        logger.warning("Lead WhatsApp rejeitado (IP não autorizado) | ip=%s", ip)
        raise HTTPException(status_code=403, detail="acesso negado")

    whatsapp_canonico = normalizar_whatsapp_br(data.whatsapp)
    if not whatsapp_canonico:
        # 2026-09-30: registra o motivo (sem o número — LGPD)
        logger.warning("Lead recusado (WhatsApp fora do padrão de celular BR) | digitos=%d | ip=%s",
                       sum(c.isdigit() for c in (data.whatsapp or "")), ip)
        raise HTTPException(status_code=422, detail="dados inválidos")

    try:
        resultado = _upsert_lead(
            whatsapp_canonico=whatsapp_canonico,
            origem=ORIGEM_WHATSAPP,
            nome=data.nome,
            ref=data.ref,
            mensagem=data.mensagem,
            ip_origem=None,
            consentimento_lgpd=False,
            cnpj=data.cnpj,
            perfil=data.perfil,
            qualificacao=data.etapa,
            razao_social=data.razao_social,
        )
    except Exception:
        logger.exception("Erro ao gravar lead do whatsapp")
        raise HTTPException(status_code=500, detail="Erro ao processar.")

    if data.chat_id:
        # 2026-10-02: guarda a conversa para o painel (falha aqui nunca perde o lead)
        try:
            _db = SessionLocal()
            _db.execute(text("UPDATE leads SET whatsapp_chat_id = :c WHERE id = :i"), {"c": data.chat_id, "i": resultado["id"]})
            _db.commit(); _db.close()
        except Exception as e:
            logger.warning("Lead WhatsApp: chat_id não gravado: %s", e)

    return {"ok": True, "id": resultado["id"]}


# ─── 2026-10-02: histórico das conversas do WhatsApp do funil (painel do lead) ─────────
class LeadMensagemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    msg_id: str
    chat_id: str
    from_me: bool = False
    tipo: str = "chat"
    corpo: Optional[str] = None
    timestamp: int
    midia_mime: Optional[str] = None
    midia_nome: Optional[str] = None
    midia_dados: Optional[str] = None
    bot: bool = False   # 2026-10-02: enviada pelo robô do funil
    telefone: Optional[str] = None   # 2026-10-02: telefone de quem escreveu (liga a conversa ao lead)

    @field_validator("chat_id")
    @classmethod
    def _v_chat(cls, v):
        if not re.fullmatch(r"[0-9]{5,25}@(c\.us|lid)", v or ""):
            raise ValueError("chat_id inválido")
        return v

    @field_validator("msg_id")
    @classmethod
    def _v_msg(cls, v):
        if not v or len(v) > 160:
            raise ValueError("msg_id inválido")
        return v

    @field_validator("tipo")
    @classmethod
    def _v_tipo(cls, v):
        return v if v in ("chat", "ptt", "audio", "image", "video", "document", "sticker") else "chat"


@router.post("/whatsapp/mensagem")
async def registrar_mensagem_whatsapp(data: LeadMensagemRequest, request: Request):
    token_recebido = request.headers.get("x-lead-token", "")
    if not LEADS_WHATSAPP_TOKEN or not hmac.compare_digest(token_recebido, LEADS_WHATSAPP_TOKEN):
        raise HTTPException(status_code=403, detail="acesso negado")
    if extrair_ip_real(request) != LEADS_WHATSAPP_IP_PERMITIDO:
        raise HTTPException(status_code=403, detail="acesso negado")
    dados = data.midia_dados if data.midia_dados and len(data.midia_dados) <= 7 * 1024 * 1024 else None
    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT IGNORE INTO leads_mensagens (msg_id, chat_id, from_me, bot, tipo, corpo, midia_mime, midia_nome, midia_dados, enviado_em)
            VALUES (:m, :c, :f, :bot, :t, :b, :mm, :mn, :md, FROM_UNIXTIME(:ts))"""),
            {"m": data.msg_id, "c": data.chat_id, "f": 1 if data.from_me else 0, "bot": 1 if data.bot else 0, "t": data.tipo,
             "b": (data.corpo or "")[:4000] or None, "mm": (data.midia_mime or "")[:100] if dados else None,
             "mn": (data.midia_nome or "")[:255] or None if dados else None, "md": dados, "ts": data.timestamp})
        # 2026-10-02: cliente escreveu — liga esta conversa ao lead do mesmo telefone (cadastro manual,
        # ou WhatsApp que trocou @c.us por @lid) e move o histórico antigo para o id novo
        if not data.from_me and data.telefone:
            canon = normalizar_whatsapp_br(data.telefone)
            lead = db.execute(text("SELECT id, whatsapp_chat_id FROM leads WHERE whatsapp = :w"), {"w": canon}).fetchone() if canon else None
            if lead and lead.whatsapp_chat_id != data.chat_id:
                db.execute(text("UPDATE leads SET whatsapp_chat_id = :c WHERE id = :i"), {"c": data.chat_id, "i": lead.id})
                if lead.whatsapp_chat_id:
                    db.execute(text("UPDATE leads_mensagens SET chat_id = :c WHERE chat_id = :o"),
                               {"c": data.chat_id, "o": lead.whatsapp_chat_id})
                logger.info("Lead %s ligado à conversa do WhatsApp", lead.id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Erro ao gravar mensagem do whatsapp")
        raise HTTPException(status_code=500, detail="Erro ao processar.")
    finally:
        db.close()
    return {"ok": True}
