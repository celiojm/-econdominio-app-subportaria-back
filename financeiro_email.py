# ================================================================================
#  PATH: backend/financeiro/financeiro_email.py
#  DESCRIPTION: Módulo de envio de emails para cobranças e recebimentos
#  VERSÃO: 2.0.0 - CORRIGIDO - SEM ACENTO NO REMETENTE
#  DATA: 21/12/2024
#  CORREÇÕES:
#    - Removido acento do nome do remetente (Financeiro Econdominio)
#    - Adicionado timeout nas conexões SMTP
#    - Melhorias no tratamento de erros
#    - Logs mais detalhados
# ================================================================================

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date
from typing import Optional
import logging
import os

logger = logging.getLogger(__name__)

# ================================================================================
#  CONFIGURAÇÕES DO SERVIDOR DE EMAIL
# ================================================================================

EMAIL_CONFIG = {
    "smtp_server": os.getenv("EMAIL_SMTP_SERVER", "mail.econdominio.com.br"),
    "smtp_port": int(os.getenv("EMAIL_SMTP_PORT", "465")),
    "username": os.getenv("EMAIL_SMTP_USERNAME", "financeiro@econdominio.com.br"),
    "password": os.getenv("EMAIL_SMTP_PASSWORD", ""),
    "from_email": os.getenv("EMAIL_FROM_ADDRESS", "financeiro@econdominio.com.br"),
    "from_name": "Financeiro Econdominio",  # SEM ACENTO - CRITICAL!
    "use_ssl": os.getenv("EMAIL_USE_SSL", "true").lower() == "true",
    "timeout": 30  # Timeout de 30 segundos
}

# Validar senha
if not EMAIL_CONFIG["password"]:
    logger.warning("⚠️  EMAIL_SMTP_PASSWORD não configurado no .env!")

logger.info(f"📧 Configuração de email carregada:")
logger.info(f"   - Servidor: {EMAIL_CONFIG['smtp_server']}:{EMAIL_CONFIG['smtp_port']}")
logger.info(f"   - Usuário: {EMAIL_CONFIG['username']}")
logger.info(f"   - Remetente: {EMAIL_CONFIG['from_name']} <{EMAIL_CONFIG['from_email']}>")

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
        logger.info("=" * 80)
        logger.info(f"📧 [EMAIL] Iniciando envio de email")
        logger.info(f"📧 [EMAIL] Para: {destinatario}")
        logger.info(f"📧 [EMAIL] Assunto: {assunto}")
        logger.info("=" * 80)
        
        # Criar mensagem
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = f"{EMAIL_CONFIG['from_name']} <{EMAIL_CONFIG['from_email']}>"
        msg["To"] = destinatario
        msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
        
        logger.info(f"📧 [EMAIL] Remetente configurado: {msg['From']}")
        
        # Adicionar corpo texto (fallback)
        if corpo_texto:
            part1 = MIMEText(corpo_texto, "plain", "utf-8")
            msg.attach(part1)
            logger.info(f"📧 [EMAIL] Corpo texto adicionado")
        
        # Adicionar corpo HTML
        part2 = MIMEText(corpo_html, "html", "utf-8")
        msg.attach(part2)
        logger.info(f"📧 [EMAIL] Corpo HTML adicionado")
        
        # Criar conexão SSL e enviar
        context = ssl.create_default_context()
        
        logger.info(f"📧 [EMAIL] Conectando ao servidor SMTP...")
        logger.info(f"📧 [EMAIL] {EMAIL_CONFIG['smtp_server']}:{EMAIL_CONFIG['smtp_port']}")
        
        with smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"], timeout=EMAIL_CONFIG["timeout"]) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            logger.info(f"📧 [EMAIL] Conectado! Autenticando...")
            server.login(EMAIL_CONFIG["username"], EMAIL_CONFIG["password"])
            logger.info(f"📧 [EMAIL] Autenticado! Enviando mensagem...")
            
            server.sendmail(
                EMAIL_CONFIG["from_email"],
                destinatario,
                msg.as_string()
            )
        
        logger.info("=" * 80)
        logger.info(f"✅ [EMAIL] Email enviado com SUCESSO!")
        logger.info(f"✅ [EMAIL] Destinatário: {destinatario}")
        logger.info("=" * 80)
        
        return {"success": True, "message": f"Email enviado para {destinatario}"}
        
    except smtplib.SMTPAuthenticationError as e:
        logger.error("=" * 80)
        logger.error(f"❌ [EMAIL] Erro de autenticação SMTP!")
        logger.error(f"❌ [EMAIL] Verifique usuário e senha")
        logger.error(f"❌ [EMAIL] Erro: {e}")
        logger.error("=" * 80)
        return {"success": False, "message": "Erro de autenticação no servidor de email"}
        
    except smtplib.SMTPException as e:
        logger.error("=" * 80)
        logger.error(f"❌ [EMAIL] Erro SMTP!")
        logger.error(f"❌ [EMAIL] Erro: {e}")
        logger.error("=" * 80)
        return {"success": False, "message": f"Erro ao enviar email: {str(e)}"}
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ [EMAIL] Erro crítico ao enviar email!")
        logger.error(f"❌ [EMAIL] Tipo: {type(e).__name__}")
        logger.error(f"❌ [EMAIL] Mensagem: {str(e)}")
        logger.error("=" * 80)
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "message": f"Erro: {str(e)}"}


# ================================================================================
#  TEMPLATES DE EMAIL
# ================================================================================

def get_template_base(conteudo: str) -> str:
    """Template base para todos os emails"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5f5f5;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f5f5f5; padding: 20px 0;">
            <tr>
                <td align="center">
                    <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                        <!-- Header -->
                        <tr>
                            <td style="background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%); padding: 30px; text-align: center;">
                                <h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 600;">
                                    💰 Econdominio
                                </h1>
                                <p style="color: #93c5fd; margin: 5px 0 0 0; font-size: 14px;">
                                    Sistema Financeiro
                                </p>
                            </td>
                        </tr>
                        
                        <!-- Conteúdo -->
                        <tr>
                            <td style="padding: 30px;">
                                {conteudo}
                            </td>
                        </tr>
                        
                        <!-- Footer -->
                        <tr>
                            <td style="background-color: #f8fafc; padding: 20px; text-align: center; border-top: 1px solid #e2e8f0;">
                                <p style="color: #64748b; font-size: 12px; margin: 0;">
                                    Este e um email automatico do sistema Econdominio.<br>
                                    Em caso de duvidas, entre em contato conosco.
                                </p>
                                <p style="color: #94a3b8; font-size: 11px; margin: 10px 0 0 0;">
                                    © {datetime.now().year} Econdominio - Todos os direitos reservados
                                </p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """


def template_nova_cobranca(
    nome_condominio: str,
    valor: float,
    vencimento: str,
    descricao: str,
    link_pagamento: str
) -> str:
    """Template para email de nova cobrança"""
    
    valor_formatado = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    
    conteudo = f"""
        <h2 style="color: #1e40af; margin: 0 0 20px 0; font-size: 20px;">
            Nova Cobranca Gerada
        </h2>
        
        <p style="color: #475569; font-size: 15px; line-height: 1.6; margin: 0 0 20px 0;">
            Ola,<br><br>
            Uma nova cobranca foi gerada para o condominio <strong>{nome_condominio}</strong>.
        </p>
        
        <!-- Box de informações -->
        <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8fafc; border-radius: 8px; padding: 20px; margin: 20px 0;">
            <tr>
                <td style="padding: 15px;">
                    <table width="100%">
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Descricao:</td>
                            <td style="color: #1e293b; font-size: 14px; font-weight: 600; text-align: right;">{descricao}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Valor:</td>
                            <td style="color: #059669; font-size: 20px; font-weight: 700; text-align: right;">{valor_formatado}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Vencimento:</td>
                            <td style="color: #1e293b; font-size: 14px; font-weight: 600; text-align: right;">{vencimento}</td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
        
        <!-- Botão de pagamento -->
        <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
                <td align="center" style="padding: 20px 0;">
                    <a href="{link_pagamento}" 
                       style="display: inline-block; background: linear-gradient(135deg, #059669 0%, #10b981 100%); 
                              color: #ffffff; text-decoration: none; padding: 15px 40px; border-radius: 8px; 
                              font-size: 16px; font-weight: 600; box-shadow: 0 4px 6px rgba(5, 150, 105, 0.3);">
                        💳 Pagar Agora
                    </a>
                </td>
            </tr>
        </table>
        
        <p style="color: #64748b; font-size: 13px; text-align: center; margin: 20px 0 0 0;">
            Ou copie e cole o link abaixo no seu navegador:<br>
            <a href="{link_pagamento}" style="color: #3b82f6; word-break: break-all;">{link_pagamento}</a>
        </p>
    """
    
    return get_template_base(conteudo)


def template_pagamento_confirmado(
    nome_condominio: str,
    valor: float,
    data_pagamento: str,
    forma_pagamento: str,
    descricao: str,
    validade_ate: str,
    dias_restantes: int
) -> str:
    """Template para confirmação de pagamento"""
    
    valor_formatado = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    
    conteudo = f"""
        <h2 style="color: #059669; margin: 0 0 20px 0; font-size: 20px;">
            ✅ Pagamento Confirmado!
        </h2>
        
        <p style="color: #475569; font-size: 15px; line-height: 1.6; margin: 0 0 20px 0;">
            Ola,<br><br>
            Seu pagamento foi confirmado com sucesso!
        </p>
        
        <!-- Box de confirmação -->
        <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; margin: 20px 0;">
            <tr>
                <td style="padding: 20px;">
                    <table width="100%">
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Condominio:</td>
                            <td style="color: #1e293b; font-size: 14px; font-weight: 600; text-align: right;">{nome_condominio}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Descricao:</td>
                            <td style="color: #1e293b; font-size: 14px; font-weight: 600; text-align: right;">{descricao}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Valor Pago:</td>
                            <td style="color: #059669; font-size: 20px; font-weight: 700; text-align: right;">{valor_formatado}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Data:</td>
                            <td style="color: #1e293b; font-size: 14px; font-weight: 600; text-align: right;">{data_pagamento}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Forma de Pagamento:</td>
                            <td style="color: #1e293b; font-size: 14px; font-weight: 600; text-align: right;">{forma_pagamento}</td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
        
        <table width="100%" cellpadding="0" cellspacing="0" style="margin: 20px 0;">
            <tr>
                <td align="center">
                    <p style="color: #059669; font-size: 16px; font-weight: 600; margin: 0;">
                        Sua assinatura esta valida ate: {validade_ate}<br>
                        ({dias_restantes} dias restantes)
                    </p>
                </td>
            </tr>
        </table>
        
        <p style="color: #64748b; font-size: 13px; text-align: center; margin: 20px 0 0 0;">
            Agradecemos pela confianca!
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
    """Template para lembrete de cobrança (pendente ou vencida)"""
    
    valor_formatado = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    
    if dias_atraso > 0:
        titulo = "⚠️ Cobranca em Atraso"
        cor_titulo = "#dc2626"
        cor_bg = "#fef2f2"
        cor_border = "#fecaca"
        mensagem = f"A cobranca abaixo esta <strong>vencida ha {dias_atraso} dia(s)</strong>. Por favor, regularize o pagamento."
    else:
        titulo = "🔔 Lembrete de Cobranca"
        cor_titulo = "#f59e0b"
        cor_bg = "#fffbeb"
        cor_border = "#fde68a"
        mensagem = "Segue abaixo o lembrete da cobranca pendente."
    
    conteudo = f"""
        <h2 style="color: {cor_titulo}; margin: 0 0 20px 0; font-size: 20px;">
            {titulo}
        </h2>
        
        <p style="color: #475569; font-size: 15px; line-height: 1.6; margin: 0 0 20px 0;">
            Ola,<br><br>
            {mensagem}
        </p>
        
        <!-- Box de informações -->
        <table width="100%" cellpadding="0" cellspacing="0" style="background-color: {cor_bg}; border: 1px solid {cor_border}; border-radius: 8px; margin: 20px 0;">
            <tr>
                <td style="padding: 20px;">
                    <table width="100%">
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Condominio:</td>
                            <td style="color: #1e293b; font-size: 14px; font-weight: 600; text-align: right;">{nome_condominio}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Descricao:</td>
                            <td style="color: #1e293b; font-size: 14px; font-weight: 600; text-align: right;">{descricao}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Valor:</td>
                            <td style="color: #dc2626; font-size: 20px; font-weight: 700; text-align: right;">{valor_formatado}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b; font-size: 13px; padding: 8px 0;">Vencimento:</td>
                            <td style="color: #1e293b; font-size: 14px; font-weight: 600; text-align: right;">{vencimento}</td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
        
        <!-- Botão de pagamento -->
        <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
                <td align="center" style="padding: 20px 0;">
                    <a href="{link_pagamento}" 
                       style="display: inline-block; background: linear-gradient(135deg, #059669 0%, #10b981 100%); 
                              color: #ffffff; text-decoration: none; padding: 15px 40px; border-radius: 8px; 
                              font-size: 16px; font-weight: 600; box-shadow: 0 4px 6px rgba(5, 150, 105, 0.3);">
                        💳 Pagar Agora
                    </a>
                </td>
            </tr>
        </table>
        
        <p style="color: #64748b; font-size: 13px; text-align: center; margin: 20px 0 0 0;">
            Ou copie e cole o link abaixo no seu navegador:<br>
            <a href="{link_pagamento}" style="color: #3b82f6; word-break: break-all;">{link_pagamento}</a>
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
    
    assunto = f"💰 Nova Cobranca - {nome_condominio}"
    corpo_html = template_nova_cobranca(
        nome_condominio=nome_condominio,
        valor=valor,
        vencimento=vencimento,
        descricao=descricao,
        link_pagamento=link_pagamento
    )
    corpo_texto = f"""
Nova Cobranca Gerada

Condominio: {nome_condominio}
Descricao: {descricao}
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

Condominio: {nome_condominio}
Descricao: {descricao}
Valor Pago: R$ {valor:.2f}
Data: {data_pagamento}
Forma de Pagamento: {forma_pagamento}

Sua assinatura esta valida ate: {validade_ate} ({dias_restantes} dias restantes)
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
        assunto = f"⚠️ Cobranca em Atraso - {nome_condominio}"
    else:
        assunto = f"🔔 Lembrete de Cobranca - {nome_condominio}"
    
    corpo_html = template_lembrete_cobranca(
        nome_condominio=nome_condominio,
        valor=valor,
        vencimento=vencimento,
        descricao=descricao,
        link_pagamento=link_pagamento,
        dias_atraso=dias_atraso
    )
    corpo_texto = f"""
{'Cobranca em Atraso' if dias_atraso > 0 else 'Lembrete de Cobranca'}

Condominio: {nome_condominio}
Descricao: {descricao}
Valor: R$ {valor:.2f}
Vencimento: {vencimento}
{'Dias em atraso: ' + str(dias_atraso) if dias_atraso > 0 else ''}

Link para pagamento: {link_pagamento}
    """
    
    return enviar_email(email_destino, assunto, corpo_html, corpo_texto)
