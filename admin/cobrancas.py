# ========================================
# ALTERAÇÃO 2026-09-26: cobrança sempre BOLETO (boleto + PIX na mesma fatura), nunca só PIX
# ARQUIVO: cobrancas.py
# PASTA: admin/
# DESCRIÇÃO: Cobranças Routes - Módulo Admin com Email, WhatsApp e integração Asaas
# VERSÃO: 3.1.0 - criar_cobranca chama emitir_nf() (financeiro_nfe.py) logo apos
#         o boleto ser salvo, quando condominio.nfe_antes_pagamento=1 (Etapa 3,
#         NFE_FINANCEIRO.md). Conexao aiomysql PROPRIA, separada do conn
#         sincrono do resto do arquivo — roda fora da transacao do boleto.
#         historico_cobrancas passa a devolver asaas_invoice_id, invoice_status,
#         invoice_pdf_url e nf_erro por cobranca (causa raiz de cobrancas pagas
#         sempre mostrarem "sem NF" na tela, mesmo com nota emitida). Testado
#         e provado antes deste deploy em dev2_back (arquitetura identica).
# VERSÃO: 3.0.0 - Criação de cobrança via Asaas API (sandbox/production)
# ========================================
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal
import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx

from .database import get_db_connection
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Cobranças"])


# ========================================
# Models
# ========================================
class CobrancaCreate(BaseModel):
    condominio_id: int
    valor: float
    data_vencimento: str
    forma_pagamento: Optional[str] = 'pix'
    descricao: Optional[str] = None
    dias_validade: Optional[int] = 30


class CobrancaResponse(BaseModel):
    id: int
    id_condominio: int
    valor: float
    status: str
    data_vencimento: str
    data_pagamento: Optional[str] = None
    descricao: Optional[str] = None
    forma_pagamento: Optional[str] = None


# ========================================
# Configurações de Email
# ========================================
EMAIL_SMTP_SERVER = os.getenv("EMAIL_SMTP_SERVER", "mail.econdominio.com.br")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "465"))
EMAIL_SMTP_USERNAME = os.getenv("EMAIL_SMTP_USERNAME", "financeiro@econdominio.com.br")
EMAIL_SMTP_PASSWORD = os.getenv("EMAIL_SMTP_PASSWORD", "")
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "financeiro@econdominio.com.br")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "eCondomínio Financeiro")


# ========================================
# Configurações Z-API (WhatsApp)
# ========================================
ZAPI_INSTANCE_ID = os.getenv("ZAPI_INSTANCE_ID", "")
ZAPI_TOKEN = os.getenv("ZAPI_TOKEN", "")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")
ZAPI_API_URL = os.getenv("ZAPI_API_URL", "https://api.z-api.io")


# ========================================
# Configurações Asaas
# ========================================
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY", "")
ASAAS_ENV = os.getenv("ASAAS_ENV", "sandbox")
ASAAS_SANDBOX = ASAAS_ENV in ["sandbox", "homologacao", "hmlg"]

if ASAAS_SANDBOX:
    ASAAS_BASE_URL = os.getenv("ASAAS_URL_SANDBOX", "https://api-sandbox.asaas.com/v3")
    ASAAS_LINK_BASE = "https://sandbox.asaas.com/i"
    logger.info("🧪 Cobranças Admin: Modo SANDBOX ativado")
else:
    ASAAS_BASE_URL = os.getenv("ASAAS_URL_PRODUCTION", "https://api.asaas.com/v3")
    ASAAS_LINK_BASE = "https://www.asaas.com/i"
    logger.info("🚀 Cobranças Admin: Modo PRODUÇÃO ativado")


def get_asaas_headers():
    """Retorna headers para API do Asaas"""
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "access_token": ASAAS_API_KEY
    }


# ========================================
# Funções auxiliares
# ========================================

def normalizar_status(status: str) -> str:
    """Normaliza os diferentes status para um padrão"""
    if not status:
        return 'pendente'

    status_lower = status.lower()

    if status_lower in ['pago', 'received', 'confirmed', 'received_in_cash']:
        return 'pago'
    if status_lower in ['pendente', 'pending']:
        return 'pendente'
    if status_lower in ['vencido', 'overdue']:
        return 'vencido'
    if status_lower in ['cancelado', 'cancelled', 'cancelada']:
        return 'cancelada'
    if status_lower in ['estornado', 'estornada']:
        return 'estornada'

    return status_lower


def gerar_link_pagamento(asaas_payment_id: str) -> Optional[str]:
    """
    Gera link de pagamento do Asaas.
    Usa sandbox ou produção conforme ASAAS_ENV.
    """
    if asaas_payment_id:
        asaas_id = asaas_payment_id.replace('pay_', '')
        return f"{ASAAS_LINK_BASE}/{asaas_id}"
    return None


def format_currency(value: float) -> str:
    """Formata valor para moeda brasileira"""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_phone(phone: str) -> str:
    """Formata telefone para padrão internacional (55XXXXXXXXXXX)"""
    numbers = ''.join(filter(str.isdigit, phone or ''))

    if numbers.startswith('55') and len(numbers) in [12, 13]:
        return numbers
    if len(numbers) in [10, 11]:
        return f"55{numbers}"
    if len(numbers) in [8, 9]:
        return f"5548{numbers}"

    return numbers


# ========================================
# Integração Asaas - Buscar ou Criar Cliente
# ========================================

async def buscar_ou_criar_cliente_asaas(condominio: dict, conn) -> str:
    """
    Busca ou cria um cliente no Asaas.
    Retorna o customer_id do Asaas.
    Usa conexão pymysql (conn) do admin.
    """
    try:
        with conn.cursor() as cursor:
            # Verificar se já tem customer_id no banco
            cursor.execute(
                "SELECT asaas_customer_id FROM condominios WHERE id = %s",
                (condominio['id'],)
            )
            row = cursor.fetchone()

            if row and row.get('asaas_customer_id'):
                logger.info(f"✅ Cliente já existe no Asaas: {row['asaas_customer_id']}")
                return row['asaas_customer_id']

        # Criar cliente no Asaas
        logger.info(f"📝 Criando cliente no Asaas: {condominio['nome']}")

        email = (condominio.get('cobranca_email') or
                 condominio.get('email_financeiro') or
                 condominio.get('email') or
                 f"contato{condominio['id']}@econdominio.com.br")

        telefone = condominio.get('cobranca_whats') or condominio.get('telefone') or ""
        telefone_limpo = ''.join(filter(str.isdigit, telefone))

        cnpj_limpo = (condominio.get('cnpj', '')
                      .replace('.', '').replace('/', '').replace('-', ''))

        payload = {
            "name": condominio["nome"],
            "cpfCnpj": cnpj_limpo,
            "email": email,
            "externalReference": f"COND_{condominio['id']}",
            "notificationDisabled": False
        }

        if telefone_limpo:
            payload["mobilePhone"] = telefone_limpo[:11]
        if condominio.get('cep'):
            payload["postalCode"] = condominio['cep'].replace('-', '')
        if condominio.get('endereco'):
            payload["address"] = condominio['endereco']
        if condominio.get('numero'):
            payload["addressNumber"] = condominio['numero']
        if condominio.get('complemento'):
            payload["complement"] = condominio['complemento']
        if condominio.get('bairro'):
            payload["province"] = condominio['bairro']

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{ASAAS_BASE_URL}/customers",
                headers=get_asaas_headers(),
                json=payload
            )

            if response.status_code in [200, 201]:
                data = response.json()
                customer_id = data.get("id")

                # Salvar customer_id no banco
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE condominios SET asaas_customer_id = %s WHERE id = %s",
                        (customer_id, condominio['id'])
                    )
                    conn.commit()

                logger.info(f"✅ Cliente criado no Asaas: {customer_id}")
                return customer_id
            else:
                error_text = response.text
                logger.error(f"❌ Erro ao criar cliente no Asaas: {error_text}")
                raise Exception(f"Erro Asaas ao criar cliente: HTTP {response.status_code} - {error_text}")

    except Exception as e:
        logger.error(f"❌ Erro ao buscar/criar cliente Asaas: {e}")
        raise


async def criar_cobranca_asaas(customer_id: str, valor: float, data_vencimento: str,
                                descricao: str, forma_pagamento: str) -> dict:
    """
    Cria uma cobrança no Asaas e retorna os dados (incluindo asaas_payment_id).
    """
    billing_type_map = {
        "pix": "PIX",
        "boleto": "BOLETO",
        "credit_card": "CREDIT_CARD",
        "undefined": "UNDEFINED",
    }
    billing_type = "BOLETO"  # regra: sempre boleto + PIX, nunca só PIX

    payload = {
        "customer": customer_id,
        "billingType": billing_type,
        "value": valor,
        "dueDate": data_vencimento,
        "description": descricao,
        "externalReference": f"ADMIN_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    }

    logger.info(f"🚀 Enviando cobrança para Asaas: {valor} | {data_vencimento} | {billing_type}")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{ASAAS_BASE_URL}/payments",
            headers=get_asaas_headers(),
            json=payload
        )

        if response.status_code in [200, 201]:
            data = response.json()
            logger.info(f"✅ Cobrança criada no Asaas: {data.get('id')}")
            return data
        else:
            error_text = response.text
            logger.error(f"❌ Erro ao criar cobrança no Asaas: {error_text}")
            raise Exception(f"Erro Asaas: HTTP {response.status_code} - {error_text}")


# ========================================
# Funções de Email
# ========================================

def enviar_email(destinatario: str, assunto: str, corpo_html: str, corpo_texto: str = None) -> dict:
    """Envia email via SMTP"""
    try:
        if not EMAIL_SMTP_PASSWORD:
            logger.error("EMAIL_SMTP_PASSWORD não configurado")
            return {"success": False, "message": "Configuração de email incompleta"}

        msg = MIMEMultipart('alternative')
        msg['Subject'] = assunto
        msg['From'] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>"
        msg['To'] = destinatario

        if corpo_texto:
            msg.attach(MIMEText(corpo_texto, 'plain', 'utf-8'))
        msg.attach(MIMEText(corpo_html, 'html', 'utf-8'))

        with smtplib.SMTP(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_SMTP_USERNAME, EMAIL_SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM_ADDRESS, destinatario, msg.as_string())

        logger.info(f"✅ Email enviado para {destinatario}")
        return {"success": True, "message": "Email enviado com sucesso"}

    except Exception as e:
        logger.error(f"❌ Erro ao enviar email: {e}")
        return {"success": False, "message": str(e)}


def gerar_email_cobranca(nome_condominio: str, valor: float, vencimento: str,
                         descricao: str, link_pagamento: str, status: str = 'pendente',
                         data_pagamento: str = None) -> tuple:
    """Gera HTML e texto do email de cobrança"""

    if status == 'pago':
        assunto = f"✅ Pagamento Confirmado - {nome_condominio}"
        corpo_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #10B981, #059669); color: white; padding: 30px; border-radius: 10px; text-align: center;">
                <h1 style="margin: 0;">✅ Pagamento Confirmado!</h1>
            </div>

            <div style="padding: 30px; background: #f9fafb; border-radius: 10px; margin-top: 20px;">
                <h2 style="color: #1f2937; margin-top: 0;">Olá!</h2>
                <p style="color: #4b5563;">Confirmamos o recebimento do seu pagamento para <strong>{nome_condominio}</strong>.</p>

                <div style="background: white; padding: 20px; border-radius: 8px; border-left: 4px solid #10B981;">
                    <p style="margin: 5px 0;"><strong>📋 Descrição:</strong> {descricao}</p>
                    <p style="margin: 5px 0;"><strong>💰 Valor Pago:</strong> {format_currency(valor)}</p>
                    <p style="margin: 5px 0;"><strong>📅 Data Pagamento:</strong> {data_pagamento or '-'}</p>
                </div>

                <p style="color: #6b7280; font-size: 14px; margin-top: 20px;">
                    Agradecemos pela confiança! 🙏
                </p>
            </div>

            <div style="text-align: center; padding: 20px; color: #9ca3af; font-size: 12px;">
                <p>Este é um email automático do sistema eCondomínio.</p>
            </div>
        </body>
        </html>
        """
        corpo_texto = f"""
Pagamento Confirmado!

Olá! Confirmamos o recebimento do seu pagamento para {nome_condominio}.

Descrição: {descricao}
Valor Pago: {format_currency(valor)}
Data Pagamento: {data_pagamento or '-'}

Agradecemos pela confiança!

--
eCondomínio Financeiro
        """
    else:
        assunto = f"💳 Cobrança - {nome_condominio}"
        corpo_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #3B82F6, #1D4ED8); color: white; padding: 30px; border-radius: 10px; text-align: center;">
                <h1 style="margin: 0;">💳 Cobrança Disponível</h1>
            </div>

            <div style="padding: 30px; background: #f9fafb; border-radius: 10px; margin-top: 20px;">
                <h2 style="color: #1f2937; margin-top: 0;">Olá!</h2>
                <p style="color: #4b5563;">Segue os dados para pagamento referente ao <strong>{nome_condominio}</strong>.</p>

                <div style="background: white; padding: 20px; border-radius: 8px; border-left: 4px solid #3B82F6;">
                    <p style="margin: 5px 0;"><strong>📋 Descrição:</strong> {descricao}</p>
                    <p style="margin: 5px 0;"><strong>💰 Valor:</strong> {format_currency(valor)}</p>
                    <p style="margin: 5px 0;"><strong>📅 Vencimento:</strong> {vencimento}</p>
                </div>

                <div style="text-align: center; margin: 30px 0;">
                    <a href="{link_pagamento}" style="display: inline-block; background: #10B981; color: white; padding: 15px 40px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 16px;">
                        💳 PAGAR AGORA
                    </a>
                </div>

                <p style="color: #6b7280; font-size: 14px; text-align: center;">
                    Ou copie o link: <a href="{link_pagamento}" style="color: #3B82F6;">{link_pagamento}</a>
                </p>
            </div>

            <div style="text-align: center; padding: 20px; color: #9ca3af; font-size: 12px;">
                <p>Este é um email automático do sistema eCondomínio.</p>
                <p>Dúvidas? Entre em contato conosco.</p>
            </div>
        </body>
        </html>
        """
        corpo_texto = f"""
Cobrança Disponível

Olá! Segue os dados para pagamento referente ao {nome_condominio}.

Descrição: {descricao}
Valor: {format_currency(valor)}
Vencimento: {vencimento}

Link para pagamento:
{link_pagamento}

--
eCondomínio Financeiro
        """

    return assunto, corpo_html, corpo_texto


# ========================================
# Funções de WhatsApp
# ========================================

async def enviar_whatsapp_zapi(telefone: str, mensagem: str) -> dict:
    """Envia mensagem via Z-API"""
    if not all([ZAPI_INSTANCE_ID, ZAPI_TOKEN]):
        logger.error("Z-API não configurado")
        return {"success": False, "error": "Z-API não configurado"}

    formatted_phone = format_phone(telefone)
    url = f"{ZAPI_API_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"

    headers = {"Content-Type": "application/json"}
    if ZAPI_CLIENT_TOKEN:
        headers["Client-Token"] = ZAPI_CLIENT_TOKEN

    payload = {"phone": formatted_phone, "message": mensagem}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            logger.info(f"Z-API Response: {response.status_code}")

            if response.status_code == 200:
                return {"success": True, "message": "WhatsApp enviado com sucesso"}
            else:
                return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}

    except Exception as e:
        logger.error(f"Erro Z-API: {e}")
        return {"success": False, "error": str(e)}


def gerar_mensagem_whatsapp(nome_condominio: str, valor: float, vencimento: str,
                            descricao: str, link_pagamento: str, status: str = 'pendente',
                            data_pagamento: str = None) -> str:
    """Gera mensagem de WhatsApp com link de pagamento do Asaas"""

    if status == 'pago':
        mensagem = f"""✅ *Pagamento Confirmado!*

Olá! Confirmamos o recebimento do seu pagamento para *{nome_condominio}*.

📋 *Detalhes:*
• Descrição: {descricao}
• Valor Pago: {format_currency(valor)}
• Data: {data_pagamento or '-'}

Agradecemos pela confiança! 🙏

_Mensagem automática - eCondomínio_"""
    else:
        mensagem = f"""🔔 *Cobrança Disponível*

Olá! Segue os dados para pagamento:

🏢 *{nome_condominio}*

📋 *Detalhes:*
• Descrição: {descricao}
• Valor: {format_currency(valor)}
• Vencimento: {vencimento}

💳 *Pagar Agora:*
{link_pagamento}

_Mensagem automática - eCondomínio_"""

    return mensagem


# ========================================
# Rotas de Cobranças
# ========================================

@router.get("/cobrancas")
async def listar_cobrancas(
    condominio_id: Optional[int] = None,
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Lista cobranças - filtrado por condomínio e permissão do usuário"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            role = current_user.get('role', '')
            user_condominio = current_user.get('condominio_id')

            query = """
                SELECT
                    c.id_cobranca as id,
                    c.id_condominio,
                    c.valor,
                    c.valor_pago,
                    c.status,
                    c.data_vencimento,
                    c.data_pagamento,
                    c.forma_pagamento,
                    c.descricao,
                    c.asaas_payment_id,
                    c.data_criacao,
                    cond.nome as condominio_nome,
                    cond.validade_ate
                FROM cobrancas c
                LEFT JOIN condominios cond ON c.id_condominio = cond.id
                WHERE 1=1
            """
            params = []

            if role == 'admin_sistema':
                if condominio_id:
                    query += " AND c.id_condominio = %s"
                    params.append(condominio_id)
            else:
                cond_filter = condominio_id if condominio_id else user_condominio
                query += " AND c.id_condominio = %s"
                params.append(cond_filter)

            if status:
                query += " AND c.status = %s"
                params.append(status)

            query += " ORDER BY c.data_vencimento DESC LIMIT 100"

            cursor.execute(query, params)
            cobrancas = cursor.fetchall()

            result = []
            for c in cobrancas:
                result.append({
                    "id": c['id'],
                    "id_cobranca": c['id'],
                    "id_condominio": c['id_condominio'],
                    "condominio_nome": c.get('condominio_nome'),
                    "valor": float(c['valor']) if c['valor'] else 0,
                    "valor_pago": float(c['valor_pago']) if c.get('valor_pago') else None,
                    "status": normalizar_status(c['status']),
                    "data_vencimento": str(c['data_vencimento']) if c['data_vencimento'] else None,
                    "data_pagamento": str(c['data_pagamento']) if c.get('data_pagamento') else None,
                    "forma_pagamento": c.get('forma_pagamento'),
                    "descricao": c.get('descricao'),
                    "validade_assinatura": str(c.get('validade_ate')) if c.get('validade_ate') else None,
                    "link_pagamento": gerar_link_pagamento(c.get('asaas_payment_id')),
                    "asaas_payment_id": c.get('asaas_payment_id'),
                    "data_criacao": str(c['data_criacao']) if c.get('data_criacao') else None
                })

            return {"cobrancas": result, "total": len(result)}

    except Exception as e:
        logger.error(f"Erro ao listar cobranças: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.get("/cobrancas/historico/{condominio_id}")
async def historico_cobrancas(
    condominio_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Histórico completo de cobranças de um condomínio com totalizadores"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            role = current_user.get('role', '')

            if role not in ['admin_sistema', 'sindico', 'admin_condominio']:
                raise HTTPException(status_code=403, detail="Sem permissão")

            # Buscar dados do condomínio
            cursor.execute("""
                SELECT id, nome, cnpj, email, telefone, sindico, validade_ate,
                       cobranca_responsavel, cobranca_email, cobranca_whats,
                       total_apartamentos, plano_selecionado,
                       valor_mensal_base, valor_plano_final
                FROM condominios WHERE id = %s
            """, (condominio_id,))
            condominio = cursor.fetchone()

            if not condominio:
                raise HTTPException(status_code=404, detail="Condomínio não encontrado")

            # Buscar cobranças
            cursor.execute("""
                SELECT
                    id_cobranca as id,
                    id_condominio,
                    valor,
                    valor_pago,
                    status,
                    data_vencimento,
                    data_pagamento,
                    forma_pagamento,
                    descricao,
                    asaas_payment_id,
                    asaas_invoice_id,
                    invoice_status,
                    invoice_pdf_url,
                    nf_erro,
                    data_criacao
                FROM cobrancas
                WHERE id_condominio = %s
                ORDER BY data_vencimento DESC
            """, (condominio_id,))
            cobrancas_raw = cursor.fetchall()

            # Buscar assinatura ativa
            cursor.execute("""
                SELECT
                    id_assinatura as id,
                    tipo_plano,
                    valor,
                    ciclo,
                    data_inicio,
                    data_renovacao,
                    validade_ate,
                    status
                FROM assinaturas
                WHERE id_condominio = %s
                ORDER BY data_criacao DESC
                LIMIT 1
            """, (condominio_id,))
            assinatura = cursor.fetchone()

            # Calcular totalizadores
            total = len(cobrancas_raw)
            pagas = 0
            pendentes = 0
            vencidas = 0
            total_cobrado = 0
            total_recebido = 0
            hoje = date.today()

            cobrancas = []
            for c in cobrancas_raw:
                valor = float(c['valor']) if c['valor'] else 0
                total_cobrado += valor

                status_norm = normalizar_status(c['status'])

                if status_norm == 'pago':
                    pagas += 1
                    total_recebido += float(c.get('valor_pago') or c['valor'] or 0)
                elif status_norm == 'pendente':
                    if c['data_vencimento'] and c['data_vencimento'] < hoje:
                        vencidas += 1
                    else:
                        pendentes += 1
                elif status_norm == 'vencido':
                    vencidas += 1

                cobrancas.append({
                    "id": c['id'],
                    "id_cobranca": c['id'],
                    "valor": valor,
                    "status": status_norm,
                    "data_vencimento": str(c['data_vencimento']) if c['data_vencimento'] else None,
                    "data_pagamento": str(c['data_pagamento']) if c.get('data_pagamento') else None,
                    "forma_pagamento": c.get('forma_pagamento'),
                    "descricao": c.get('descricao'),
                    "link_pagamento": gerar_link_pagamento(c.get('asaas_payment_id')),
                    "asaas_payment_id": c.get('asaas_payment_id'),
                    "asaas_invoice_id": c.get('asaas_invoice_id'),
                    "invoice_status": c.get('invoice_status'),
                    "invoice_pdf_url": c.get('invoice_pdf_url'),
                    "nf_erro": c.get('nf_erro'),
                })

            return {
                "condominio": {
                    "id": condominio['id'],
                    "nome": condominio['nome'],
                    "cnpj": condominio.get('cnpj'),
                    "validade_ate": str(condominio.get('validade_ate')) if condominio.get('validade_ate') else None,
                    "total_apartamentos": condominio.get('total_apartamentos'),
                    "plano_selecionado": condominio.get('plano_selecionado'),
                    "valor_mensal_base": float(condominio['valor_mensal_base']) if condominio.get('valor_mensal_base') else None,
                    "valor_plano_final": float(condominio['valor_plano_final']) if condominio.get('valor_plano_final') else None,
                },
                "contato": {
                    "responsavel": condominio.get('cobranca_responsavel') or condominio.get('sindico') or condominio['nome'],
                    "email": condominio.get('cobranca_email') or condominio.get('email') or '-',
                    "whatsapp": condominio.get('cobranca_whats') or condominio.get('telefone') or '-'
                },
                "assinatura": {
                    "id": assinatura['id'] if assinatura else None,
                    "plano_nome": assinatura.get('tipo_plano') if assinatura else None,
                    "valor": float(assinatura['valor']) if assinatura and assinatura.get('valor') else None,
                    "status": assinatura.get('status') if assinatura else None,
                    "data_inicio": str(assinatura.get('data_inicio')) if assinatura and assinatura.get('data_inicio') else None,
                    "data_fim": str(assinatura.get('validade_ate')) if assinatura and assinatura.get('validade_ate') else None,
                    "validade_ate": str(assinatura.get('validade_ate')) if assinatura and assinatura.get('validade_ate') else None
                } if assinatura else None,
                "totalizadores": {
                    "total": total,
                    "pagas": pagas,
                    "pendentes": pendentes,
                    "vencidas": vencidas,
                    "total_cobrado": total_cobrado,
                    "total_recebido": total_recebido
                },
                "cobrancas": cobrancas
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar histórico: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.get("/assinaturas/condominio/{condominio_id}")
async def buscar_assinatura(
    condominio_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Busca assinatura ativa de um condomínio"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            role = current_user.get('role', '')
            user_condominio = current_user.get('condominio_id')

            if role != 'admin_sistema' and user_condominio != condominio_id:
                raise HTTPException(status_code=403, detail="Sem permissão")

            cursor.execute("""
                SELECT
                    a.id_assinatura as id,
                    a.tipo_plano as plano_nome,
                    a.valor,
                    a.ciclo,
                    a.data_inicio,
                    a.validade_ate as data_fim,
                    a.status,
                    c.validade_ate as condominio_validade
                FROM assinaturas a
                LEFT JOIN condominios c ON a.id_condominio = c.id
                WHERE a.id_condominio = %s
                ORDER BY a.data_criacao DESC
                LIMIT 1
            """, (condominio_id,))

            assinatura = cursor.fetchone()

            if not assinatura:
                cursor.execute("SELECT validade_ate FROM condominios WHERE id = %s", (condominio_id,))
                cond = cursor.fetchone()
                if cond and cond.get('validade_ate'):
                    return {
                        "id": None,
                        "plano_nome": "Avulso",
                        "status": "ativa",
                        "data_inicio": None,
                        "data_fim": str(cond['validade_ate']),
                        "validade_ate": str(cond['validade_ate'])
                    }
                return None

            return {
                "id": assinatura['id'],
                "plano_nome": assinatura.get('plano_nome'),
                "valor": float(assinatura['valor']) if assinatura.get('valor') else None,
                "ciclo": assinatura.get('ciclo'),
                "status": assinatura.get('status'),
                "data_inicio": str(assinatura.get('data_inicio')) if assinatura.get('data_inicio') else None,
                "data_fim": str(assinatura.get('data_fim') or assinatura.get('condominio_validade')) if (assinatura.get('data_fim') or assinatura.get('condominio_validade')) else None,
                "validade_ate": str(assinatura.get('data_fim') or assinatura.get('condominio_validade')) if (assinatura.get('data_fim') or assinatura.get('condominio_validade')) else None
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar assinatura: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.post("/cobrancas")
async def criar_cobranca(
    dados: CobrancaCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Cria uma nova cobrança NO ASAAS e salva no banco local.
    Fluxo: 1) Busca/cria cliente Asaas → 2) Cria payment no Asaas → 3) Salva no banco com asaas_payment_id
    """
    conn = get_db_connection()
    try:
        role = current_user.get('role', '')

        if role not in ['admin_sistema', 'sindico', 'admin_condominio']:
            raise HTTPException(status_code=403, detail="Sem permissão")

        with conn.cursor() as cursor:
            # Buscar condomínio completo
            cursor.execute("""
                SELECT id, nome, cnpj, email, telefone, sindico,
                       cobranca_responsavel, cobranca_email, cobranca_whats,
                       email_financeiro, cep, endereco, numero, complemento, bairro,
                       cidade, estado, asaas_customer_id, nfe_antes_pagamento
                FROM condominios WHERE id = %s
            """, (dados.condominio_id,))
            condominio = cursor.fetchone()

            if not condominio:
                raise HTTPException(status_code=404, detail="Condomínio não encontrado")

        # ========================================
        # PASSO 1: Buscar ou criar cliente no Asaas
        # ========================================
        cond_dict = {
            'id': condominio['id'],
            'nome': condominio['nome'],
            'cnpj': condominio.get('cnpj', ''),
            'email': condominio.get('email', ''),
            'email_financeiro': condominio.get('email_financeiro'),
            'cobranca_email': condominio.get('cobranca_email'),
            'telefone': condominio.get('telefone', ''),
            'cobranca_whats': condominio.get('cobranca_whats'),
            'cep': condominio.get('cep', ''),
            'endereco': condominio.get('endereco', ''),
            'numero': condominio.get('numero', ''),
            'complemento': condominio.get('complemento', ''),
            'bairro': condominio.get('bairro', ''),
        }

        customer_id = await buscar_ou_criar_cliente_asaas(cond_dict, conn)

        # ========================================
        # PASSO 2: Criar cobrança no Asaas
        # ========================================
        descricao = dados.descricao or f"Mensalidade - {condominio['nome']}"

        asaas_data = await criar_cobranca_asaas(
            customer_id=customer_id,
            valor=dados.valor,
            data_vencimento=dados.data_vencimento,
            descricao=descricao,
            forma_pagamento=dados.forma_pagamento or 'pix'
        )

        asaas_payment_id = asaas_data.get("id")
        link_pagamento = gerar_link_pagamento(asaas_payment_id)

        # ========================================
        # PASSO 3: Salvar no banco local COM asaas_payment_id
        # ========================================
        cobranca_id = None
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO cobrancas (
                        id_condominio, valor, data_vencimento,
                        forma_pagamento, descricao, status,
                        asaas_payment_id, asaas_customer_id,
                        data_criacao, data_atualizacao
                    ) VALUES (%s, %s, %s, %s, %s, 'pendente', %s, %s, NOW(), NOW())
                """, (
                    dados.condominio_id,
                    dados.valor,
                    dados.data_vencimento,
                    (dados.forma_pagamento or 'pix').lower(),
                    descricao,
                    asaas_payment_id,
                    customer_id,
                ))
                conn.commit()
                cobranca_id = cursor.lastrowid

                logger.info(f"✅ Cobrança salva no banco! ID local: {cobranca_id}, Asaas: {asaas_payment_id}")

        except Exception as db_err:
            logger.error(f"❌ Erro ao salvar cobrança no banco (Asaas já criou {asaas_payment_id}): {db_err}")
            # NÃO fazer rollback da criação no Asaas - cobrança já existe lá

        # ========================================
        # PASSO 4: NF-e ANTES do pagamento (Etapa 3, NFE_FINANCEIRO.md)
        # ========================================
        # So dispara se o boleto foi salvo (cobranca_id setado) E o condominio
        # tem a flag ligada — condominio com nfe_antes_pagamento=0 (100% da
        # base hoje) nao entra nesse bloco, comportamento identico ao de antes.
        #
        # Conexao PROPRIA (aiomysql, separada do `conn` sincrono/pymysql usado
        # acima) e usada de proposito: o boleto ja foi commitado no passo 3, e
        # emitir_nf() so aceita conexao async. Chamar aqui, depois do commit,
        # garante que a emissao roda FORA da transacao do boleto — lentidao ou
        # falha na Asaas nunca segura nem desfaz o boleto, que ja existe e ja
        # foi confirmado antes desta linha rodar.
        #
        # emitir_nf() NUNCA levanta excecao (retorna sempre dict {ok, ...}) e
        # ja tem sua propria trava de idempotencia: consulta o estado ATUAL de
        # invoice_status no banco antes de decidir se emite — nao depende de
        # webhook ter chegado.
        if cobranca_id and condominio.get("nfe_antes_pagamento"):
            try:
                import aiomysql
                from financeiro.financeiro_nfe import emitir_nf, DB_CONFIG

                nf_conn = await aiomysql.connect(**DB_CONFIG)
                try:
                    resultado_nf = await emitir_nf(nf_conn, cobranca_id)
                    logger.info(
                        f"[NFS-e antes do pagamento] cobranca={cobranca_id} "
                        f"condominio={dados.condominio_id} resultado={resultado_nf}"
                    )
                finally:
                    nf_conn.close()
            except Exception as nf_err:
                logger.error(
                    f"[NFS-e antes do pagamento] falha inesperada ao tentar emitir "
                    f"NF pra cobranca={cobranca_id}: {nf_err}"
                )

        return {
            "success": True,
            "id": cobranca_id,
            "asaas_payment_id": asaas_payment_id,
            "link_pagamento": link_pagamento,
            "message": "Cobrança criada com sucesso"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao criar cobrança: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao criar cobrança: {str(e)}")
    finally:
        conn.close()


@router.post("/cobrancas/{cobranca_id}/estornar")
async def estornar_cobranca(
    cobranca_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Estorna uma cobrança paga"""
    conn = get_db_connection()
    try:
        role = current_user.get('role', '')

        if role not in ['admin_sistema', 'sindico', 'admin_condominio']:
            raise HTTPException(status_code=403, detail="Sem permissão")

        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE cobrancas
                SET status = 'estornada', data_atualizacao = NOW()
                WHERE id_cobranca = %s
            """, (cobranca_id,))

            conn.commit()

            return {"success": True, "message": "Cobrança estornada"}

    except Exception as e:
        conn.rollback()
        logger.error(f"Erro ao estornar: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


# ========================================
# Rotas de Email
# ========================================

@router.post("/cobrancas/email/reenviar/{id_cobranca}")
async def reenviar_email_cobranca(
    id_cobranca: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Reenvia email de cobrança com link de pagamento do Asaas
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Buscar cobrança
            cursor.execute("""
                SELECT
                    c.id_cobranca,
                    c.asaas_payment_id,
                    c.id_condominio,
                    c.valor,
                    c.data_vencimento,
                    c.data_pagamento,
                    c.status,
                    c.descricao,
                    c.forma_pagamento,
                    cond.nome as nome_condominio,
                    cond.cobranca_email,
                    cond.email_financeiro,
                    cond.email
                FROM cobrancas c
                JOIN condominios cond ON cond.id = c.id_condominio
                WHERE c.asaas_payment_id = %s OR c.id_cobranca = %s
                LIMIT 1
            """, (id_cobranca, int(id_cobranca) if id_cobranca.isdigit() else 0))

            cobranca = cursor.fetchone()

            if not cobranca:
                raise HTTPException(status_code=404, detail="Cobrança não encontrada")

            # Email destino
            email_destino = cobranca.get('cobranca_email') or cobranca.get('email_financeiro') or cobranca.get('email')

            if not email_destino:
                raise HTTPException(status_code=400, detail="Condomínio não possui email cadastrado")

            # Gerar link de pagamento do Asaas
            link_pagamento = gerar_link_pagamento(cobranca.get('asaas_payment_id'))

            if not link_pagamento:
                raise HTTPException(status_code=400, detail="Cobrança não possui link de pagamento Asaas")

            # Formatar datas
            vencimento_fmt = cobranca['data_vencimento'].strftime("%d/%m/%Y") if cobranca.get('data_vencimento') else "-"
            pagamento_fmt = cobranca['data_pagamento'].strftime("%d/%m/%Y") if cobranca.get('data_pagamento') else "-"

            status_norm = normalizar_status(cobranca.get('status', ''))

            # Gerar email
            assunto, corpo_html, corpo_texto = gerar_email_cobranca(
                nome_condominio=cobranca['nome_condominio'],
                valor=float(cobranca['valor']),
                vencimento=vencimento_fmt,
                descricao=cobranca.get('descricao') or 'Mensalidade',
                link_pagamento=link_pagamento,
                status=status_norm,
                data_pagamento=pagamento_fmt
            )

            # Enviar email
            resultado = enviar_email(email_destino, assunto, corpo_html, corpo_texto)

            if resultado.get("success"):
                logger.info(f"✅ Email enviado para {email_destino} - Cobrança: {id_cobranca}")
                return {
                    "success": True,
                    "message": f"Email enviado para {email_destino}",
                    "email_destino": email_destino,
                    "link_pagamento": link_pagamento
                }
            else:
                raise HTTPException(status_code=500, detail=resultado.get("message"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao enviar email: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


# ========================================
# Rotas de WhatsApp
# ========================================

@router.post("/cobrancas/whatsapp/enviar/{id_cobranca}")
async def enviar_whatsapp_cobranca(
    id_cobranca: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Envia WhatsApp de cobrança com link de pagamento do Asaas
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Buscar cobrança
            cursor.execute("""
                SELECT
                    c.id_cobranca,
                    c.asaas_payment_id,
                    c.id_condominio,
                    c.valor,
                    c.data_vencimento,
                    c.data_pagamento,
                    c.status,
                    c.descricao,
                    c.forma_pagamento,
                    cond.nome as nome_condominio,
                    cond.cobranca_whats,
                    cond.telefone
                FROM cobrancas c
                JOIN condominios cond ON cond.id = c.id_condominio
                WHERE c.asaas_payment_id = %s OR c.id_cobranca = %s
                LIMIT 1
            """, (id_cobranca, int(id_cobranca) if id_cobranca.isdigit() else 0))

            cobranca = cursor.fetchone()

            if not cobranca:
                raise HTTPException(status_code=404, detail="Cobrança não encontrada")

            # Telefone
            telefone = cobranca.get('cobranca_whats') or cobranca.get('telefone')

            if not telefone:
                raise HTTPException(status_code=400, detail="Condomínio não possui WhatsApp cadastrado")

            # Gerar link de pagamento do Asaas
            link_pagamento = gerar_link_pagamento(cobranca.get('asaas_payment_id'))

            if not link_pagamento:
                raise HTTPException(status_code=400, detail="Cobrança não possui link de pagamento Asaas")

            # Formatar datas
            vencimento_fmt = cobranca['data_vencimento'].strftime("%d/%m/%Y") if cobranca.get('data_vencimento') else "-"
            pagamento_fmt = cobranca['data_pagamento'].strftime("%d/%m/%Y") if cobranca.get('data_pagamento') else "-"

            status_norm = normalizar_status(cobranca.get('status', ''))

            # Gerar mensagem
            mensagem = gerar_mensagem_whatsapp(
                nome_condominio=cobranca['nome_condominio'],
                valor=float(cobranca['valor']),
                vencimento=vencimento_fmt,
                descricao=cobranca.get('descricao') or 'Mensalidade',
                link_pagamento=link_pagamento,
                status=status_norm,
                data_pagamento=pagamento_fmt
            )

            # Enviar WhatsApp
            resultado = await enviar_whatsapp_zapi(telefone, mensagem)

            if resultado.get("success"):
                logger.info(f"✅ WhatsApp enviado para {telefone} - Cobrança: {id_cobranca}")
                return {
                    "success": True,
                    "message": "WhatsApp enviado com sucesso",
                    "telefone": telefone,
                    "link_pagamento": link_pagamento
                }
            else:
                raise HTTPException(status_code=500, detail=resultado.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao enviar WhatsApp: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
