#!/usr/bin/env python3
# ALTERAÇÃO 2026-09-27: v1.3.0 - venc 10d, pagante = tem pagamento (exclui só 'sistema'), DB_NAME do .env, NF antes do pagamento
# ============================================================================
# ARQUIVO: gerar_cobrancas_renovacao.py
# PASTA: /home/visionlpr/app_subportaria_back/
# DESCRIÇÃO: Gera boleto+PIX automaticamente para condomínios com licença
#            vencendo em até 6 dias. Envia por WhatsApp e Email.
#            Apenas condomínios ativos, com pelo menos 1 pagamento confirmado.
# VERSÃO: 1.2.0 - valor = mensal x meses x desconto (igual ao modal); trava vs último pago; --dry-run; log único
# ALTERAÇÃO: 2026-08-29
# ALTERAÇÕES v1.1.0 (2026-06-08):
#   - BUG FIX: anti-duplicata agora bloqueia cobranças pendentes mesmo vencidas
#     (removido AND p2.data_vencimento >= CURDATE())
#   - BUG FIX: calcular_valor() lê faixas e descontos do banco (tabela_precos
#     e tabela_precos_planos) em vez de valores hardcoded
#   - Fallback hardcoded mantido apenas para falha de conexão ao banco
# CRON: 0 9 * * * flock -n /tmp/renovacao.lock /home/visionlpr/app_subportaria_back/.env.worker/bin/python3 /home/visionlpr/app_subportaria_back/gerar_cobrancas_renovacao.py >> /home/visionlpr/app_subportaria_back/gerar_cobrancas_renovacao.log 2>&1
# ============================================================================
import os
import sys
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date, timedelta
import httpx

os.chdir('/home/visionlpr/app_subportaria_back')
sys.path.insert(0, '/home/visionlpr/app_subportaria_back')

from dotenv import load_dotenv
load_dotenv('/home/visionlpr/app_subportaria_back/.env.worker')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()]   # cron já redireciona para o .log
)
logger = logging.getLogger(__name__)

ASAAS_API_KEY = os.getenv('ASAAS_API_KEY', '')
ASAAS_BASE    = os.getenv('ASAAS_BASE_URL', 'https://api.asaas.com/v3')
ASAAS_HEADERS = {'access_token': ASAAS_API_KEY, 'Content-Type': 'application/json'}

SMTP_HOST  = os.getenv('EMAIL_SMTP_SERVER', 'smtp-relay.brevo.com')
SMTP_PORT  = int(os.getenv('EMAIL_SMTP_PORT', 587))
SMTP_USER  = os.getenv('EMAIL_SMTP_USERNAME', '')
SMTP_PASS  = os.getenv('EMAIL_SMTP_PASSWORD', '')
SMTP_FROM  = os.getenv('EMAIL_FROM_ADDRESS', 'financeiro@econdominio.com.br')
SMTP_NAME  = os.getenv('EMAIL_FROM_NAME', 'Financeiro eCondomínio')

EMPRESA_NOME     = "E-CONDOMÍNIO SISTEMAS DE GESTÃO LTDA"
EMPRESA_CNPJ     = "64.931.933/0001-85"
EMPRESA_SITE     = "https://econdominio.com.br"
EMPRESA_FONE     = "(48) 3035-1252"
EMPRESA_LOGO_URL = "https://admin.econdominio.com.br/logo_header_50h.f52445df23437789f112.png"

DIAS_ANTECEDENCIA = 6   # gerar cobrança quando faltar X dias
DRY_RUN = '--dry-run' in sys.argv          # só simula: não cria no Asaas, não grava, não envia
ADMIN_WHATSAPP = '5548984046118'           # recebe alerta de valor divergente
TOLERANCIA_PCT = 5.0                       # divergência máxima vs último pago do mesmo plano
VENCIMENTO_BOLETO = 10  # boleto vence em X dias (régua 2026-09-27)


def fmt_moeda(v):
    try:
        return f"R$ {float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return str(v)


def fmt_data(d):
    if not d:
        return '—'
    try:
        return date.fromisoformat(str(d)[:10]).strftime('%d/%m/%Y')
    except Exception:
        return str(d)


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


def calcular_valor(total_apartamentos: int, plano: str, conn=None) -> float:
    """
    Calcula valor do plano consultando tabela_precos e tabela_precos_planos do banco.
    Fallback hardcoded apenas em caso de falha de conexão.
    """
    qtd = max(total_apartamentos or 0, 1)

    # --- Faixas de preço (lê do banco) ---
    base = 28.61  # fallback mínimo
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT tipo, valor_fixo, coef_a, coef_b,
                       fator_min, fator_max, qtd_min, qtd_max
                FROM tabela_precos
                WHERE ativo = 1
                  AND qtd_min <= %s
                  AND (qtd_max IS NULL OR qtd_max >= %s)
                ORDER BY qtd_min DESC
                LIMIT 1
            """, (qtd, qtd))
            row = cur.fetchone()
            if row:
                tipo, valor_fixo, coef_a, coef_b, fator_min, fator_max, qtd_min, qtd_max = row
                if tipo == 'fixo':
                    # faixa 1 (≤25): valor_fixo é o total da faixa
                    # faixa 5 (>2000): valor_fixo é o preço unitário
                    if qtd_min == 1:
                        base = float(valor_fixo)
                    else:
                        base = round(qtd * float(valor_fixo), 2)
                else:
                    f_min = fator_min if fator_min is not None else qtd_min
                    f_max = fator_max if fator_max is not None else (qtd_max or qtd)
                    fator = (qtd - f_min) / max((f_max - f_min), 1)
                    base  = round(qtd * (float(coef_a) - float(coef_b) * fator), 2)
                logger.info(f"   Faixa: qtd={qtd} tipo={tipo} base={base}")
        except Exception as e:
            logger.warning(f"   ⚠️ Erro ao ler tabela_precos: {e} — usando fallback")
            # fallback hardcoded
            if qtd <= 25:
                base = 28.61
            elif qtd <= 500:
                fator = (qtd - 25) / 475
                base = round(qtd * (0.9312 - 0.1152 * fator), 2)
            elif qtd <= 999:
                fator = (qtd - 500) / 499
                base = round(qtd * (0.816 - 0.24 * fator), 2)
            elif qtd <= 2000:
                fator = (qtd - 1000) / 1000
                base = round(qtd * (0.576 - 0.0768 * fator), 2)
            else:
                base = round(qtd * 0.4992, 2)

    # --- Planos e descontos (lê do banco) ---
    desc = 0
    m    = 1
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT desconto_pct, meses
                FROM tabela_precos_planos
                WHERE codigo = %s AND ativo = 1
                LIMIT 1
            """, ((plano or 'mensal').lower(),))
            row = cur.fetchone()
            if row:
                desc = float(row[0])
                m    = int(row[1])
                logger.info(f"   Plano: {plano} desconto={desc}% meses={m}")
            else:
                logger.warning(f"   ⚠️ Plano '{plano}' não encontrado no banco — usando fallback mensal")
        except Exception as e:
            logger.warning(f"   ⚠️ Erro ao ler tabela_precos_planos: {e} — usando fallback")
            # fallback hardcoded apenas em erro de conexão
            _d = {'mensal': 0, 'trimestral': 5, 'semestral': 10, 'anual': 20}
            _m = {'mensal': 1, 'trimestral': 3, 'semestral': 6,  'anual': 12}
            plano_id = (plano or 'mensal').lower()
            desc = _d.get(plano_id, 0)
            m    = _m.get(plano_id, 1)

    valor_mes   = base * (1 - desc / 100)
    valor_total = round(valor_mes * m if m > 1 else valor_mes, 2)
    logger.info(f"   Valor calculado: base={base} desc={desc}% meses={m} total={valor_total}")
    return valor_total


def criar_cobranca_asaas(cond: dict, valor: float, vencimento: str, descricao: str) -> dict:
    """Cria cobrança BOLETO (com QR Code PIX) no Asaas. notificationDisabled=True."""
    with httpx.Client(timeout=30) as client:
        customer_id = cond.get('asaas_customer_id')
        if not customer_id:
            cnpj  = ''.join(filter(str.isdigit, cond.get('cnpj') or ''))
            email = cond.get('cobranca_email') or cond.get('email') or f"contato{cond['id']}@econdominio.com.br"
            tel   = ''.join(filter(str.isdigit, cond.get('cobranca_whats') or cond.get('telefone') or ''))
            r = client.post(f"{ASAAS_BASE}/customers", headers=ASAAS_HEADERS, json={
                "name":                 cond['nome'],
                "cpfCnpj":              cnpj,
                "email":                email,
                "phone":                tel[:11] if tel else None,
                "externalReference":    f"COND_{cond['id']}",
                "notificationDisabled": True,
            })
            if r.status_code not in (200, 201):
                raise Exception(f"Erro criar customer: {r.text}")
            customer_id = r.json().get('id')
            logger.info(f"   Cliente Asaas criado: {customer_id}")

            # Desabilitar todas as notificações individuais
            try:
                rn = client.get(f"{ASAAS_BASE}/customers/{customer_id}/notifications",
                                headers=ASAAS_HEADERS)
                notif_ids = [n['id'] for n in rn.json().get('data', [])
                             if not n.get('deleted', False)]
                if notif_ids:
                    client.post(f"{ASAAS_BASE}/notifications/batch",
                                headers=ASAAS_HEADERS,
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
                                })
                    logger.info(f"   🔕 {len(notif_ids)} notificações desabilitadas para {customer_id}")
            except Exception as en:
                logger.warning(f"   ⚠️ Não foi possível desabilitar notificações: {en}")

        from datetime import datetime
        ext_ref = f"COND_{cond['id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        r = client.post(f"{ASAAS_BASE}/payments", headers=ASAAS_HEADERS, json={
            "customer":          customer_id,
            "billingType":       "BOLETO",
            "value":             float(valor),
            "dueDate":           vencimento,
            "description":       descricao,
            "externalReference": ext_ref,
        })
        if r.status_code not in (200, 201):
            raise Exception(f"Erro criar payment: {r.text}")

        data = r.json()
        return {
            "asaas_payment_id":  data.get('id'),
            "asaas_customer_id": customer_id,
            "link_pagamento":    data.get('invoiceUrl') or f"https://www.asaas.com/i/{data.get('id','').replace('pay_','')}",
        }


def enviar_whatsapp(numero: str, mensagem: str) -> bool:
    """Envia mensagem via Z-API."""
    try:
        zapi_url      = os.getenv('ZAPI_URL', '')
        zapi_token    = os.getenv('ZAPI_TOKEN', '')
        zapi_security = os.getenv('ZAPI_SECURITY_TOKEN', os.getenv('ZAPI_CLIENT_TOKEN', ''))

        if not zapi_url or not zapi_token:
            logger.warning("   Z-API não configurado — pulando WhatsApp")
            return False

        n = numero
        if n.startswith('0'):
            n = n[1:]
        if not n.startswith('55'):
            n = '55' + n

        url = f"{zapi_url}/send-text"
        headers = {'Content-Type': 'application/json'}
        if zapi_security:
            headers['Client-Token'] = zapi_security

        with httpx.Client(timeout=15) as client:
            r = client.post(url, headers=headers, json={
                "phone":   n,
                "message": mensagem,
                "token":   zapi_token,
            })
            if r.status_code == 200:
                logger.info(f"   📱 WhatsApp enviado → {n}")
                return True
            else:
                logger.warning(f"   ⚠️ WhatsApp erro {r.status_code}: {r.text[:100]}")
                return False
    except Exception as e:
        logger.error(f"   ❌ Erro WhatsApp: {e}")
        return False


def enviar_email_renovacao(cond: dict, valor: float, vencimento_licenca: str,
                            nova_validade: str, link_pagamento: str,
                            vencimento_boleto: str, descricao: str) -> bool:
    """Envia email com boleto+PIX de renovação."""
    destinos = _email_destinos(cond)
    if not destinos:
        logger.warning(f"   Sem email para {cond['nome']}")
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
      <td><img src="{EMPRESA_LOGO_URL}" alt="eCondomínio" height="44" style="display:block;" onerror="this.style.display='none'"></td>
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
    <h2 style="color:#d97706;margin:0 0 8px 0;font-size:22px;">🔔 Renovação de Licença</h2>
    <p style="margin:0 0 20px 0;">
      <span style="background:#fffbeb;color:#d97706;border:1px solid #fde68a;
                   padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;">
        ⏰ Vence em {vencimento_licenca}
      </span>
    </p>
    <p style="color:#475569;font-size:15px;line-height:1.7;margin:0 0 24px 0;">
      Prezado(a) gestor(a) do <strong>{nome}</strong>,<br><br>
      Sua licença do sistema eCondomínio vence em breve.
      Segue a cobrança para renovação:
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
        <td style="font-weight:600;color:#1e293b;">{vencimento_boleto}</td>
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
      Link direto: <a href="{link_pagamento}" style="color:#3b82f6;">{link_pagamento}</a>
    </p>
  </td></tr>
  <tr><td style="padding:0 32px;"><hr style="border:none;border-top:1px solid #e2e8f0;margin:0;"></td></tr>
  <tr><td style="background:#f8fafc;padding:20px 32px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="vertical-align:top;">
        <p style="margin:0 0 4px 0;color:#1e3a5f;font-size:12px;font-weight:700;">{EMPRESA_NOME}</p>
        <p style="margin:0;color:#64748b;font-size:11px;line-height:1.7;">
          CNPJ: {EMPRESA_CNPJ}<br>
          Rua Professora Sofia Quint de Souza, 544, Capoeiras<br>
          Florianópolis - SC · CEP 88085-040<br>
          {EMPRESA_FONE} · <a href="mailto:{SMTP_FROM}" style="color:#3b82f6;">{SMTP_FROM}</a>
        </p>
      </td>
      <td style="text-align:right;vertical-align:top;white-space:nowrap;">
        <p style="margin:0;color:#94a3b8;font-size:10px;">Email automático do sistema</p>
      </td>
    </tr></table>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"🔔 Renovação de Licença — {nome}"
        msg['From']    = f"{SMTP_NAME} <{SMTP_FROM}>"
        msg['To']      = ', '.join(destinos)
        msg.attach(MIMEText(html, 'html', 'utf-8'))
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
            srv.ehlo(); srv.starttls(context=ctx); srv.ehlo()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(SMTP_FROM, destinos, msg.as_string())
        logger.info(f"   📧 Email enviado → {destinos}")
        return True
    except Exception as e:
        logger.error(f"   ❌ Erro email: {e}")
        return False


def emitir_nf_antes(id_cobranca):
    """NF-e antes do pagamento (condomínio com nfe_antes_pagamento=1). Nunca derruba a renovação."""
    try:
        import asyncio
        import aiomysql
        from financeiro.financeiro_nfe import emitir_nf, DB_CONFIG

        async def _run():
            nf_conn = await aiomysql.connect(**DB_CONFIG)
            try:
                return await emitir_nf(nf_conn, id_cobranca)
            finally:
                nf_conn.close()
        logger.info(f"   🧾 NF antes do pagamento: {asyncio.run(_run())}")
    except Exception as e:
        logger.error(f"   ❌ NF antes do pagamento falhou (cobrança {id_cobranca}): {e}")


def main():
    logger.info("=== Iniciando geração de cobranças de renovação ===")

    import pymysql

    try:
        conn = pymysql.connect(
            host=os.getenv('DB_HOST', '10.3.1.3'),
            port=int(os.getenv('DB_PORT', 6033)),
            user=os.getenv('DB_USER', 'econdo'),
            password=os.getenv('DB_PASSWORD', ''),
            database=os.getenv('DB_NAME', 'AdmGeral'),
            charset='utf8mb4',
            connect_timeout=10,
        )
    except Exception as e:
        logger.error(f"❌ Erro ao conectar ao banco: {e}")
        return

    cur = conn.cursor()
    hoje = date.today()
    limite = hoje + timedelta(days=DIAS_ANTECEDENCIA)

    # Busca condomínios elegíveis:
    # - ativo = 1
    # - pagante = tem pagamento confirmado (assinatura_status só exclui 'sistema')
    # - validade_ate entre hoje e hoje+DIAS_ANTECEDENCIA
    # - tem pelo menos 1 pagamento confirmado
    # - NÃO tem cobrança pendente em nenhum status (paga ou não) — sem restrição de data
    try:
        cur.execute("""
            SELECT
                c.id, c.nome, c.cnpj, c.email, c.telefone,
                c.cobranca_email, c.cobranca_whats, c.email_financeiro,
                c.validade_ate, c.plano_selecionado, c.total_apartamentos,
                c.asaas_customer_id, c.assinatura_status,
                c.valor_plano_final, c.valor_mensal_base, c.nfe_antes_pagamento
            FROM condominios c
            WHERE c.ativo = 1
              AND c.assinatura_status <> 'sistema'
              AND c.validade_ate IS NOT NULL
              AND c.validade_ate >= %s
              AND c.validade_ate <= %s
              AND EXISTS (
                SELECT 1 FROM cobrancas p
                WHERE p.id_condominio = c.id
                  AND p.status IN ('pago','RECEIVED','CONFIRMED','RECEIVED_IN_CASH')
              )
              AND NOT EXISTS (
                SELECT 1 FROM cobrancas p2
                WHERE p2.id_condominio = c.id
                  AND LOWER(p2.status) IN ('pendente','pending')
              )
            ORDER BY c.validade_ate ASC
        """, (hoje.isoformat(), limite.isoformat()))

        condominios = cur.fetchall()
        logger.info(f"   {len(condominios)} condomínios para renovar")
    except Exception as e:
        logger.error(f"❌ Erro ao buscar condomínios: {e}")
        conn.close()
        return

    if not condominios:
        logger.info("   Nenhum condomínio elegível para renovação hoje.")
        conn.close()
        logger.info("=== Concluído ===")
        return

    ok = 0
    erro = 0
    vencimento_boleto = (hoje + timedelta(days=VENCIMENTO_BOLETO)).isoformat()

    for row in condominios:
        (cond_id, nome, cnpj, email, telefone,
         cobranca_email, cobranca_whats, email_financeiro,
         validade_ate, plano, total_apts,
         asaas_customer_id, assin_status,
         valor_plano_final, valor_mensal_base, nfe_antes_pagamento) = row

        cond = {
            'id': cond_id, 'nome': nome, 'cnpj': cnpj,
            'email': email, 'telefone': telefone,
            'cobranca_email': cobranca_email, 'cobranca_whats': cobranca_whats,
            'email_financeiro': email_financeiro,
            'asaas_customer_id': asaas_customer_id,
            'plano_selecionado': plano,
        }

        dias_restantes = (validade_ate - hoje).days
        logger.info(f"\n→ {nome} | validade: {validade_ate} ({dias_restantes}d restantes)")

        # --- Valor: mesma fórmula do modal do financeiro ---
        # valor_plano_final = valor MENSAL base; multiplica por meses e aplica desconto do plano
        plano_cod  = (plano or 'mensal').lower()
        plano_nome = plano_cod.capitalize()
        descricao  = f"{plano_nome} - {nome}"

        valor_mensal = float(valor_plano_final or 0)
        if valor_mensal <= 0:
            valor_mensal = calcular_valor(total_apts or 0, 'mensal', conn=conn)

        meses, desc_pct = 1, 0.0
        cur.execute("""
            SELECT meses, desconto_pct FROM tabela_precos_planos
            WHERE codigo = %s AND ativo = 1 LIMIT 1
        """, (plano_cod,))
        r = cur.fetchone()
        if r:
            meses, desc_pct = int(r[0]), float(r[1])
        else:
            logger.warning(f"   ⚠️ Plano '{plano_cod}' não encontrado em tabela_precos_planos — tratando como mensal")
        valor = round(valor_mensal * meses * (1 - desc_pct / 100), 2)
        logger.info(f"   Valor: {valor_mensal:.2f} x {meses} meses - {desc_pct:.0f}% = R$ {valor:.2f}")

        # --- Trava: compara com o último pagamento do MESMO plano ---
        cur.execute("""
            SELECT valor FROM cobrancas
            WHERE id_condominio = %s
              AND status IN ('pago','RECEIVED','CONFIRMED','RECEIVED_IN_CASH')
              AND descricao LIKE %s
            ORDER BY data_pagamento DESC LIMIT 1
        """, (cond_id, f"{plano_nome} - %"))
        r = cur.fetchone()
        ultimo_pago = float(r[0]) if r else None
        if ultimo_pago and abs(valor - ultimo_pago) / ultimo_pago * 100 > TOLERANCIA_PCT:
            aviso = (f"⚠️ RENOVAÇÃO NÃO GERADA — valor divergente\n"
                     f"{nome}\nPlano: {plano_nome}\n"
                     f"Calculado: {fmt_moeda(valor)}\nÚltimo pago: {fmt_moeda(ultimo_pago)}\n"
                     f"Validade: {fmt_data(validade_ate)} ({dias_restantes}d)\n"
                     f"Corrija valor_plano_final do condomínio ou gere manualmente.")
            logger.error(f"   ❌ VALOR DIVERGENTE: calculado {valor:.2f} x último pago {ultimo_pago:.2f} — NÃO gerado")
            if not DRY_RUN:
                enviar_whatsapp(ADMIN_WHATSAPP, aviso)
            continue

        # --- Nova validade após pagamento ---
        dias_plano = {'mensal': 30, 'trimestral': 90, 'semestral': 180, 'anual': 365}
        dias_add   = dias_plano.get(plano_cod, 30)
        nova_validade = validade_ate + timedelta(days=dias_add)

        if DRY_RUN:
            logger.info(f"   [DRY-RUN] Geraria BOLETO R$ {valor:.2f} venc {vencimento_boleto} "
                        f"| último pago: {ultimo_pago} | nova validade: {nova_validade}"
                        f"{' + NF antes do pagamento' if nfe_antes_pagamento else ''}")
            continue

        try:
            # 1. Criar cobrança no Asaas
            logger.info(f"   Criando cobrança BOLETO R$ {valor:.2f} venc {vencimento_boleto}...")
            result = criar_cobranca_asaas(cond, valor, vencimento_boleto, descricao)
            asaas_payment_id  = result['asaas_payment_id']
            asaas_customer_id = result['asaas_customer_id']
            link_pagamento    = result['link_pagamento']
            logger.info(f"   ✅ Cobrança criada: {asaas_payment_id} | {link_pagamento}")

            # 2. Salvar no banco
            cur.execute("""
                INSERT INTO cobrancas
                  (id_condominio, valor, status, data_vencimento, forma_pagamento,
                   descricao, asaas_payment_id, asaas_customer_id)
                VALUES (%s, %s, 'pendente', %s, 'boleto', %s, %s, %s)
            """, (cond_id, valor, vencimento_boleto, descricao,
                  asaas_payment_id, asaas_customer_id))

            # Atualiza asaas_customer_id no condomínio se necessário
            if asaas_customer_id and asaas_customer_id != cond.get('asaas_customer_id'):
                cur.execute(
                    "UPDATE condominios SET asaas_customer_id = %s WHERE id = %s",
                    (asaas_customer_id, cond_id)
                )

            conn.commit()
            id_cobranca = cur.lastrowid
            logger.info(f"   💾 Salvo no banco: id_cobranca={id_cobranca}")

            # 2b. NF-e antes do pagamento (flag do condomínio)
            if nfe_antes_pagamento:
                emitir_nf_antes(id_cobranca)

            # 3. Enviar Email
            enviar_email_renovacao(
                cond=cond,
                valor=valor,
                vencimento_licenca=fmt_data(validade_ate),
                nova_validade=fmt_data(nova_validade),
                link_pagamento=link_pagamento,
                vencimento_boleto=fmt_data(vencimento_boleto),
                descricao=descricao,
            )

            # 4. Enviar WhatsApp
            numero = _whatsapp_numero(cond)
            if numero:
                mensagem = (
                    f"🔔 *Renovação de Licença — eCondomínio*\n\n"
                    f"Prezado(a) gestor(a) do *{nome}*,\n\n"
                    f"Sua licença vence em *{fmt_data(validade_ate)}* ({dias_restantes} dias).\n\n"
                    f"📋 *Cobrança de Renovação:*\n"
                    f"• Plano: {plano_nome}\n"
                    f"• Valor: {fmt_moeda(valor)}\n"
                    f"• Vencimento do boleto: {fmt_data(vencimento_boleto)}\n"
                    f"• Nova validade: {fmt_data(nova_validade)}\n\n"
                    f"💳 *Pagar (Boleto ou PIX):*\n{link_pagamento}\n\n"
                    f"Dúvidas: {EMPRESA_FONE} | {SMTP_FROM}"
                )
                enviar_whatsapp(numero, mensagem)
            else:
                logger.warning(f"   Sem WhatsApp para {nome}")

            ok += 1

        except Exception as e:
            logger.error(f"   ❌ Erro ao processar {nome}: {e}")
            conn.rollback()
            erro += 1

    conn.close()
    logger.info(f"\n=== Concluído: {ok} cobranças geradas, {erro} erros ===")


if __name__ == '__main__':
    main()
