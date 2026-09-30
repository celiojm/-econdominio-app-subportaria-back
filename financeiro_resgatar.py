# =============================================================================
# ARQUIVO : financeiro_resgatar.py
# ROTA: POST /api/financeiro/assinaturas/cadastros/{condominio_id}/resgatar
#
# FLUXO:
#   - contato_condominios = pré-cadastro
#   - Se confirmado=0 (pendente) → envia link de confirmação de cadastro
#   - Se confirmado=1 (ativo)    → envia links do painel + app + orientação de senha
# =============================================================================

import os
import ssl
import logging
import smtplib
import httpx

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÕES
# ─────────────────────────────────────────────────────────────────────────────

def get_zapi_config():
    return {
        "instance":     os.getenv("ZAPI_INSTANCE_ID",  ""),
        "token":        os.getenv("ZAPI_TOKEN",        ""),
        "client_token": os.getenv("ZAPI_CLIENT_TOKEN", ""),
    }

def get_email_config():
    return {
        "smtp_server": os.getenv("EMAIL_SMTP_SERVER",     "smtp-relay.brevo.com"),
        "smtp_port":   int(os.getenv("EMAIL_SMTP_PORT",   "587")),
        "username":    os.getenv("EMAIL_SMTP_USERNAME",   ""),
        "password":    os.getenv("EMAIL_SMTP_PASSWORD",   ""),
        "from_email":  os.getenv("RESGATAR_FROM_ADDRESS", "contato@econdominio.com.br"),
        "from_name":   os.getenv("RESGATAR_FROM_NAME",    "Contato eCondomínio"),
    }

PAINEL_URL = "https://admin.econdominio.com.br"
MOBILE_URL = "https://portaria.econdominio.com.br"
SITE_URL   = "https://econdominio.com.br"
CONFIRMAR_URL = "https://painel.econdominio.app.br"


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _enviar_whatsapp(telefone: str, mensagem: str):
    zapi = get_zapi_config()
    url  = (
        f"https://api.z-api.io/instances/{zapi['instance']}"
        f"/token/{zapi['token']}/send-text"
    )
    tel = ''.join(c for c in telefone if c.isdigit())
    if not tel.startswith("55"):
        tel = "55" + tel
    headers = {
        "Content-Type": "application/json",
        "Client-Token":  zapi["client_token"],
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json={"phone": tel, "message": mensagem}, headers=headers)
            if resp.status_code == 200:
                return True, ""
            return False, f"Z-API {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return False, str(e)


def _enviar_email(destinatario: str, assunto: str, corpo_html: str):
    cfg = get_email_config()
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"]    = f"{cfg['from_name']} <{cfg['from_email']}>"
        msg["To"]      = destinatario
        msg.attach(MIMEText(corpo_html, "html", "utf-8"))

        with smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"], timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(cfg["username"], cfg["password"])
            s.sendmail(cfg["from_email"], [destinatario], msg.as_string())

        logger.info(f"[resgatar] Email OK → {destinatario}")
        return True, ""
    except Exception as e:
        logger.error(f"[resgatar] Erro email {destinatario}: {e}")
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# MENSAGENS — FLUXO 1: Cadastro pendente (não confirmado)
# ─────────────────────────────────────────────────────────────────────────────

def _msg_whatsapp_pendente(nome_cond: str, link: str) -> str:
    return (
        f"Olá, *{nome_cond}*! Tudo bem? 👋\n\n"
        f"Percebi que você iniciou o cadastro do seu condomínio no *eCondomínio* "
        f"e chegou até a etapa de criar a senha, mas não finalizou.\n\n"
        f"✅ Você pode concluir agora por aqui (leva menos de 2 minutos):\n"
        f"👉 {link}\n\n"
        f"🎁 Lembrando: você tem *7 dias grátis* para testar e não há cobrança agora.\n\n"
        f"Se preferir, me diga o nome do condomínio e a quantidade de unidades "
        f"que eu te ajudo a concluir rapidinho por WhatsApp.\n\n"
        f"Abraço,\n*Equipe eCondomínio*\n{SITE_URL}"
    )


def _email_pendente(nome_cond: str, link: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;max-width:600px;margin:auto;
             padding:20px;color:#333;background:#f5f5f5;">
  <div style="background:linear-gradient(135deg,#1d4ed8,#7c3aed);border-radius:16px;
              padding:28px;text-align:center;margin-bottom:20px;">
    <h1 style="color:white;margin:0;font-size:24px;">e-Condomínio</h1>
    <p style="color:#bfdbfe;margin:6px 0 0;font-size:14px;">Você quase chegou lá! 🏁</p>
  </div>
  <div style="background:white;border-radius:12px;padding:28px;margin-bottom:16px;
              box-shadow:0 1px 4px rgba(0,0,0,0.08);">
    <p style="font-size:16px;line-height:1.7;margin:0 0 16px;">
      Olá, <strong>{nome_cond}</strong>! Tudo bem? 👋
    </p>
    <p style="font-size:15px;line-height:1.7;color:#475569;margin:0 0 24px;">
      Percebi que você iniciou o cadastro do seu condomínio no <strong>eCondomínio</strong>
      e chegou até a etapa de criar a senha, mas não finalizou.<br>
      Leva menos de 2 minutos para concluir — clique no botão abaixo:
    </p>
    <div style="text-align:center;margin:24px 0;">
      <a href="{link}"
         style="background:linear-gradient(135deg,#16a34a,#15803d);color:white;
                text-decoration:none;padding:16px 40px;border-radius:12px;
                font-weight:bold;font-size:17px;display:inline-block;">
        ✅ Continuar Cadastro
      </a>
    </div>
    <div style="background:#fefce8;border:1px solid #fde047;border-radius:10px;
                padding:16px;margin-top:20px;">
      <p style="color:#854d0e;font-weight:bold;margin:0 0 6px;">🎁 Lembrando:</p>
      <p style="color:#713f12;font-size:14px;margin:0;">
        Você tem <strong>7 dias grátis</strong> para testar e não há cobrança agora.
      </p>
    </div>
  </div>
  <div style="background:white;border-radius:12px;padding:16px;margin-bottom:16px;">
    <p style="font-size:12px;color:#64748b;margin:0 0 6px;">Se o botão não funcionar, copie e cole:</p>
    <a href="{link}" style="color:#2563eb;font-size:12px;word-break:break-all;">{link}</a>
  </div>
  <p style="font-size:13px;color:#94a3b8;text-align:center;">
    Abraço, <strong style="color:#475569;">Equipe eCondomínio</strong> •
    <a href="{SITE_URL}" style="color:#2563eb;">econdominio.com.br</a>
  </p>
</body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
# MENSAGENS — FLUXO 2: Conta já ativada (confirmado=1)
# ─────────────────────────────────────────────────────────────────────────────

def _msg_whatsapp_ativo(nome_cond: str, email: str) -> str:
    return (
        f"Olá, *{nome_cond}*! 👋\n\n"
        f"Sua conta no *eCondomínio* já está ativa! Acesse pelos links abaixo:\n\n"
        f"🖥️ *Painel Administrativo:*\n{PAINEL_URL}\n\n"
        f"📱 *App Operacional (portaria):*\n{MOBILE_URL}\n\n"
        f"🔑 *Seu login:* {email}\n\n"
        f"Caso não lembre a senha, clique em *\"Esqueci a senha\"* na tela de acesso "
        f"que enviaremos um link de redefinição para o seu email.\n\n"
        f"Qualquer dúvida, estamos aqui! 😊\n"
        f"*Equipe eCondomínio* • {SITE_URL}"
    )


def _email_ativo(nome_cond: str, email_login: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;max-width:600px;margin:auto;
             padding:20px;color:#333;background:#f5f5f5;">
  <div style="background:linear-gradient(135deg,#16a34a,#059669);border-radius:16px;
              padding:28px;text-align:center;margin-bottom:20px;">
    <h1 style="color:white;margin:0;font-size:24px;">e-Condomínio</h1>
    <p style="color:#bbf7d0;margin:6px 0 0;font-size:14px;">Sua conta está ativa! ✅</p>
  </div>
  <div style="background:white;border-radius:12px;padding:28px;margin-bottom:16px;
              box-shadow:0 1px 4px rgba(0,0,0,0.08);">
    <p style="font-size:16px;line-height:1.7;margin:0 0 16px;">
      Olá, <strong>{nome_cond}</strong>! 👋
    </p>
    <p style="font-size:15px;color:#475569;margin:0 0 24px;">
      Sua conta no <strong>eCondomínio</strong> já está ativa. Acesse pelos links abaixo:
    </p>

    <!-- Login -->
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                padding:16px;margin-bottom:20px;">
      <p style="font-size:13px;color:#64748b;margin:0 0 4px;">Seu login:</p>
      <p style="font-family:monospace;font-size:15px;font-weight:bold;
                color:#1e293b;margin:0;">{email_login}</p>
    </div>

    <!-- Botões -->
    <div style="display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap;">
      <a href="{PAINEL_URL}"
         style="flex:1;min-width:140px;display:inline-block;background:#2563eb;color:white;
                text-align:center;padding:14px 16px;border-radius:10px;
                text-decoration:none;font-weight:bold;font-size:14px;">
        🖥️ Painel Administrativo
      </a>
      <a href="{MOBILE_URL}"
         style="flex:1;min-width:140px;display:inline-block;background:#7c3aed;color:white;
                text-align:center;padding:14px 16px;border-radius:10px;
                text-decoration:none;font-weight:bold;font-size:14px;">
        📱 App Operacional
      </a>
    </div>

    <!-- Esqueci a senha -->
    <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:16px;">
      <p style="color:#1e40af;font-weight:bold;margin:0 0 6px;">🔑 Não lembra a senha?</p>
      <p style="color:#1e3a8a;font-size:14px;margin:0;">
        Na tela de acesso, clique em <strong>"Esqueci a senha"</strong> e enviaremos
        um link de redefinição para o seu email.
      </p>
    </div>
  </div>
  <p style="font-size:13px;color:#94a3b8;text-align:center;">
    Equipe <strong style="color:#475569;">eCondomínio</strong> •
    <a href="{SITE_URL}" style="color:#2563eb;">econdominio.com.br</a>
  </p>
</body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
# PAYLOAD
# ─────────────────────────────────────────────────────────────────────────────

class ResgatarPayload(BaseModel):
    canal: str = "whatsapp"   # 'whatsapp' | 'email' | 'ambos'


# ─────────────────────────────────────────────────────────────────────────────
# ROTA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/assinaturas/cadastros/{condominio_id}/resgatar")
async def resgatar_cliente(
    condominio_id: int,
    payload: ResgatarPayload = ResgatarPayload(),
    db: Session = Depends(get_db)
):
    # 1. Buscar cadastro
    row = db.execute(text("""
        SELECT id, nome_fantasia, razao_social, sindico_nome,
               sindico_email, sindico_whatsapp, chave_confirmacao,
               status, confirmado
        FROM contato_condominios
        WHERE id = :id
        LIMIT 1
    """), {"id": condominio_id}).fetchone()

    if not row:
        raise HTTPException(status_code=404,
            detail=f"Pré-cadastro #{condominio_id} não encontrado")

    nome_cond  = (row[1] or row[2] or "Condomínio").strip()
    sindico    = (row[3] or "Síndico").strip()
    email      = (row[4] or "").strip()
    whatsapp   = (row[5] or "").strip()
    chave      = (row[6] or "").strip()
    status     = (row[7] or "").strip()
    confirmado = bool(row[8])

    if not email and not whatsapp:
        raise HTTPException(status_code=400,
            detail="Nenhum contato disponível (sem email e sem WhatsApp)")

    canal  = payload.canal
    canais = []
    erros  = []

    # ─────────────────────────────────────────────────────────────────────────
    # FLUXO 1: Conta já ativada → envia links de acesso
    # ─────────────────────────────────────────────────────────────────────────
    if confirmado:
        email_login = email or "seu email cadastrado"

        if whatsapp and canal in ("whatsapp", "ambos"):
            ok, err = await _enviar_whatsapp(whatsapp, _msg_whatsapp_ativo(nome_cond, email_login))
            if ok: canais.append("WhatsApp")
            else:  erros.append(f"WhatsApp: {err}")

        if email and canal in ("email", "ambos"):
            ok, err = _enviar_email(
                email,
                f"🔑 Acesse o eCondomínio — {nome_cond}",
                _email_ativo(nome_cond, email_login)
            )
            if ok: canais.append("Email")
            else:  erros.append(f"Email: {err}")

    # ─────────────────────────────────────────────────────────────────────────
    # FLUXO 2: Cadastro pendente → envia link de confirmação
    # ─────────────────────────────────────────────────────────────────────────
    else:
        if not chave:
            raise HTTPException(status_code=404,
                detail="Chave de confirmação não encontrada ou expirada.")

        link = f"{CONFIRMAR_URL}/confirmar/{chave}"

        if whatsapp and canal in ("whatsapp", "ambos"):
            ok, err = await _enviar_whatsapp(whatsapp, _msg_whatsapp_pendente(nome_cond, link))
            if ok: canais.append("WhatsApp")
            else:  erros.append(f"WhatsApp: {err}")

        if email and canal in ("email", "ambos"):
            ok, err = _enviar_email(
                email,
                f"🎉 e-Condomínio — Confirme seu cadastro, {nome_cond}!",
                _email_pendente(nome_cond, link)
            )
            if ok: canais.append("Email")
            else:  erros.append(f"Email: {err}")

    if not canais:
        detalhe = " | ".join(erros) if erros else "Falha ao enviar."
        raise HTTPException(status_code=500, detail=detalhe)

    # Registrar observação
    from datetime import datetime
    agora      = datetime.now().strftime("%d/%m/%Y %H:%M")
    tipo       = "Acesso (conta ativa)" if confirmado else "Confirmação de cadastro"
    nova_linha = f"[{agora}] Resgate enviado ({tipo}) via {' e '.join(canais)}."

    try:
        db.execute(text("""
            UPDATE contato_condominios
            SET observacoes = CASE
                WHEN observacoes IS NULL OR TRIM(observacoes) = ''
                THEN :nova_linha
                ELSE CONCAT(observacoes, '\n', :nova_linha)
            END
            WHERE id = :id
        """), {"nova_linha": nova_linha, "id": row[0]})
        db.commit()
    except Exception as e:
        logger.warning(f"[resgatar] Falha ao registrar observação: {e}")

    return {
        "success":    True,
        "message":    f"Enviado via: {' e '.join(canais)}",
        "canais":     canais,
        "sindico":    sindico,
        "condominio": nome_cond,
        "tipo":       "ativo" if confirmado else "pendente",
    }
