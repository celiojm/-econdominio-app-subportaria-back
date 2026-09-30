"""
Sistema de Recuperação de Senha com Envio Real
Integra com Z-API (WhatsApp) e SMTP (Email)
"""
import os
import secrets
import smtplib
import ssl
import requests
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Armazenamento temporário de tokens (em produção, usar Redis ou banco)
reset_tokens = {}

def generate_reset_token(user_id: int) -> str:
    """Gera token único de recuperação"""
    token = secrets.token_urlsafe(32)
    
    reset_tokens[token] = {
        'user_id': user_id,
        'expires_at': datetime.now() + timedelta(hours=1)
    }
    
    return token

def verify_reset_token(token: str) -> Optional[int]:
    """Verifica se token é válido e retorna user_id"""
    if token not in reset_tokens:
        return None
    
    data = reset_tokens[token]
    
    if datetime.now() > data['expires_at']:
        del reset_tokens[token]
        return None
    
    return data['user_id']

def invalidate_token(token: str):
    """Invalida token após uso"""
    if token in reset_tokens:
        del reset_tokens[token]

def send_whatsapp_recovery(phone: str, reset_link: str, user_name: str) -> bool:
    """Envia link de recuperação via WhatsApp"""
    try:
        # Configurações Z-API do .env
        instance_id = os.getenv("ZAPI_INSTANCE_ID")
        token = os.getenv("ZAPI_TOKEN")
        api_url = os.getenv("ZAPI_API_URL", "https://api.z-api.io")
        client_token = os.getenv("ZAPI_CLIENT_TOKEN")
        
        if not instance_id or not token:
            print("⚠️ Z-API não configurado")
            return False
        
        url = f"{api_url}/instances/{instance_id}/token/{token}/send-text"
        
        headers = {"Content-Type": "application/json"}
        if client_token:
            headers["Client-Token"] = client_token
        
        # Formatar telefone (remover caracteres especiais)
        phone_clean = ''.join(filter(str.isdigit, phone))
        if not phone_clean.startswith('55'):
            phone_clean = '55' + phone_clean
        
        message = f"""🔐 *Recuperação de Senha - Econdomínio*

Olá {user_name}!

Recebemos uma solicitação para redefinir sua senha.

🔗 *Clique no link abaixo para criar uma nova senha:*
{reset_link}

⏰ Este link expira em *1 hora*.

❗ Se você não solicitou esta recuperação, ignore esta mensagem e sua senha permanecerá inalterada.

---
Sistema de Gestão de Encomendas v2.0"""
        
        data = {
            "phone": phone_clean,
            "message": message
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            print(f"✅ WhatsApp enviado para {phone_clean}")
            return True
        else:
            print(f"❌ Erro WhatsApp: HTTP {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Erro ao enviar WhatsApp: {e}")
        return False

def send_email_recovery(email: str, reset_link: str, user_name: str) -> bool:
    """Envia link de recuperação via Email"""
    try:
        # Configurações SMTP do .env
        smtp_server = os.getenv("EMAIL_SMTP_SERVER", "mail.econdominio.com.br")
        smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "465"))
        smtp_username = os.getenv("EMAIL_SMTP_USERNAME", "suporte@econdominio.com.br")
        smtp_password = os.getenv("EMAIL_SMTP_PASSWORD")
        
        # Email de suporte
        from_email = "suporte@econdominio.com.br"
        from_name = "Sistema Econdomínio"
        
        if not smtp_password:
            print("⚠️ SMTP não configurado")
            return False
        
        # Criar mensagem
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🔐 Recuperação de Senha - Econdomínio"
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = email
        
        # Versão HTML
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                .icon {{ font-size: 48px; margin-bottom: 10px; }}
                .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                .button {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; margin: 20px 0; font-weight: bold; }}
                .info-box {{ background: #e3f2fd; border-left: 4px solid #2196f3; padding: 15px; margin: 20px 0; border-radius: 5px; }}
                .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; border-radius: 5px; color: #856404; }}
                .footer {{ text-align: center; color: #666; font-size: 12px; margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; }}
            </style>
        </head>
        <body>
            <div class="header">
                <div class="icon">🔐</div>
                <h1 style="margin: 0;">Recuperação de Senha</h1>
            </div>
            <div class="content">
                <p>Olá <strong>{user_name}</strong>,</p>
                <p>Recebemos uma solicitação para redefinir a senha da sua conta no Sistema de Encomendas.</p>
                <p style="text-align: center;">
                    <a href="{reset_link}" class="button">🔒 Redefinir Minha Senha</a>
                </p>
                <div class="info-box">
                    <strong>⏰ Importante:</strong> Este link é válido por <strong>1 hora</strong>.
                </div>
                <div class="warning">
                    <strong>⚠️ Não solicitou?</strong><br>
                    Se você não pediu para redefinir sua senha, ignore este email. Sua senha permanecerá inalterada e sua conta está segura.
                </div>
                <p style="font-size: 12px; color: #666;">
                    Se o botão não funcionar, copie e cole este link no navegador:<br>
                    <code style="background: #f5f5f5; padding: 5px; display: inline-block; word-break: break-all;">{reset_link}</code>
                </p>
            </div>
            <div class="footer">
                <p>Sistema de Gestão de Encomendas v2.0</p>
                <p>Este é um email automático, não responda.</p>
            </div>
        </body>
        </html>
        """
        
        # Versão texto
        texto = f"""🔐 RECUPERAÇÃO DE SENHA - ECONDOMÍNIO

Olá {user_name},

Recebemos uma solicitação para redefinir a senha da sua conta no Sistema de Encomendas.

🔗 Clique no link abaixo para redefinir sua senha:
{reset_link}

⏰ Este link é válido por 1 hora.

⚠️ NÃO SOLICITOU?
Se você não pediu para redefinir sua senha, ignore este email. Sua senha permanecerá inalterada.

---
Sistema de Gestão de Encomendas v2.0
Este é um email automático, não responda."""
        
        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        
        # Enviar email
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_username, smtp_password)
            server.sendmail(from_email, [email], msg.as_string())
        
        print(f"✅ Email enviado para {email}")
        return True
        
    except Exception as e:
        print(f"❌ Erro ao enviar email: {e}")
        return False

async def send_reset_link(
    db: Session,
    identifier: str,
    method: str,
    base_url: str = "https://portaria.econdominio.com.br"
) -> bool:
    """
    Envia link de recuperação por Email ou WhatsApp
    """
    from mobile.auth_models import MobileOperador
    
    # Buscar usuário
    user = db.query(MobileOperador).filter(
        (MobileOperador.email == identifier) |
        (MobileOperador.telefone == identifier)
    ).first()
    
    if not user:
        # Por segurança, não revelar se usuário existe
        print(f"⚠️ Usuário não encontrado: {identifier}")
        return True
    
    # Gerar token
    token = generate_reset_token(user.id)
    reset_link = f"{base_url}/reset-password?token={token}"
    
    print(f"📋 Token gerado para usuário: {user.nome} ({user.email or user.telefone})")
    
    # Enviar por WhatsApp ou Email
    if method == "whatsapp" and user.telefone:
        success = send_whatsapp_recovery(user.telefone, reset_link, user.nome)
        if success:
            print(f"✅ Link de recuperação enviado via WhatsApp")
        else:
            print(f"❌ Falha ao enviar WhatsApp")
        return success
    
    elif method == "email" and user.email:
        success = send_email_recovery(user.email, reset_link, user.nome)
        if success:
            print(f"✅ Link de recuperação enviado via Email")
        else:
            print(f"❌ Falha ao enviar Email")
        return success
    
    print(f"⚠️ Método {method} não disponível para este usuário")
    return False

async def reset_password_with_token(
    db: Session,
    token: str,
    new_password: str
) -> bool:
    """
    Redefine senha usando token válido
    """
    from mobile.auth_models import MobileOperador
    from mobile.auth_service import hash_password
    
    # Verificar token
    user_id = verify_reset_token(token)
    if not user_id:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado")
    
    # Buscar usuário
    user = db.query(MobileOperador).filter(MobileOperador.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    
    # Atualizar senha
    user.senha_hash = hash_password(new_password)
    db.commit()
    
    print(f"✅ Senha redefinida para usuário: {user.nome} ({user.email or user.telefone})")
    
    # Invalidar token
    invalidate_token(token)
    
    return True
