# ================================================================================
# ALTERAÇÃO 2026-09-26: webhook exige asaas-access-token ou assinatura válida (antes aceitava sem header)
# ALTERAÇÃO 2026-09-26: rota(s) de teste sem autenticação removida(s) (segurança)
#  PATH: backend/financeiro/financeiro_webhook.py
#  DESCRIPTION: Módulo financeiro – Econdomínio / Inforseg
#               Webhook para receber callbacks do Asaas
#  VERSÃO: 3.0 - CORRIGIDO: dias de validade lidos do banco (plano_selecionado)
#                            não usa mais VALOR_DIAS_MAP hardcoded
# ================================================================================

from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel

from app.database import SessionLocal

import os
import logging
import hmac
import hashlib

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["Webhook"])


# ================================================================================
#  HELPER PARA OBTER SESSÃO
# ================================================================================

def get_session():
    return SessionLocal()


# ================================================================================
#  SCHEMAS DO WEBHOOK
# ================================================================================

class WebhookPayload(BaseModel):
    event: str
    payment: Optional[dict] = None
    subscription: Optional[dict] = None


# ================================================================================
#  MAPEAMENTO DE DIAS POR PLANO (usa plano_selecionado do banco)
# ================================================================================

# Mapeamento do nome do plano -> dias de validade
# Usado como fallback quando não há id_assinatura/id_plano
PLANO_DIAS_MAP = {
    'mensal':     30,
    'trimestral': 90,
    'semestral':  180,
    'anual':      360,
}


def inferir_dias_validade_por_plano(
    plano_selecionado: str = None,
    descricao: str = None
) -> int:
    """
    Infere os dias de validade baseado no plano_selecionado do condomínio.
    NÃO usa mais valor monetário como referência (valores são dinâmicos por qtd aptos).

    Prioridade:
    1. plano_selecionado do condomínio (campo no banco)
    2. Descrição da cobrança
    3. Default: 30 dias (mensal)
    """
    # 1. Pelo plano salvo no condomínio
    if plano_selecionado:
        plano_lower = plano_selecionado.lower().strip()
        if plano_lower in PLANO_DIAS_MAP:
            dias = PLANO_DIAS_MAP[plano_lower]
            logger.info(f"📅 Dias inferidos pelo plano '{plano_selecionado}': {dias}")
            return dias

    # 2. Pela descrição da cobrança
    if descricao:
        desc_lower = descricao.lower()
        if 'anual' in desc_lower:
            return 360
        elif 'semestral' in desc_lower:
            return 180
        elif 'trimestral' in desc_lower:
            return 90
        elif 'mensal' in desc_lower:
            return 30

    logger.warning(
        f"⚠️ Não foi possível inferir dias de validade "
        f"(plano={plano_selecionado}, desc={descricao}). Usando 30 dias (padrão mensal)."
    )
    return 30


# ================================================================================
#  VALIDAÇÃO DO WEBHOOK
# ================================================================================

def validate_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    if not secret:
        return True
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ================================================================================
#  ENDPOINT DO WEBHOOK
# ================================================================================

@router.post("/asaas")
async def webhook_asaas(
    request: Request,
    asaas_signature: Optional[str] = Header(None, alias="asaas-signature")
):
    """
    Recebe notificações de eventos do Asaas.

    Eventos suportados:
    - PAYMENT_CONFIRMED / PAYMENT_RECEIVED: Atualiza status + validade do condomínio
    - PAYMENT_OVERDUE: Marca cobrança como vencida
    - PAYMENT_REFUNDED: Marca como estornada
    - PAYMENT_DELETED: Marca como cancelada

    URL para configurar no painel Asaas:
    https://seu-dominio.com/api/financeiro/webhook/asaas
    """
    body = await request.body()

    # Fechado por padrão: exige token (asaas-access-token) ou assinatura HMAC válida.
    webhook_secret = os.getenv("ASAAS_WEBHOOK_SECRET")
    token_header = request.headers.get("asaas-access-token")
    autenticado = bool(webhook_secret) and bool(
        (token_header and hmac.compare_digest(token_header, webhook_secret))
        or (asaas_signature and validate_webhook_signature(body, asaas_signature, webhook_secret))
    )
    if not autenticado:
        logger.warning("Webhook Asaas rejeitado: sem token/assinatura válida (headers=%s, ip=%s)",
                       sorted(request.headers.keys()), request.headers.get("x-real-ip"))
        raise HTTPException(status_code=401, detail="Não autorizado")

    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Erro ao parsear webhook: {e}")
        raise HTTPException(status_code=400, detail="Payload inválido")

    event = payload.get("event")
    payment_data = payload.get("payment", {})

    logger.info(f"🔔 Webhook recebido: {event} - Payment ID: {payment_data.get('id')}")

    try:
        if event in ["PAYMENT_CONFIRMED", "PAYMENT_RECEIVED", "PAYMENT_RECEIVED_IN_CASH"]:
            await process_payment_confirmed(event, payment_data)
        elif event in ["PAYMENT_CREATED", "PAYMENT_UPDATED", "PAYMENT_OVERDUE"]:
            await process_payment_event(event, payment_data)
        elif event == "PAYMENT_REFUNDED":
            await process_payment_refunded(payment_data)
        elif event == "PAYMENT_DELETED":
            await process_payment_deleted(payment_data)
        else:
            logger.info(f"Evento não processado: {event}")

        return {"success": True, "event": event}

    except Exception as e:
        logger.error(f"❌ Erro ao processar webhook {event}: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


# ================================================================================
#  PROCESSAMENTO DE PAGAMENTO CONFIRMADO
# ================================================================================

async def process_payment_confirmed(event: str, payment_data: dict):
    """
    Processa pagamento confirmado:
    1. Atualiza status da cobrança para 'pago'
    2. Calcula dias de validade a partir do plano_selecionado do condomínio
    3. Atualiza validade_ate do condomínio
    4. Envia email de confirmação
    """
    asaas_payment_id = payment_data.get("id")
    if not asaas_payment_id:
        logger.warning("Payment sem ID")
        return

    db = get_session()

    try:
        # Buscar cobrança + dados do condomínio incluindo plano_selecionado
        result = db.execute(text("""
            SELECT
                c.id_cobranca,
                c.id_condominio,
                c.valor,
                c.descricao,
                c.id_assinatura,
                cond.validade_ate       AS validade_atual,
                cond.nome               AS condominio_nome,
                cond.plano_selecionado  AS plano_selecionado,
                cond.cobranca_email     AS cobranca_email,
                cond.email_financeiro   AS email_financeiro,
                cond.email              AS email
            FROM cobrancas c
            JOIN condominios cond ON cond.id = c.id_condominio
            WHERE c.asaas_payment_id = :asaas_id
            LIMIT 1
        """), {"asaas_id": asaas_payment_id})

        row = result.fetchone()

        if not row:
            logger.warning(f"⚠️ Cobrança não encontrada: {asaas_payment_id}")
            return

        id_cobranca      = row.id_cobranca
        id_condominio    = row.id_condominio
        valor            = float(row.valor) if row.valor else 0
        descricao        = row.descricao
        validade_atual   = row.validade_atual
        condominio_nome  = row.condominio_nome
        plano_selecionado = row.plano_selecionado

        logger.info(
            f"📋 Processando pagamento para {condominio_nome} "
            f"(ID: {id_condominio}, plano: {plano_selecionado})"
        )

        # 1. Atualizar status da cobrança
        valor_pago = payment_data.get("value") or payment_data.get("netValue") or valor

        db.execute(text("""
            UPDATE cobrancas
            SET status = 'pago',
                data_pagamento = :data_pagamento,
                valor_pago = :valor_pago,
                data_atualizacao = NOW()
            WHERE id_cobranca = :id_cobranca
        """), {
            "data_pagamento": date.today(),
            "valor_pago": valor_pago,
            "id_cobranca": id_cobranca
        })

        logger.info(f"✅ Cobrança {id_cobranca} marcada como PAGA")

        # 2. Calcular dias de validade
        dias_validade = None

        # Tentar pelo id_assinatura -> tabela planos (mais preciso)
        if row.id_assinatura:
            plano_result = db.execute(text("""
                SELECT p.dias_validade
                FROM assinaturas a
                JOIN planos p ON p.id_plano = a.id_plano
                WHERE a.id_assinatura = :id_assinatura
            """), {"id_assinatura": row.id_assinatura})
            plano_row = plano_result.fetchone()
            if plano_row and plano_row.dias_validade:
                dias_validade = plano_row.dias_validade
                logger.info(f"📅 Dias obtidos da tabela planos: {dias_validade}")

        # ✅ CORRIGIDO: usa plano_selecionado do condomínio (não valor monetário)
        if not dias_validade:
            dias_validade = inferir_dias_validade_por_plano(
                plano_selecionado=plano_selecionado,
                descricao=descricao
            )

        # 3. Calcular data base para nova validade
        hoje = date.today()
        if validade_atual and validade_atual > hoje:
            data_base = validade_atual  # Soma a partir da validade atual (renovação antecipada)
            logger.info(f"📅 Renovação antecipada — base: {validade_atual}")
        else:
            data_base = hoje            # Vencido ou sem validade — começa hoje
            logger.info(f"📅 Vencido/novo — base: {hoje}")

        nova_validade = data_base + timedelta(days=dias_validade)

        # 4. Atualizar validade do condomínio
        db.execute(text("""
            UPDATE condominios
            SET validade_ate = :nova_validade,
                assinatura_status = 'ativa'
            WHERE id = :id_condominio
        """), {
            "nova_validade": nova_validade,
            "id_condominio": id_condominio
        })

        logger.info(
            f"🎉 VALIDADE ATUALIZADA! {condominio_nome}: "
            f"{validade_atual} → {nova_validade} (+{dias_validade} dias)"
        )

        # 5. Atualizar assinatura se existir
        if row.id_assinatura:
            db.execute(text("""
                UPDATE assinaturas
                SET validade_ate = :nova_validade,
                    status = 'ativa',
                    data_atualizacao = NOW()
                WHERE id_assinatura = :id_assinatura
            """), {
                "nova_validade": nova_validade,
                "id_assinatura": row.id_assinatura
            })
            logger.info(f"✅ Assinatura {row.id_assinatura} atualizada")

        db.commit()

        # 6. Enviar email de confirmação (não bloqueia em caso de falha)
        email_destino = row.cobranca_email or row.email_financeiro or row.email
        if email_destino:
            try:
                from .financeiro_email import enviar_email_pagamento_confirmado

                hoje_fmt = date.today().strftime("%d/%m/%Y")
                validade_fmt = nova_validade.strftime("%d/%m/%Y")
                dias_restantes = (nova_validade - date.today()).days
                forma = payment_data.get("billingType") or "PIX"

                resultado = enviar_email_pagamento_confirmado(
                    email_destino=email_destino,
                    nome_condominio=condominio_nome,
                    valor=float(valor_pago),
                    data_pagamento=hoje_fmt,
                    forma_pagamento=forma,
                    descricao=descricao or "Mensalidade",
                    validade_ate=validade_fmt,
                    dias_restantes=dias_restantes
                )
                if resultado.get("success"):
                    logger.info(f"📧 Email de confirmação enviado para {email_destino}")
                else:
                    logger.warning(f"⚠️ Email não enviado: {resultado.get('message')}")
            except Exception as e:
                logger.error(f"❌ Erro ao enviar email de confirmação: {e}")
        else:
            logger.warning(f"⚠️ Sem email cadastrado para {condominio_nome} — confirmação não enviada")

    except Exception as e:
        logger.error(f"❌ Erro ao processar pagamento confirmado: {e}")
        db.rollback()
        raise
    finally:
        db.close()


# ================================================================================
#  OUTROS EVENTOS
# ================================================================================

async def process_payment_event(event: str, payment_data: dict):
    asaas_payment_id = payment_data.get("id")
    if not asaas_payment_id:
        return

    db = get_session()
    try:
        asaas_status = payment_data.get("status")
        status_map = {
            "PENDING": "pendente",
            "OVERDUE": "vencido",
        }
        novo_status = status_map.get(asaas_status)

        if novo_status:
            db.execute(text("""
                UPDATE cobrancas
                SET status = :status,
                    data_atualizacao = NOW()
                WHERE asaas_payment_id = :asaas_id
            """), {"status": novo_status, "asaas_id": asaas_payment_id})
            db.commit()
            logger.info(f"✅ Cobrança {asaas_payment_id} → {novo_status}")
    finally:
        db.close()


async def process_payment_refunded(payment_data: dict):
    asaas_payment_id = payment_data.get("id")
    if not asaas_payment_id:
        return
    db = get_session()
    try:
        db.execute(text("""
            UPDATE cobrancas
            SET status = 'estornada', data_atualizacao = NOW()
            WHERE asaas_payment_id = :asaas_id
        """), {"asaas_id": asaas_payment_id})
        db.commit()
        logger.info(f"✅ Cobrança {asaas_payment_id} estornada")
    finally:
        db.close()


async def process_payment_deleted(payment_data: dict):
    asaas_payment_id = payment_data.get("id")
    if not asaas_payment_id:
        return
    db = get_session()
    try:
        db.execute(text("""
            UPDATE cobrancas
            SET status = 'cancelada', data_atualizacao = NOW()
            WHERE asaas_payment_id = :asaas_id
        """), {"asaas_id": asaas_payment_id})
        db.commit()
        logger.info(f"✅ Cobrança {asaas_payment_id} cancelada via webhook")
    finally:
        db.close()


# ================================================================================
#  ENDPOINTS DE TESTE / DEBUG
# ================================================================================

@router.get("/asaas/test")
async def test_webhook():
    return {
        "status": "ok",
        "message": "Webhook Asaas v3.0 — dias de validade por plano_selecionado",
        "env": os.getenv("ASAAS_ENV", "production"),
        "timestamp": datetime.now().isoformat()
    }


# [removido 2026-09-26, segurança] @router.post("/asaas/test-validade")
async def test_validade_update(id_condominio: int, dias: int = 30):
    """Endpoint para testar atualização de validade manualmente."""
    db = get_session()
    try:
        result = db.execute(text("""
            SELECT nome, validade_ate, plano_selecionado FROM condominios WHERE id = :id
        """), {"id": id_condominio})
        row = result.fetchone()

        if not row:
            return {"error": "Condomínio não encontrado"}

        hoje = date.today()
        data_base = row.validade_ate if (row.validade_ate and row.validade_ate > hoje) else hoje
        nova_validade = data_base + timedelta(days=dias)

        db.execute(text("""
            UPDATE condominios SET validade_ate = :nova_validade WHERE id = :id
        """), {"nova_validade": nova_validade, "id": id_condominio})
        db.commit()

        return {
            "success": True,
            "condominio": row.nome,
            "plano_selecionado": row.plano_selecionado,
            "validade_anterior": str(row.validade_ate) if row.validade_ate else None,
            "nova_validade": str(nova_validade),
            "dias_adicionados": dias
        }
    finally:
        db.close()


# [removido 2026-09-26, segurança] @router.post("/asaas/simular-pagamento")
async def simular_pagamento(asaas_payment_id: str):
    """
    Simula um webhook de PAYMENT_CONFIRMED para testes.
    Útil quando o servidor não é acessível publicamente pelo Asaas.
    """
    fake_payment = {
        "id": asaas_payment_id,
        "status": "CONFIRMED",
        "value": 0,
        "billingType": "PIX"
    }
    await process_payment_confirmed("PAYMENT_CONFIRMED", fake_payment)
    return {
        "success": True,
        "message": f"Pagamento simulado para {asaas_payment_id}"
    }
