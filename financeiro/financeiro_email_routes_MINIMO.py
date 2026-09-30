"""Rotas de email - VERSÃO MÍNIMA"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from financeiro.financeiro_auth import get_current_user
from financeiro.financeiro_email import EmailService

router = APIRouter(prefix="/email", tags=["Email"])
email_service = EmailService()


@router.post("/reenviar/{cobranca_id}")
async def reenviar_email(
    cobranca_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Envia email de cobrança"""
    try:
        # Buscar dados via SQL direto
        sql = text("""
            SELECT 
                c.id, c.valor, c.vencimento, c.descricao,
                c.link_boleto, c.qrcode_pix, c.condominio_id,
                cond.nome_condominio, cond.cobranca_email
            FROM Cobranca c
            JOIN Condominio cond ON c.condominio_id = cond.id
            WHERE c.id = :id
        """)
        
        result = db.execute(sql, {"id": cobranca_id}).fetchone()
        
        if not result:
            raise HTTPException(404, "Cobrança não encontrada")
        
        email_destino = result[8]
        if not email_destino:
            raise HTTPException(400, "Email não cadastrado")
        
        # Enviar
        dados = {
            "destinatario": email_destino,
            "destinatario_nome": result[7],
            "valor": float(result[1]),
            "vencimento": result[2],
            "descricao": result[3] or "Cobrança INFORSEG",
            "link_boleto": result[4],
            "link_pix": result[5]
        }
        
        resultado = await email_service.enviar_email_cobranca(dados)
        
        if resultado.get("success"):
            return {
                "success": True,
                "message": "Email enviado",
                "email_destino": email_destino
            }
        else:
            raise HTTPException(500, resultado.get("error"))
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
