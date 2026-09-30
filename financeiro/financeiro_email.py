# ================================================================================
#  PATH: backend/financeiro/financeiro_email.py
#  DESCRIPTION: Módulo de envio de emails para cobranças e recebimentos
#  VERSÃO: 1.0.2 - CORRIGIDO: Lê configurações do .env sem fallback
# ================================================================================

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date
from typing import Optional
import logging
#import os

#logger = logging.getLogger(__name__)
import os
logger = logging.getLogger(__name__)

# ── Dados da empresa ──────────────────────────────────────────────────────────
EMPRESA_NOME     = "E-CONDOMÍNIO SISTEMAS DE GESTÃO LTDA"
EMPRESA_CNPJ     = "64.931.933/0001-85"
EMPRESA_SITE     = "https://econdominio.com.br"
EMPRESA_EMAIL    = "financeiro@econdominio.com.br"
EMPRESA_FONE     = "(48) 3035-1252"
EMPRESA_ENDERECO = "Rua Professora Sofia Quint de Souza, 544, Capoeiras"
EMPRESA_CIDADE   = "Florianópolis - SC · CEP 88085-040"
EMPRESA_LOGO_URL = "https://admin.econdominio.com.br/logo_header_50h.f52445df23437789f112.png"
# ================================================================================
#  CONFIGURAÇÕES DO SERVIDOR DE EMAIL - LÊ DO .ENV
# ================================================================================

def get_email_config():
    """
    Retorna configurações de email do .env
    IMPORTANTE: Sempre lê do .env, sem valores hardcoded como fallback
    """
    password = os.getenv("EMAIL_SMTP_PASSWORD", "")
    
    if not password:
        logger.error("❌ EMAIL_SMTP_PASSWORD não configurado no .env!")
        raise ValueError("EMAIL_SMTP_PASSWORD não configurado no .env")
    
    return {
        "smtp_server": os.getenv("EMAIL_SMTP_SERVER", "mail.econdominio.com.br"),
        "smtp_port": int(os.getenv("EMAIL_SMTP_PORT", "465")),
        "username": os.getenv("EMAIL_SMTP_USERNAME", "financeiro@econdominio.com.br"),
        "password": password,  # LÊ DO .ENV (Infor$10)2-(38%%40)
        "from_email": os.getenv("EMAIL_FROM_ADDRESS", "financeiro@econdominio.com.br"),
        "from_name": os.getenv("EMAIL_FROM_NAME", "Financeiro Econdominio"),
        "use_ssl": os.getenv("EMAIL_USE_SSL", "true").lower() == "true"
    }

# Inicializar configuração uma vez ao carregar o módulo
EMAIL_CONFIG = get_email_config()

# ================================================================================
#  FUNÇÃO DE ENVIO DE EMAIL
# ================================================================================

def enviar_email(
    destinatario: str,
    assunto: str,
    corpo_html: str,
    corpo_texto: Optional[str] = None
) -> dict:
    """
    Envia email usando SMTP com SSL.
    
    Args:
        destinatario: Email do destinatário
        assunto: Assunto do email
        corpo_html: Corpo do email em HTML
        corpo_texto: Corpo do email em texto puro (opcional)
    
    Returns:
        dict com success e message
    """
    try:
        # Criar mensagem
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = f"{EMAIL_CONFIG['from_name']} <{EMAIL_CONFIG['from_email']}>"
        msg["To"] = destinatario
        
        # Adicionar corpo texto (fallback)
        if corpo_texto:
            part1 = MIMEText(corpo_texto, "plain", "utf-8")
            msg.attach(part1)
        
        # Adicionar corpo HTML
        part2 = MIMEText(corpo_html, "html", "utf-8")
        msg.attach(part2)
        
        # Criar conexão SSL e enviar
        context = ssl.create_default_context()
        
        logger.info(f"📧 Enviando email para {destinatario} via {EMAIL_CONFIG['smtp_server']}:{EMAIL_CONFIG['smtp_port']}")
        
        with smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"], timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_CONFIG["username"], EMAIL_CONFIG["password"])
            server.sendmail(
                EMAIL_CONFIG["from_email"],
                destinatario,
                msg.as_string()
            )
        
        logger.info(f"✅ Email enviado com sucesso para {destinatario}")
        return {"success": True, "message": f"Email enviado para {destinatario}"}
        
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"❌ Erro de autenticação SMTP: {e}")
        return {"success": False, "message": "Erro de autenticação no servidor de email. Verifique usuário e senha."}
    except smtplib.SMTPException as e:
        logger.error(f"❌ Erro SMTP: {e}")
        return {"success": False, "message": f"Erro ao enviar email: {str(e)}"}
    except Exception as e:
        logger.error(f"❌ Erro ao enviar email: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": f"Erro: {str(e)}"}


# ================================================================================
#  TEMPLATES DE EMAIL
# ================================================================================
def get_template_base(conteudo: str) -> str:
    """Template base para todos os emails — com identidade visual eCondomínio."""
    ano = datetime.now().year
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>eCondomínio</title>
</head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f8;padding:24px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#ffffff;border-radius:10px;overflow:hidden;
                  box-shadow:0 4px 16px rgba(0,0,0,0.10);max-width:600px;">
      <!-- Cabeçalho -->
      <tr>
        <td style="background:linear-gradient(135deg,#1a3a5c 0%,#2563eb 100%);padding:28px 32px;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="vertical-align:middle;">
                <img src="{EMPRESA_LOGO_URL}" alt="eCondomínio" height="44"
                     style="display:block;max-height:44px;"
                     onerror="this.style.display='none'">
              </td>
              <td style="text-align:right;vertical-align:middle;">
                <p style="margin:0;color:#93c5fd;font-size:12px;line-height:1.6;">
                  {EMPRESA_FONE}<br>
                  <a href="mailto:{EMPRESA_EMAIL}" style="color:#bfdbfe;text-decoration:none;">{EMPRESA_EMAIL}</a><br>
                  <a href="{EMPRESA_SITE}" style="color:#bfdbfe;text-decoration:none;">{EMPRESA_SITE}</a>
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
      <!-- Conteúdo -->
      <tr>
        <td style="padding:32px;">
          {conteudo}
        </td>
      </tr>
      <!-- Separador -->
      <tr>
        <td style="padding:0 32px;">
          <hr style="border:none;border-top:1px solid #e2e8f0;margin:0;">
        </td>
      </tr>
      <!-- Rodapé -->
      <tr>
        <td style="background:#f8fafc;padding:20px 32px;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="vertical-align:top;padding-right:16px;">
                <p style="margin:0 0 4px 0;color:#1e3a5f;font-size:12px;font-weight:700;">{EMPRESA_NOME}</p>
                <p style="margin:0;color:#64748b;font-size:11px;line-height:1.7;">
                  CNPJ: {EMPRESA_CNPJ}<br>
                  {EMPRESA_ENDERECO}<br>
                  {EMPRESA_CIDADE}<br>
                  {EMPRESA_FONE} ·
                  <a href="mailto:{EMPRESA_EMAIL}" style="color:#3b82f6;text-decoration:none;">{EMPRESA_EMAIL}</a>
                </p>
              </td>
              <td style="vertical-align:top;text-align:right;white-space:nowrap;">
                <p style="margin:0;color:#94a3b8;font-size:10px;">
                  Email automático do sistema<br>
                  © {ano} eCondomínio
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

def template_nova_cobranca(
    nome_condominio: str,
    valor: float,
    vencimento: str,
    descricao: str,
    link_pagamento: str
) -> str:
    """Template para email de nova cobrança — reutiliza lembrete."""
    return template_lembrete_cobranca(
        nome_condominio=nome_condominio,
        valor=valor,
        vencimento=vencimento,
        descricao=descricao,
        link_pagamento=link_pagamento,
        dias_atraso=0
    )


def template_pagamento_confirmado(
    nome_condominio: str,
    valor: float,
    data_pagamento: str,
    forma_pagamento: str,
    descricao: str,
    validade_ate: str,
    dias_restantes: int
) -> str:
    """Template para confirmação de pagamento."""
    valor_fmt = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    forma_label = {
        'pix':'⚡ PIX','PIX':'⚡ PIX',
        'boleto':'📄 Boleto Bancário','BOLETO':'📄 Boleto Bancário',
        'cartao':'💳 Cartão','CARTAO':'💳 Cartão'
    }.get(forma_pagamento, forma_pagamento or '—')

    if dias_restantes > 30:
        cor_val = "#059669"; icone_val = "✅"
    elif dias_restantes > 7:
        cor_val = "#d97706"; icone_val = "📅"
    else:
        cor_val = "#dc2626"; icone_val = "⚠️"

    conteudo = f"""
      <h2 style="color:#059669;margin:0 0 8px 0;font-size:22px;">✅ Pagamento Confirmado!</h2>
      <p style="margin:0 0 24px 0;">
        <span style="background:#d1fae5;color:#065f46;border:1px solid #a7f3d0;
                     padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;">
          ✔ PAGO
        </span>
      </p>
      <p style="color:#475569;font-size:15px;line-height:1.7;margin:0 0 24px 0;">
        Prezado(a) gestor(a) do <strong>{nome_condominio}</strong>,<br><br>
        Confirmamos o recebimento do seu pagamento. Sua assinatura está ativa!
      </p>
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background:linear-gradient(135deg,#d1fae5 0%,#a7f3d0 100%);
                    border-radius:8px;margin:0 0 16px 0;">
        <tr><td style="padding:20px;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="color:#064e3b;font-size:13px;padding:7px 0;border-bottom:1px solid rgba(6,78,59,0.15);">Descrição</td>
              <td style="color:#065f46;font-size:14px;font-weight:600;text-align:right;border-bottom:1px solid rgba(6,78,59,0.15);">{descricao}</td>
            </tr>
            <tr>
              <td style="color:#064e3b;font-size:13px;padding:7px 0;border-bottom:1px solid rgba(6,78,59,0.15);">Valor Pago</td>
              <td style="color:#065f46;font-size:24px;font-weight:700;text-align:right;border-bottom:1px solid rgba(6,78,59,0.15);">{valor_fmt}</td>
            </tr>
            <tr>
              <td style="color:#064e3b;font-size:13px;padding:7px 0;border-bottom:1px solid rgba(6,78,59,0.15);">Data do Pagamento</td>
              <td style="color:#065f46;font-size:14px;font-weight:600;text-align:right;border-bottom:1px solid rgba(6,78,59,0.15);">{data_pagamento}</td>
            </tr>
            <tr>
              <td style="color:#064e3b;font-size:13px;padding:7px 0;">Forma de Pagamento</td>
              <td style="color:#065f46;font-size:14px;font-weight:600;text-align:right;">{forma_label}</td>
            </tr>
          </table>
        </td></tr>
      </table>
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background:#f0f9ff;border:1px solid #bfdbfe;border-radius:8px;margin:0 0 24px 0;">
        <tr><td style="padding:16px;text-align:center;">
          <p style="color:#1e40af;font-size:15px;margin:0;font-weight:600;">
            {icone_val} Assinatura válida até:
            <span style="color:{cor_val};font-size:18px;display:block;margin-top:4px;">{validade_ate}</span>
          </p>
          <p style="color:#64748b;font-size:12px;margin:4px 0 0 0;">{dias_restantes} dias restantes</p>
        </td></tr>
      </table>
      <p style="color:#64748b;font-size:13px;text-align:center;margin:0;">
        Dúvidas? Entre em contato:<br>
        <a href="mailto:{EMPRESA_EMAIL}" style="color:#2563eb;">{EMPRESA_EMAIL}</a> · {EMPRESA_FONE}
      </p>
    """
    return get_template_base(conteudo)


def template_lembrete_cobranca(
    nome_condominio: str,
    valor: float,
    vencimento: str,
    descricao: str,
    link_pagamento: str,
    dias_atraso: int = 0
) -> str:
    """Template para lembrete de cobrança — pendente ou em atraso."""
    valor_fmt = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    if dias_atraso > 0:
        titulo     = "⚠️ Cobrança em Atraso"
        cor_titulo = "#dc2626"
        cor_bg     = "#fef2f2"
        cor_border = "#fecaca"
        cor_valor  = "#dc2626"
        mensagem   = (f"A cobrança abaixo está <strong>vencida há {dias_atraso} dia(s)</strong>. "
                      f"Por favor, regularize o quanto antes para manter seu acesso ativo.")
        badge      = (f'<span style="background:#fef2f2;color:#dc2626;border:1px solid #fecaca;'
                      f'padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;">'
                      f'⚠️ VENCIDA há {dias_atraso} dia(s)</span>')
    else:
        titulo     = "🔔 Lembrete de Cobrança"
        cor_titulo = "#d97706"
        cor_bg     = "#fffbeb"
        cor_border = "#fde68a"
        cor_valor  = "#059669"
        mensagem   = "Segue o lembrete da sua cobrança pendente. Efetue o pagamento até a data de vencimento."
        badge      = (f'<span style="background:#fffbeb;color:#d97706;border:1px solid #fde68a;'
                      f'padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;">'
                      f'⏰ Pendente</span>')

    conteudo = f"""
      <h2 style="color:{cor_titulo};margin:0 0 8px 0;font-size:22px;">{titulo}</h2>
      <p style="margin:0 0 20px 0;">{badge}</p>
      <p style="color:#475569;font-size:15px;line-height:1.7;margin:0 0 24px 0;">
        Prezado(a) gestor(a) do <strong>{nome_condominio}</strong>,<br><br>
        {mensagem}
      </p>
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background:{cor_bg};border:1px solid {cor_border};
                    border-radius:8px;margin:0 0 24px 0;">
        <tr><td style="padding:20px;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="color:#64748b;font-size:13px;padding:7px 0;border-bottom:1px solid {cor_border};width:45%;">Condomínio</td>
              <td style="color:#1e293b;font-size:14px;font-weight:600;text-align:right;border-bottom:1px solid {cor_border};">{nome_condominio}</td>
            </tr>
            <tr>
              <td style="color:#64748b;font-size:13px;padding:7px 0;border-bottom:1px solid {cor_border};">Descrição</td>
              <td style="color:#1e293b;font-size:14px;font-weight:600;text-align:right;border-bottom:1px solid {cor_border};">{descricao}</td>
            </tr>
            <tr>
              <td style="color:#64748b;font-size:13px;padding:7px 0;border-bottom:1px solid {cor_border};">Valor</td>
              <td style="color:{cor_valor};font-size:22px;font-weight:700;text-align:right;border-bottom:1px solid {cor_border};">{valor_fmt}</td>
            </tr>
            <tr>
              <td style="color:#64748b;font-size:13px;padding:7px 0;">Vencimento</td>
              <td style="color:#1e293b;font-size:14px;font-weight:600;text-align:right;">{vencimento}</td>
            </tr>
          </table>
        </td></tr>
      </table>
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td align="center" style="padding:8px 0 12px 0;">
          <a href="{link_pagamento}"
             style="display:inline-block;background:linear-gradient(135deg,#1a3a5c 0%,#2563eb 100%);
                    color:#fff;text-decoration:none;padding:15px 48px;border-radius:8px;
                    font-size:16px;font-weight:700;box-shadow:0 4px 12px rgba(37,99,235,0.35);">
            💳 Pagar Agora
          </a>
        </td></tr>
        <tr><td align="center" style="padding:0 0 8px 0;">
          <a href="{link_pagamento}"
             style="display:inline-block;background:#f1f5f9;color:#475569;
                    text-decoration:none;padding:10px 24px;border-radius:6px;
                    font-size:13px;border:1px solid #e2e8f0;">
            📄 Visualizar / Baixar Boleto
          </a>
        </td></tr>
      </table>
      <p style="color:#94a3b8;font-size:11px;text-align:center;margin:16px 0 0 0;word-break:break-all;">
        Link direto: <a href="{link_pagamento}" style="color:#3b82f6;">{link_pagamento}</a>
      </p>
    """
    return get_template_base(conteudo)

# ================================================================================
#  FUNÇÕES DE ENVIO ESPECÍFICAS
# ================================================================================

def enviar_email_nova_cobranca(
    email_destino: str,
    nome_condominio: str,
    valor: float,
    vencimento: str,
    descricao: str,
    link_pagamento: str
) -> dict:
    """Envia email de nova cobrança gerada"""
    
    assunto = f"💰 Nova Cobrança - {nome_condominio}"
    corpo_html = template_nova_cobranca(
        nome_condominio=nome_condominio,
        valor=valor,
        vencimento=vencimento,
        descricao=descricao,
        link_pagamento=link_pagamento
    )
    corpo_texto = f"""
Nova Cobrança Gerada

Condomínio: {nome_condominio}
Descrição: {descricao}
Valor: R$ {valor:.2f}
Vencimento: {vencimento}

Link para pagamento: {link_pagamento}
    """
    
    return enviar_email(email_destino, assunto, corpo_html, corpo_texto)


def enviar_email_pagamento_confirmado(
    email_destino: str,
    nome_condominio: str,
    valor: float,
    data_pagamento: str,
    forma_pagamento: str,
    descricao: str,
    validade_ate: str,
    dias_restantes: int
) -> dict:
    """Envia email de confirmação de pagamento"""
    
    assunto = f"✅ Pagamento Confirmado - {nome_condominio}"
    corpo_html = template_pagamento_confirmado(
        nome_condominio=nome_condominio,
        valor=valor,
        data_pagamento=data_pagamento,
        forma_pagamento=forma_pagamento,
        descricao=descricao,
        validade_ate=validade_ate,
        dias_restantes=dias_restantes
    )
    corpo_texto = f"""
Pagamento Confirmado!

Condomínio: {nome_condominio}
Descrição: {descricao}
Valor Pago: R$ {valor:.2f}
Data: {data_pagamento}
Forma de Pagamento: {forma_pagamento}

Sua assinatura está válida até: {validade_ate} ({dias_restantes} dias restantes)
    """
    
    return enviar_email(email_destino, assunto, corpo_html, corpo_texto)


def enviar_email_lembrete(
    email_destino: str,
    nome_condominio: str,
    valor: float,
    vencimento: str,
    descricao: str,
    link_pagamento: str,
    dias_atraso: int = 0
) -> dict:
    """Envia email de lembrete de cobrança"""
    
    if dias_atraso > 0:
        assunto = f"⚠️ Cobrança em Atraso - {nome_condominio}"
    else:
        assunto = f"🔔 Lembrete de Cobrança - {nome_condominio}"
    
    corpo_html = template_lembrete_cobranca(
        nome_condominio=nome_condominio,
        valor=valor,
        vencimento=vencimento,
        descricao=descricao,
        link_pagamento=link_pagamento,
        dias_atraso=dias_atraso
    )
    corpo_texto = f"""
{'Cobrança em Atraso' if dias_atraso > 0 else 'Lembrete de Cobrança'}

Condomínio: {nome_condominio}
Descrição: {descricao}
Valor: R$ {valor:.2f}
Vencimento: {vencimento}
{'Dias em atraso: ' + str(dias_atraso) if dias_atraso > 0 else ''}

Link para pagamento: {link_pagamento}
    """
    
    return enviar_email(email_destino, assunto, corpo_html, corpo_texto)
