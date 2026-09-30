# ========================================
# Password Reset Routes - Módulo Admin
# Recuperação de senha via WhatsApp/Email
# ========================================

import os
import re
import secrets
import hashlib
import smtplib
import ssl
import requests
import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .database import get_db_connection
from .auth_service import auth_service

logger = logging.getLogger(__name__)

router = APIRouter()

# ========================================
# Configurações
# ========================================

TOKEN_EXPIRE_MINUTES = 30
FRONTEND_URL = os.getenv("PAINEL_FRONTEND_URL", "https://admin.econdominio.com.br")

# Z-API WhatsApp
ZAPI_INSTANCE_ID = os.getenv("ZAPI_INSTANCE_ID", "")
ZAPI_TOKEN = os.getenv("ZAPI_TOKEN", "")
ZAPI_API_URL = os.getenv("ZAPI_API_URL", "https://api.z-api.io")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")

# Email SMTP
EMAIL_SMTP_SERVER = os.getenv("EMAIL_SMTP_SERVER", "mail.econdominio.com.br")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "465"))
EMAIL_SMTP_USERNAME = os.getenv("EMAIL_SMTP_USERNAME", "suporte@econdominio.app.br")
EMAIL_SMTP_PASSWORD = os.getenv("EMAIL_SMTP_PASSWORD", "")
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "suporte@econdominio.app.br")
EMAIL_FROM_NAME = "E-Condomínio Suporte"

# Storage temporário de tokens (em produção usar Redis/Banco)
reset_tokens = {}


# ========================================
# Schemas
# ========================================

class ForgotPasswordRequest(BaseModel):
    identificador: str  # email ou telefone
    metodo: str = "whatsapp"  # "whatsapp" ou "email"


class ResetPasswordRequest(BaseModel):
    token: str
    nova_senha: str


# ========================================
# Funções auxiliares
# ========================================

def generate_reset_token(user_id: int, email: str) -> str:
    """Gera token único de recuperação"""
    token = secrets.token_urlsafe(32)
    
    reset_tokens[token] = {
        'user_id': user_id,
        'email': email,
        'expires_at': datetime.now() + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
        'created_at': datetime.now()
    }
    
    logger.info(f"🔑 Token de reset criado para user_id={user_id}")
    return token


def verify_reset_token(token: str) -> Optional[dict]:
    """Verifica se token é válido e retorna dados"""
    if token not in reset_tokens:
        return None
    
    data = reset_tokens[token]
    
    if datetime.now() > data['expires_at']:
        del reset_tokens[token]
        return None
    
    return data


def invalidate_token(token: str):
    """Invalida token após uso"""
    if token in reset_tokens:
        del reset_tokens[token]


def normalizar_telefone(telefone: str) -> str:
    """Remove formatação e adiciona código do país"""
    numeros = re.sub(r'\D', '', telefone)
    if not numeros.startswith('55'):
        numeros = '55' + numeros
    return numeros


def send_whatsapp_message(phone: str, message: str) -> bool:
    """Envia mensagem WhatsApp via Z-API"""
    try:
        if not ZAPI_INSTANCE_ID or not ZAPI_TOKEN:
            logger.warning("⚠️ Z-API não configurado")
            return False
        
        url = f"{ZAPI_API_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
        
        headers = {"Content-Type": "application/json"}
        if ZAPI_CLIENT_TOKEN:
            headers["Client-Token"] = ZAPI_CLIENT_TOKEN
        
        phone_clean = normalizar_telefone(phone)
        
        data = {
            "phone": phone_clean,
            "message": message
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            logger.info(f"✅ WhatsApp enviado para: {phone_clean}")
            return True
        else:
            logger.error(f"❌ Erro WhatsApp: HTTP {response.status_code} - {response.text}")
            return False
    
    except Exception as e:
        logger.error(f"❌ Exceção ao enviar WhatsApp: {e}")
        return False


def send_email(to_email: str, subject: str, html_content: str, text_content: str) -> bool:
    """Envia email via SMTP"""
    try:
        if not EMAIL_SMTP_PASSWORD:
            logger.warning("⚠️ SMTP não configurado")
            return False
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>"
        msg["To"] = to_email
        
        msg.attach(MIMEText(text_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))
        
        context = ssl.create_default_context()
        with smtplib.SMTP(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_SMTP_USERNAME, EMAIL_SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM_ADDRESS, [to_email], msg.as_string())
        
        logger.info(f"✅ Email enviado para: {to_email}")
        return True
    
    except Exception as e:
        logger.error(f"❌ Erro ao enviar email: {e}")
        return False


# ========================================
# Rotas
# ========================================

@router.post("/forgot-password")
async def forgot_password(request: ForgotPasswordRequest, req: Request):
    """
    Envia link de recuperação de senha via WhatsApp ou Email
    """
    identificador = request.identificador.strip()
    metodo = request.metodo.lower()
    
    if metodo not in ['whatsapp', 'email']:
        raise HTTPException(status_code=400, detail="Método inválido. Use 'whatsapp' ou 'email'")
    
    # Buscar usuário
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Verifica se é email ou telefone
            if '@' in identificador:
                cursor.execute("""
                    SELECT id, email, nome, telefone 
                    FROM mobile_operadores 
                    WHERE LOWER(email) = LOWER(%s) AND ativo = 1
                """, (identificador,))
            else:
                telefone_normalizado = re.sub(r'\D', '', identificador)
                cursor.execute("""
                    SELECT id, email, nome, telefone 
                    FROM mobile_operadores 
                    WHERE telefone = %s AND ativo = 1
                """, (telefone_normalizado,))
            
            user = cursor.fetchone()
            
            if not user:
                # Por segurança, não revelar se usuário existe
                logger.warning(f"⚠️ Tentativa de recuperação para usuário não encontrado: {identificador}")
                return {"message": "Se o usuário existir, um link de recuperação será enviado."}
            
            # Gerar token
            token = generate_reset_token(user['id'], user['email'])
            reset_url = f"{FRONTEND_URL}/redefinir-senha?token={token}"
            
            # Enviar por WhatsApp
            if metodo == 'whatsapp':
                if not user['telefone']:
                    raise HTTPException(status_code=400, detail="Usuário não possui telefone cadastrado")
                
                message = f"""🔐 *RECUPERAÇÃO DE SENHA*

Olá *{user['nome']}*!

Você solicitou a redefinição de senha do Painel Admin.

🔗 *Clique no link abaixo:*
{reset_url}

⏰ *Válido por {TOKEN_EXPIRE_MINUTES} minutos*

Se você não solicitou, ignore esta mensagem.

---
E-Condomínio - Painel Admin"""
                
                success = send_whatsapp_message(user['telefone'], message)
                
                if not success:
                    raise HTTPException(status_code=500, detail="Erro ao enviar WhatsApp. Tente novamente.")
            
            # Enviar por Email
            else:
                if not user['email']:
                    raise HTTPException(status_code=400, detail="Usuário não possui email cadastrado")
                
                subject = "🔐 Recuperação de Senha - E-Condomínio"
                
                html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
                        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 10px 10px 0 0; text-align: center; }}
                        .content {{ background: #ffffff; padding: 30px; border: 1px solid #e0e0e0; border-top: none; }}
                        .button {{ display: inline-block; background: #667eea; color: white; padding: 15px 40px; text-decoration: none; border-radius: 5px; margin: 20px 0; font-weight: bold; }}
                        .footer {{ background: #f5f5f5; padding: 20px; text-align: center; font-size: 12px; color: #666; border-radius: 0 0 10px 10px; }}
                        .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; border-radius: 5px; }}
                    </style>
                </head>
                <body>
                    <div class="header">
                        <h1 style="margin: 0;">🔐 Recuperação de Senha</h1>
                    </div>
                    <div class="content">
                        <p>Olá <strong>{user['nome']}</strong>,</p>
                        <p>Recebemos uma solicitação para redefinir a senha da sua conta no <strong>Painel Admin</strong>.</p>
                        <p style="text-align: center;">
                            <a href="{reset_url}" class="button">REDEFINIR SENHA</a>
                        </p>
                        <p style="font-size: 12px; color: #666;">
                            Ou copie e cole este link no navegador:<br>
                            <code style="background: #f5f5f5; padding: 5px 10px; border-radius: 3px;">{reset_url}</code>
                        </p>
                        <div class="warning">
                            <strong>⏰ Atenção:</strong> Este link expira em <strong>{TOKEN_EXPIRE_MINUTES} minutos</strong>.
                        </div>
                        <p style="font-size: 14px; color: #666;">
                            Se você não solicitou esta alteração, ignore este email.
                        </p>
                    </div>
                    <div class="footer">
                        <p>E-Condomínio - Painel Admin</p>
                        <p>Este é um email automático, não responda.</p>
                    </div>
                </body>
                </html>
                """
                
                text = f"""RECUPERAÇÃO DE SENHA - E-CONDOMÍNIO

Olá {user['nome']},

Recebemos uma solicitação para redefinir a senha da sua conta.

Acesse o link abaixo para criar uma nova senha:
{reset_url}

⏰ Este link expira em {TOKEN_EXPIRE_MINUTES} minutos.

Se você não solicitou, ignore este email.

---
E-Condomínio - Painel Admin"""
                
                success = send_email(user['email'], subject, html, text)
                
                if not success:
                    raise HTTPException(status_code=500, detail="Erro ao enviar email. Tente novamente.")
            
            logger.info(f"✅ Link de recuperação enviado para {user['nome']} via {metodo}")
            return {"message": "Link de recuperação enviado com sucesso!"}
    
    finally:
        conn.close()


@router.get("/verify-reset-token/{token}")
async def verify_token(token: str):
    """
    Verifica se o token de reset é válido
    """
    data = verify_reset_token(token)
    
    if not data:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado")
    
    return {
        "valid": True,
        "email": data['email'],
        "expires_in_minutes": int((data['expires_at'] - datetime.now()).total_seconds() / 60)
    }


@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest):
    """
    Redefine a senha usando o token
    """
    # Verificar token
    data = verify_reset_token(request.token)
    
    if not data:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado")
    
    # Validar nova senha
    if len(request.nova_senha) < 6:
        raise HTTPException(status_code=400, detail="A senha deve ter pelo menos 6 caracteres")
    
    # Atualizar senha no banco
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            novo_hash = auth_service.hash_senha(request.nova_senha)
            
            cursor.execute("""
                UPDATE mobile_operadores 
                SET senha_hash = %s 
                WHERE id = %s
            """, (novo_hash, data['user_id']))
            
            conn.commit()
            
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Usuário não encontrado")
            
            # Invalidar token
            invalidate_token(request.token)
            
            logger.info(f"✅ Senha redefinida com sucesso para user_id={data['user_id']}")
            return {"message": "Senha redefinida com sucesso!"}
    
    finally:
        conn.close()
