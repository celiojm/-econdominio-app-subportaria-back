"""Rotas de email - VERSÃO FINAL CORRIGIDA"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from app.database import get_db
from financeiro.financeiro_email import EmailService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/email", tags=["Email"])
email_service = EmailService()


@router.post("/reenviar/{cobranca_id}")
async def reenviar_email(
    cobranca_id: int,
    db: Session = Depends(get_db)
):
    """Envia email de cobrança"""
    try:
        logger.info(f"📧 Enviando email para cobrança {cobranca_id}")
        
        # Buscar dados via SQL - CAMPOS CORRETOS
        sql = text("""
            SELECT 
                c.id_cobranca, c.valor, c.data_vencimento, c.descricao,
                c.asaas_payment_id, c.id_condominio,
                cond.nome, cond.cobranca_email
            FROM cobrancas c
            JOIN condominios cond ON c.id_condominio = cond.id
            WHERE c.id_cobranca = :id
        """)
        
        result = db.execute(sql, {"id": cobranca_id}).fetchone()
        
        if not result:
            raise HTTPException(404, "Cobrança não encontrada")
        
        email_destino = result[7]  # cobranca_email
        if not email_destino:
            raise HTTPException(400, "Email não cadastrado no condomínio")
        
        # Preparar dados
        dados = {
            "destinatario": email_destino,
            "destinatario_nome": result[6] or "Cliente",  # nome
            "valor": float(result[1]) if result[1] else 0.0,
            "vencimento": result[2],  # data_vencimento
            "descricao": result[3] or "Cobrança INFORSEG",
            "link_boleto": None,
            "link_pix": None
        }
        
        logger.info(f"📧 Enviando para {email_destino}")
        
        # Enviar email
        resultado = await email_service.enviar_email_cobranca(dados)
        
        if resultado.get("success"):
            logger.info(f"✅ Email enviado!")
            return {
                "success": True,
                "message": "Email enviado com sucesso",
                "email_destino": email_destino
            }
        else:
            logger.error(f"❌ Erro: {resultado.get('error')}")
            raise HTTPException(500, resultado.get("error", "Erro ao enviar"))
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro: {str(e)}", exc_info=True)
        raise HTTPException(500, str(e))


@router.post("/teste")
async def testar_email(email_destino: str):
    """Envia email de teste"""
    try:
        logger.info(f"🧪 Email de teste para {email_destino}")
        resultado = await email_service.enviar_email_teste(email_destino)
        
        if resultado.get("success"):
            return {"success": True, "message": "Email enviado"}
        else:
            raise HTTPException(500, resultado.get("error"))
    except Exception as e:
        raise HTTPException(500, str(e))
