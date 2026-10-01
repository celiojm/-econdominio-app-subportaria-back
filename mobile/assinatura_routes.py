# ================================================================================
# ALTERAÇÃO 2026-09-28: segurança — gerar-cobranca e reenviar-boleto exigem login do app
#                      (get_current_operador) e só o condomínio do operador (ou admin_sistema)
# ALTERAÇÃO 2026-09-27: padronização 7 status — /verificar com os 7 status (rotulo, alertas)
# ALTERAÇÃO 2026-09-27: régua de cobrança — /verificar com situação + data_vencimento opcional
# ARQUIVO: assinatura_routes.py
# PASTA: /home/visionlpr/backend/mobile/
# DESCRIÇÃO: Rotas para verificação de assinatura e geração de cobranças mobile
# VERSÃO: 2.1.0 - Preço lido de tabela_precos/tabela_precos_planos (fim do hardcode)
# CRIAÇÃO: 2026-01-xx   ALTERAÇÃO: 2026-07-26
# ================================================================================
from app.database import get_db, SessionLocal
from financeiro.financeiro_preco_routes import _calcular_valor_mensal_base
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, date, timedelta
from pydantic import BaseModel
from typing import Optional, List
from app.services.assinatura_situacao import situacao_assinatura
from mobile.auth_routes import get_current_operador


def _checar_operador_do_condominio(operador, condominio_id: int):
    """Só o operador do próprio condomínio (ou admin_sistema) gera/reenvia boleto."""
    if operador is None:  # chamada interna já autenticada (ex.: /painel/financeiro/gerar-cobranca)
        return
    role = getattr(operador.role, "value", operador.role)
    if role != "admin_sistema" and operador.condominio_id != condominio_id:
        raise HTTPException(status_code=403, detail="Sem permissão para este condomínio")
import httpx
import os
import ssl
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from app.database import get_db, SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mobile/assinatura", tags=["Mobile - Assinatura"])

# ================================================================================
#  CONFIGURAÇÃO ASAAS
# ================================================================================

ASAAS_API_KEY  = os.getenv("ASAAS_API_KEY", "")
ASAAS_BASE_URL = os.getenv("ASAAS_BASE_URL", "https://api.asaas.com/v3")

def get_asaas_headers():
    return {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json"
    }

# ================================================================================
#  CONFIGURAÇÃO EMAIL / SMTP
# ================================================================================

SMTP_HOST  = os.getenv("EMAIL_SMTP_SERVER", "smtp-relay.brevo.com")
SMTP_PORT  = int(os.getenv("EMAIL_SMTP_PORT", 587))
SMTP_USER  = os.getenv("EMAIL_SMTP_USERNAME", "")
SMTP_PASS  = os.getenv("EMAIL_SMTP_PASSWORD", "")
SMTP_FROM  = os.getenv("EMAIL_FROM_ADDRESS", "financeiro@econdominio.com.br")
SMTP_NAME  = os.getenv("EMAIL_FROM_NAME", "Financeiro eCondomínio")

EMPRESA_NOME     = "E-CONDOMÍNIO SISTEMAS DE GESTÃO LTDA"
EMPRESA_CNPJ     = "64.931.933/0001-85"
EMPRESA_SITE     = "https://econdominio.com.br"
EMPRESA_FONE     = "(48) 3035-1252"
EMPRESA_LOGO_URL = "https://admin.econdominio.com.br/logo_header_50h.f52445df23437789f112.png"

# ================================================================================
#  CONFIGURAÇÃO WHATSAPP (Z-API)
# ================================================================================

ZAPI_URL            = os.getenv("ZAPI_URL", "")
ZAPI_TOKEN          = os.getenv("ZAPI_TOKEN", "")
ZAPI_SECURITY_TOKEN = os.getenv("ZAPI_SECURITY_TOKEN", os.getenv("ZAPI_CLIENT_TOKEN", ""))

# ================================================================================
#  VENCIMENTO DO BOLETO
# ================================================================================

VENCIMENTO_BOLETO_DIAS = 3   # boleto vence em X dias a partir de hoje

# ================================================================================
#  SCHEMAS
# ================================================================================

class AssinaturaResponse(BaseModel):
    validade_ate: Optional[str]
    status: str
    dias_restantes: int
    mensagem: str
    link_pagamento: Optional[str]
    asaas_payment_id: Optional[str]
    condominio_nome: str
    total_unidades: int
    tem_boleto_pendente: bool


class PlanoOpcao(BaseModel):
    label: str           # "Mensal"
    dias_validade: int   # 30
    valor_mensal: float  # valor por mês (para exibição)
    valor_total: float   # valor cobrado (pode ser mensal * meses)
    desconto_pct: int    # 0, 5, 10, 15
    nova_validade: str   # DD/MM/YYYY


class PlanoDisponivelResponse(BaseModel):
    planos: List[PlanoOpcao]
    total_apartamentos: int
    validade_atual: Optional[str]


class GerarCobrancaRequest(BaseModel):
    dias_validade: int = 30   # 30, 90, 180, 360


# ================================================================================
#  FUNÇÕES AUXILIARES
# ================================================================================

def fmt_moeda(v: float) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return str(v)


def fmt_data(d) -> str:
    if not d:
        return '—'
    try:
        return date.fromisoformat(str(d)[:10]).strftime('%d/%m/%Y')
    except Exception:
        return str(d)
# ---------------------------------------------------------------------------
# Preço: fonte única = tabela_precos / tabela_precos_planos (banco).
# NÃO reintroduzir fórmula hardcoded aqui. Ver financeiro/financeiro_preco_routes.py
# ---------------------------------------------------------------------------

def _carregar_faixas(db) -> list:
    """Faixas ativas da tabela_precos, com o piso valor_minimo."""
    rows = db.execute(text("""
        SELECT qtd_min, qtd_max, tipo, valor_fixo, valor_minimo,
               coef_a, coef_b, fator_min, fator_max
        FROM tabela_precos
        WHERE ativo = 1
        ORDER BY qtd_min ASC
    """)).fetchall()
    if not rows:
        raise HTTPException(status_code=500, detail="Tabela de precos nao configurada")
    return [dict(r._mapping) for r in rows]


def _desconto_por_meses(db, meses: int) -> float:
    """Desconto (%) do plano, lido de tabela_precos_planos."""
    row = db.execute(text("""
        SELECT desconto_pct FROM tabela_precos_planos
        WHERE meses = :meses AND ativo = 1 LIMIT 1
    """), {"meses": meses}).fetchone()
    if not row:
        raise HTTPException(status_code=500, detail=f"Plano de {meses} meses nao configurado")
    return float(row.desconto_pct)


def calcular_valor_mensal(total_apartamentos: int, db=None) -> float:
    """Valor base mensal sem desconto - lido do banco."""
    propria = db is None
    if propria:
        db = SessionLocal()
    try:
        return _calcular_valor_mensal_base(total_apartamentos or 0, _carregar_faixas(db))
    finally:
        if propria:
            db.close()


def calcular_valor_plano(total_apartamentos: int, dias_validade: int, db=None) -> tuple:
    """
    Retorna (valor_mensal, valor_total, desconto_pct) para um plano.
    dias_validade: 30=mensal, 90=trimestral, 180=semestral, 360=anual
    Faixas e descontos vem do banco - sem fallback hardcoded (falha ruidosa
    e melhor que faturar preco errado em silencio).
    """
    propria = db is None
    if propria:
        db = SessionLocal()
    try:
        meses       = {30: 1, 90: 3, 180: 6, 360: 12}.get(dias_validade, 1)
        base_mensal = _calcular_valor_mensal_base(total_apartamentos or 0, _carregar_faixas(db))
        desc_pct    = _desconto_por_meses(db, meses)
        valor_mensal_com_desc = round(base_mensal * (1 - desc_pct / 100), 2)
        valor_total = round(valor_mensal_com_desc * meses, 2) if meses > 1 else valor_mensal_com_desc
        return valor_mensal_com_desc, valor_total, desc_pct
    finally:
        if propria:
            db.close()





def gerar_link_pagamento(asaas_payment_id: str) -> str:
    if not asaas_payment_id:
        return None
    asaas_id = asaas_payment_id.replace('pay_', '')
    return f"https://www.asaas.com/i/{asaas_id}"


def _email_destinos(cond: dict) -> list:
    emails = []
    for campo in ['cobranca_email', 'email_financeiro', 'email']:
        v = (cond.get(campo) or '').strip()
        if v and v not in emails:
            emails.append(v)
    return emails[:2]


def _whatsapp_numero(cond: dict) -> str:
    numero = (cond.get('cobranca_whats') or cond.get('telefone') or '').strip()
    return ''.join(filter(str.isdigit, numero))


# ================================================================================
#  ASAAS — CRIAR/BUSCAR CLIENTE (com notificações desabilitadas)
# ================================================================================

async def buscar_ou_criar_cliente_asaas(condominio: dict, db: Session) -> str:
    """Busca ou cria cliente no Asaas. Sempre desabilita notificações."""
    try:
        customer_id = condominio.get("asaas_customer_id")

        async with httpx.AsyncClient(timeout=30) as client:
            if not customer_id:
                # Criar novo cliente
                cnpj     = ''.join(filter(str.isdigit, condominio.get('cnpj') or ''))
                email    = (condominio.get('cobranca_email') or
                            condominio.get('email_financeiro') or
                            condominio.get('email') or
                            f"contato{condominio['id']}@econdominio.com.br")
                tel      = ''.join(filter(str.isdigit,
                            condominio.get('cobranca_whats') or
                            condominio.get('telefone') or ''))

                r = await client.post(
                    f"{ASAAS_BASE_URL}/customers",
                    headers=get_asaas_headers(),
                    json={
                        "name":                 condominio["nome"],
                        "cpfCnpj":              cnpj,
                        "email":                email,
                        "phone":                tel[:11] if tel else None,
                        "externalReference":    f"COND_{condominio['id']}",
                        "notificationDisabled": True,
                    }
                )
                if r.status_code not in (200, 201):
                    logger.error(f"Erro criar customer Asaas: {r.text}")
                    return None

                customer_id = r.json().get('id')
                logger.info(f"Cliente Asaas criado: {customer_id}")

                # Salvar no banco
                db.execute(
                    text("UPDATE condominios SET asaas_customer_id = :cid WHERE id = :id"),
                    {"cid": customer_id, "id": condominio["id"]}
                )
                db.commit()

            # Desabilitar TODAS as notificações individuais (sempre, mesmo cliente existente)
            try:
                rn = await client.get(
                    f"{ASAAS_BASE_URL}/customers/{customer_id}/notifications",
                    headers=get_asaas_headers()
                )
                notif_ids = [n['id'] for n in rn.json().get('data', [])
                             if not n.get('deleted', False)]
                if notif_ids:
                    await client.post(
                        f"{ASAAS_BASE_URL}/notifications/batch",
                        headers=get_asaas_headers(),
                        json={
                            "customer": customer_id,
                            "notifications": [
                                {
                                    "id":                          nid,
                                    "enabled":                     False,
                                    "emailEnabledForCustomer":     False,
                                    "smsEnabledForCustomer":       False,
                                    "whatsappEnabledForCustomer":  False,
                                    "phoneCallEnabledForCustomer": False,
                                    "emailEnabledForProvider":     False,
                                    "smsEnabledForProvider":       False,
                                }
                                for nid in notif_ids
                            ]
                        }
                    )
                    logger.info(f"🔕 {len(notif_ids)} notificações desabilitadas para {customer_id}")
            except Exception as en:
                logger.warning(f"⚠️ Não foi possível desabilitar notificações: {en}")

        return customer_id

    except Exception as e:
        logger.error(f"Erro buscar/criar cliente Asaas: {e}")
        return None


# ================================================================================
#  ASAAS — CRIAR COBRANÇA BOLETO+PIX
# ================================================================================

async def criar_cobranca_asaas(
    condominio: dict,
    valor: float,
    dias_validade: int,
    customer_id: str,
    db: Session,
    data_vencimento: Optional[str] = None,
) -> dict:
    """Cria cobrança BOLETO (com QR Code PIX) no Asaas e salva no banco."""

    data_vencimento = data_vencimento or (date.today() + timedelta(days=VENCIMENTO_BOLETO_DIAS)).strftime("%Y-%m-%d")

    mapa_periodo = {30: "Mensal", 90: "Trimestral", 180: "Semestral", 360: "Anual"}
    periodo = mapa_periodo.get(dias_validade, f"{dias_validade} dias")
    mapa_plano = {30: "mensal", 90: "trimestral", 180: "semestral", 360: "anual"}
    plano_slug = mapa_plano.get(dias_validade, "mensal")

    descricao  = f"{periodo} - {condominio['nome']}"
    ext_ref    = f"COND_{condominio['id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    payload = {
        "customer":          customer_id,
        "billingType":       "BOLETO",    # gera boleto bancário + QR Code PIX combinado
        "value":             float(valor),
        "dueDate":           data_vencimento,
        "description":       descricao,
        "externalReference": ext_ref,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{ASAAS_BASE_URL}/payments",
                headers=get_asaas_headers(),
                json=payload
            )

            if r.status_code not in (200, 201):
                logger.error(f"Erro criar payment Asaas: {r.text}")
                return {"success": False, "error": r.text}

            data          = r.json()
            payment_id    = data.get('id')
            link_pagamento = data.get('invoiceUrl') or gerar_link_pagamento(payment_id)

            # Salvar em cobrancas
            db.execute(text("""
                INSERT INTO cobrancas
                  (id_condominio, valor, data_vencimento, status,
                   descricao, asaas_payment_id, asaas_customer_id,
                   forma_pagamento, data_criacao, data_atualizacao)
                VALUES
                  (:id_cond, :valor, :venc, 'pendente',
                   :descricao, :payment_id, :customer_id,
                   'boleto', NOW(), NOW())
            """), {
                "id_cond":    condominio["id"],
                "valor":      valor,
                "venc":       data_vencimento,
                "descricao":  descricao,
                "payment_id": payment_id,
                "customer_id": customer_id,
            })

            # Atualizar plano_selecionado no condomínio
            db.execute(text("""
                UPDATE condominios
                SET plano_selecionado = :plano
                WHERE id = :id
            """), {"plano": plano_slug, "id": condominio["id"]})

            db.commit()
            logger.info(f"✅ Cobrança criada: {payment_id} | {link_pagamento}")

            return {
                "success":        True,
                "asaas_payment_id": payment_id,
                "link_pagamento": link_pagamento,
                "valor":          valor,
                "data_vencimento": data_vencimento,
                "descricao":      descricao,
                "periodo":        periodo,
                "plano_slug":     plano_slug,
            }

    except Exception as e:
        logger.error(f"Erro criar cobrança Asaas: {e}")
        return {"success": False, "error": str(e)}


# ================================================================================
#  ENVIO DE EMAIL
# ================================================================================

def enviar_email_cobranca(
    cond: dict,
    valor: float,
    descricao: str,
    link_pagamento: str,
    data_vencimento_boleto: str,
    validade_atual: str,
    nova_validade: str,
) -> bool:
    destinos = _email_destinos(cond)
    if not destinos:
        logger.warning(f"Sem email para {cond['nome']}")
        return False

    nome    = cond['nome']
    valor_f = fmt_moeda(valor)

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f8;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
       style="background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,.10);max-width:600px;">
  <tr><td style="background:linear-gradient(135deg,#1a3a5c 0%,#2563eb 100%);padding:28px 32px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td><img src="{EMPRESA_LOGO_URL}" alt="eCondomínio" height="44"
               style="display:block;" onerror="this.style.display='none'"></td>
      <td style="text-align:right;vertical-align:middle;">
        <p style="margin:0;color:#93c5fd;font-size:12px;line-height:1.6;">
          {EMPRESA_FONE}<br>
          <a href="mailto:{SMTP_FROM}" style="color:#bfdbfe;text-decoration:none;">{SMTP_FROM}</a><br>
          <a href="{EMPRESA_SITE}" style="color:#bfdbfe;text-decoration:none;">{EMPRESA_SITE}</a>
        </p>
      </td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:32px;">
    <h2 style="color:#d97706;margin:0 0 8px 0;font-size:22px;">🔔 Boleto de Renovação Gerado</h2>
    <p style="color:#475569;font-size:15px;line-height:1.7;margin:0 0 24px 0;">
      Prezado(a) gestor(a) do <strong>{nome}</strong>,<br><br>
      Segue a cobrança de renovação da licença do sistema eCondomínio:
    </p>
    <table width="100%" cellpadding="8" cellspacing="0"
           style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;font-size:14px;margin:0 0 24px 0;">
      <tr style="border-bottom:1px solid #fde68a;">
        <td style="color:#92400e;width:45%;">Descrição</td>
        <td style="font-weight:600;color:#1e293b;">{descricao}</td>
      </tr>
      <tr style="background:#fff7ed;border-bottom:1px solid #fde68a;">
        <td style="color:#92400e;">Valor</td>
        <td style="font-weight:700;color:#059669;font-size:22px;">{valor_f}</td>
      </tr>
      <tr style="border-bottom:1px solid #fde68a;">
        <td style="color:#92400e;">Vencimento do boleto</td>
        <td style="font-weight:600;color:#1e293b;">{data_vencimento_boleto}</td>
      </tr>
      <tr style="background:#fff7ed;border-bottom:1px solid #fde68a;">
        <td style="color:#92400e;">Validade atual</td>
        <td style="font-weight:600;color:#1e293b;">{validade_atual}</td>
      </tr>
      <tr style="background:#fff7ed;">
        <td style="color:#92400e;">Nova validade após pagamento</td>
        <td style="font-weight:700;color:#1a3a5c;">{nova_validade}</td>
      </tr>
    </table>
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr><td align="center" style="padding:8px 0 12px 0;">
        <a href="{link_pagamento}"
           style="display:inline-block;background:linear-gradient(135deg,#1a3a5c 0%,#2563eb 100%);
                  color:#fff;text-decoration:none;padding:15px 48px;border-radius:8px;
                  font-size:16px;font-weight:700;box-shadow:0 4px 12px rgba(37,99,235,0.35);">
          💳 Pagar Agora (Boleto ou PIX)
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
      Link: <a href="{link_pagamento}" style="color:#3b82f6;">{link_pagamento}</a>
    </p>
  </td></tr>
  <tr><td style="padding:0 32px;"><hr style="border:none;border-top:1px solid #e2e8f0;margin:0;"></td></tr>
  <tr><td style="background:#f8fafc;padding:20px 32px;">
    <p style="margin:0 0 4px 0;color:#1e3a5f;font-size:12px;font-weight:700;">{EMPRESA_NOME}</p>
    <p style="margin:0;color:#64748b;font-size:11px;line-height:1.7;">
      CNPJ: {EMPRESA_CNPJ} · Rua Professora Sofia Quint de Souza, 544, Capoeiras<br>
      Florianópolis - SC · CEP 88085-040 · {EMPRESA_FONE} ·
      <a href="mailto:{SMTP_FROM}" style="color:#3b82f6;">{SMTP_FROM}</a>
    </p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"🔔 Boleto de Renovação — {nome}"
        msg['From']    = f"{SMTP_NAME} <{SMTP_FROM}>"
        msg['To']      = ', '.join(destinos)
        msg.attach(MIMEText(html, 'html', 'utf-8'))
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
            srv.ehlo(); srv.starttls(context=ctx); srv.ehlo()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(SMTP_FROM, destinos, msg.as_string())
        logger.info(f"📧 Email enviado → {destinos}")
        return True
    except Exception as e:
        logger.error(f"❌ Erro email: {e}")
        return False


# ================================================================================
#  ENVIO DE WHATSAPP (Z-API)
# ================================================================================

def enviar_whatsapp_cobranca(
    cond: dict,
    valor: float,
    descricao: str,
    link_pagamento: str,
    data_vencimento_boleto: str,
    nova_validade: str,
) -> bool:
    if not ZAPI_URL or not ZAPI_TOKEN:
        logger.warning("Z-API não configurado — pulando WhatsApp")
        return False

    numero = _whatsapp_numero(cond)
    if not numero:
        logger.warning(f"Sem WhatsApp para {cond['nome']}")
        return False

    n = numero
    if n.startswith('0'):
        n = n[1:]
    if not n.startswith('55'):
        n = '55' + n

    mensagem = (
        f"🔔 *Boleto de Renovação — eCondomínio*\n\n"
        f"Prezado(a) gestor(a) do *{cond['nome']}*,\n\n"
        f"Seu boleto de renovação foi gerado:\n\n"
        f"📋 *Detalhes:*\n"
        f"• {descricao}\n"
        f"• Valor: {fmt_moeda(valor)}\n"
        f"• Vencimento: {data_vencimento_boleto}\n"
        f"• Nova validade: {nova_validade}\n\n"
        f"💳 *Pagar (Boleto ou PIX):*\n{link_pagamento}\n\n"
        f"Dúvidas: {EMPRESA_FONE} | {SMTP_FROM}"
    )

    try:
        headers = {'Content-Type': 'application/json'}
        if ZAPI_SECURITY_TOKEN:
            headers['Client-Token'] = ZAPI_SECURITY_TOKEN

        with httpx.Client(timeout=15) as client:
            r = client.post(
                f"{ZAPI_URL}/send-text",
                headers=headers,
                json={"phone": n, "message": mensagem, "token": ZAPI_TOKEN}
            )
            if r.status_code == 200:
                logger.info(f"📱 WhatsApp enviado → {n}")
                return True
            else:
                logger.warning(f"⚠️ WhatsApp erro {r.status_code}: {r.text[:100]}")
                return False
    except Exception as e:
        logger.error(f"❌ Erro WhatsApp: {e}")
        return False


# ================================================================================
#  ENDPOINTS
# ================================================================================

@router.get("/verificar/{condominio_id}")
async def verificar_assinatura(condominio_id: int, db: Session = Depends(get_db)):
    """
    Verifica o status da assinatura do condomínio.
    Retorna status, dias_restantes, link de pagamento se houver boleto pendente.
    NÃO gera cobrança automaticamente — isso agora é feito via /gerar-cobranca.
    """
    try:
        result = db.execute(text("""
            SELECT
                c.id, c.nome, c.validade_ate, c.assinatura_status,
                c.total_apartamentos, c.asaas_customer_id,
                c.email, c.email_financeiro, c.cobranca_email,
                c.telefone, c.cobranca_whats, c.cnpj, c.bonificado
            FROM condominios c
            WHERE c.id = :id
        """), {"id": condominio_id})

        cond = result.fetchone()
        if not cond:
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")

        hoje            = date.today()
        validade_ate    = cond.validade_ate
        bonificado      = bool(getattr(cond, 'bonificado', False))
        total_unidades  = cond.total_apartamentos or 0
        dias_restantes  = (validade_ate - hoje).days if validade_ate else -999

        # Situação vem da fonte única (app/services/assinatura_situacao.py)
        sit = situacao_assinatura(db, condominio_id)
        situacao = sit["status"]
        dias_restantes = sit["dias_restantes"]
        mensagem = sit["mensagem"]

        # Compatibilidade com o frontend atual: ativa | carencia | bonificado | expirada
        if sit["tela_bloqueada"]:
            status = "expirada"
        elif situacao == "nao_convertido" or not sit["pode_receber"] and sit["dias_restantes"] < 0:
            status = "carencia"
        elif sit["bonificado_vigente"]:
            status = "bonificado"
        else:
            status = "ativa"

        # Buscar boleto pendente existente (não gera automaticamente)
        link_pagamento   = None
        asaas_payment_id = None
        tem_boleto       = False

        cobranca = db.execute(text("""
            SELECT asaas_payment_id, valor, data_vencimento
            FROM cobrancas
            WHERE id_condominio = :id
              AND LOWER(status) IN ('pendente', 'pending')
              AND data_vencimento >= CURDATE()
            ORDER BY data_criacao DESC
            LIMIT 1
        """), {"id": condominio_id}).fetchone()

        # 2026-10-01: boleto atual (último não cancelado) pendente e já vencido
        ultimo_boleto = db.execute(text("""
            SELECT status, data_vencimento FROM cobrancas
            WHERE id_condominio = :id AND LOWER(status) NOT IN ('cancelada', 'cancelado')
            ORDER BY data_vencimento DESC, id_cobranca DESC LIMIT 1
        """), {"id": condominio_id}).fetchone()
        boleto_vencido = bool(ultimo_boleto and (ultimo_boleto.status or '').lower() in ('pendente', 'pending')
                              and ultimo_boleto.data_vencimento and ultimo_boleto.data_vencimento < date.today())

        if cobranca and cobranca.asaas_payment_id:
            asaas_payment_id = cobranca.asaas_payment_id
            link_pagamento   = gerar_link_pagamento(asaas_payment_id)
            tem_boleto       = True

        return {
            "validade_ate":       fmt_data(validade_ate),
            "status":             status,
            "dias_restantes":     dias_restantes,
            "mensagem":           mensagem,
            "link_pagamento":     link_pagamento,
            "asaas_payment_id":   asaas_payment_id,
            "condominio_nome":    cond.nome,
            "total_unidades":     total_unidades,
            "tem_boleto_pendente": tem_boleto,
            "boleto_vencido":     boleto_vencido,
            "situacao":           situacao,
            "rotulo":             sit["rotulo"],
            "alertas":            sit["alertas"],
            "dias_para_desativar": sit["dias_para_desativar"],
            "pode_receber":       sit["pode_receber"],
            "pode_entregar":      sit["pode_entregar"],
            "somente_leitura":    sit["somente_leitura"],
            "tela_bloqueada":     sit["tela_bloqueada"],
            "pode_continuar":     sit["pode_continuar"],
            "cadastro_incompleto": sit["cadastro_incompleto"],
            "mostrar_aviso_pagamento": sit["mostrar_aviso_pagamento"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro verificar assinatura: {e}")
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao verificar assinatura: {str(e)}")


@router.get("/planos-disponiveis/{condominio_id}")
async def planos_disponiveis(condominio_id: int, db: Session = Depends(get_db)):
    """
    Retorna os planos disponíveis com valores calculados dinamicamente
    baseados no total de apartamentos do condomínio.
    """
    try:
        result = db.execute(text("""
            SELECT id, nome, total_apartamentos, validade_ate, valor_plano_final
            FROM condominios WHERE id = :id
        """), {"id": condominio_id})
        cond = result.fetchone()
        if not cond:
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")
        total_apts     = cond.total_apartamentos or 0
        validade_ate   = cond.validade_ate
        # Cliente com valor_plano_final travado (>0) mantém o preco contratado -
        # nao mostrar preco novo da tabela_precos para quem nao foi reajustado.
        valor_travado  = float(cond.valor_plano_final or 0)
        planos_config = [
            {"label": "Mensal",     "dias": 30,  "meses": 1},
            {"label": "Trimestral", "dias": 90,  "meses": 3},
            {"label": "Semestral",  "dias": 180, "meses": 6},
            {"label": "Anual",      "dias": 360, "meses": 12},
        ]
        planos = []
        hoje = date.today()
        for p in planos_config:
            if valor_travado > 0:
                desc_pct     = _desconto_por_meses(db, p["meses"])
                valor_mensal = round(valor_travado * (1 - desc_pct / 100), 2)
                valor_total  = round(valor_mensal * p["meses"], 2) if p["meses"] > 1 else valor_mensal
            else:
                valor_mensal, valor_total, desc_pct = calcular_valor_plano(total_apts, p["dias"])
            # Nova validade: a partir de validade_ate ou hoje (o que for maior)
            base_validade = validade_ate if (validade_ate and validade_ate >= hoje) else hoje
            nova_validade = base_validade + timedelta(days=p["dias"])
            planos.append({
                "label":        p["label"],
                "dias_validade": p["dias"],
                "valor_mensal": valor_mensal,
                "valor_total":  valor_total,
                "desconto_pct": desc_pct,
                "nova_validade": fmt_data(nova_validade),
            })


        return {
            "planos":              planos,
            "total_apartamentos":  total_apts,
            "validade_atual":      fmt_data(validade_ate),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro planos disponíveis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gerar-cobranca/{condominio_id}")
async def gerar_cobranca(
    condominio_id: int,
    dados: GerarCobrancaRequest,
    db: Session = Depends(get_db),
    operador=Depends(get_current_operador)
):
    """
    Gera nova cobrança BOLETO+PIX para o condomínio.
    Cria cliente no Asaas se necessário (com notificações desabilitadas).
    Salva em cobrancas, atualiza plano_selecionado.
    Envia email e WhatsApp.
    """
    _checar_operador_do_condominio(operador, condominio_id)
    try:
        result = db.execute(text("""
            SELECT
                c.id, c.nome, c.total_apartamentos, c.asaas_customer_id,
                c.email, c.email_financeiro, c.cobranca_email,
                c.telefone, c.cobranca_whats, c.cnpj, c.validade_ate,
                c.valor_plano_final
            FROM condominios c
            WHERE c.id = :id
        """), {"id": condominio_id})

        cond = result.fetchone()
        if not cond:
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")

        # Verificar se já tem cobrança pendente para não duplicar
        boleto_existente = db.execute(text("""
            SELECT asaas_payment_id FROM cobrancas
            WHERE id_condominio = :id
              AND LOWER(status) IN ('pendente','pending')
              AND data_vencimento >= CURDATE()
            LIMIT 1
        """), {"id": condominio_id}).fetchone()

        if boleto_existente:
            return {
                "success":          True,
                "ja_existia":       True,
                "link_pagamento":   gerar_link_pagamento(boleto_existente.asaas_payment_id),
                "asaas_payment_id": boleto_existente.asaas_payment_id,
                "message":          "Já existe boleto pendente para este condomínio."
            }

        total_apts = cond.total_apartamentos or 0

        # Calcular valor — usa valor_plano_final se definido, senão calcula dinâmico
        valor_fixo = float(cond.valor_plano_final or 0)
        if valor_fixo > 0:
            valor_total = valor_fixo
        else:
            _, valor_total, _ = calcular_valor_plano(total_apts, dados.dias_validade)

        cond_dict = {
            "id":              cond.id,
            "nome":            cond.nome,
            "email":           cond.email,
            "email_financeiro": cond.email_financeiro,
            "cobranca_email":  cond.cobranca_email,
            "telefone":        cond.telefone,
            "cobranca_whats":  cond.cobranca_whats,
            "cnpj":            cond.cnpj or "",
            "asaas_customer_id": cond.asaas_customer_id,
        }

        # Criar/buscar cliente no Asaas (com notificações desabilitadas)
        customer_id = await buscar_ou_criar_cliente_asaas(cond_dict, db)
        if not customer_id:
            raise HTTPException(status_code=500, detail="Erro ao criar cliente no gateway de pagamento")

        cond_dict["asaas_customer_id"] = customer_id

        # Criar cobrança no Asaas
        resultado = await criar_cobranca_asaas(cond_dict, valor_total, dados.dias_validade, customer_id, db)

        if not resultado.get("success"):
            raise HTTPException(status_code=500, detail=resultado.get("error", "Erro ao gerar cobrança"))

        link_pagamento       = resultado["link_pagamento"]
        data_vencimento_str  = resultado["data_vencimento"]
        descricao            = resultado["descricao"]

        # Calcular nova validade para exibição
        validade_ate = cond.validade_ate
        hoje = date.today()
        base_validade = validade_ate if (validade_ate and validade_ate >= hoje) else hoje
        nova_validade = base_validade + timedelta(days=dados.dias_validade)

        # Enviar email
        try:
            enviar_email_cobranca(
                cond=cond_dict,
                valor=valor_total,
                descricao=descricao,
                link_pagamento=link_pagamento,
                data_vencimento_boleto=fmt_data(data_vencimento_str),
                validade_atual=fmt_data(validade_ate),
                nova_validade=fmt_data(nova_validade),
            )
        except Exception as e_email:
            logger.error(f"Erro email após gerar cobrança: {e_email}")

        # Enviar WhatsApp
        try:
            enviar_whatsapp_cobranca(
                cond=cond_dict,
                valor=valor_total,
                descricao=descricao,
                link_pagamento=link_pagamento,
                data_vencimento_boleto=fmt_data(data_vencimento_str),
                nova_validade=fmt_data(nova_validade),
            )
        except Exception as e_wpp:
            logger.error(f"Erro WhatsApp após gerar cobrança: {e_wpp}")

        return {
            "success":          True,
            "ja_existia":       False,
            "link_pagamento":   link_pagamento,
            "asaas_payment_id": resultado["asaas_payment_id"],
            "valor":            valor_total,
            "dias_validade":    dados.dias_validade,
            "data_vencimento":  data_vencimento_str,
            "nova_validade":    fmt_data(nova_validade),
            "descricao":        descricao,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro gerar cobrança: {e}")
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao gerar cobrança: {str(e)}")


@router.post("/reenviar-boleto/{condominio_id}")
async def reenviar_boleto(condominio_id: int, db: Session = Depends(get_db),
                          operador=Depends(get_current_operador)):
    """
    Reenvia o email e WhatsApp do boleto pendente existente.
    """
    _checar_operador_do_condominio(operador, condominio_id)
    try:
        result = db.execute(text("""
            SELECT
                cb.asaas_payment_id, cb.valor, cb.descricao, cb.data_vencimento,
                c.nome, c.email, c.email_financeiro, c.cobranca_email,
                c.telefone, c.cobranca_whats, c.validade_ate
            FROM cobrancas cb
            JOIN condominios c ON c.id = cb.id_condominio
            WHERE cb.id_condominio = :id
              AND LOWER(cb.status) IN ('pendente','pending')
              AND cb.data_vencimento >= CURDATE()
            ORDER BY cb.data_criacao DESC
            LIMIT 1
        """), {"id": condominio_id})

        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Nenhum boleto pendente encontrado")

        link_pagamento = gerar_link_pagamento(row.asaas_payment_id)
        cond_dict = {
            "id":             condominio_id,
            "nome":           row.nome,
            "email":          row.email,
            "email_financeiro": row.email_financeiro,
            "cobranca_email": row.cobranca_email,
            "telefone":       row.telefone,
            "cobranca_whats": row.cobranca_whats,
        }

        email_ok = enviar_email_cobranca(
            cond=cond_dict,
            valor=float(row.valor),
            descricao=row.descricao or f"Renovação - {row.nome}",
            link_pagamento=link_pagamento,
            data_vencimento_boleto=fmt_data(row.data_vencimento),
            validade_atual=fmt_data(row.validade_ate),
            nova_validade="—",
        )

        wpp_ok = enviar_whatsapp_cobranca(
            cond=cond_dict,
            valor=float(row.valor),
            descricao=row.descricao or f"Renovação - {row.nome}",
            link_pagamento=link_pagamento,
            data_vencimento_boleto=fmt_data(row.data_vencimento),
            nova_validade="—",
        )

        return {
            "success":        True,
            "link_pagamento": link_pagamento,
            "email_enviado":  email_ok,
            "wpp_enviado":    wpp_ok,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro reenviar boleto: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def status_assinatura():
    return {
        "status":            "ok",
        "asaas_configurado": bool(ASAAS_API_KEY),
        "zapi_configurado":  bool(ZAPI_URL and ZAPI_TOKEN),
        "timestamp":         datetime.now().isoformat()
    }
