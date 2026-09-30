# ~/encomenda_v2/backend/app/api/storage.py
# V2.5 - Combinado: Proxy de imagens + Upload de etiquetas/assinaturas
# Funciona com: Receber.tsx (mobile) + EncomendaAuditoria.tsx (admin)

from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
import logging
import traceback
import httpx
import os

from app.database import get_db
from app.services.image_storage_service import image_storage_service
from app.models.encomenda import Encomenda
from app.api.auth import get_current_user  # Para validação JWT no proxy de imagens

router = APIRouter()
logger = logging.getLogger(__name__)

# ✅ URLs do servidor de storage - suporta ambos os nomes de variáveis
STORAGE_BASE_URL = os.getenv('IMAGE_STORAGE_BASE_URL') or os.getenv('STORAGE_URL', 'http://127.0.0.1:4000')
STORAGE_API_KEY = os.getenv('IMAGE_STORAGE_API_KEY') or os.getenv('STORAGE_API_KEY', '')  # 2026-09-28: sem chave fixa no código

logger.info(f"✅ Storage configurado: {STORAGE_BASE_URL}")


# =============================================================================
# ENDPOINT: Proxy seguro de imagens com validação JWT
# Usado pelo EncomendaAuditoria.tsx (admin)
# =============================================================================
@router.get("/image/{filename:path}")
async def get_image_proxy(
    filename: str,
    current_user: dict = Depends(get_current_user),  # ← Valida JWT!
    db: Session = Depends(get_db)
):
    """
    Proxy seguro para imagens do storage.
    Valida JWT antes de buscar a imagem no servidor de storage.
    
    Isso impede acesso direto às imagens sem autenticação.
    """
    try:
        # Validar nome do arquivo (segurança básica contra path traversal)
        if '..' in filename or filename.startswith('/'):
            logger.warning(f"[STORAGE PROXY] Tentativa de path traversal: {filename}")
            raise HTTPException(
                status_code=400,
                detail="Nome de arquivo inválido"
            )

        # Construir URL do storage
        storage_url = f"{STORAGE_BASE_URL}/storage/image/{filename}"

        logger.info(f"[STORAGE PROXY] Usuário '{current_user.get('username', 'unknown')}' "
                   f"(cond: {current_user.get('condominio_id')}) solicitou: {filename}")

        # Fazer requisição ao storage com API key
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                storage_url,
                headers={
                    "x-api-key": STORAGE_API_KEY,
                    "Accept": "image/*"
                }
            )

        # Verificar se a imagem foi encontrada
        if response.status_code == 404:
            logger.warning(f"[STORAGE PROXY] Imagem não encontrada: {filename}")
            raise HTTPException(
                status_code=404,
                detail="Imagem não encontrada"
            )

        if response.status_code == 401:
            logger.error(f"[STORAGE PROXY] Erro de autenticação no storage para: {filename}")
            raise HTTPException(
                status_code=502,
                detail="Erro de autenticação com o servidor de storage"
            )

        if response.status_code != 200:
            logger.error(f"[STORAGE PROXY] Erro do storage: {response.status_code} para {filename}")
            raise HTTPException(
                status_code=502,
                detail=f"Erro ao buscar imagem: {response.status_code}"
            )

        # Determinar content-type
        content_type = response.headers.get("content-type", "image/jpeg")

        # Retornar a imagem com cache headers (privado - só para usuário autenticado)
        return Response(
            content=response.content,
            media_type=content_type,
            headers={
                "Cache-Control": "private, max-age=3600",  # Cache 1h, privado
                "X-Content-Type-Options": "nosniff"
            }
        )

    except HTTPException:
        raise
    except httpx.TimeoutException:
        logger.error(f"[STORAGE PROXY] Timeout ao buscar imagem: {filename}")
        raise HTTPException(
            status_code=504,
            detail="Timeout ao buscar imagem"
        )
    except Exception as e:
        logger.error(f"[STORAGE PROXY] Erro ao buscar imagem {filename}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Erro interno ao buscar imagem"
        )


# =============================================================================
# ENDPOINT: Upload de Etiqueta
# Usado pelo Receber.tsx (mobile)
# =============================================================================
@router.post("/upload/etiqueta")
async def upload_etiqueta(
    payload: Dict[Any, Any] = Body(...),
    db: Session = Depends(get_db)
):
    """
    Endpoint para upload de imagem de etiqueta após criar a encomenda.
    Usa o código de rastreio para nomear o arquivo.

    Payload esperado:
    {
        "image": "data:image/jpeg;base64,...",
        "encomenda_id": 123,
        "codigo_rastreio": "AA123456789BR"
    }
    """
    try:
        logger.info("=" * 80)
        logger.info("=== RECEBENDO UPLOAD DE ETIQUETA ===")

        # DEBUG: Mostrar TODAS as chaves do payload
        logger.info(f"🔍 Chaves recebidas: {list(payload.keys())}")

        # DEBUG: Tentar diferentes variações de nome
        encomenda_id_variations = {
            'encomenda_id': payload.get('encomenda_id'),
            'encomendaId': payload.get('encomendaId'),
            'id': payload.get('id'),
            'encomenda': payload.get('encomenda'),
        }
        logger.info(f"🔍 Tentativas de encontrar ID: {encomenda_id_variations}")

        # Extrair dados do payload - TESTAR MÚLTIPLAS VARIAÇÕES
        encomenda_id = (
            payload.get('encomenda_id') or
            payload.get('encomendaId') or
            payload.get('id')
        )
        codigo_rastreio = payload.get('codigo_rastreio') or payload.get('codigoRastreio')
        image_data = payload.get('image', '')

        logger.info(f"📊 Encomenda ID extraído: {encomenda_id} (tipo: {type(encomenda_id).__name__})")
        logger.info(f"📊 Código Rastreio: {codigo_rastreio}")
        logger.info(f"📊 Tamanho da imagem: {len(image_data) if image_data else 0} chars")
        logger.info("=" * 80)

        # Validações básicas
        if not encomenda_id:
            raise HTTPException(status_code=400, detail="encomenda_id é obrigatório")

        if not image_data:
            raise HTTPException(status_code=400, detail="image é obrigatória")

        # Verificar se a encomenda existe
        logger.info(f"Buscando encomenda ID: {encomenda_id}")
        encomenda = db.query(Encomenda).filter(Encomenda.id == encomenda_id).first()

        if not encomenda:
            logger.error(f"Encomenda {encomenda_id} não encontrada no banco")
            raise HTTPException(status_code=404, detail="Encomenda não encontrada")

        logger.info(f"Encomenda encontrada: {encomenda.nome_destinatario}")
        logger.info(f"Código de rastreio no banco: {encomenda.codigo_rastreio}")

        # Se não vier código no request, pegar do banco
        codigo_final = codigo_rastreio or encomenda.codigo_rastreio

        logger.info(f"Código final a ser usado: {codigo_final}")
        logger.info("=" * 80)

        # Processar e fazer upload da imagem
        logger.info("Chamando image_storage_service.process_and_upload_etiqueta...")
        resultado = await image_storage_service.process_and_upload_etiqueta(
            image_base64=image_data,
            identificador=encomenda.nome_destinatario,
            codigo_rastreio=codigo_final
        )

        logger.info("=" * 80)
        logger.info("=== UPLOAD CONCLUÍDO COM SUCESSO ===")
        logger.info(f"Nome personalizado: {resultado['nome_personalizado']}")
        logger.info(f"Nome servidor: {resultado['nome_servidor']}")
        logger.info(f"URL: {resultado['url']}")
        logger.info("=" * 80)

        # Atualizar a encomenda com os novos nomes de arquivo
        logger.info("Atualizando encomenda no banco...")
        encomenda.img_etiqueta = resultado['nome_personalizado']
        encomenda.img_etiqueta_server = resultado['nome_servidor']
        db.commit()

        logger.info(f"Encomenda {encomenda_id} atualizada com sucesso")

        return {
            "success": True,
            "filename": resultado['nome_servidor'],
            "url": resultado['url'],
            "nome_personalizado": resultado['nome_personalizado']
        }

    except HTTPException as he:
        logger.error(f"HTTPException: {he.detail}")
        raise
    except Exception as e:
        logger.error("=" * 80)
        logger.error("=== ERRO NO UPLOAD DA ETIQUETA ===")
        logger.error(f"Tipo do erro: {type(e).__name__}")
        logger.error(f"Mensagem: {str(e)}")
        logger.error(f"Traceback completo:\n{traceback.format_exc()}")
        logger.error("=" * 80)
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao fazer upload da etiqueta: {str(e)}"
        )


# =============================================================================
# ENDPOINT: Upload Base64 (Assinaturas)
# Usado para salvar assinaturas de entrega
# =============================================================================
@router.post("/upload/base64")
async def upload_base64(
    payload: Dict[Any, Any] = Body(...),
    db: Session = Depends(get_db)
):
    """
    Endpoint para upload de imagens em base64 diretamente para o storage.
    Usado principalmente para assinaturas.

    Payload esperado:
    {
        "image": "data:image/png;base64,...",
        "filename": "ass_123.png"
    }
    """
    try:
        logger.info("=" * 80)
        logger.info("=== RECEBENDO UPLOAD BASE64 (ASSINATURA) ===")

        image_data = payload.get('image', '')
        filename = payload.get('filename', '')

        logger.info(f"📊 Filename: {filename}")
        logger.info(f"📊 Tamanho da imagem: {len(image_data) if image_data else 0} chars")
        logger.info(f"📊 Storage URL: {STORAGE_BASE_URL}")

        if not image_data:
            raise HTTPException(status_code=400, detail="image é obrigatória")

        if not filename:
            raise HTTPException(status_code=400, detail="filename é obrigatório")

        # ✅ Enviar diretamente para o storage server
        storage_endpoint = f"{STORAGE_BASE_URL}/upload/base64"
        logger.info(f"Enviando para storage: {storage_endpoint}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                storage_endpoint,
                json=payload,
                headers={
                    'Content-Type': 'application/json',
                    'x-api-key': STORAGE_API_KEY
                }
            )

            logger.info(f"📊 Response status do storage: {response.status_code}")

            if response.status_code not in [200, 201]:
                error_text = response.text
                logger.error(f"❌ Erro do storage: {error_text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Erro do storage: {error_text}"
                )

            result = response.json()
            logger.info("=" * 80)
            logger.info("=== UPLOAD BASE64 CONCLUÍDO ===")
            logger.info(f"Resultado: {result}")
            logger.info("=" * 80)

            return result

    except HTTPException as he:
        raise
    except Exception as e:
        logger.error("=" * 80)
        logger.error("=== ERRO NO UPLOAD BASE64 ===")
        logger.error(f"Tipo do erro: {type(e).__name__}")
        logger.error(f"Mensagem: {str(e)}")
        logger.error(f"Traceback completo:\n{traceback.format_exc()}")
        logger.error("=" * 80)
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao fazer upload base64: {str(e)}"
        )


# =============================================================================
# ENDPOINT: Health Check
# =============================================================================
@router.get("/health")
async def storage_health():
    """
    Endpoint para verificar se o storage está configurado corretamente.
    """
    try:
        # Testar conexão com o storage
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{STORAGE_BASE_URL}/health",
                headers={"x-api-key": STORAGE_API_KEY}
            )
            storage_ok = response.status_code == 200
    except:
        storage_ok = False

    return {
        "status": "ok",
        "storage_url": STORAGE_BASE_URL,
        "storage_configured": bool(STORAGE_BASE_URL and STORAGE_API_KEY),
        "storage_connected": storage_ok
    }
