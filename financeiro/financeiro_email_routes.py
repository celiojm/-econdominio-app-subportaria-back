# ================================================================================
#  PATH: backend/financeiro/financeiro_email_routes.py
#  DESCRIPTION: Rotas de email para cobranças - COM VALIDAÇÕES
#  VERSÃO: 4.0 - Validações de status e vencimento
# ================================================================================

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import date, datetime
from pydantic import BaseModel
from typing import Optional
import logging

from app.database import get_db
from .financeiro_email import (
    enviar_email_nova_cobranca,
    enviar_email_pagamento_confirmado,
    enviar_email_lembrete
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ================================================================================
#  MODELOS PYDANTIC
# ================================================================================

class EnviarEmailRequest(BaseModel):
    id_cobranca: str
    tipo: str = "lembrete"  # nova, confirmacao, lembrete


class EmailTesteRequest(BaseModel):
    email: str


# ================================================================================
#  FUNÇÕES AUXILIARES
# ================================================================================

def gerar_link_pagamento(asaas_payment_id: str = None, id_cobranca: int = None) -> str:
    """
    Gera o link de pagamento correto.
    Se tiver asaas_payment_id, usa o link do Asaas.
    Caso contrário, usa link local.
    """
    if asaas_payment_id:
        # Remover prefixo 'pay_' do ID do Asaas
        asaas_id = asaas_payment_id.replace('pay_', '')
        return f"https://www.asaas.com/i/{asaas_id}"
    else:
        # Link local (fallback)
        return f"https://financeiro.econdominio.app.br/pagar/{id_cobranca}"


def verificar_status_cobranca(status: str, data_vencimento: date, data_pagamento: date = None) -> dict:
    """
    Verifica o status da cobrança e retorna ação a ser tomada.
    
    Regras:
    1. Se PAGA → pode enviar email de confirmação
    2. Se PENDENTE + NÃO VENCIDA → pode enviar lembrete
    3. Se PENDENTE + VENCIDA → NÃO enviar, solicitar nova cobrança
    
    Returns:
        {
            "pode_enviar": bool,
            "tipo_email": str,  # "confirmacao", "lembrete", None
            "motivo_bloqueio": str,  # Se não pode enviar
            "dias_atraso": int
        }
    """
    hoje = date.today()
    
    # Status que indicam pagamento
    status_pagos = ['pago', 'RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH']
    
    # Cobrança paga
    if status.upper() in [s.upper() for s in status_pagos]:
        return {
            "pode_enviar": True,
            "tipo_email": "confirmacao",
            "motivo_bloqueio": None,
            "dias_atraso": 0,
            "mensagem_operador": None
        }
    
    # Cobrança pendente
    if not data_vencimento:
        # Sem data de vencimento, pode enviar
        return {
            "pode_enviar": True,
            "tipo_email": "lembrete",
            "motivo_bloqueio": None,
            "dias_atraso": 0,
            "mensagem_operador": None
        }
    
    # Calcular dias de atraso
    dias_atraso = (hoje - data_vencimento).days
    
    if dias_atraso > 0:
        # VENCIDA - NÃO ENVIAR
        return {
            "pode_enviar": False,
            "tipo_email": None,
            "motivo_bloqueio": f"Cobrança vencida há {dias_atraso} dia(s)",
            "dias_atraso": dias_atraso,
            "mensagem_operador": f"⚠️ Esta cobrança está vencida há {dias_atraso} dia(s) (vencimento: {data_vencimento.strftime('%d/%m/%Y')}). Por favor, gere uma nova cobrança para este condomínio."
        }
    else:
        # NÃO VENCIDA - PODE ENVIAR
        dias_restantes = abs(dias_atraso)
        return {
            "pode_enviar": True,
            "tipo_email": "lembrete",
            "motivo_bloqueio": None,
            "dias_atraso": 0,
            "dias_restantes": dias_restantes,
            "mensagem_operador": None
        }


# ================================================================================
#  ROTAS DE EMAIL
# ================================================================================

@router.post("/reenviar/{id_cobranca}")
async def reenviar_email_cobranca(id_cobranca: str, db: Session = Depends(get_db)):
    """
    Reenvia email de cobrança com validações:
    - Se PAGA: envia confirmação de pagamento
    - Se PENDENTE + NÃO VENCIDA: envia lembrete
    - Se PENDENTE + VENCIDA: NÃO envia, retorna mensagem ao operador
    """
    try:
        # Buscar cobrança
        result = db.execute(text("""
            SELECT 
                c.id_cobranca,
                c.asaas_payment_id,
                c.id_condominio,
                c.valor,
                c.data_vencimento,
                c.data_pagamento,
                c.status,
                c.descricao,
                c.forma_pagamento,
                cond.nome as nome_condominio,
                cond.cobranca_email,
                cond.email_financeiro,
                cond.email,
                cond.cobranca_whats,
                cond.validade_ate
            FROM cobrancas c
            JOIN condominios cond ON cond.id = c.id_condominio
            WHERE c.asaas_payment_id = :id OR c.id_cobranca = :id_int
            LIMIT 1
        """), {
            "id": id_cobranca,
            "id_int": int(id_cobranca) if id_cobranca.isdigit() else 0
        })
        
        cobranca = result.fetchone()
        
        if not cobranca:
            raise HTTPException(status_code=404, detail="Cobrança não encontrada")
        
        # Email: prioridade cobranca_email > email_financeiro > email
        email_destino = cobranca.cobranca_email or cobranca.email_financeiro or cobranca.email
        
        if not email_destino:
            raise HTTPException(status_code=400, detail="Condomínio não possui email cadastrado")
        
        # VALIDAR STATUS E VENCIMENTO
        validacao = verificar_status_cobranca(
            cobranca.status,
            cobranca.data_vencimento,
            cobranca.data_pagamento
        )
        
        # Se não pode enviar (cobrança vencida)
        if not validacao["pode_enviar"]:
            logger.warning(f"⚠️ Tentativa de enviar email para cobrança vencida: {id_cobranca}")
            return {
                "success": False,
                "pode_enviar": False,
                "motivo": validacao["motivo_bloqueio"],
                "mensagem_operador": validacao["mensagem_operador"],
                "dias_atraso": validacao["dias_atraso"],
                "data_vencimento": cobranca.data_vencimento.strftime("%d/%m/%Y") if cobranca.data_vencimento else None,
                "status": cobranca.status
            }
        
        # Formatar datas
        vencimento_fmt = cobranca.data_vencimento.strftime("%d/%m/%Y") if cobranca.data_vencimento else "-"
        pagamento_fmt = cobranca.data_pagamento.strftime("%d/%m/%Y") if cobranca.data_pagamento else "-"
        
        # Gerar link de pagamento CORRETO (Asaas)
        link_pagamento = gerar_link_pagamento(cobranca.asaas_payment_id, cobranca.id_cobranca)
        
        # Enviar email conforme tipo
        if validacao["tipo_email"] == "confirmacao":
            # COBRANÇA PAGA - Email de confirmação
            
            # Calcular validade restante
            dias_restantes = 0
            if cobranca.validade_ate:
                diff = (cobranca.validade_ate - date.today()).days
                if diff > 0:
                    dias_restantes = diff
            
            validade_fmt = cobranca.validade_ate.strftime("%d/%m/%Y") if cobranca.validade_ate else "-"
            
            resultado = enviar_email_pagamento_confirmado(
                email_destino=email_destino,
                nome_condominio=cobranca.nome_condominio,
                valor=float(cobranca.valor),
                data_pagamento=pagamento_fmt,
                forma_pagamento=cobranca.forma_pagamento or "PIX",
                descricao=cobranca.descricao or "Mensalidade",
                validade_ate=validade_fmt,
                dias_restantes=dias_restantes
            )
            
            tipo_enviado = "confirmação de pagamento"
            
        else:
            # COBRANÇA PENDENTE - Email de lembrete
            resultado = enviar_email_lembrete(
                email_destino=email_destino,
                nome_condominio=cobranca.nome_condominio,
                valor=float(cobranca.valor),
                vencimento=vencimento_fmt,
                descricao=cobranca.descricao or "Mensalidade",
                link_pagamento=link_pagamento,
                dias_atraso=0  # Não está vencida
            )
            
            tipo_enviado = "lembrete"
        
        if resultado.get("success"):
            logger.info(f"✅ Email de {tipo_enviado} enviado para {email_destino} - Cobrança: {id_cobranca}")
            logger.info(f"🔗 Link de pagamento: {link_pagamento}")
            return {
                "success": True,
                "pode_enviar": True,
                "message": f"Email de {tipo_enviado} enviado para {email_destino}",
                "email_destino": email_destino,
                "link_pagamento": link_pagamento,
                "tipo_email": tipo_enviado,
                "status_cobranca": cobranca.status,
                "data_vencimento": vencimento_fmt,
                "data_pagamento": pagamento_fmt if cobranca.data_pagamento else None
            }
        else:
            logger.error(f"❌ Erro ao enviar email: {resultado.get('message')}")
            raise HTTPException(status_code=500, detail=resultado.get("message"))
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao reenviar email: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao enviar email: {str(e)}")


@router.post("/enviar")
async def enviar_email_cobranca(request: EnviarEmailRequest, db: Session = Depends(get_db)):
    """
    Envia email de cobrança (nova, confirmação ou lembrete)
    COM VALIDAÇÕES de status e vencimento
    """
    try:
        # Buscar cobrança
        result = db.execute(text("""
            SELECT 
                c.id_cobranca,
                c.asaas_payment_id,
                c.id_condominio,
                c.valor,
                c.data_vencimento,
                c.data_pagamento,
                c.status,
                c.descricao,
                c.forma_pagamento,
                cond.nome as nome_condominio,
                cond.cobranca_email,
                cond.email_financeiro,
                cond.email,
                cond.cobranca_whats,
                cond.validade_ate
            FROM cobrancas c
            JOIN condominios cond ON cond.id = c.id_condominio
            WHERE c.asaas_payment_id = :id OR c.id_cobranca = :id_int
            LIMIT 1
        """), {
            "id": request.id_cobranca,
            "id_int": int(request.id_cobranca) if request.id_cobranca.isdigit() else 0
        })
        
        cobranca = result.fetchone()
        
        if not cobranca:
            raise HTTPException(status_code=404, detail="Cobrança não encontrada")
        
        # Email destino
        email_destino = cobranca.cobranca_email or cobranca.email_financeiro or cobranca.email
        
        if not email_destino:
            raise HTTPException(status_code=400, detail="Condomínio não possui email cadastrado")
        
        # Se tipo não for "nova", validar status e vencimento
        if request.tipo != "nova":
            validacao = verificar_status_cobranca(
                cobranca.status,
                cobranca.data_vencimento,
                cobranca.data_pagamento
            )
            
            # Se não pode enviar (cobrança vencida)
            if not validacao["pode_enviar"]:
                logger.warning(f"⚠️ Tentativa de enviar email para cobrança vencida: {request.id_cobranca}")
                return {
                    "success": False,
                    "pode_enviar": False,
                    "motivo": validacao["motivo_bloqueio"],
                    "mensagem_operador": validacao["mensagem_operador"],
                    "dias_atraso": validacao["dias_atraso"],
                    "data_vencimento": cobranca.data_vencimento.strftime("%d/%m/%Y") if cobranca.data_vencimento else None
                }
        
        # Formatar datas
        vencimento_fmt = cobranca.data_vencimento.strftime("%d/%m/%Y") if cobranca.data_vencimento else "-"
        pagamento_fmt = cobranca.data_pagamento.strftime("%d/%m/%Y") if cobranca.data_pagamento else "-"
        
        # Gerar link de pagamento CORRETO (Asaas)
        link_pagamento = gerar_link_pagamento(cobranca.asaas_payment_id, cobranca.id_cobranca)
        
        # Enviar email conforme tipo
        if request.tipo == "nova":
            resultado = enviar_email_nova_cobranca(
                email_destino=email_destino,
                nome_condominio=cobranca.nome_condominio,
                valor=float(cobranca.valor),
                vencimento=vencimento_fmt,
                descricao=cobranca.descricao or "Mensalidade",
                link_pagamento=link_pagamento
            )
        elif request.tipo == "confirmacao":
            # Calcular validade restante
            dias_restantes = 0
            if cobranca.validade_ate:
                diff = (cobranca.validade_ate - date.today()).days
                if diff > 0:
                    dias_restantes = diff
            
            validade_fmt = cobranca.validade_ate.strftime("%d/%m/%Y") if cobranca.validade_ate else "-"
            
            resultado = enviar_email_pagamento_confirmado(
                email_destino=email_destino,
                nome_condominio=cobranca.nome_condominio,
                valor=float(cobranca.valor),
                data_pagamento=pagamento_fmt,
                forma_pagamento=cobranca.forma_pagamento or "PIX",
                descricao=cobranca.descricao or "Mensalidade",
                validade_ate=validade_fmt,
                dias_restantes=dias_restantes
            )
        else:  # lembrete (default)
            resultado = enviar_email_lembrete(
                email_destino=email_destino,
                nome_condominio=cobranca.nome_condominio,
                valor=float(cobranca.valor),
                vencimento=vencimento_fmt,
                descricao=cobranca.descricao or "Mensalidade",
                link_pagamento=link_pagamento,
                dias_atraso=0
            )
        
        if resultado.get("success"):
            logger.info(f"✅ Email enviado para {email_destino} - Tipo: {request.tipo}")
            logger.info(f"🔗 Link de pagamento: {link_pagamento}")
            return {
                "success": True,
                "pode_enviar": True,
                "message": f"Email enviado para {email_destino}",
                "email_destino": email_destino,
                "link_pagamento": link_pagamento,
                "tipo_email": request.tipo
            }
        else:
            logger.error(f"❌ Erro ao enviar email: {resultado.get('message')}")
            raise HTTPException(status_code=500, detail=resultado.get("message"))
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao enviar email: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao enviar email: {str(e)}")


@router.get("/email/teste")
async def testar_email(email: str):
    """
    Endpoint de teste de email
    """
    try:
        from .financeiro_email import enviar_email
        
        resultado = enviar_email(
            destinatario=email,
            assunto="🧪 Teste de Email - Sistema Financeiro",
            corpo_html=f"""
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <div style="background: #4CAF50; color: white; padding: 20px; border-radius: 10px;">
                    <h1>✅ Teste de Email</h1>
                    <p>Se você está vendo isso, está funcionando!</p>
                </div>
                <div style="margin-top: 20px; padding: 20px; background: #f5f5f5; border-radius: 5px;">
                    <p><strong>Enviado em:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>
                    <p><strong>Para:</strong> {email}</p>
                </div>
            </body>
            </html>
            """,
            corpo_texto=f"Teste de Email\n\nSe você está vendo isso, está funcionando!\n\nEnviado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
        )
        
        if resultado.get("success"):
            return {"success": True, "message": f"Email enviado para {email}"}
        else:
            raise HTTPException(status_code=500, detail=resultado.get("message"))
            
    except Exception as e:
        logger.error(f"❌ Erro no teste de email: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================================
#  ROTAS DE INFORMAÇÕES
# ================================================================================

@router.get("/email/status")
async def status_email():
    """
    Retorna status do sistema de email
    """
    import os
    
    config = {
        "smtp_server": os.getenv("EMAIL_SMTP_SERVER", "mail.econdominio.com.br"),
        "smtp_port": os.getenv("EMAIL_SMTP_PORT", "465"),
        "username": os.getenv("EMAIL_SMTP_USERNAME", "financeiro@econdominio.com.br"),
        "from_email": os.getenv("EMAIL_FROM_ADDRESS", "financeiro@econdominio.com.br"),
        "has_password": bool(os.getenv("EMAIL_SMTP_PASSWORD"))
    }
    
    return {
        "status": "ok",
        "config": config,
        "timestamp": datetime.now().isoformat()
    }


@router.get("/validar/{id_cobranca}")
async def validar_cobranca_email(id_cobranca: str, db: Session = Depends(get_db)):
    """
    Valida se uma cobrança pode receber email
    Útil para o frontend saber se deve habilitar o botão de email
    """
    try:
        # Buscar cobrança
        result = db.execute(text("""
            SELECT 
                c.id_cobranca,
                c.status,
                c.data_vencimento,
                c.data_pagamento,
                cond.nome as nome_condominio,
                cond.cobranca_email,
                cond.email_financeiro,
                cond.email
            FROM cobrancas c
            JOIN condominios cond ON cond.id = c.id_condominio
            WHERE c.asaas_payment_id = :id OR c.id_cobranca = :id_int
            LIMIT 1
        """), {
            "id": id_cobranca,
            "id_int": int(id_cobranca) if id_cobranca.isdigit() else 0
        })
        
        cobranca = result.fetchone()
        
        if not cobranca:
            return {
                "pode_enviar": False,
                "motivo": "Cobrança não encontrada"
            }
        
        # Verificar email
        email_destino = cobranca.cobranca_email or cobranca.email_financeiro or cobranca.email
        if not email_destino:
            return {
                "pode_enviar": False,
                "motivo": "Condomínio não possui email cadastrado"
            }
        
        # Validar status e vencimento
        validacao = verificar_status_cobranca(
            cobranca.status,
            cobranca.data_vencimento,
            cobranca.data_pagamento
        )
        
        return {
            "pode_enviar": validacao["pode_enviar"],
            "tipo_email": validacao["tipo_email"],
            "motivo": validacao["motivo_bloqueio"],
            "mensagem_operador": validacao["mensagem_operador"],
            "dias_atraso": validacao.get("dias_atraso", 0),
            "status": cobranca.status,
            "data_vencimento": cobranca.data_vencimento.strftime("%d/%m/%Y") if cobranca.data_vencimento else None,
            "data_pagamento": cobranca.data_pagamento.strftime("%d/%m/%Y") if cobranca.data_pagamento else None,
            "email_destino": email_destino
        }
        
    except Exception as e:
        logger.error(f"❌ Erro ao validar cobrança: {e}")
        return {
            "pode_enviar": False,
            "motivo": f"Erro ao validar: {str(e)}"
        }
