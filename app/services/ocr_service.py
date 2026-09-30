import base64
import io
import re
from PIL import Image
import pytesseract
from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

class OCRService:
    """Serviço para processamento OCR de etiquetas"""
    
    @staticmethod
    def process_image(image_file) -> Tuple[str, Optional[str]]:
        """
        Processa a imagem e extrai texto usando OCR
        
        Args:
            image_file: Arquivo de imagem do Flask/FastAPI
            
        Returns:
            Tuple[texto_extraido, erro_msg]
        """
        try:
            # Abrir imagem
            if hasattr(image_file, 'file'):  # FastAPI
                image = Image.open(image_file.file)
            else:  # Flask
                image = Image.open(image_file.stream)
            
            # Realizar OCR
            text = pytesseract.image_to_string(image, lang='por')
            logger.info(f"Texto extraído com sucesso: {len(text)} caracteres")
            return text, None
            
        except Exception as e:
            logger.error(f"Erro no processamento OCR: {str(e)}")
            return "", str(e)
    
    @staticmethod
    def extract_apartment_info(text: str) -> str:
        """Extrai informação do apartamento do texto"""
        apt_patterns = [
            r'(?:apt|apto|apartamento|ap)\.?\s*[:.]?\s*(\d+[A-Za-z]?)',
            r'(?:unidade|und)\.?\s*[:.]?\s*(\d+[A-Za-z]?)',
            r'\b(\d{2,4}[A-Za-z]?)\b'  # Números de 2-4 dígitos
        ]
        
        for pattern in apt_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return ""
    
    @staticmethod
    def extract_block_info(text: str) -> str:
        """Extrai informação do bloco do texto"""
        bloco_patterns = [
            r'(?:bloco|bl|torre|edificio|edif)\.?\s*[:.]?\s*([A-Za-z]\d?|\d+)',
            r'(?:bl|blc|tor)\.?\s*[:.]?\s*([A-Za-z]\d?)',
        ]
        
        for pattern in bloco_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).upper()
        return ""
    
    @staticmethod
    def extract_tracking_code(text: str) -> str:
        """Extrai código de rastreamento ou código de barras"""
        codigo_patterns = [
            r'([A-Z]{2}\d{9}[A-Z]{2})',  # Padrão Correios
            r'\b(\d{13,44})\b',  # Código de barras longo
            r'(?:codigo|cod|rastreio|rastreamento)\.?\s*[:.]?\s*([A-Za-z0-9]+)'
        ]
        
        for pattern in codigo_patterns:
            match = re.search(pattern, text)
            if match:
                if '(' in pattern:
                    return match.group(1)
                return match.group(0)
        return ""
    
    @staticmethod
    def extract_recipient_name(text: str) -> str:
        """Extrai o nome do destinatário"""
        lines = text.strip().split('\n')
        
        for line in lines:
            clean_line = line.strip()
            
            # Pular linhas muito curtas ou com apenas números
            if len(clean_line) > 5 and re.search(r'[A-Za-z]{3,}', clean_line):
                # Pular se for endereço, código ou informação estrutural
                skip_words = ['rua', 'av', 'avenida', 'cep', 'bloco', 'apt', 
                             'apartamento', 'torre', 'unidade', 'andar']
                
                if not any(word in clean_line.lower() for word in skip_words):
                    # Limpar o nome de caracteres especiais desnecessários
                    nome = re.sub(r'[^\w\s\-\.]+', '', clean_line)
                    return nome.strip()
        
        return ""
    
    @staticmethod
    def process_label(image_file) -> Dict:
        """
        Processa uma etiqueta completa e retorna os dados extraídos
        
        Args:
            image_file: Arquivo de imagem
            
        Returns:
            Dict com os dados extraídos
        """
        # Processar imagem com OCR
        text, error = OCRService.process_image(image_file)
        
        if error:
            return {
                "status": "error",
                "error": error,
                "extracted_data": None
            }
        
        # Extrair informações
        nome = OCRService.extract_recipient_name(text)
        apartamento = OCRService.extract_apartment_info(text)
        bloco = OCRService.extract_block_info(text)
        codigo = OCRService.extract_tracking_code(text)
        
        # Montar resultado
        result = {
            "extracted_data": {
                "nome": nome or "Não identificado",
                "apartamento": apartamento or "",
                "bloco": bloco or "",
                "codigo_barra": codigo or ""
            },
            "full_text": text,
            "status": "success",
            "source": "local_ocr_tesseract"
        }
        
        logger.info(f"Processamento concluído: {result['extracted_data']}")
        return result
