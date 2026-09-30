from sqlalchemy import text
# ================================================================================
# ARQUIVO: afiliado_webhook.py
# PASTA:   ~/backend/afiliado/
# CAMINHO: visionlpr@vps60688:~/backend/afiliado/afiliado_webhook.py
# ================================================================================
# DESCRIÇÃO: Integração com webhook do Asaas para calcular comissões automaticamente
# ================================================================================

from sqlalchemy.orm import Session
import logging
from typing import Dict, Optional
from datetime import datetime

from .afiliado_service import calcular_comissao_afiliado

logger = logging.getLogger(__name__)


def processar_webhook_pagamento(db: Session, payload: Dict) -> Optional[Dict]:
    """
    Processa webhook de pagamento do Asaas e calcula comissão se aplicável
    
    Esta função deve ser chamada pelo webhook handler principal após um pagamento ser confirmado
    
    Eventos que acionam: PAYMENT_RECEIVED, PAYMENT_CONFIRMED
    
    Args:
        db: Sessão do banco de dados
        payload: Payload do webhook do Asaas
        
    Returns:
        Dict com resultado do processamento ou None se não aplicável
    """
    try:
        evento = payload.get('event')
        
        # Verificar se é evento de pagamento
        if evento not in ['PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED']:
            logger.info(f"Evento {evento} não requer processamento de comissão")
            return None
        
        payment = payload.get('payment', {})
        payment_id = payment.get('id')
        
        if not payment_id:
            logger.warning("Webhook sem payment_id")
            return None
        
        # Buscar cobrança pelo asaas_payment_id
        cobranca = db.execute(text("""
            SELECT id_cobranca, id_condominio, valor, status
            FROM cobrancas
            WHERE asaas_payment_id = :payment_id
            """), {"payment_id": payment_id}).fetchone()
        
        if not cobranca:
            logger.warning(f"Cobrança não encontrada para payment_id: {payment_id}")
            return None
        
        # Verificar se cobrança está paga
        if cobranca.status not in ['pago', 'RECEIVED', 'CONFIRMED']:
            logger.info(f"Cobrança {cobranca.id_cobranca} ainda não está paga")
            return None
        
        logger.info(f"Processando comissão para cobrança {cobranca.id_cobranca}")
        
        # Calcular comissão
        resultado = calcular_comissao_afiliado(db, cobranca.id_cobranca)
        
        if resultado:
            logger.info(f"Comissão processada: {resultado}")
            return {
                "success": True,
                "cobranca_id": cobranca.id_cobranca,
                "resultado": resultado
            }
        else:
            logger.info(f"Nenhuma comissão gerada para cobrança {cobranca.id_cobranca}")
            return {
                "success": True,
                "cobranca_id": cobranca.id_cobranca,
                "resultado": "sem_comissao"
            }
            
    except Exception as e:
        logger.error(f"Erro ao processar webhook de pagamento: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def processar_cancelamento_assinatura(db: Session, payload: Dict) -> Optional[Dict]:
    """
    Processa cancelamento de assinatura e cancela comissões pendentes
    
    Eventos que acionam: SUBSCRIPTION_CANCELLED
    """
    try:
        evento = payload.get('event')
        
        if evento != 'SUBSCRIPTION_CANCELLED':
            return None
        
        subscription = payload.get('subscription', {})
        customer_id = subscription.get('customer')
        
        if not customer_id:
            return None
        
        # Buscar condomínio
        condominio = db.execute(text("""
            SELECT id FROM condominios
            WHERE asaas_customer_id = :customer_id
            """), {"customer_id": customer_id}).fetchone()
        
        if not condominio:
            logger.warning(f"Condomínio não encontrado para customer_id: {customer_id}")
            return None
        
        # Cancelar comissões pendentes
        result = db.execute(text("""
            UPDATE afiliado_comissoes
            SET status = 'cancelada',
                motivo_bloqueio = 'assinatura_cancelada',
                observacoes = CONCAT(COALESCE(observacoes, ''), ' - Cancelada por cancelamento de assinatura em ', NOW())
            WHERE condominio_id = :condominio_id
              AND status = 'pendente'
            """), {"condominio_id": condominio.id})
        
        db.commit()
        
        linhas = result.rowcount
        logger.info(f"{linhas} comissões canceladas para condomínio {condominio.id}")
        
        return {
            "success": True,
            "condominio_id": condominio.id,
            "comissoes_canceladas": linhas
        }
        
    except Exception as e:
        logger.error(f"Erro ao processar cancelamento: {e}")
        db.rollback()
        return {
            "success": False,
            "error": str(e)
        }


# Adicionar ao webhook handler principal (financeiro_webhook.py):
"""
# No arquivo financeiro_webhook.py, adicionar:

from afiliado.afiliado_webhook import processar_webhook_pagamento, processar_cancelamento_assinatura

@router.post("/webhook/asaas")
async def webhook_asaas(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
        evento = payload.get('event')
        
        # ... processamento normal do webhook ...
        
        # ADICIONAR: Processar comissões de afiliado
        if evento in ['PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED']:
            processar_webhook_pagamento(db, payload)
        elif evento == 'SUBSCRIPTION_CANCELLED':
            processar_cancelamento_assinatura(db, payload)
        
        return {"received": True}
        
    except Exception as e:
        logger.error(f"Erro no webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
"""
