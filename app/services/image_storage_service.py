# ~/encomenda_v2/backend/app/services/image_storage_service.py

import aiohttp
import os
import base64
import logging
from datetime import datetime
from typing import Optional, Dict, Any
import hashlib
import re

logger = logging.getLogger(__name__)

class ImageStorageService:
    def __init__(self):
        # CORREÇÃO: Usar as variáveis corretas do .env
        self.storage_url = os.getenv("IMAGE_STORAGE_BASE_URL", "http://localhost:4000")
        self.storage_api_key = os.getenv("IMAGE_STORAGE_API_KEY", "")
        
        # Log para debug (remover em produção)
        logger.info(f"Storage URL configurado: {self.storage_url}")
        
        if not self.storage_api_key:
            logger.warning("IMAGE_STORAGE_API_KEY não configurada no .env")

    def _sanitize_filename(self, texto: str) -> str:
        """Sanitizar texto para usar como nome de arquivo"""
        if not texto:
            return "sem_nome"
        # Remover caracteres especiais, manter apenas letras, números e underscores
        texto_limpo = re.sub(r'[^a-zA-Z0-9_-]', '_', texto)
        # Limitar tamanho
        return texto_limpo[:50] if texto_limpo else "sem_nome"

    async def process_and_upload_etiqueta(
        self, 
        image_base64: str, 
        identificador: str = None,
        codigo_rastreio: str = None
    ) -> Dict[str, str]:
        """Processar e fazer upload de etiqueta"""
        try:
            # Criar nome do arquivo no formato: etiqueta_CODIGOBARRAS_AAAAMMDD_HHMMSS.jpg
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            logger.info(f"=== GERANDO NOME DO ARQUIVO ===")
            logger.info(f"Código de rastreio recebido: '{codigo_rastreio}'")
            logger.info(f"Timestamp: {timestamp}")
            
            if codigo_rastreio:
                # SEMPRE usar código de rastreio quando disponível
                codigo_limpo = self._sanitize_filename(codigo_rastreio)
                nome_personalizado = f"etiqueta_{codigo_limpo}_{timestamp}.jpg"
                logger.info(f"✅ Nome gerado COM código: {nome_personalizado}")
            else:
                # Fallback apenas se não houver código de rastreio
                nome_personalizado = f"etiqueta_sem_codigo_{timestamp}.jpg"
                logger.warning(f"⚠️ Nome gerado SEM código: {nome_personalizado}")

            # Upload da imagem
            resultado = await self.upload_image(image_base64, nome_personalizado)
            
            return {
                "nome_personalizado": nome_personalizado,
                "nome_servidor": resultado["filename"],
                "url": resultado["url"]
            }
        except Exception as e:
            logger.error(f"Erro ao processar etiqueta: {str(e)}")
            raise

    async def process_and_upload_assinatura(
        self, 
        image_base64: str, 
        encomenda_id: int
    ) -> str:
        """Processar e fazer upload de assinatura"""
        try:
            nome_arquivo = f"ass_{encomenda_id}.jpg"
            
            # Upload da imagem
            resultado = await self.upload_image(image_base64, nome_arquivo)
            
            return resultado["filename"]
        except Exception as e:
            logger.error(f"Erro ao processar assinatura: {str(e)}")
            raise

    async def upload_image(self, image_base64: str, filename: str) -> Dict[str, Any]:
        """Fazer upload de imagem para o servidor de storage"""
        try:
            # Garantir que a imagem tem o prefixo data:image
            if not image_base64.startswith('data:image'):
                # Detectar tipo de imagem se possível
                if image_base64.startswith('/9j/'):  # JPEG
                    image_base64 = f"data:image/jpeg;base64,{image_base64}"
                else:  # Default para PNG
                    image_base64 = f"data:image/png;base64,{image_base64}"

            async with aiohttp.ClientSession() as session:
                url = f"{self.storage_url}/upload/base64"
                
                payload = {
                    "image": image_base64,
                    "filename": filename
                }
                
                headers = {
                    "x-api-key": self.storage_api_key,
                    "Content-Type": "application/json",
                    "User-Agent": "EncomendaApp/2.3.0"
                }
                
                logger.debug(f"Enviando upload para: {url}")
                
                async with session.post(url, json=payload, headers=headers) as response:
                    result = await response.json()
                    
                    # Verificar se a resposta tem success = true OU status 200
                    if response.status == 200 or result.get("success") == True:
                        # Extrair dados corretamente da resposta
                        if result.get("data"):
                            # Se tem campo "data", usar ele
                            data = result["data"]
                            return {
                                "success": True,
                                "filename": data.get("filename", filename),
                                "url": data.get("url", f"/image/{data.get('filename', filename)}")
                            }
                        else:
                            # Caso contrário, usar diretamente o result
                            return {
                                "success": True,
                                "filename": result.get("filename", filename),
                                "url": result.get("url", f"/image/{filename}")
                            }
                    else:
                        # Só tratar como erro se realmente for erro
                        raise Exception(f"Erro no upload: Status {response.status} - {result}")
                        
        except Exception as e:
            logger.error(f"Erro ao fazer upload da imagem: {str(e)}")
            raise

    async def get_image_url(self, filename: str) -> str:
        """Obter URL de uma imagem"""
        if not filename:
            return None
        # Ajustar URL para incluir o path completo
        if filename.startswith('/'):
            return f"{self.storage_url}{filename}"
        else:
            return f"{self.storage_url}/img/{filename}"

# Instância global
image_storage_service = ImageStorageService()
