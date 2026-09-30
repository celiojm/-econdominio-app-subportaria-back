"""
# ALTERAÇÃO 2026-09-27: cadastro público cria condomínio como 'trial' (antes 'ativa' sem pagamento)
# ALTERAÇÃO 2026-09-27: régua de cobrança — teste grátis 14 dias (só novos cadastros)
================================================================================
BACKEND: Router de Autenticação do Painel - e-Condomínio
ARQUIVO: cliente/router.py
DESCRIÇÃO: Endpoints de autenticação e cadastro público com confirmação
           e registro de 7 dias bonificados
ATUALIZADO: Fev/2026 - Suporte a síndico com múltiplos condos via WhatsApp
            WhatsApp duplicado: PERMITIDO (não cria senha via nome WhatsApp)
            Email duplicado: BLOQUEADO (UNIQUE KEY mantida)
================================================================================
"""

import os
import secrets
import hashlib
import logging
import smtplib
import ssl
import asyncio
import httpx
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, EmailStr
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from jose import jwt

from app.database import get_db
from mobile.auth_service import hash_password, verify_password, create_access_token
from financeiro.financeiro_preco_routes import _calcular_valor_mensal_base

# Fila WhatsApp
try:
    from app.services.whatsapp import queue_whatsapp_message
    USAR_FILA_WHATSAPP = True
except ImportError:
    USAR_FILA_WHATSAPP = False

logger = logging.getLogger(__name__)


def _queue_whatsapp(telefone, mensagem, tipo="CONFIRMACAO_WHATSAPP"):
    """
    1. Tenta Z-API direto (imediato)
    2. Se falhar, enfileira para Meta API (worker existente)
    """
    import requests as _req, os as _os

    tel = ''.join(d for d in str(telefone) if d.isdigit())
    if not tel.startswith('55'):
        tel = '55' + tel

    # 1. Z-API direto
    try:
        zapi_url   = _os.getenv('ZAPI_API_URL', 'http://191.252.221.192:8080')
        zapi_inst  = _os.getenv('ZAPI_INSTANCE_ID', '')
        zapi_token = _os.getenv('ZAPI_TOKEN', '')
        cli_token  = _os.getenv('ZAPI_CLIENT_TOKEN', '')
        url = f"{zapi_url}/instances/{zapi_inst}/token/{zapi_token}/send-text"
        r = _req.post(url,
            json={"phone": tel, "message": mensagem},
            headers={"Content-Type": "application/json", "Client-Token": cli_token},
            timeout=10)
        if r.status_code == 200:
            body = r.json() if 'application/json' in r.headers.get('content-type','') else {}
            if not body.get('error') and not body.get('erro'):
                logger.info(f"WhatsApp Z-API enviado para {tel}")
                return True
        logger.warning(f"Z-API falhou ({r.status_code}) — tentando fila Meta")
    except Exception as e:
        logger.warning(f"Z-API erro: {e} — tentando fila Meta")

    # 2. Fallback fila Meta
    try:
        if USAR_FILA_WHATSAPP:
            ok = queue_whatsapp_message(
                tipo_evento=tipo,
                telefone=telefone,
                mensagem_original=mensagem,
                payload={"texto": mensagem},
                delay_minutes=0
            )
            logger.info(f"WhatsApp enfileirado Meta: {ok}")
            return ok
    except Exception as e:
        logger.error(f"Erro fila Meta: {e}")
    return False


def enviar_email_credenciais(email_destino, nome, email_acesso, senha, validade):
    import smtplib, ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    try:
        cfg = get_email_config()
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "e-Condominio - Suas credenciais de acesso"
        msg["From"] = f"{cfg['from_name']} <{cfg['from_email']}>"
        msg["To"] = email_destino
        try:
            val_fmt = validade.strftime("%d/%m/%Y")
        except Exception:
            val_fmt = str(validade)
        html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
          <div style="background:#2563eb;padding:25px;text-align:center">
            <h1 style="color:#fff;margin:0">Bem-vindo ao e-Condominio!</h1>
          </div>
          <div style="padding:25px;background:#f9f9f9;border:1px solid #e0e0e0">
            <p>Ola, <strong>{nome}</strong>! Cadastro confirmado!</p>
            <table style="width:100%;background:#fff;padding:15px;border:1px solid #ddd;border-radius:8px">
              <tr><td style="color:#666;padding:6px">Email</td>
                  <td style="font-weight:bold;padding:6px">{email_acesso}</td></tr>
              <tr><td style="color:#666;padding:6px">Senha</td>
                  <td style="font-weight:bold;font-size:18px;padding:6px">{senha}</td></tr>
              <tr><td style="color:#666;padding:6px">Teste gratis ate</td>
                  <td style="font-weight:bold;color:#16a34a;padding:6px">{val_fmt}</td></tr>
            </table>
            <div style="text-align:center;margin:20px 0">
              <a href="https://admin.econdominio.com.br"
                 style="background:#2563eb;color:#fff;padding:12px 28px;
                        border-radius:6px;text-decoration:none;font-weight:bold">
                Acessar o Painel
              </a>
            </div>
            <p style="font-size:12px;color:#999">Recomendamos alterar sua senha no primeiro acesso.</p>
          </div>
          <div style="background:#eee;padding:8px;text-align:center">
            <p style="color:#999;font-size:11px;margin:0">e-Condominio | econdominio.com.br | (48) 98840-6118</p>
          </div>
        </div>"""
        msg.attach(MIMEText(html, "html", "utf-8"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"], timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(cfg["username"], cfg["password"])
            s.sendmail(cfg["from_email"], [email_destino], msg.as_string())
        logger.info(f"Email credenciais enviado para {email_destino}")
        return True
    except Exception as e:
        logger.error(f"Erro email credenciais: {e}")
        return False


router = APIRouter(prefix="/painel/auth", tags=["Painel - Autenticação"])

# ==============================================================================
# CONFIGURAÇÕES
# ==============================================================================

from app.services.assinatura_situacao import TRIAL_DIAS  # noqa: E402
DIAS_BONIFICADOS = TRIAL_DIAS  # Período de teste grátis (14 dias, régua de cobrança)

# ==============================================================================
# MODELOS
# ==============================================================================

class LoginRequest(BaseModel):
    identificador: str
    nome: Optional[str] = None
    senha: str

class ForgotPasswordRequest(BaseModel):
    identificador: str
    metodo: str = "whatsapp"

class ResetPasswordRequest(BaseModel):
    token: str
    nova_senha: str

class ChangePasswordRequest(BaseModel):
    senha_atual: str
    nova_senha: str
    confirmar_senha: str

# ==============================================================================
# FUNÇÕES AUXILIARES
# ==============================================================================

def get_email_config():
    """Retorna configurações de email do .env — sempre usar variáveis do .env"""
    return {
        "smtp_server": os.getenv("EMAIL_SMTP_SERVER", os.getenv("BREVO_SMTP_HOST", "smtp-relay.brevo.com")),
        "smtp_port":   int(os.getenv("EMAIL_SMTP_PORT", os.getenv("BREVO_SMTP_PORT", "587"))),
        "username":    os.getenv("EMAIL_SMTP_USERNAME", os.getenv("BREVO_SMTP_USER", "")),
        "password":    os.getenv("EMAIL_SMTP_PASSWORD", os.getenv("BREVO_SMTP_KEY", "")),
        "from_email":  os.getenv("EMAIL_FROM_ADDRESS", os.getenv("BREVO_FROM_ADDRESS", "contato@econdominio.com.br")),
        "from_name":   os.getenv("EMAIL_FROM_NAME",    os.getenv("BREVO_FROM_NAME",    "eCondominio")),
        "use_ssl":     os.getenv("EMAIL_USE_SSL", "false").lower() == "true"
    }

def get_zapi_config():
    """Retorna configurações do Z-API"""
    return {
        "instance": os.getenv("ZAPI_INSTANCE_ID", ""),
        "token": os.getenv("ZAPI_TOKEN", ""),
        "client_token": os.getenv("ZAPI_CLIENT_TOKEN", "")
    }

def gerar_token_reset():
    return secrets.token_urlsafe(32)

def enviar_email_reset_senha(email_destino: str, nome: str, token: str):
    """Envia email com link de reset de senha"""
    try:
        email_config = get_email_config()
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🔐 e-Condomínio - Recuperação de Senha"
        msg["From"] = f"{email_config['from_name']} <{email_config['from_email']}>"
        msg["To"] = email_destino

        link_reset = f"https://admin.econdominio.com.br/reset-password/{token}"

        texto = f"""
e-Condomínio - Recuperação de Senha

Olá, {nome}!

Você solicitou a recuperação de senha do sistema e-Condomínio.

Para redefinir sua senha, acesse o link abaixo:

{link_reset}

⚠️ Este link é válido por 1 hora.

Se você não solicitou esta recuperação, ignore esta mensagem.

---
e-Condomínio
Sistema de Gestão de Encomendas
https://econdominio.com.br
        """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8">
            <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f5f5f5; }}
                .container {{ background: white; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
                .header {{ background: linear-gradient(135deg, #dc2626 0%, #b91c1c 100%); color: white; padding: 30px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 28px; }}
                .content {{ padding: 30px; }}
                .btn {{ display: inline-block; background: linear-gradient(135deg, #2563eb 0%, #7c3aed 100%); color: white; padding: 15px 40px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 16px; margin: 20px 0; }}
                .warning {{ background: #fef3c7; border-left: 4px solid #f59e0b; padding: 12px 15px; margin: 20px 0; border-radius: 8px; font-size: 14px; }}
                .footer {{ background: #f8fafc; padding: 20px 30px; text-align: center; color: #64748b; font-size: 14px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header"><h1>🔐 Recuperação de Senha</h1></div>
                <div class="content">
                    <p style="font-size: 18px;">Olá, <strong>{nome}</strong>!</p>
                    <p>Você solicitou a recuperação de senha do sistema e-Condomínio.</p>
                    <p>Para redefinir sua senha, clique no botão abaixo:</p>
                    <div style="text-align: center;"><a href="{link_reset}" class="btn">🔑 Redefinir Minha Senha</a></div>
                    <p style="font-size: 12px; word-break: break-all; background: #f1f5f9; padding: 10px; border-radius: 6px;">{link_reset}</p>
                    <div class="warning"><strong>⚠️ Atenção:</strong> Este link é válido por <strong>1 hora</strong>.</div>
                    <p style="font-size: 14px; color: #64748b;">Se você não solicitou esta recuperação, ignore esta mensagem.</p>
                </div>
                <div class="footer"><p><strong>e-Condomínio</strong></p><p><a href="https://econdominio.com.br" style="color: #2563eb;">econdominio.com.br</a></p></div>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        context = ssl.create_default_context()
        if email_config["use_ssl"]:
            with smtplib.SMTP(email_config["smtp_server"], email_config["smtp_port"], timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(email_config["username"], email_config["password"])
                server.sendmail(email_config["from_email"], [email_destino], msg.as_string())
        else:
            with smtplib.SMTP(email_config["smtp_server"], email_config["smtp_port"], timeout=30) as server:
                server.starttls(context=context)
                server.login(email_config["username"], email_config["password"])
                server.sendmail(email_config["from_email"], [email_destino], msg.as_string())

        logger.info(f"Email de reset enviado para {email_destino}")
        return True
    except Exception as e:
        logger.error(f"Erro ao enviar email de reset: {str(e)}")
        return False


async def enviar_whatsapp_reset_senha(telefone: str, nome: str, token: str):
    """Envia WhatsApp com link de reset de senha"""
    link_reset = f"https://admin.econdominio.com.br/reset-password/{token}"

    mensagem = f"""🔐 *e-Condomínio - Recuperação de Senha*

Olá, *{nome}*!

Você solicitou a recuperação de senha.

Para redefinir sua senha, clique no link abaixo:

👉 {link_reset}

⚠️ Este link é válido por 1 hora.

Se você não solicitou, ignore esta mensagem.

---
*e-Condomínio*
Sistema de Gestão de Encomendas"""

    ok = _queue_whatsapp(telefone, mensagem)
    logger.info(f"WhatsApp confirmacao enfileirado para {telefone}: {ok}")
    return ok


# ==============================================================================
# ENDPOINTS DE AUTENTICAÇÃO
# ==============================================================================

@router.post("/login")
async def login(data: LoginRequest, db: Session = Depends(get_db)):
    """Login no painel administrativo"""
    try:
        identificador = data.identificador or data.nome

        if '@' in identificador:
            query = text("""
                SELECT mo.id, mo.nome, mo.email, mo.telefone, mo.senha_hash, mo.role,
                       mo.condominio_id, mo.ativo, c.nome as condominio_nome
                FROM mobile_operadores mo
                LEFT JOIN condominios c ON c.id = mo.condominio_id
                WHERE mo.email = :identificador AND mo.ativo = 1
            """)
        else:
            telefone = identificador.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            query = text("""
                SELECT mo.id, mo.nome, mo.email, mo.telefone, mo.senha_hash, mo.role,
                       mo.condominio_id, mo.ativo, c.nome as condominio_nome
                FROM mobile_operadores mo
                LEFT JOIN condominios c ON c.id = mo.condominio_id
                WHERE REPLACE(REPLACE(REPLACE(REPLACE(mo.telefone, ' ', ''), '-', ''), '(', ''), ')', '') = :identificador
                AND mo.ativo = 1
            """)
            identificador = telefone

        result = db.execute(query, {"identificador": identificador}).fetchone()

        if not result:
            raise HTTPException(status_code=401, detail="Usuário não encontrado ou inativo")

        if not verify_password(data.senha, result[4]):
            raise HTTPException(status_code=401, detail="Senha incorreta")

        token = create_access_token(data={"sub": str(result[0]), "email": result[2]})

        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": result[0],
                "nome": result[1],
                "email": result[2],
                "telefone": result[3],
                "role": result[5],
                "condominio_id": result[6],
                "ativo": result[7],
                "condominio_nome": result[8]
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no login: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao fazer login: {str(e)}")


@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Solicita recuperação de senha por WhatsApp ou Email"""
    try:
        identificador = data.identificador

        if '@' in identificador:
            query = text("SELECT id, nome, email, telefone FROM mobile_operadores WHERE email = :identificador AND ativo = 1")
        else:
            telefone = identificador.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            query = text("""
                SELECT id, nome, email, telefone FROM mobile_operadores
                WHERE REPLACE(REPLACE(REPLACE(REPLACE(telefone, ' ', ''), '-', ''), '(', ''), ')', '') = :identificador AND ativo = 1
            """)
            identificador = telefone

        result = db.execute(query, {"identificador": identificador}).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        operador_id, nome, email, telefone = result[0], result[1], result[2], result[3]

        token = gerar_token_reset()
        expira_em = datetime.now() + timedelta(hours=1)

        db.execute(
            text("UPDATE mobile_operadores SET reset_token = :token, reset_token_expira = :expira WHERE id = :id"),
            {"token": token, "expira": expira_em, "id": operador_id}
        )
        db.commit()

        enviado = False
        if data.metodo == "email":
            if not email:
                raise HTTPException(status_code=400, detail="Este usuário não possui email cadastrado")
            enviado = enviar_email_reset_senha(email, nome, token)
            metodo_usado = "email"
        else:
            if not telefone:
                raise HTTPException(status_code=400, detail="Este usuário não possui telefone cadastrado")
            enviado = await enviar_whatsapp_reset_senha(telefone, nome, token)
            metodo_usado = "WhatsApp"

        if not enviado:
            raise HTTPException(status_code=500, detail=f"Erro ao enviar {metodo_usado}")

        return {"success": True, "message": f"Link de recuperação enviado por {metodo_usado}", "metodo": data.metodo}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro na recuperação de senha: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao processar solicitação: {str(e)}")


@router.get("/verify-reset-token/{token}")
async def verify_reset_token(token: str, db: Session = Depends(get_db)):
    try:
        result = db.execute(
            text("SELECT id, nome, email, reset_token_expira FROM mobile_operadores WHERE reset_token = :token AND ativo = 1"),
            {"token": token}
        ).fetchone()

        if not result:
            return {"valid": False, "error": "Token inválido"}
        if result[3] and datetime.now() > result[3]:
            return {"valid": False, "error": "Token expirado"}

        return {"valid": True, "data": {"nome": result[1], "email": result[2]}}
    except Exception as e:
        logger.error(f"Erro ao verificar token: {str(e)}")
        return {"valid": False, "error": str(e)}


@router.post("/reset-password")
async def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        result = db.execute(
            text("SELECT id, nome, reset_token_expira FROM mobile_operadores WHERE reset_token = :token AND ativo = 1"),
            {"token": data.token}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=400, detail="Token inválido")
        if result[2] and datetime.now() > result[2]:
            raise HTTPException(status_code=400, detail="Token expirado. Solicite uma nova recuperação.")

        nova_senha_hash = hash_password(data.nova_senha)
        db.execute(
            text("UPDATE mobile_operadores SET senha_hash = :senha, reset_token = NULL, reset_token_expira = NULL WHERE id = :id"),
            {"senha": nova_senha_hash, "id": result[0]}
        )
        db.commit()
        return {"success": True, "message": "Senha alterada com sucesso!"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao resetar senha: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao alterar senha: {str(e)}")


@router.get("/me")
async def get_current_user(db: Session = Depends(get_db)):
    raise HTTPException(status_code=401, detail="Token necessário")


@router.post("/change-password")
async def change_password(data: ChangePasswordRequest, db: Session = Depends(get_db)):
    if data.nova_senha != data.confirmar_senha:
        raise HTTPException(status_code=400, detail="Senhas não conferem")
    return {"success": True, "message": "Senha alterada com sucesso"}


@router.post("/logout")
async def logout():
    return {"success": True, "message": "Logout realizado"}


# ==============================================================================
# ROUTER DE CADASTRO DE CLIENTE (PÚBLICO)
# ==============================================================================

cliente_router = APIRouter(prefix="/cliente", tags=["Cliente - Cadastro Público"])

class CadastroClienteRequest(BaseModel):
    cnpj: str
    razao_social: str
    nome_fantasia: Optional[str] = None
    endereco: Optional[str] = None
    numero: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None
    cep: Optional[str] = None

    sindico_nome: str
    sindico_email: EmailStr
    sindico_whatsapp: str
    sindico_cpf: Optional[str] = None

    total_apartamentos: int
    email_condominio: Optional[str] = None
    telefone_condominio: Optional[str] = None
    whatsapp_condominio: Optional[str] = None

    financeiro_responsavel: Optional[str] = None
    financeiro_email: Optional[str] = None
    financeiro_whatsapp: Optional[str] = None

    plano_selecionado: Optional[str] = 'mensal'
    forma_pagamento: Optional[str] = 'mensal'
    # SEGURANCA: estes 3 campos sao aceitos por compatibilidade mas
    # SEMPRE sobrescritos no servidor (ver cadastrar_cliente). Nunca
    # confiar em valor de preco vindo do cliente/formulario publico.
    valor_mensal_base: Optional[float] = 0.0
    valor_plano_final: Optional[float] = 0.0
    plano_desconto: Optional[int] = 0

    # ✅ NOVO: Flag para síndico com WhatsApp já cadastrado
    sindico_existente_whatsapp: Optional[bool] = False


class ConfirmarCadastroRequest(BaseModel):
    chave: str


# ==============================================================================
# FUNÇÕES AUXILIARES - WHATSAPP CADASTRO
# ==============================================================================

async def enviar_whatsapp_confirmacao(telefone: str, nome: str, chave: str):
    link_confirmacao = f"https://admin.econdominio.com.br/confirmar/{chave}"

    mensagem = f"""🎉 *e-Condomínio - Confirme seu Cadastro!*

Olá, *{nome}*!

Seu cadastro foi recebido com sucesso!

Para ativar sua conta e receber suas credenciais de acesso, clique no link abaixo:

👉 {link_confirmacao}

⚠️ Este link é válido por 48 horas.

Após a confirmação, você receberá:
✅ 7 dias de acesso gratuito
✅ Email e senha para acessar o painel
✅ Acesso completo ao sistema

Qualquer dúvida, estamos à disposição!

---
*e-Condomínio*
Sistema de Gestão de Encomendas
https://econdominio.com.br"""

    ok = _queue_whatsapp(telefone, mensagem)
    logger.info(f"WhatsApp credenciais enfileirado para {telefone}: {ok}")
    return ok


async def enviar_whatsapp_credenciais(telefone: str, nome: str, email: str, senha: str, validade: date):
    validade_formatada = validade.strftime("%d/%m/%Y")

    mensagem = f"""🎉 *Bem-vindo ao e-Condomínio!*

Olá, *{nome}*!

Seu cadastro foi confirmado com sucesso!

🎁 *Período de Teste Gratuito*
Você tem *7 dias grátis* para experimentar todas as funcionalidades!
📅 Válido até: *{validade_formatada}*

📱 *Acesse o painel:*
https://admin.econdominio.com.br

🔐 *Suas credenciais:*
▪️ Email: *{email}*
▪️ Senha: *{senha}*

📲 *App para operadores:*
https://portaria.econdominio.com.br

⚠️ Recomendamos que altere sua senha no primeiro acesso.

Qualquer dúvida, estamos à disposição!

---
*e-Condomínio*
Sistema de Gestão de Encomendas
https://econdominio.com.br"""

    ok = _queue_whatsapp(telefone, mensagem)
    logger.info(f"WhatsApp novo_condominio enfileirado para {telefone}: {ok}")
    return ok


async def enviar_whatsapp_novo_condominio(telefone: str, nome: str, novo_condominio: str, email_acesso: str, senha: str, validade: date):
    """
    ✅ NOVO: WhatsApp para síndico existente que cadastrou novo condomínio
    Informa as credenciais do NOVO email de acesso
    """
    validade_formatada = validade.strftime("%d/%m/%Y")

    mensagem = f"""🏢 *e-Condomínio - Novo Condomínio Cadastrado!*

Olá, *{nome}*!

O condomínio *{novo_condominio}* foi cadastrado com sucesso! 🎉

🎁 *7 dias grátis* para testar!
📅 Válido até: *{validade_formatada}*

📱 *Acesse o painel:*
https://admin.econdominio.com.br

🔐 *Credenciais para este condomínio:*
▪️ Email: *{email_acesso}*
▪️ Senha: *{senha}*

⚠️ *Atenção:* Este email e senha são para o novo condomínio. Seus outros condomínios continuam com as credenciais anteriores.

Qualquer dúvida, estamos à disposição!

---
*e-Condomínio*
Sistema de Gestão de Encomendas
https://econdominio.com.br"""

    ok = _queue_whatsapp(telefone, mensagem)
    logger.info(f"WhatsApp reset_senha enfileirado para {telefone}: {ok}")
    return ok


def enviar_email_credenciais(email_destino: str, nome: str, email_login: str, senha: str, validade: date):
    """Envia email com credenciais de acesso"""
    try:
        email_config = get_email_config()
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🎉 e-Condomínio - Bem-vindo! Suas credenciais de acesso"
        msg["From"] = f"{email_config['from_name']} <{email_config['from_email']}>"
        msg["To"] = email_destino

        validade_formatada = validade.strftime("%d/%m/%Y")

        texto = f"""
e-Condomínio - Bem-vindo!

Olá, {nome}!

Seu cadastro foi confirmado com sucesso!

🎁 PERÍODO DE TESTE GRATUITO
Você tem 7 dias grátis para experimentar todas as funcionalidades!
Válido até: {validade_formatada}

Acesse o painel: https://admin.econdominio.com.br

Suas credenciais:
- Email: {email_login}
- Senha: {senha}

App para operadores: https://portaria.econdominio.com.br

⚠️ Recomendamos que altere sua senha no primeiro acesso.

---
e-Condomínio - https://econdominio.com.br
        """

        html = f"""
        <!DOCTYPE html>
        <html><head><meta charset="UTF-8">
            <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f5f5f5; }}
                .container {{ background: white; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
                .header {{ background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; padding: 30px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 28px; }}
                .content {{ padding: 30px; }}
                .trial-box {{ background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); border-radius: 12px; padding: 20px; margin: 20px 0; text-align: center; }}
                .credentials {{ background: #f1f5f9; border-radius: 12px; padding: 20px; margin: 20px 0; }}
                .btn {{ display: inline-block; background: linear-gradient(135deg, #2563eb 0%, #7c3aed 100%); color: white; padding: 15px 40px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 16px; margin: 10px 5px; }}
                .footer {{ background: #f8fafc; padding: 20px 30px; text-align: center; color: #64748b; font-size: 14px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header"><h1>🎉 Bem-vindo ao e-Condomínio!</h1></div>
                <div class="content">
                    <p style="font-size: 18px;">Olá, <strong>{nome}</strong>!</p>
                    <p>Seu cadastro foi confirmado com sucesso!</p>
                    <div class="trial-box">
                        <h3 style="color: #92400e; margin: 0 0 10px 0;">🎁 Período de Teste Gratuito</h3>
                        <p>Você tem <strong>7 dias grátis</strong>!</p>
                        <p style="font-size: 24px; font-weight: bold; color: #b45309;">📅 Válido até: {validade_formatada}</p>
                    </div>
                    <div class="credentials">
                        <h3 style="margin-top: 0;">🔐 Suas Credenciais de Acesso</h3>
                        <p><strong>Email:</strong> {email_login}</p>
                        <p><strong>Senha:</strong> {senha}</p>
                        <p style="font-size: 12px; color: #64748b;">⚠️ Recomendamos que altere sua senha no primeiro acesso.</p>
                    </div>
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="https://admin.econdominio.com.br" class="btn">🖥️ Acessar Painel</a>
                    </div>
                </div>
                <div class="footer"><p><strong>e-Condomínio</strong></p></div>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        context = ssl.create_default_context()
        if email_config["use_ssl"]:
            with smtplib.SMTP(email_config["smtp_server"], email_config["smtp_port"], timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(email_config["username"], email_config["password"])
                server.sendmail(email_config["from_email"], [email_destino], msg.as_string())
        else:
            with smtplib.SMTP(email_config["smtp_server"], email_config["smtp_port"], timeout=30) as server:
                server.starttls(context=context)
                server.login(email_config["username"], email_config["password"])
                server.sendmail(email_config["from_email"], [email_destino], msg.as_string())

        logger.info(f"Email de credenciais enviado para {email_destino}")
        return True
    except Exception as e:
        logger.error(f"Erro ao enviar email: {str(e)}")
        return False


async def enviar_whatsapp_sindico_existente(telefone: str, nome: str, novo_condominio: str):
    """Envia WhatsApp orientando síndico existente (quando NÃO veio flag do frontend)"""
    mensagem = f"""👋 *e-Condomínio - Novo Condomínio Detectado*

Olá, *{nome}*!

Identificamos que você já possui cadastro no e-Condomínio e está tentando cadastrar um novo condomínio:

🏢 *{novo_condominio}*

Como você é um *síndico profissional* que gerencia múltiplos condomínios, o cadastro deve ser feito pelo painel administrativo.

📱 *Siga os passos:*

1️⃣ Acesse: https://admin.econdominio.com.br
2️⃣ Faça login com suas credenciais
3️⃣ Vá em *Condomínios* no menu lateral
4️⃣ Clique em *Novo Condomínio*
5️⃣ Preencha os dados do novo condomínio

Assim você poderá gerenciar todos os seus condomínios em um único painel! 🎉

Precisa de ajuda? Responda esta mensagem.

---
*e-Condomínio*
Sistema de Gestão de Encomendas
https://econdominio.com.br"""

    ok = _queue_whatsapp(telefone, mensagem)
    logger.info(f"WhatsApp sindico_existente enfileirado para {telefone}: {ok}")
    return ok


def enviar_email_sindico_existente(email_destino: str, nome: str, novo_condominio: str):
    """Envia email orientando síndico existente a cadastrar pelo painel"""
    try:
        email_config = get_email_config()
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🏢 e-Condomínio - Cadastre seu novo condomínio pelo painel"
        msg["From"] = f"{email_config['from_name']} <{email_config['from_email']}>"
        msg["To"] = email_destino

        texto = f"""
e-Condomínio - Novo Condomínio Detectado

Olá, {nome}!

Identificamos que você já possui cadastro no e-Condomínio e está tentando cadastrar:

🏢 {novo_condominio}

Siga os passos:
1. Acesse: https://admin.econdominio.com.br
2. Faça login com suas credenciais
3. Vá em "Condomínios" no menu lateral
4. Clique em "Novo Condomínio"
5. Preencha os dados do novo condomínio

---
e-Condomínio - https://econdominio.com.br
        """

        html = f"""
        <!DOCTYPE html>
        <html><head><meta charset="UTF-8">
            <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f5f5f5; }}
                .container {{ background: white; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
                .header {{ background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); color: white; padding: 30px; text-align: center; }}
                .content {{ padding: 30px; }}
                .btn {{ display: inline-block; background: linear-gradient(135deg, #2563eb 0%, #7c3aed 100%); color: white; padding: 15px 40px; text-decoration: none; border-radius: 8px; font-weight: bold; margin: 20px 0; }}
                .footer {{ background: #f8fafc; padding: 20px 30px; text-align: center; color: #64748b; font-size: 14px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header"><h1>🏢 Novo Condomínio Detectado</h1></div>
                <div class="content">
                    <p style="font-size: 18px;">Olá, <strong>{nome}</strong>!</p>
                    <p>Você já possui cadastro e está tentando cadastrar:</p>
                    <div style="background: #e0f2fe; border-radius: 12px; padding: 20px; margin: 20px 0; text-align: center; border-left: 4px solid #0284c7;">
                        <h3 style="color: #0369a1; margin: 0;">🏢 {novo_condominio}</h3>
                    </div>
                    <p>Cadastre pelo painel administrativo:</p>
                    <div style="text-align: center;"><a href="https://admin.econdominio.com.br" class="btn">🖥️ Acessar Painel</a></div>
                </div>
                <div class="footer"><p><strong>e-Condomínio</strong></p></div>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        context = ssl.create_default_context()
        if email_config["use_ssl"]:
            with smtplib.SMTP(email_config["smtp_server"], email_config["smtp_port"], timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(email_config["username"], email_config["password"])
                server.sendmail(email_config["from_email"], [email_destino], msg.as_string())
        else:
            with smtplib.SMTP(email_config["smtp_server"], email_config["smtp_port"], timeout=30) as server:
                server.starttls(context=context)
                server.login(email_config["username"], email_config["password"])
                server.sendmail(email_config["from_email"], [email_destino], msg.as_string())

        logger.info(f"Email de síndico existente enviado para {email_destino}")
        return True
    except Exception as e:
        logger.error(f"Erro ao enviar email: {str(e)}")
        return False


# ==============================================================================
# ✅ NOVO ENDPOINT: VERIFICAR WHATSAPP
# ==============================================================================

@cliente_router.get("/verificar-whatsapp/{telefone}")
async def verificar_whatsapp(telefone: str, db: Session = Depends(get_db)):
    """
    Verifica se WhatsApp já está cadastrado.
    Retorna info para frontend mostrar aviso e exigir email diferente.
    NÃO bloqueia o cadastro.
    """
    try:
        telefone_limpo = telefone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

        # Verificar em mobile_operadores
        resultado = db.execute(
            text("""
                SELECT mo.id, mo.nome, mo.email, c.nome as condominio_nome
                FROM mobile_operadores mo
                JOIN condominios c ON c.id = mo.condominio_id
                WHERE REPLACE(REPLACE(REPLACE(REPLACE(mo.telefone, ' ', ''), '-', ''), '(', ''), ')', '') = :telefone
                AND mo.ativo = 1
                LIMIT 1
            """),
            {"telefone": telefone_limpo}
        ).fetchone()

        if resultado:
            return {
                "existe": True,
                "nome": resultado[1],
                "email_existente": resultado[2],
                "condominio": resultado[3]
            }

        return {"existe": False}

    except Exception as e:
        logger.error(f"Erro ao verificar WhatsApp: {str(e)}")
        return {"existe": False}


# ==============================================================================
# ENDPOINT DE CADASTRO - PRINCIPAL
# ==============================================================================

@cliente_router.post("/cadastrar")
async def cadastrar_cliente(data: CadastroClienteRequest, request: Request, db: Session = Depends(get_db)):
    """
    Cadastro público de novo cliente/condomínio.

    REGRAS:
    - WhatsApp duplicado: PERMITIDO (se sindico_existente_whatsapp=True)
      → Não cria senha via nome WhatsApp
    - Email duplicado: SEMPRE BLOQUEADO (UNIQUE KEY mantida)
      → Frontend deve exigir email diferente
    """
    try:
        # ============================================
        # SEGURANCA: valor do plano NUNCA vem do payload do cliente.
        # Recalcula sempre a partir de total_apartamentos e tabela_precos.
        # ?valormensal=0.01 na URL publica do cadastro sobrescrevia o
        # preco gravado no banco sem nenhuma validacao - corrigido aqui.
        # ============================================
        faixas_precos = db.execute(text("""
            SELECT qtd_min, qtd_max, tipo, valor_fixo, valor_minimo,
                   coef_a, coef_b, fator_min, fator_max
            FROM tabela_precos WHERE ativo = 1 ORDER BY qtd_min ASC
        """)).fetchall()
        if not faixas_precos:
            raise HTTPException(status_code=500, detail="Tabela de precos nao configurada")
        faixas_precos = [dict(r._mapping) for r in faixas_precos]
        valor_calculado = _calcular_valor_mensal_base(data.total_apartamentos or 0, faixas_precos)
        data.valor_mensal_base = valor_calculado
        data.valor_plano_final = valor_calculado
        data.plano_desconto = 0

        # ============================================
        # VERIFICAR EMAIL (SEMPRE BLOQUEADO SE DUPLICADO)
        # ============================================
        sindico_por_email = db.execute(
            text("""
                SELECT mo.id, mo.nome, mo.email, c.nome as condominio_nome
                FROM mobile_operadores mo
                JOIN condominios c ON c.id = mo.condominio_id
                WHERE mo.email = :email AND mo.ativo = 1
                LIMIT 1
            """),
            {"email": data.sindico_email}
        ).fetchone()

        if sindico_por_email:
            # Email duplicado é SEMPRE bloqueado, independente de flag
            raise HTTPException(
                status_code=400,
                detail=f"EMAIL_JA_CADASTRADO|{data.sindico_email}|{sindico_por_email[3]}|Informe um email diferente para este condomínio."
            )

        # ============================================
        # VERIFICAR WHATSAPP
        # ============================================
        telefone_limpo = data.sindico_whatsapp.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        sindico_por_whatsapp = db.execute(
            text("""
                SELECT mo.id, mo.nome, mo.email, mo.telefone, c.nome as condominio_nome
                FROM mobile_operadores mo
                JOIN condominios c ON c.id = mo.condominio_id
                WHERE REPLACE(REPLACE(REPLACE(REPLACE(mo.telefone, ' ', ''), '-', ''), '(', ''), ')', '') = :telefone
                AND mo.ativo = 1
                LIMIT 1
            """),
            {"telefone": telefone_limpo}
        ).fetchone()

        is_whatsapp_existente = False
        operador_existente_id = None

        if sindico_por_whatsapp:
            if not data.sindico_existente_whatsapp:
                # Frontend NÃO sinalizou → bloquear (comportamento antigo)
                await enviar_whatsapp_sindico_existente(
                    data.sindico_whatsapp,
                    sindico_por_whatsapp[1],
                    data.nome_fantasia or data.razao_social
                )
                if sindico_por_whatsapp[2]:
                    enviar_email_sindico_existente(
                        sindico_por_whatsapp[2],
                        sindico_por_whatsapp[1],
                        data.nome_fantasia or data.razao_social
                    )
                raise HTTPException(
                    status_code=400,
                    detail=f"SINDICO_EXISTENTE_WHATSAPP|{data.sindico_whatsapp}|{sindico_por_whatsapp[4]}"
                )
            else:
                # ✅ Frontend sinalizou → permitir, guardar referência
                is_whatsapp_existente = True
                operador_existente_id = sindico_por_whatsapp[0]
                logger.info(f"Síndico com WhatsApp existente cadastrando novo condomínio: {telefone_limpo} - Email novo: {data.sindico_email}")

        # ============================================
        # VERIFICAR CNPJ
        # ============================================
        existe_condominio = db.execute(
            text("SELECT id FROM condominios WHERE cnpj = :cnpj"),
            {"cnpj": data.cnpj}
        ).fetchone()

        if existe_condominio:
            raise HTTPException(
                status_code=400,
                detail="CNPJ já cadastrado no sistema. Acesse https://admin.econdominio.com.br e use 'Recuperar Senha' se necessário."
            )

        existe_contato = db.execute(
            text("SELECT id, status FROM contato_condominios WHERE cnpj = :cnpj"),
            {"cnpj": data.cnpj}
        ).fetchone()

        if existe_contato:
            status = existe_contato[1]
            if status == 'convertido':
                raise HTTPException(status_code=400, detail="CNPJ já cadastrado. Acesse o painel para fazer login.")
            elif status == 'pendente':
                raise HTTPException(status_code=400, detail="Cadastro pendente de confirmação. Verifique seu WhatsApp ou solicite novo link.")

        # ============================================
        # SALVAR PRÉ-CADASTRO
        # ============================================
        chave_confirmacao = secrets.token_urlsafe(32)
        ip_cadastro = request.client.host if request.client else None

        db.execute(
            text("""
                INSERT INTO contato_condominios (
                    cnpj, razao_social, nome_fantasia,
                    endereco, numero, complemento, bairro, cidade, estado, cep,
                    sindico_nome, sindico_email, sindico_whatsapp, sindico_cpf,
                    total_apartamentos, email_condominio, telefone_condominio, whatsapp_condominio,
                    financeiro_responsavel, financeiro_email, financeiro_whatsapp,
                    plano_selecionado, forma_pagamento, valor_mensal_base, valor_plano_final, plano_desconto,
                    chave_confirmacao, status, ip_cadastro, data_cadastro,
                    sindico_existente, operador_existente_id
                ) VALUES (
                    :cnpj, :razao_social, :nome_fantasia,
                    :endereco, :numero, :complemento, :bairro, :cidade, :estado, :cep,
                    :sindico_nome, :sindico_email, :sindico_whatsapp, :sindico_cpf,
                    :total_apartamentos, :email_condominio, :telefone_condominio, :whatsapp_condominio,
                    :financeiro_responsavel, :financeiro_email, :financeiro_whatsapp,
                    :plano_selecionado, :forma_pagamento, :valor_mensal_base, :valor_plano_final, :plano_desconto,
                    :chave_confirmacao, 'pendente', :ip_cadastro, NOW(),
                    :sindico_existente, :operador_existente_id
                )
            """),
            {
                "cnpj": data.cnpj,
                "razao_social": data.razao_social,
                "nome_fantasia": data.nome_fantasia,
                "endereco": data.endereco,
                "numero": data.numero,
                "complemento": data.complemento,
                "bairro": data.bairro,
                "cidade": data.cidade,
                "estado": data.estado,
                "cep": data.cep,
                "sindico_nome": data.sindico_nome,
                "sindico_email": data.sindico_email,
                "sindico_whatsapp": data.sindico_whatsapp,
                "sindico_cpf": data.sindico_cpf,
                "total_apartamentos": data.total_apartamentos,
                "email_condominio": data.email_condominio,
                "telefone_condominio": data.telefone_condominio,
                "whatsapp_condominio": data.whatsapp_condominio,
                "financeiro_responsavel": data.financeiro_responsavel,
                "financeiro_email": data.financeiro_email,
                "financeiro_whatsapp": data.financeiro_whatsapp,
                "plano_selecionado": data.plano_selecionado,
                "forma_pagamento": data.forma_pagamento,
                "valor_mensal_base": data.valor_mensal_base,
                "valor_plano_final": data.valor_plano_final,
                "plano_desconto": data.plano_desconto,
                "chave_confirmacao": chave_confirmacao,
                "ip_cadastro": ip_cadastro,
                "sindico_existente": 1 if is_whatsapp_existente else 0,
                "operador_existente_id": operador_existente_id
            }
        )
        db.commit()

        # Enviar WhatsApp com link de confirmação (background - não bloqueia)
        async def enviar_confirmacao_background():
            try:
                await enviar_whatsapp_confirmacao(data.sindico_whatsapp, data.sindico_nome, chave_confirmacao)
            except Exception as e:
                logger.warning(f"Erro ao enviar WhatsApp de confirmação: {e}")

        asyncio.create_task(enviar_confirmacao_background())

        logger.info(
            f"Pré-cadastro: {data.razao_social} - CNPJ: {data.cnpj} - "
            f"Plano: {data.plano_selecionado} - Valor: R$ {data.valor_plano_final} - "
            f"WhatsApp existente: {is_whatsapp_existente}"
        )

        return {
            "success": True,
            "message": "Cadastro recebido! Enviamos um link de confirmação para seu WhatsApp.",
            "condominio_id": None,
            "sindico_existente_whatsapp": is_whatsapp_existente
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao cadastrar cliente: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao realizar cadastro: {str(e)}")


@cliente_router.get("/verificar-chave/{chave}")
async def verificar_chave(chave: str, db: Session = Depends(get_db)):
    try:
        result = db.execute(
            text("""
                SELECT id, nome_fantasia, razao_social, sindico_nome, status, data_cadastro
                FROM contato_condominios WHERE chave_confirmacao = :chave
            """),
            {"chave": chave}
        ).fetchone()

        if not result:
            return {"valid": False, "error": "Link inválido"}

        status = result[4]
        data_cadastro = result[5]

        if status == 'convertido':
            return {"valid": False, "error": "Link já utilizado", "status": "convertido"}

        if data_cadastro:
            expira_em = data_cadastro + timedelta(hours=48)
            if datetime.now() > expira_em:
                return {"valid": False, "error": "Link expirado"}

        nome = result[1] or result[2]
        return {"valid": True, "data": {"nome": nome, "sindico": result[3]}}

    except Exception as e:
        logger.error(f"Erro ao verificar chave: {str(e)}")
        return {"valid": False, "error": str(e)}


@cliente_router.post("/confirmar")
async def confirmar_cadastro(data: ConfirmarCadastroRequest, request: Request, db: Session = Depends(get_db)):
    """
    Confirma cadastro e converte para condomínio ativo.

    Se sindico_existente=1: Cria operador com EMAIL NOVO (diferente) + senha temporária
    Se sindico_existente=0: Fluxo normal (cria operador com email + senha)

    Em AMBOS os casos cria novo registro em mobile_operadores com email ÚNICO.
    O nome do WhatsApp NÃO é usado como senha quando é WhatsApp existente.
    """
    try:
        contato = db.execute(
            text("""
                SELECT id, cnpj, razao_social, nome_fantasia,
                       endereco, numero, complemento, bairro, cidade, estado, cep,
                       sindico_nome, sindico_email, sindico_whatsapp, sindico_cpf,
                       total_apartamentos, email_condominio, telefone_condominio, whatsapp_condominio,
                       financeiro_responsavel, financeiro_email, financeiro_whatsapp,
                       plano_selecionado, forma_pagamento, valor_mensal_base, valor_plano_final, plano_desconto,
                       status, data_cadastro,
                       sindico_existente, operador_existente_id
                FROM contato_condominios
                WHERE chave_confirmacao = :chave
            """),
            {"chave": data.chave}
        ).fetchone()

        if not contato:
            raise HTTPException(status_code=400, detail="Link de confirmação inválido")

        # Índices: status=27, data_cadastro=28, sindico_existente=29, operador_existente_id=30
        if contato[27] == 'convertido':
            raise HTTPException(status_code=400, detail="Este cadastro já foi confirmado anteriormente")

        data_cadastro = contato[28]
        if data_cadastro:
            expira_em = data_cadastro + timedelta(hours=48)
            if datetime.now() > expira_em:
                raise HTTPException(status_code=400, detail="Link expirado. Faça um novo cadastro.")

        is_whatsapp_existente = bool(contato[29]) if contato[29] else False
        operador_existente_id = contato[30]

        # Verificar CNPJ
        cnpj = contato[1]
        existe = db.execute(text("SELECT id FROM condominios WHERE cnpj = :cnpj"), {"cnpj": cnpj}).fetchone()

        if existe:
            db.execute(text("UPDATE contato_condominios SET status = 'convertido' WHERE id = :id"), {"id": contato[0]})
            db.commit()
            raise HTTPException(status_code=400, detail="CNPJ já cadastrado no sistema")

        # ============================================
        # CRIAR CONDOMÍNIO
        # ============================================
        data_hoje = date.today()
        data_validade = data_hoje + timedelta(days=DIAS_BONIFICADOS)

        result = db.execute(
            text("""
                INSERT INTO condominios (
                    cnpj, nome, endereco, numero, complemento, bairro, cidade, estado, cep,
                    email, telefone, sindico, total_apartamentos, ativo,
                    cobranca_responsavel, cobranca_email, cobranca_whats,
                    validade_ate, assinatura_status,
                    periodo_bonificado, data_inicio_bonificado, data_fim_bonificado,
                    data_cadastro
                ) VALUES (
                    :cnpj, :nome, :endereco, :numero, :complemento, :bairro, :cidade, :estado, :cep,
                    :email, :telefone, :sindico, :total_apartamentos, 1,
                    :cobranca_responsavel, :cobranca_email, :cobranca_whats,
                    :validade_ate, 'trial',
                    1, :data_inicio, :data_fim,
                    NOW()
                )
            """),
            {
                "cnpj": cnpj,
                "nome": contato[3] or contato[2],
                "endereco": contato[4],
                "numero": contato[5],
                "complemento": contato[6],
                "bairro": contato[7],
                "cidade": contato[8],
                "estado": contato[9],
                "cep": contato[10],
                "email": contato[16],
                "telefone": contato[17],
                "sindico": contato[11],
                "total_apartamentos": contato[15],
                "cobranca_responsavel": contato[19],
                "cobranca_email": contato[20],
                "cobranca_whats": contato[21],
                "validade_ate": data_validade,
                "data_inicio": data_hoje,
                "data_fim": data_validade
            }
        )
        db.commit()
        condominio_id = result.lastrowid

        # ============================================
        # CRIAR OPERADOR (SEMPRE com email ÚNICO + senha temporária)
        # ============================================
        senha_temp = secrets.token_urlsafe(8)
        senha_hash = hash_password(senha_temp)

        # O email do sindico JÁ FOI VALIDADO como único no /cadastrar
        db.execute(
            text("""
                INSERT INTO mobile_operadores (
                    condominio_id, nome, email, telefone,
                    senha_hash, role, ativo
                ) VALUES (
                    :condominio_id, :nome, :email, :telefone,
                    :senha_hash, 'sindico', 1
                )
            """),
            {
                "condominio_id": condominio_id,
                "nome": contato[11],   # sindico_nome
                "email": contato[12],  # sindico_email (sempre único)
                "telefone": contato[13],  # sindico_whatsapp
                "senha_hash": senha_hash
            }
        )
        db.commit()

        # Atualizar status do contato
        ip_confirmacao = request.client.host if request.client else None
        db.execute(
            text("""
                UPDATE contato_condominios
                SET status = 'convertido', confirmado = 1, data_confirmacao = NOW(), ip_confirmacao = :ip
                WHERE id = :id
            """),
            {"id": contato[0], "ip": ip_confirmacao}
        )
        db.commit()

        # ============================================
        # ENVIAR CREDENCIAIS (em background - não bloqueia o response)
        # ============================================
        async def enviar_notificacoes_background():
            """Envia WhatsApp e Email em background para não travar a tela"""
            if is_whatsapp_existente:
                try:
                    await enviar_whatsapp_novo_condominio(
                        contato[13], contato[11], contato[3] or contato[2],
                        contato[12], senha_temp, data_validade
                    )
                except Exception as e:
                    logger.warning(f"Erro ao enviar WhatsApp novo condomínio: {e}")
            else:
                try:
                    await enviar_whatsapp_credenciais(contato[13], contato[11], contato[12], senha_temp, data_validade)
                except Exception as e:
                    logger.warning(f"Erro ao enviar WhatsApp de credenciais: {e}")
            try:
                enviar_email_credenciais(contato[12], contato[11], contato[12], senha_temp, data_validade)
            except Exception as e:
                logger.warning(f"Erro ao enviar email de credenciais: {e}")

        # Dispara em background (não espera terminar)
        asyncio.create_task(enviar_notificacoes_background())

        logger.info(
            f"Cadastro confirmado: {contato[2]} (ID: {condominio_id}) - "
            f"Plano: {contato[22]} - Valor: R$ {contato[25]} - "
            f"Validade: {data_validade} - WhatsApp existente: {is_whatsapp_existente}"
        )

        return {
            "success": True,
            "message": "Cadastro confirmado com sucesso!",
            "data": {
                "condominio_id": condominio_id,
                "nome": contato[3] or contato[2],
                "email": contato[12],
                "senha": senha_temp,
                "painel_url": "https://admin.econdominio.com.br",
                "mobile_url": "https://portaria.econdominio.com.br",
                "validade_ate": data_validade.strftime("%d/%m/%Y"),
                "dias_bonificados": DIAS_BONIFICADOS,
                "plano_selecionado": contato[22],
                "valor_mensal_base": float(contato[24]),
                "valor_plano_final": float(contato[25]),
                "plano_desconto": contato[26],
                "sindico_existente_whatsapp": is_whatsapp_existente
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao confirmar cadastro: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao confirmar cadastro: {str(e)}")


@cliente_router.post("/reenviar-confirmacao")
async def reenviar_confirmacao(cnpj: str, db: Session = Depends(get_db)):
    try:
        cnpj_limpo = cnpj.replace(".", "").replace("/", "").replace("-", "")

        contato = db.execute(
            text("""
                SELECT id, sindico_nome, sindico_whatsapp, chave_confirmacao, status
                FROM contato_condominios
                WHERE REPLACE(REPLACE(REPLACE(cnpj, '.', ''), '/', ''), '-', '') = :cnpj
                ORDER BY data_cadastro DESC LIMIT 1
            """),
            {"cnpj": cnpj_limpo}
        ).fetchone()

        if not contato:
            raise HTTPException(status_code=404, detail="Cadastro não encontrado")

        if contato[4] == 'convertido':
            raise HTTPException(status_code=400, detail="Cadastro já confirmado. Acesse o painel para fazer login.")

        nova_chave = secrets.token_urlsafe(32)

        db.execute(
            text("UPDATE contato_condominios SET chave_confirmacao = :chave, data_cadastro = NOW() WHERE id = :id"),
            {"chave": nova_chave, "id": contato[0]}
        )
        db.commit()

        await enviar_whatsapp_confirmacao(contato[2], contato[1], nova_chave)

        return {"success": True, "message": "Novo link de confirmação enviado para o WhatsApp"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao reenviar confirmação: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao reenviar: {str(e)}")
