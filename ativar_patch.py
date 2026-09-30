# ==============================================================================
# ROTA: POST /assinaturas/cadastros/{id}/ativar
# Converte cadastro pendente → confirmado + envia credenciais via WhatsApp/Email
# ==============================================================================

import secrets
import string
import smtplib
import httpx
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

PAINEL_URL = "https://admin.econdominio.com.br"
MOBILE_URL = "https://portaria.econdominio.com.br"

def _gerar_senha(tamanho: int = 10) -> str:
    alfabeto = (string.ascii_letters
                .replace("l", "").replace("O", "").replace("I", "")
                + string.digits.replace("0", ""))
    return "".join(secrets.choice(alfabeto) for _ in range(tamanho))


def _enviar_email_ativacao(dest: str, nome_cond: str, sindico: str, email: str, senha: str) -> bool:
    try:
        smtp_server   = os.getenv("EMAIL_SMTP_SERVER",   "rbr16.dizinc.com")
        smtp_port     = int(os.getenv("EMAIL_SMTP_PORT", "465"))
        smtp_user     = os.getenv("EMAIL_SMTP_USERNAME", "contato@econdominio.com.br")
        smtp_pass     = os.getenv("EMAIL_SMTP_PASSWORD", "")
        from_addr     = os.getenv("EMAIL_FROM_ADDRESS",  "contato@econdominio.com.br")
        from_name     = os.getenv("EMAIL_FROM_NAME",     "e-Condomínio")

        corpo = f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden">
          <div style="background:linear-gradient(135deg,#16a34a,#059669);padding:28px 24px;text-align:center">
            <h1 style="color:#fff;margin:0;font-size:22px">🎉 Conta Ativada!</h1>
            <p style="color:#bbf7d0;margin:6px 0 0;font-size:14px">{nome_cond}</p>
          </div>
          <div style="padding:28px 24px;background:#fff">
            <p style="color:#374151;margin:0 0 16px">Olá, <strong>{sindico}</strong>!</p>
            <p style="color:#374151;margin:0 0 20px">
              Sua conta no <strong>e-Condomínio</strong> foi ativada pela nossa equipe. Confira suas credenciais:
            </p>
            <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:16px;margin-bottom:20px">
              <table style="width:100%;border-collapse:collapse">
                <tr>
                  <td style="padding:6px 0;color:#6b7280;font-size:13px">Email / Login</td>
                  <td style="padding:6px 0;font-weight:600;font-family:monospace;color:#1f2937;text-align:right">{email}</td>
                </tr>
                <tr>
                  <td style="padding:6px 0;color:#6b7280;font-size:13px">Senha temporária</td>
                  <td style="padding:6px 0;text-align:right">
                    <span style="background:#fef3c7;color:#92400e;font-family:monospace;font-weight:700;font-size:16px;padding:2px 10px;border-radius:6px">{senha}</span>
                  </td>
                </tr>
              </table>
            </div>
            <div style="display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap">
              <a href="{PAINEL_URL}" style="flex:1;min-width:140px;display:inline-block;background:#2563eb;color:#fff;text-align:center;padding:12px 16px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
                🖥️ Painel Administrativo
              </a>
              <a href="{MOBILE_URL}" style="flex:1;min-width:140px;display:inline-block;background:#7c3aed;color:#fff;text-align:center;padding:12px 16px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
                📱 App Operacional
              </a>
            </div>
            <div style="background:linear-gradient(135deg,#16a34a,#059669);border-radius:10px;padding:16px;text-align:center;color:#fff;margin-bottom:20px">
              <p style="margin:0;font-weight:700;font-size:16px">🎁 7 DIAS GRÁTIS ATIVADOS!</p>
              <p style="margin:4px 0 0;font-size:13px;color:#bbf7d0">Você não será cobrado agora. Só pague se gostar!</p>
            </div>
            <p style="color:#6b7280;font-size:12px;text-align:center;margin:0">
              ⚠️ Por segurança, altere sua senha no primeiro acesso.<br>
              Dúvidas? <a href="mailto:suporte@econdominio.com.br" style="color:#2563eb">suporte@econdominio.com.br</a>
            </p>
          </div>
          <div style="background:#f9fafb;padding:12px;text-align:center;border-top:1px solid #e5e7eb">
            <p style="color:#9ca3af;font-size:11px;margin:0">© 2026 e-Condomínio • Todos os direitos reservados</p>
          </div>
        </div>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🎉 Sua conta no e-Condomínio foi ativada — {nome_cond}"
        msg["From"]    = f"{from_name} <{from_addr}>"
        msg["To"]      = dest
        msg.attach(MIMEText(corpo, "html", "utf-8"))

        with smtplib.SMTP_SSL(smtp_server, smtp_port) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(from_addr, dest, msg.as_string())
        return True
    except Exception as e:
        print(f"[ERRO] email ativacao: {e}")
        return False


async def _enviar_whatsapp_ativacao(whatsapp: str, nome_cond: str, email: str, senha: str) -> bool:
    try:
        zapi_instance = os.getenv("ZAPI_INSTANCE_ID", "")
        zapi_token    = os.getenv("ZAPI_TOKEN", "")
        zapi_url      = os.getenv("ZAPI_API_URL", "http://191.252.221.192:8080")
        client_token  = os.getenv("ZAPI_CLIENT_TOKEN", "")

        tel = whatsapp.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if not tel.startswith("55"):
            tel = f"55{tel}"

        msg = (
            f"🎉 *{nome_cond}* — sua conta foi *ativada* pela nossa equipe!\n\n"
            f"📧 *Login:* {email}\n"
            f"🔑 *Senha:* `{senha}`\n\n"
            f"🖥️ *Painel Administrativo:*\n{PAINEL_URL}\n\n"
            f"📱 *App Operacional (portaria):*\n{MOBILE_URL}\n\n"
            f"⚠️ Altere sua senha no primeiro acesso.\n"
            f"🎁 Você tem *7 dias grátis* — aproveite!\n\n"
            f"Dúvidas? wa.me/5548984046118"
        )

        url = f"{zapi_url}/instances/{zapi_instance}/token/{zapi_token}/send-text"
        headers = {"Client-Token": client_token, "Content-Type": "application/json"}
        payload = {"phone": tel, "message": msg}

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=payload, headers=headers)
            return r.status_code == 200
    except Exception as e:
        print(f"[ERRO] whatsapp ativacao: {e}")
        return False


@router.post("/assinaturas/cadastros/{cadastro_id}/ativar")
async def ativar_conta_condominio(cadastro_id: int, db: Session = Depends(get_db)):
    """
    Ativa a conta de um cadastro pendente/convertido.
    - Marca confirmado=1, status='confirmado'
    - Gera senha temporária
    - Envia credenciais via WhatsApp e Email
    """
    try:
        # 1. Busca o cadastro
        row = db.execute(text("""
            SELECT id, razao_social, nome_fantasia, sindico_nome,
                   sindico_email, sindico_whatsapp, confirmado, status
            FROM contato_condominios
            WHERE id = :id
        """), {"id": cadastro_id}).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Cadastro não encontrado")

        if row[6]:  # confirmado
            raise HTTPException(status_code=409, detail="ALREADY_CONFIRMED: Conta já foi ativada anteriormente")

        nome_cond = row[2] or row[1] or "Condomínio"
        sindico   = row[3] or "Síndico"
        email     = (row[4] or "").strip()
        whatsapp  = (row[5] or "").strip()

        if not email:
            raise HTTPException(status_code=422, detail="Email do síndico não cadastrado")

        # 2. Gera senha
        senha = _gerar_senha(10)

        # 3. Atualiza banco
        db.execute(text("""
            UPDATE contato_condominios
            SET confirmado       = 1,
                status           = 'confirmado',
                data_confirmacao = NOW(),
                data_atualizacao = NOW()
            WHERE id = :id
        """), {"id": cadastro_id})
        db.commit()

        # 4. Envia notificações
        canais = []

        if whatsapp and len(whatsapp.replace(" ", "")) >= 10:
            ok_wpp = await _enviar_whatsapp_ativacao(whatsapp, nome_cond, email, senha)
            if ok_wpp:
                canais.append("WhatsApp")

        if email:
            ok_email = _enviar_email_ativacao(email, nome_cond, sindico, email, senha)
            if ok_email:
                canais.append("Email")

        return {
            "success": True,
            "message": f"Conta ativada. Enviado via: {' e '.join(canais) if canais else 'nenhum canal'}",
            "data": {
                "email":      email,
                "senha":      senha,
                "painel_url": PAINEL_URL,
                "mobile_url": MOBILE_URL,
                "canais":     canais,
                "sindico":    sindico,
                "condominio": nome_cond,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[ERRO] ativar_conta id={cadastro_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao ativar conta: {str(e)}")
