"""
================================================================================
ARQUIVO: esqueci_senha_service.py
PASTA:   ~/backend/mobile/
CAMINHO: /home/visionlpr/backend/mobile/esqueci_senha_service.py
================================================================================
Serviço de recuperação de senha
- Geração de token seguro
- Envio por WhatsApp (Z-API) ou Email (SMTP)
- Validação e expiração de tokens
================================================================================
"""

import os
import secrets
import hashlib
import smtplib
import ssl
import requests
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .auth_models import PasswordResetToken, MobileOperador

logger = logging.getLogger(__name__)

# ========================== CONFIGURAÇÕES ==========================

# Token Settings
TOKEN_EXPIRE_MINUTES = 30
FRONTEND_URL = os.getenv("MOBILE_FRONTEND_URL", "https://portaria.econdominio.com.br")

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


# ========================== FUNÇÕES DE TOKEN ==========================

def create_reset_token(
    db: Session,
    operador: MobileOperador,
    metodo_envio: str,
    ip: str = None
) -> str:
    """
    Cria um token de recuperação de senha.
    Retorna o token em formato URL-safe.
    """
    # Gerar token aleatório
    token_raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token_raw.encode()).hexdigest()
    
    expire = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    
    # Invalidar tokens anteriores do mesmo operador
    db.query(PasswordResetToken).filter(
        and_(
            PasswordResetToken.operador_id == operador.id,
            PasswordResetToken.usado == False
        )
    ).update({"usado": True, "usado_em": datetime.utcnow()}, synchronize_session=False)
    db.commit()
    
    # Criar novo token
    reset_token = PasswordResetToken(
        operador_id=operador.id,
        token_hash=token_hash,
        metodo_envio=metodo_envio,
        expira_em=expire,
        criado_em=datetime.utcnow(),
        ip_solicitacao=ip
    )
    
    db.add(reset_token)
    db.commit()
    
    logger.info(f"🔑 Token de reset criado para: {operador.email} via {metodo_envio}")
    return token_raw


def verify_reset_token(db: Session, token: str) -> Optional[MobileOperador]:
    """
    Verifica um token de recuperação de senha.
    Retorna o operador se válido, None se inválido/expirado/usado.
    """
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    
    reset_token = db.query(PasswordResetToken).filter(
        and_(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.usado == False,
            PasswordResetToken.expira_em > datetime.utcnow()
        )
    ).first()
    
    if not reset_token:
        return None
    
    # Buscar operador
    operador = db.query(MobileOperador).filter(
        MobileOperador.id == reset_token.operador_id
    ).first()
    
    return operador


def mark_token_as_used(db: Session, token: str) -> bool:
    """Marca um token como usado"""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    
    result = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash
    ).update({
        "usado": True,
        "usado_em": datetime.utcnow()
    }, synchronize_session=False)
    
    db.commit()
    return result > 0


# ========================== FUNÇÕES DE ENVIO ==========================

def send_whatsapp_message(phone: str, message: str) -> Tuple[bool, Optional[str]]:
    """Envia mensagem WhatsApp via Z-API"""
    try:
        url = f"{ZAPI_API_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
        
        headers = {"Content-Type": "application/json"}
        if ZAPI_CLIENT_TOKEN:
            headers["Client-Token"] = ZAPI_CLIENT_TOKEN
        
        data = {
            "phone": phone,
            "message": message
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            logger.info(f"✅ WhatsApp enviado para: {phone}")
            return True, None
        else:
            error_msg = f"HTTP {response.status_code}: {response.text}"
            logger.error(f"❌ Erro ao enviar WhatsApp: {error_msg}")
            return False, error_msg
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Exceção ao enviar WhatsApp: {error_msg}")
        return False, error_msg


def send_email(to_email: str, subject: str, html_content: str, text_content: str) -> Tuple[bool, Optional[str]]:
    """Envia email via SMTP"""
    try:
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
        return True, None
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Erro ao enviar email: {error_msg}")
        return False, error_msg


# ========================== FUNÇÕES PRINCIPAIS ==========================

def send_reset_link(
    db: Session,
    operador: MobileOperador,
    metodo_envio: str,
    ip: str = None
) -> Tuple[bool, Optional[str]]:
    """
    Envia link de recuperação de senha.
    
    Args:
        operador: Operador que esqueceu a senha
        metodo_envio: 'email' ou 'whatsapp'
        ip: IP da solicitação
    
    Returns:
        (sucesso, mensagem_erro)
    """
    # Criar token
    token = create_reset_token(db, operador, metodo_envio, ip)
    reset_url = f"{FRONTEND_URL}/redefinir-senha?token={token}"
    
    if metodo_envio == "whatsapp":
        if not operador.telefone:
            return False, "Operador não possui telefone cadastrado"
        
        message = f"""🔐 *RECUPERAÇÃO DE SENHA*

Olá *{operador.nome}*!

Recebemos uma solicitação para redefinir sua senha.

🔗 *Clique no link abaixo:*
{reset_url}

⏰ *Válido por {TOKEN_EXPIRE_MINUTES} minutos*

Se você não solicitou, ignore esta mensagem.

---
E-Condomínio - Sistema Mobile"""
        
        return send_whatsapp_message(operador.telefone, message)
    
    else:  # email
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
                .button:hover {{ background: #764ba2; }}
                .footer {{ background: #f5f5f5; padding: 20px; text-align: center; font-size: 12px; color: #666; border-radius: 0 0 10px 10px; }}
                .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1 style="margin: 0;">🔐 Recuperação de Senha</h1>
            </div>
            <div class="content">
                <p>Olá <strong>{operador.nome}</strong>,</p>
                <p>Recebemos uma solicitação para redefinir a senha da sua conta no <strong>E-Condomínio Mobile</strong>.</p>
                <p style="text-align: center;">
                    <a href="{reset_url}" class="button">REDEFINIR SENHA</a>
                </p>
                <p style="font-size: 12px; color: #666;">
                    Ou copie e cole este link no navegador:<br>
                    <code style="background: #f5f5f5; padding: 5px 10px; border-radius: 3px; display: inline-block; margin-top: 5px;">{reset_url}</code>
                </p>
                <div class="warning">
                    <strong>⏰ Atenção:</strong> Este link expira em <strong>{TOKEN_EXPIRE_MINUTES} minutos</strong>.
                </div>
                <p style="font-size: 14px; color: #666;">
                    Se você não solicitou esta alteração, ignore este email. Sua senha permanecerá a mesma.
                </p>
            </div>
            <div class="footer">
                <p>E-Condomínio - Sistema de Gestão de Encomendas</p>
                <p>Este é um email automático, não responda.</p>
            </div>
        </body>
        </html>
        """
        
        text = f"""RECUPERAÇÃO DE SENHA - E-CONDOMÍNIO

Olá {operador.nome},

Recebemos uma solicitação para redefinir a senha da sua conta.

Acesse o link abaixo para criar uma nova senha:
{reset_url}

⏰ Este link expira em {TOKEN_EXPIRE_MINUTES} minutos.

Se você não solicitou, ignore este email.

---
E-Condomínio - Sistema Mobile
Este é um email automático, não responda."""
        
        return send_email(operador.email, subject, html, text)


def cleanup_expired_tokens(db: Session) -> int:
    """Remove tokens expirados ou usados há mais de 7 dias"""
    cutoff = datetime.utcnow() - timedelta(days=7)
    
    deleted = db.query(PasswordResetToken).filter(
        or_(
            PasswordResetToken.expira_em < cutoff,
            and_(
                PasswordResetToken.usado == True,
                PasswordResetToken.usado_em < cutoff
            )
        )
    ).delete(synchronize_session=False)
    
    db.commit()
    logger.info(f"🧹 Limpeza: {deleted} tokens de reset removidos")
    return deleted


# ========================== EXPORTAÇÕES ==========================

__all__ = [
    'create_reset_token',
    'verify_reset_token',
    'mark_token_as_used',
    'send_reset_link',
    'cleanup_expired_tokens',
]
