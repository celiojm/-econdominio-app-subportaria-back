# ~/encomenda_v2/backend/app/api/whatsapp.py
# API de envio de WhatsApp - Z-API
# Versão: 4.1.0 - todas as rotas exigem login da equipe do sistema (2026-10-06)
# Versão anterior: 4.0.0 - Migrado para Z-API
# Data: 2025-12-08
# Documentação: https://developer.z-api.io/

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
import logging
import httpx
import os

from app.database import get_db

# 2026-10-06: TODAS as rotas /api/whatsapp/* exigem login da equipe do sistema (admin_sistema: master ou colaborador).
# Antes dependiam só do bloqueio no NGINX (snippets/bloqueio-rotas-inseguras.conf), que continua como 2ª camada.
from fastapi import Request
from app.services.protecao_financeiro import ler_token_interno


async def _so_equipe_sistema(request: Request):
    auth = request.headers.get("authorization", "")
    quem = ler_token_interno(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    if not quem:
        raise HTTPException(status_code=401, detail="Não autenticado")
    if quem.get("origem") != "admin":
        raise HTTPException(status_code=403, detail="Apenas a equipe do sistema")
    return quem


router = APIRouter(dependencies=[Depends(_so_equipe_sistema)])
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURAÇÕES DO Z-API
# ============================================================================
ZAPI_INSTANCE_ID = os.getenv('ZAPI_INSTANCE_ID', "")
ZAPI_TOKEN = os.getenv('ZAPI_TOKEN', "")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")
ZAPI_API_URL = os.getenv('ZAPI_API_URL', 'https://api.z-api.io')

# URL completa da instância
ZAPI_INSTANCE_URL = f"{ZAPI_API_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}"


def formatar_numero_zapi(numero: str) -> str:
    """
    Formata o número de telefone para o padrão Z-API.
    
    O Z-API aceita números no formato:
    - 5548999887766 (com código do país, sem +)
    
    Retorna o número limpo com código do país.
    """
    if not numero:
        return ''
    
    # Remove caracteres não numéricos
    numero_limpo = ''.join(c for c in numero if c.isdigit())
    
    # Se não começar com 55 (Brasil), adiciona
    if not numero_limpo.startswith('55') and len(numero_limpo) <= 11:
        numero_limpo = '55' + numero_limpo
    
    logger.info(f"📱 Número formatado: {numero} → {numero_limpo}")
    return numero_limpo


@router.post("/enviar")
async def enviar_whatsapp(
    payload: Dict[Any, Any] = Body(...),
    db: Session = Depends(get_db)
):
    """
    Endpoint para envio de mensagens WhatsApp via Z-API.

    Payload esperado:
    {
        "numero": "+5548999887766",
        "mensagem": "Texto da mensagem",
        "tipo": "recebimento" | "entrega" | "cadastro",
        "imagem_base64": "data:image/jpeg;base64,..." (opcional)
    }
    """
    try:
        logger.info("=" * 80)
        logger.info("=== Z-API - ENVIANDO WHATSAPP V4.0.0 ===")

        numero = payload.get('numero', '')
        mensagem = payload.get('mensagem', '')
        tipo = payload.get('tipo', 'recebimento')
        imagem_base64 = payload.get('imagem_base64', None)

        logger.info(f"📱 Número original: {numero}")
        logger.info(f"📱 Tipo: {tipo}")
        logger.info(f"📱 Mensagem (primeiros 100 chars): {mensagem[:100] if mensagem else '(vazia)'}...")
        logger.info(f"📱 Tem imagem: {'Sim' if imagem_base64 else 'Não'}")

        # Validações
        if not numero:
            raise HTTPException(status_code=400, detail="Número é obrigatório")

        if not mensagem:
            raise HTTPException(status_code=400, detail="Mensagem é obrigatória")

        # Formatar número
        numero_formatado = formatar_numero_zapi(numero)
        
        if not numero_formatado:
            raise HTTPException(status_code=400, detail="Número inválido após formatação")

        logger.info(f"📱 Enviando para Z-API: {ZAPI_INSTANCE_URL}")
        logger.info(f"📱 Instance ID: {ZAPI_INSTANCE_ID}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            
            # Decidir se envia texto ou imagem
            if imagem_base64:
                # Enviar mensagem com imagem
                result = await enviar_imagem_zapi(
                    client, 
                    numero_formatado, 
                    imagem_base64, 
                    mensagem  # caption
                )
            else:
                # Enviar apenas texto
                result = await enviar_texto_zapi(
                    client, 
                    numero_formatado, 
                    mensagem
                )

            logger.info("=" * 80)
            logger.info("=== Z-API - WHATSAPP ENVIADO COM SUCESSO ===")
            logger.info(f"Resultado: {result}")
            logger.info("=" * 80)

            return {
                "success": True,
                "message": "WhatsApp enviado com sucesso via Z-API",
                "numero": numero_formatado,
                "tipo": tipo,
                "provider": "z-api",
                "resultado": result
            }

    except HTTPException as he:
        raise
    except Exception as e:
        logger.error("=" * 80)
        logger.error("=== Z-API - ERRO AO ENVIAR WHATSAPP ===")
        logger.error(f"Tipo do erro: {type(e).__name__}")
        logger.error(f"Mensagem: {str(e)}")
        logger.error("=" * 80)
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao enviar WhatsApp via Z-API: {str(e)}"
        )


async def enviar_texto_zapi(client: httpx.AsyncClient, numero: str, mensagem: str) -> dict:
    """
    Envia mensagem de texto via Z-API.
    
    Endpoint: POST /send-text
    Content-Type: application/json
    Body: {"phone": "5548999999999", "message": "Texto"}
    """
    url = f"{ZAPI_INSTANCE_URL}/send-text"
    
    # Z-API usa JSON
    payload = {
        "phone": numero,
        "message": mensagem
    }
    
    headers = {
        'Content-Type': 'application/json',
        'Client-Token': os.getenv('ZAPI_CLIENT_TOKEN', "")
    }
    
    logger.info(f"📤 POST {url}")
    logger.info(f"📤 To: {numero}")
    
    response = await client.post(url, json=payload, headers=headers)
    
    logger.info(f"📥 Status: {response.status_code}")
    logger.info(f"📥 Response: {response.text[:500] if response.text else '(vazio)'}")
    
    if response.status_code not in [200, 201]:
        error_text = response.text
        logger.error(f"❌ Erro Z-API: {error_text}")
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Erro Z-API: {error_text}"
        )
    
    try:
        return response.json()
    except:
        return {"raw_response": response.text}


async def enviar_imagem_zapi(
    client: httpx.AsyncClient, 
    numero: str, 
    imagem_base64: str, 
    caption: str = ""
) -> dict:
    """
    Envia imagem via Z-API.
    
    Endpoint: POST /send-image
    Content-Type: application/json
    Body: {"phone": "5548999999999", "image": "base64 ou URL", "caption": "legenda"}
    """
    url = f"{ZAPI_INSTANCE_URL}/send-image"
    
    # Preparar base64 - Z-API aceita com prefixo data:
    imagem = imagem_base64
    if not imagem.startswith('data:'):
        imagem = f"data:image/jpeg;base64,{imagem}"
    
    # Z-API usa JSON
    payload = {
        "phone": numero,
        "image": imagem,
        "caption": caption or ""
    }
    
    headers = {
        'Content-Type': 'application/json',
        'Client-Token': os.getenv('ZAPI_CLIENT_TOKEN', "")
    }
    
    logger.info(f"📤 POST {url}")
    logger.info(f"📤 To: {numero}")
    logger.info(f"📤 Caption: {caption[:50] if caption else '(vazio)'}...")
    logger.info(f"📤 Image base64 length: {len(imagem_base64)} chars")
    
    response = await client.post(url, json=payload, headers=headers)
    
    logger.info(f"📥 Status: {response.status_code}")
    logger.info(f"📥 Response: {response.text[:500] if response.text else '(vazio)'}")
    
    if response.status_code not in [200, 201]:
        error_text = response.text
        logger.error(f"❌ Erro Z-API (imagem): {error_text}")
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Erro Z-API ao enviar imagem: {error_text}"
        )
    
    try:
        return response.json()
    except:
        return {"raw_response": response.text}


@router.post("/enviar-imagem")
async def enviar_imagem_whatsapp(
    payload: Dict[Any, Any] = Body(...),
    db: Session = Depends(get_db)
):
    """
    Endpoint específico para envio de imagens via WhatsApp.

    Payload esperado:
    {
        "numero": "+5548999887766",
        "imagem": "data:image/jpeg;base64,..." ou "https://...",
        "legenda": "Texto opcional da legenda"
    }
    """
    try:
        logger.info("=" * 80)
        logger.info("=== Z-API - ENVIANDO IMAGEM ===")

        numero = payload.get('numero', '')
        imagem = payload.get('imagem', '')
        legenda = payload.get('legenda', '') or payload.get('caption', '')

        if not numero:
            raise HTTPException(status_code=400, detail="Número é obrigatório")

        if not imagem:
            raise HTTPException(status_code=400, detail="Imagem é obrigatória")

        numero_formatado = formatar_numero_zapi(numero)

        async with httpx.AsyncClient(timeout=60.0) as client:
            result = await enviar_imagem_zapi(
                client, 
                numero_formatado, 
                imagem, 
                legenda
            )

            return {
                "success": True,
                "message": "Imagem enviada com sucesso via Z-API",
                "numero": numero_formatado,
                "provider": "z-api",
                "resultado": result
            }

    except HTTPException as he:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao enviar imagem: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao enviar imagem via Z-API: {str(e)}"
        )


@router.get("/status")
async def status_whatsapp():
    """
    Verifica status da configuração do Z-API.
    """
    configurado = bool(ZAPI_TOKEN and ZAPI_INSTANCE_ID)
    
    # Tentar verificar status da instância
    status_instancia = "desconhecido"
    
    if configurado:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{ZAPI_INSTANCE_URL}/status"
                response = await client.get(url)
                
                if response.status_code == 200:
                    data = response.json()
                    # Z-API retorna {"connected": true/false, "smartphoneConnected": true/false}
                    connected = data.get('connected', False)
                    status_instancia = "conectado" if connected else "desconectado"
                else:
                    status_instancia = f"erro_{response.status_code}"
        except Exception as e:
            logger.warning(f"⚠️  Não foi possível verificar status da instância: {e}")
            status_instancia = "erro_conexao"

    return {
        "configurado": configurado,
        "provider": "z-api",
        "instance_id": ZAPI_INSTANCE_ID,
        "api_url": ZAPI_API_URL,
        "instance_url": ZAPI_INSTANCE_URL,
        "status_instancia": status_instancia,
        "modo": "producao" if configurado else "desenvolvimento"
    }


@router.get("/instancia/qr")
async def obter_qr_code():
    """
    Obtém o QR Code para autenticação da instância (se necessário).
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            url = f"{ZAPI_INSTANCE_URL}/qr-code"
            response = await client.get(url)
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "success": False,
                    "error": response.text,
                    "status_code": response.status_code
                }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao obter QR Code: {str(e)}"
        )


@router.post("/instancia/reiniciar")
async def reiniciar_instancia():
    """
    Reinicia a instância do Z-API.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{ZAPI_INSTANCE_URL}/restart"
            
            response = await client.post(url)
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "message": "Instância reiniciada com sucesso",
                    "resultado": response.json() if response.text else {}
                }
            else:
                return {
                    "success": False,
                    "error": response.text,
                    "status_code": response.status_code
                }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao reiniciar instância: {str(e)}"
        )


@router.post("/instancia/desconectar")
async def desconectar_instancia():
    """
    Desconecta a instância do Z-API.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{ZAPI_INSTANCE_URL}/disconnect"
            
            response = await client.post(url)
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "message": "Instância desconectada com sucesso",
                    "resultado": response.json() if response.text else {}
                }
            else:
                return {
                    "success": False,
                    "error": response.text,
                    "status_code": response.status_code
                }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao desconectar instância: {str(e)}"
        )
