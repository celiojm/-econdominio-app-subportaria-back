# ================================================================================
#  PATH: backend/financeiro/financeiro_whatsapp_routes.py
#  DESCRIPTION: Rotas de WhatsApp para cobranças - ENVIO DIRETO VIA Z-API
#  VERSÃO: 2.0 - Integrado com Z-API para envio automático
# ================================================================================

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import date, datetime
from pydantic import BaseModel
from typing import Optional
import logging
import os
import requests

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# ================================================================================
#  CONFIGURAÇÕES Z-API
# ================================================================================

def get_zapi_config():
    """Retorna configurações Z-API do ambiente"""
    return {
        "instance_id": os.getenv("ZAPI_INSTANCE_ID", ""),
        "token": os.getenv("ZAPI_TOKEN", ""),
        "client_token": os.getenv("ZAPI_CLIENT_TOKEN", ""),
        "api_url": os.getenv("ZAPI_API_URL", "https://api.z-api.io")
    }


def get_zapi_headers():
    """Retorna headers para requisições Z-API"""
    config = get_zapi_config()
    headers = {"Content-Type": "application/json"}
    if config.get('client_token'):
        headers["Client-Token"] = config['client_token']
    return headers


def enviar_mensagem_zapi(telefone: str, mensagem: str) -> dict:
    """
    Envia mensagem via Z-API diretamente
    Retorna: {"success": True/False, "error": None/str}
    """
    try:
        config = get_zapi_config()
        url = f"{config['api_url']}/instances/{config['instance_id']}/token/{config['token']}/send-text"
        
        headers = get_zapi_headers()
        
        # Garantir que telefone tenha código do país
        telefone_limpo = ''.join(filter(str.isdigit, telefone))
        if len(telefone_limpo) == 11:  # DDD + número
            telefone_limpo = "55" + telefone_limpo
        elif len(telefone_limpo) == 10:  # DDD + número fixo
            telefone_limpo = "55" + telefone_limpo
        
        data = {
            "phone": telefone_limpo,
            "message": mensagem
        }
        
        logger.info(f"📱 Enviando WhatsApp via Z-API para {telefone_limpo}")
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            logger.info(f"✅ WhatsApp enviado com sucesso para {telefone_limpo}")
            return {"success": True, "error": None, "response": result}
        else:
            error_msg = f"HTTP {response.status_code}: {response.text}"
            logger.error(f"❌ Erro Z-API: {error_msg}")
            return {"success": False, "error": error_msg}
            
    except requests.exceptions.Timeout:
        logger.error("❌ Timeout ao conectar com Z-API")
        return {"success": False, "error": "Timeout ao conectar com Z-API"}
    except Exception as e:
        logger.error(f"❌ Erro ao enviar via Z-API: {e}")
        return {"success": False, "error": str(e)}


# ================================================================================
#  MODELOS PYDANTIC
# ================================================================================

class EnviarWhatsAppRequest(BaseModel):
    id_cobranca: str
    telefone: Optional[str] = None


# ================================================================================
#  FUNÇÕES AUXILIARES
# ================================================================================

def gerar_link_pagamento(asaas_payment_id: str = None, id_cobranca: int = None) -> str:
    """Gera o link de pagamento correto"""
    if asaas_payment_id:
        asaas_id = asaas_payment_id.replace('pay_', '')
        return f"https://www.asaas.com/i/{asaas_id}"
    else:
        return f"https://financeiro.econdominio.app.br/pagar/{id_cobranca}"


def limpar_telefone(telefone: str) -> str:
    """Remove caracteres não numéricos do telefone"""
    if not telefone:
        return ""
    return ''.join(filter(str.isdigit, telefone))


def verificar_status_cobranca(status: str, data_vencimento: date, data_pagamento: date = None) -> dict:
    """
    Verifica o status da cobrança e retorna ação a ser tomada.
    """
    hoje = date.today()
    status_pagos = ['pago', 'RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH']

    if status.upper() in [s.upper() for s in status_pagos]:
        return {
            "pode_enviar": True,
            "tipo_mensagem": "confirmacao",
            "motivo_bloqueio": None,
            "dias_atraso": 0,
            "mensagem_operador": None
        }

    if not data_vencimento:
        return {
            "pode_enviar": True,
            "tipo_mensagem": "lembrete",
            "motivo_bloqueio": None,
            "dias_atraso": 0,
            "mensagem_operador": None
        }

    dias_atraso = (hoje - data_vencimento).days

    if dias_atraso > 0:
        return {
            "pode_enviar": False,
            "tipo_mensagem": None,
            "motivo_bloqueio": f"Cobrança vencida há {dias_atraso} dia(s)",
            "dias_atraso": dias_atraso,
            "mensagem_operador": f"⚠️ Esta cobrança está vencida há {dias_atraso} dia(s) (vencimento: {data_vencimento.strftime('%d/%m/%Y')}). Por favor, gere uma nova cobrança para este condomínio."
        }
    else:
        return {
            "pode_enviar": True,
            "tipo_mensagem": "lembrete",
            "motivo_bloqueio": None,
            "dias_atraso": 0,
            "mensagem_operador": None
        }


def gerar_mensagem_whatsapp(tipo: str, dados: dict) -> str:
    """Gera mensagem do WhatsApp conforme tipo"""
    if tipo == "confirmacao":
        mensagem = f"""✅ *Pagamento Confirmado!*

Olá! Confirmamos o recebimento do seu pagamento para *{dados['nome_condominio']}*.

📋 *Detalhes:*
- Valor Pago: R$ {dados['valor']:.2f}
- Data: {dados['data_pagamento']}
- Forma: {dados['forma_pagamento'].upper()}

{dados.get('mensagem_validade', '')}

Agradecemos pela confiança! 🙏"""
    else:
        mensagem = f"""🔔 *Lembrete de Cobrança*

Olá! Segue lembrete da cobrança pendente para *{dados['nome_condominio']}*.

📋 *Detalhes:*
- Descrição: {dados['descricao']}
- Valor: R$ {dados['valor']:.2f}
- Vencimento: {dados['vencimento']}

💳 *Pagar Agora:*
{dados['link_pagamento']}

{dados.get('mensagem_atraso', '')}"""

    return mensagem


# ================================================================================
#  ROTAS DE WHATSAPP
# ================================================================================

@router.post("/whatsapp/enviar/{id_cobranca}")
async def enviar_whatsapp_cobranca(
    id_cobranca: str,
    request: Optional[EnviarWhatsAppRequest] = None,
    db: Session = Depends(get_db)
):
    """
    Envia WhatsApp de cobrança DIRETAMENTE via Z-API
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
                cond.cobranca_whats,
                cond.telefone,
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

        # Telefone: usa o informado no request OU do banco
        telefone = None
        if request and request.telefone:
            telefone = request.telefone
        else:
            telefone = cobranca.cobranca_whats or cobranca.telefone

        if not telefone:
            raise HTTPException(status_code=400, detail="Condomínio não possui WhatsApp cadastrado")

        telefone_limpo = limpar_telefone(telefone)

        if not telefone_limpo:
            raise HTTPException(status_code=400, detail="Telefone inválido")

        # VALIDAR STATUS E VENCIMENTO
        validacao = verificar_status_cobranca(
            cobranca.status,
            cobranca.data_vencimento,
            cobranca.data_pagamento
        )

        if not validacao["pode_enviar"]:
            logger.warning(f"⚠️ Tentativa de enviar WhatsApp para cobrança vencida: {id_cobranca}")
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

        # Gerar link de pagamento
        link_pagamento = gerar_link_pagamento(cobranca.asaas_payment_id, cobranca.id_cobranca)

        # Preparar dados para mensagem
        dados_mensagem = {
            "nome_condominio": cobranca.nome_condominio,
            "valor": float(cobranca.valor),
            "descricao": cobranca.descricao or "Mensalidade",
            "link_pagamento": link_pagamento
        }

        if validacao["tipo_mensagem"] == "confirmacao":
            dados_mensagem.update({
                "data_pagamento": pagamento_fmt,
                "forma_pagamento": cobranca.forma_pagamento or "PIX"
            })
            if cobranca.validade_ate:
                dias_restantes = (cobranca.validade_ate - date.today()).days
                if dias_restantes > 0:
                    validade_fmt = cobranca.validade_ate.strftime("%d/%m/%Y")
                    dados_mensagem["mensagem_validade"] = f"✅ Assinatura válida até: {validade_fmt} ({dias_restantes} dias)"
                else:
                    dados_mensagem["mensagem_validade"] = "⚠️ Assinatura vencida. Por favor, renove."
            tipo_enviado = "confirmação de pagamento"
        else:
            dados_mensagem["vencimento"] = vencimento_fmt
            tipo_enviado = "lembrete"

        # Gerar texto da mensagem
        mensagem = gerar_mensagem_whatsapp(validacao["tipo_mensagem"], dados_mensagem)

        # =====================================================================
        # ENVIAR VIA Z-API (DIRETO!)
        # =====================================================================
        resultado_envio = enviar_mensagem_zapi(telefone_limpo, mensagem)

        if resultado_envio["success"]:
            logger.info(f"✅ WhatsApp enviado com sucesso para {telefone_limpo} - Cobrança: {id_cobranca}")
            return {
                "success": True,
                "enviado": True,
                "pode_enviar": True,
                "telefone": telefone,
                "telefone_limpo": telefone_limpo,
                "tipo_mensagem": tipo_enviado,
                "status_cobranca": cobranca.status,
                "data_vencimento": vencimento_fmt,
                "data_pagamento": pagamento_fmt if cobranca.data_pagamento else None,
                "link_pagamento": link_pagamento,
                "mensagem": "WhatsApp enviado com sucesso!"
            }
        else:
            logger.error(f"❌ Falha ao enviar WhatsApp: {resultado_envio['error']}")
            return {
                "success": False,
                "enviado": False,
                "pode_enviar": True,
                "erro": resultado_envio["error"],
                "telefone": telefone,
                "telefone_limpo": telefone_limpo,
                "mensagem_operador": f"Erro ao enviar WhatsApp: {resultado_envio['error']}"
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao enviar WhatsApp: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao enviar WhatsApp: {str(e)}")


@router.get("/whatsapp/validar/{id_cobranca}")
async def validar_cobranca_whatsapp(id_cobranca: str, db: Session = Depends(get_db)):
    """Valida se uma cobrança pode receber WhatsApp"""
    try:
        result = db.execute(text("""
            SELECT
                c.id_cobranca, c.status, c.data_vencimento, c.data_pagamento,
                cond.nome as nome_condominio, cond.cobranca_whats, cond.telefone
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
            return {"pode_enviar": False, "motivo": "Cobrança não encontrada"}

        telefone = cobranca.cobranca_whats or cobranca.telefone
        if not telefone:
            return {"pode_enviar": False, "motivo": "Condomínio não possui WhatsApp cadastrado"}

        validacao = verificar_status_cobranca(
            cobranca.status, cobranca.data_vencimento, cobranca.data_pagamento
        )

        return {
            "pode_enviar": validacao["pode_enviar"],
            "tipo_mensagem": validacao["tipo_mensagem"],
            "motivo": validacao["motivo_bloqueio"],
            "mensagem_operador": validacao["mensagem_operador"],
            "dias_atraso": validacao.get("dias_atraso", 0),
            "status": cobranca.status,
            "data_vencimento": cobranca.data_vencimento.strftime("%d/%m/%Y") if cobranca.data_vencimento else None,
            "telefone": telefone
        }

    except Exception as e:
        logger.error(f"❌ Erro ao validar cobrança: {e}")
        return {"pode_enviar": False, "motivo": f"Erro ao validar: {str(e)}"}


@router.get("/whatsapp/status")
async def status_whatsapp():
    """Retorna status do sistema de WhatsApp"""
    config = get_zapi_config()
    return {
        "status": "ok",
        "servico": "WhatsApp via Z-API",
        "tipo": "Envio direto automático",
        "instance_id": config["instance_id"][:8] + "...",
        "timestamp": datetime.now().isoformat()
    }
