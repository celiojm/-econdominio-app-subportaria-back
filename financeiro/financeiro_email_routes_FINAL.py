#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ROTAS DE EMAIL - Módulo Financeiro
Arquivo: financeiro_email_routes.py
Adicionar ao sistema financeiro para envio de emails de cobrança
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import date, datetime
from typing import Optional
import logging
import os

# Importar o módulo de email
from financeiro_email import (
    enviar_email_cobranca,
    enviar_email_cobranca_vencida,
    enviar_email_pagamento_confirmado
)

logger = logging.getLogger(__name__)

# Criar router
router = APIRouter(prefix="/email", tags=["Email"])


def get_db():
    """
    Função para obter sessão do banco de dados
    AJUSTAR conforme seu sistema usa o banco
    """
    # Se você usa algo como:
    # from app.database import SessionLocal
    # db = SessionLocal()
    # try:
    #     yield db
    # finally:
    #     db.close()
    pass


# ============================================================================
# ENDPOINT PRINCIPAL - Reenviar email de cobrança
# ============================================================================

@router.post("/reenviar/{cobranca_id}")
async def reenviar_email_cobranca(cobranca_id: str):
    """
    Reenvia email de cobrança pelo ID
    
    Este é o endpoint que o botão "Email" do frontend chama!
    
    Endpoint: POST /email/reenviar/{cobranca_id}
    
    Args:
        cobranca_id: ID da cobrança (ID do Asaas ou ID local)
        
    Returns:
        {
            "success": true,
            "message": "Email enviado com sucesso",
            "email_destino": "cliente@email.com"
        }
    """
    try:
        logger.info(f"📧 Reenviar email para cobrança: {cobranca_id}")
        
        # ========================================
        # OPÇÃO 1: Buscar dados do banco de dados
        # ========================================
        # Se você tem as cobranças no banco local:
        """
        from financeiro_modelos import Cobranca
        
        cobranca = db.query(Cobranca).filter(
            (Cobranca.id == cobranca_id) | (Cobranca.asaas_id == cobranca_id)
        ).first()
        
        if not cobranca:
            raise HTTPException(404, f"Cobrança {cobranca_id} não encontrada")
        
        # Buscar dados do cliente/condomínio
        condominio = cobranca.condominio
        email_destino = condominio.email_financeiro or condominio.email
        """
        
        # ========================================
        # OPÇÃO 2: Buscar direto do Asaas (IMPLEMENTAÇÃO ATUAL)
        # ========================================
        from financeiro_asaas_service import AsaasService
        
        asaas = AsaasService()
        
        # Buscar cobrança no Asaas
        try:
            payment = asaas.get_payment(cobranca_id)
        except Exception as e:
            logger.error(f"Erro ao buscar cobrança no Asaas: {e}")
            raise HTTPException(404, f"Cobrança não encontrada no Asaas: {cobranca_id}")
        
        # Buscar dados do cliente
        try:
            customer = asaas.get_customer(payment['customer'])
        except Exception as e:
            logger.error(f"Erro ao buscar cliente no Asaas: {e}")
            raise HTTPException(404, f"Cliente não encontrado")
        
        # Determinar email de destino
        # Prioridade: email_financeiro > email do customer
        email_destino = customer.get('email')
        
        # OPCIONAL: Buscar email_financeiro do banco se disponível
        # try:
        #     from financeiro_modelos import Condominio
        #     cond = db.query(Condominio).filter_by(asaas_customer_id=payment['customer']).first()
        #     if cond and cond.email_financeiro:
        #         email_destino = cond.email_financeiro
        # except:
        #     pass
        
        if not email_destino:
            raise HTTPException(400, "Email do cliente não encontrado")
        
        # Preparar dados
        nome_cliente = customer.get('name', 'Cliente')
        condominio_nome = customer.get('name', 'Condomínio')  # ou buscar do banco
        valor = float(payment.get('value', 0))
        vencimento = datetime.strptime(payment['dueDate'], "%Y-%m-%d").date()
        link_pagamento = payment.get('invoiceUrl') or payment.get('bankSlipUrl', '#')
        descricao = payment.get('description', '')
        status = payment.get('status', 'PENDING')
        
        # ========================================
        # VERIFICAR SE ESTÁ VENCIDA
        # ========================================
        hoje = date.today()
        
        if vencimento < hoje and status in ['PENDING', 'OVERDUE']:
            # Cobrança vencida - enviar email de urgência
            dias_atraso = (hoje - vencimento).days
            
            # Calcular valor com juros (2% multa + 0.033% ao dia)
            multa = valor * 0.02
            juros_diario = valor * 0.00033 * dias_atraso
            valor_com_juros = valor + multa + juros_diario
            
            logger.info(f"Enviando email de cobrança VENCIDA (atraso: {dias_atraso} dias)")
            
            sucesso = enviar_email_cobranca_vencida(
                email=email_destino,
                nome=nome_cliente,
                condominio=condominio_nome,
                valor=valor,
                valor_com_juros=valor_com_juros,
                vencimento=vencimento,
                dias_atraso=dias_atraso,
                link=link_pagamento,
                codigo=cobranca_id
            )
            
            tipo_email = "cobrança vencida"
            
        else:
            # Cobrança normal ou paga
            logger.info(f"Enviando email de cobrança NORMAL")
            
            sucesso = enviar_email_cobranca(
                email=email_destino,
                nome=nome_cliente,
                condominio=condominio_nome,
                valor=valor,
                vencimento=vencimento,
                link=link_pagamento,
                codigo=cobranca_id,
                descricao=descricao
            )
            
            tipo_email = "cobrança"
        
        # ========================================
        # RETORNAR RESULTADO
        # ========================================
        if sucesso:
            logger.info(f"✅ Email de {tipo_email} enviado para {email_destino}")
            
            return {
                "success": True,
                "message": f"Email de {tipo_email} enviado com sucesso!",
                "email_destino": email_destino,
                "tipo": tipo_email
            }
        else:
            logger.error(f"❌ Falha ao enviar email para {email_destino}")
            raise HTTPException(500, "Falha ao enviar email. Verifique os logs.")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao processar reenvio de email: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro ao processar envio de email: {str(e)}")


# ============================================================================
# ENDPOINT TESTE
# ============================================================================

@router.get("/teste")
async def testar_email():
    """
    Endpoint de teste para verificar se o módulo de email está funcionando
    
    Acesse: GET /email/teste
    """
    try:
        from datetime import timedelta
        
        logger.info("📧 Enviando email de teste...")
        
        sucesso = enviar_email_cobranca(
            email="celiojm@gmail.com",
            nome="Teste API Email",
            condominio="Econdominio Teste",
            valor=100.00,
            vencimento=date.today() + timedelta(days=5),
            link="https://exemplo.com/pagamento",
            codigo="TEST-EMAIL-API",
            descricao="Email de teste via endpoint /email/teste"
        )
        
        if sucesso:
            return {
                "success": True,
                "message": "✅ Email de teste enviado com sucesso!",
                "email_destino": "celiojm@gmail.com",
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {
                "success": False,
                "message": "❌ Falha ao enviar email de teste",
                "timestamp": datetime.now().isoformat()
            }
        
    except Exception as e:
        logger.error(f"Erro no teste de email: {e}")
        return {
            "success": False,
            "message": f"❌ Erro: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }


# ============================================================================
# ENDPOINT ADICIONAL - Status da configuração de email
# ============================================================================

@router.get("/config")
async def verificar_config_email():
    """
    Verifica se as configurações de email estão corretas
    
    Acesse: GET /email/config
    """
    try:
        from financeiro_email import EmailService
        
        service = EmailService()
        
        return {
            "success": True,
            "config": {
                "smtp_server": service.smtp_server,
                "smtp_port": service.smtp_port,
                "from_email": service.from_email,
                "from_name": service.from_name,
                "use_ssl": service.use_ssl
            },
            "message": "Configurações carregadas com sucesso"
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Erro ao carregar configurações: {str(e)}"
        }
