#!/usr/bin/env python3
# ============================================================================
# ARQUIVO: sync_nf_aprovadas.py
# PASTA: /home/visionlpr/app_subportaria_back/
# DESCRIÇÃO: Envia email unificado ao condomínio com:
#            - Confirmação de pagamento + nova validade
#            - Link da NF se já aprovada, ou aviso de que vem em breve
#            - Segundo email quando NF for aprovada (nos dias seguintes)
# VERSÃO: 1.1.0
# CRON: 30 8 * * * flock -n /tmp/sync_nf_aprov.lock /home/visionlpr/app_subportaria_back/.env.worker/bin/python3 /home/visionlpr/app_subportaria_back/sync_nf_aprovadas.py >> /home/visionlpr/app_subportaria_back/sync_nf_aprovadas.log 2>&1
# ============================================================================
import os
import sys
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date
import httpx

os.chdir('/home/visionlpr/app_subportaria_back')
sys.path.insert(0, '/home/visionlpr/app_subportaria_back')

from dotenv import load_dotenv
load_dotenv('/home/visionlpr/app_subportaria_back/.env.worker')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('/home/visionlpr/app_subportaria_back/sync_nf_aprovadas.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

ASAAS_API_KEY = os.getenv('ASAAS_API_KEY', '')
ASAAS_BASE    = os.getenv('ASAAS_BASE_URL', 'https://api.asaas.com/v3')
SMTP_HOST     = os.getenv('EMAIL_SMTP_SERVER', 'smtp-relay.brevo.com')
SMTP_PORT     = int(os.getenv('EMAIL_SMTP_PORT', 587))
SMTP_USER     = os.getenv('EMAIL_SMTP_USERNAME', '')
SMTP_PASS     = os.getenv('EMAIL_SMTP_PASSWORD', '')
SMTP_FROM     = os.getenv('EMAIL_FROM_ADDRESS', 'financeiro@econdominio.com.br')
SMTP_NAME     = os.getenv('EMAIL_FROM_NAME', 'Financeiro eCondomínio')


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


def _enviar(destinos: list, assunto: str, html: str):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = assunto
    msg['From']    = f"{SMTP_NAME} <{SMTP_FROM}>"
    msg['To']      = ', '.join(destinos)
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
        srv.ehlo(); srv.starttls(context=ctx); srv.ehlo()
        srv.login(SMTP_USER, SMTP_PASS)
        srv.sendmail(SMTP_FROM, destinos, msg.as_string())


def email_pagamento_com_nf(cond, cob, pdf_url):
    """Email completo: pagamento confirmado + NF disponível."""
    nome      = cond['nome']
    valor     = fmt_moeda(cob['valor_pago'] or cob['valor'])
    data_pag  = fmt_data(cob['data_pagamento'])
    validade  = fmt_data(cob['validade_ate'])
    descricao = cob['descricao'] or 'Mensalidade eCondomínio'
    forma     = {'PIX':'⚡ PIX','BOLETO':'📄 Boleto Bancário','CARTAO':'💳 Cartão'}.get(
                 (cob['forma_pagamento'] or '').upper(), cob['forma_pagamento'] or '—')

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
  <tr><td style="background:#1a3a5c;padding:24px 32px">
    <p style="margin:0;color:#fff;font-size:20px;font-weight:700">✅ Pagamento Confirmado</p>
    <p style="margin:4px 0 0;color:#a8c4e0;font-size:13px">eCondomínio Sistemas</p>
  </td></tr>
  <tr><td style="padding:28px 32px;color:#333">
    <p style="margin:0 0 16px">Prezado(a) gestor(a) do <strong>{nome}</strong>,</p>
    <p style="margin:0 0 20px">Seu pagamento foi confirmado e sua assinatura está ativa.</p>
    <table width="100%" cellpadding="8" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:8px;font-size:14px;margin-bottom:24px">
      <tr style="background:#f9fafb">
        <td style="color:#6b7280;border-bottom:1px solid #e5e7eb;width:45%">Descrição</td>
        <td style="font-weight:600;border-bottom:1px solid #e5e7eb">{descricao}</td>
      </tr>
      <tr>
        <td style="color:#6b7280;border-bottom:1px solid #e5e7eb">Valor pago</td>
        <td style="font-weight:700;color:#059669;border-bottom:1px solid #e5e7eb">{valor}</td>
      </tr>
      <tr style="background:#f9fafb">
        <td style="color:#6b7280;border-bottom:1px solid #e5e7eb">Forma</td>
        <td style="border-bottom:1px solid #e5e7eb">{forma}</td>
      </tr>
      <tr>
        <td style="color:#6b7280;border-bottom:1px solid #e5e7eb">Data do pagamento</td>
        <td style="border-bottom:1px solid #e5e7eb">{data_pag}</td>
      </tr>
      <tr style="background:#f9fafb">
        <td style="color:#6b7280">Validade da assinatura</td>
        <td style="font-weight:700;color:#1a3a5c">{validade}</td>
      </tr>
    </table>
    <p style="margin:0 0 16px;font-weight:600;color:#1a3a5c">📄 Nota Fiscal de Serviço</p>
    <p style="margin:0 0 20px;color:#555;font-size:14px">
      Sua NFS-e foi emitida e está disponível para download:
    </p>
    <div style="text-align:center;margin:0 0 24px">
      <a href="{pdf_url}" style="background:#1a3a5c;color:#fff;padding:12px 28px;
         text-decoration:none;border-radius:6px;font-size:14px;font-weight:600;display:inline-block">
        📄 Baixar NFS-e (PDF)
      </a>
    </div>
    <p style="margin:0;font-size:11px;color:#999">Link: <a href="{pdf_url}" style="color:#2563eb">{pdf_url}</a></p>
  </td></tr>
  <tr><td style="background:#f4f6f9;padding:14px 32px;border-top:1px solid #e5e7eb;color:#aaa;font-size:11px">
    eCondomínio Sistemas de Gestão · {SMTP_FROM}
  </td></tr>
</table></td></tr></table></body></html>"""

    destinos = _email_destinos(cond)
    if not destinos:
        return False
    _enviar(destinos, f"✅ Pagamento confirmado — {nome}", html)
    logger.info(f"   📧 Email pagamento+NF → {destinos}")
    return True


def email_pagamento_sem_nf(cond, cob):
    """Email de confirmação de pagamento — NF ainda pendente."""
    nome      = cond['nome']
    valor     = fmt_moeda(cob['valor_pago'] or cob['valor'])
    data_pag  = fmt_data(cob['data_pagamento'])
    validade  = fmt_data(cob['validade_ate'])
    descricao = cob['descricao'] or 'Mensalidade eCondomínio'
    forma     = {'PIX':'⚡ PIX','BOLETO':'📄 Boleto Bancário','CARTAO':'💳 Cartão'}.get(
                 (cob['forma_pagamento'] or '').upper(), cob['forma_pagamento'] or '—')

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
  <tr><td style="background:#1a3a5c;padding:24px 32px">
    <p style="margin:0;color:#fff;font-size:20px;font-weight:700">✅ Pagamento Confirmado</p>
    <p style="margin:4px 0 0;color:#a8c4e0;font-size:13px">eCondomínio Sistemas</p>
  </td></tr>
  <tr><td style="padding:28px 32px;color:#333">
    <p style="margin:0 0 16px">Prezado(a) gestor(a) do <strong>{nome}</strong>,</p>
    <p style="margin:0 0 20px">Seu pagamento foi confirmado e sua assinatura está ativa.</p>
    <table width="100%" cellpadding="8" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:8px;font-size:14px;margin-bottom:24px">
      <tr style="background:#f9fafb">
        <td style="color:#6b7280;border-bottom:1px solid #e5e7eb;width:45%">Descrição</td>
        <td style="font-weight:600;border-bottom:1px solid #e5e7eb">{descricao}</td>
      </tr>
      <tr>
        <td style="color:#6b7280;border-bottom:1px solid #e5e7eb">Valor pago</td>
        <td style="font-weight:700;color:#059669;border-bottom:1px solid #e5e7eb">{valor}</td>
      </tr>
      <tr style="background:#f9fafb">
        <td style="color:#6b7280;border-bottom:1px solid #e5e7eb">Forma</td>
        <td style="border-bottom:1px solid #e5e7eb">{forma}</td>
      </tr>
      <tr>
        <td style="color:#6b7280;border-bottom:1px solid #e5e7eb">Data do pagamento</td>
        <td style="border-bottom:1px solid #e5e7eb">{data_pag}</td>
      </tr>
      <tr style="background:#f9fafb">
        <td style="color:#6b7280">Validade da assinatura</td>
        <td style="font-weight:700;color:#1a3a5c">{validade}</td>
      </tr>
    </table>
    <div style="background:#fefce8;border:1px solid #fde68a;border-radius:8px;padding:16px;font-size:13px;color:#92400e">
      📄 <strong>Nota Fiscal:</strong> sua NFS-e está sendo processada pela prefeitura
      e será enviada por email assim que aprovada.
    </div>
  </td></tr>
  <tr><td style="background:#f4f6f9;padding:14px 32px;border-top:1px solid #e5e7eb;color:#aaa;font-size:11px">
    eCondomínio Sistemas de Gestão · {SMTP_FROM}
  </td></tr>
</table></td></tr></table></body></html>"""

    destinos = _email_destinos(cond)
    if not destinos:
        return False
    _enviar(destinos, f"✅ Pagamento confirmado — {nome}", html)
    logger.info(f"   📧 Email pagamento (sem NF) → {destinos}")
    return True


def email_so_nf(cond, cob, pdf_url):
    """Email apenas com a NF — pagamento já foi confirmado antes."""
    nome      = cond['nome']
    valor     = fmt_moeda(cob['valor'])
    descricao = cob['descricao'] or 'Mensalidade eCondomínio'

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
  <tr><td style="background:#1a3a5c;padding:24px 32px">
    <p style="margin:0;color:#fff;font-size:20px;font-weight:700">📄 Nota Fiscal Disponível</p>
    <p style="margin:4px 0 0;color:#a8c4e0;font-size:13px">eCondomínio Sistemas</p>
  </td></tr>
  <tr><td style="padding:28px 32px;color:#333">
    <p style="margin:0 0 16px">Prezado(a) gestor(a) do <strong>{nome}</strong>,</p>
    <p style="margin:0 0 16px">
      Sua NFS-e referente a <strong>{descricao}</strong>
      no valor de <strong>{valor}</strong> foi aprovada e está disponível:
    </p>
    <div style="text-align:center;margin:28px 0">
      <a href="{pdf_url}" style="background:#1a3a5c;color:#fff;padding:12px 28px;
         text-decoration:none;border-radius:6px;font-size:14px;font-weight:600;display:inline-block">
        📄 Baixar NFS-e (PDF)
      </a>
    </div>
    <p style="margin:0;font-size:11px;color:#999">Link: <a href="{pdf_url}" style="color:#2563eb">{pdf_url}</a></p>
  </td></tr>
  <tr><td style="background:#f4f6f9;padding:14px 32px;border-top:1px solid #e5e7eb;color:#aaa;font-size:11px">
    eCondomínio Sistemas de Gestão · {SMTP_FROM}
  </td></tr>
</table></td></tr></table></body></html>"""

    destinos = _email_destinos(cond)
    if not destinos:
        return False
    _enviar(destinos, f"📄 Nota Fiscal disponível — {nome}", html)
    logger.info(f"   📧 Email só NF → {destinos}")
    return True


def main():
    logger.info("=== Iniciando sync NFs aprovadas + emails ===")

    import pymysql

    try:
        conn = pymysql.connect(
            host=os.getenv('DB_HOST', '10.3.1.3'),
            port=int(os.getenv('DB_PORT', 6033)),
            user=os.getenv('DB_USER', 'econdo'),
            password=os.getenv('DB_PASSWORD', ''),
            database='AdmGeral',
            charset='utf8mb4',
            connect_timeout=10,
        )
        conn.cursor().execute('USE AdmGeral')
    except Exception as e:
        logger.error(f"❌ Erro ao conectar ao banco: {e}")
        return

    cur = conn.cursor()
    headers = {'access_token': ASAAS_API_KEY}

    # ── PASSO 1: Pagamentos recentes sem email de confirmação ──
    # Inclui cobranças pagas nos últimos 2 dias ainda não notificadas
    logger.info("1. Buscando pagamentos recentes sem email de confirmação...")
    try:
        cur.execute("""
            SELECT c.id_cobranca, c.valor, c.valor_pago, c.data_pagamento,
                   c.forma_pagamento, c.descricao, c.asaas_payment_id,
                   c.asaas_invoice_id, c.invoice_status, c.invoice_pdf_url,
                   COALESCE(a.validade_ate, cond.validade_ate) as validade_ate,
                   cond.nome, cond.email, cond.email_financeiro, cond.cobranca_email
            FROM cobrancas c
            JOIN condominios cond ON cond.id = c.id_condominio
            LEFT JOIN assinaturas a ON a.id_condominio = c.id_condominio
            WHERE c.status IN ('pago','RECEIVED','CONFIRMED','RECEIVED_IN_CASH')
              AND c.data_pagamento >= CURDATE() - INTERVAL 2 DAY
              AND (c.email_confirmacao_enviado IS NULL OR c.email_confirmacao_enviado = 0)
            ORDER BY c.data_pagamento DESC
            LIMIT 30
        """)
        pagamentos = cur.fetchall()
        logger.info(f"   {len(pagamentos)} pagamentos para notificar")
    except Exception as e:
        logger.error(f"❌ Erro ao buscar pagamentos: {e}")
        conn.close()
        return

    with httpx.Client(timeout=30) as client:
        for row in pagamentos:
            (id_cob, valor, valor_pago, data_pag, forma, descricao, payment_id,
             inv_id, inv_status, pdf_url_banco, validade,
             nome, email, email_fin, email_cob) = row

            cond = {'nome': nome, 'email': email,
                    'email_financeiro': email_fin, 'cobranca_email': email_cob}
            cob  = {'valor': valor, 'valor_pago': valor_pago,
                    'data_pagamento': data_pag, 'forma_pagamento': forma,
                    'descricao': descricao, 'validade_ate': validade}

            pdf_url    = pdf_url_banco
            nf_status  = inv_status

            # Verifica status atual da NF no Asaas se houver invoice_id
            if inv_id:
                try:
                    r = client.get(f"{ASAAS_BASE}/invoices/{inv_id}", headers=headers)
                    if r.status_code == 200:
                        d = r.json()
                        nf_status = d.get('status', inv_status)
                        pdf_url   = d.get('pdfUrl') or pdf_url_banco
                        # Atualiza no banco
                        cur.execute(
                            "UPDATE cobrancas SET invoice_status=%s, invoice_pdf_url=%s WHERE id_cobranca=%s",
                            (nf_status, pdf_url, id_cob)
                        )
                        conn.commit()
                except Exception as e:
                    logger.warning(f"   ⚠️ Erro ao consultar NF #{id_cob}: {e}")

            try:
                if nf_status == 'AUTHORIZED' and pdf_url:
                    # NF aprovada — email completo
                    ok = email_pagamento_com_nf(cond, cob, pdf_url)
                    if ok:
                        cur.execute(
                            "UPDATE cobrancas SET email_confirmacao_enviado=1, nf_email_enviado=1 WHERE id_cobranca=%s",
                            (id_cob,)
                        )
                else:
                    # NF pendente — email só de pagamento
                    ok = email_pagamento_sem_nf(cond, cob)
                    if ok:
                        cur.execute(
                            "UPDATE cobrancas SET email_confirmacao_enviado=1 WHERE id_cobranca=%s",
                            (id_cob,)
                        )
                conn.commit()
            except Exception as e:
                logger.error(f"   ❌ Erro ao enviar email #{id_cob}: {e}")

    # ── PASSO 2: NFs aprovadas cujo email ainda não foi enviado ──
    logger.info("2. Buscando NFs aprovadas sem email de NF enviado...")
    try:
        cur.execute("""
            SELECT c.id_cobranca, c.valor, c.descricao,
                   c.asaas_invoice_id, c.invoice_status, c.invoice_pdf_url,
                   cond.nome, cond.email, cond.email_financeiro, cond.cobranca_email
            FROM cobrancas c
            JOIN condominios cond ON cond.id = c.id_condominio
            WHERE c.asaas_invoice_id IS NOT NULL
              AND (c.nf_email_enviado IS NULL OR c.nf_email_enviado = 0)
              AND c.email_confirmacao_enviado = 1
            ORDER BY c.data_pagamento DESC
            LIMIT 30
        """)
        nfs = cur.fetchall()
        logger.info(f"   {len(nfs)} NFs para verificar")
    except Exception as e:
        logger.error(f"❌ Erro ao buscar NFs: {e}")
        conn.close()
        return

    with httpx.Client(timeout=30) as client:
        for row in nfs:
            (id_cob, valor, descricao, inv_id, inv_status,
             pdf_url_banco, nome, email, email_fin, email_cob) = row

            cond = {'nome': nome, 'email': email,
                    'email_financeiro': email_fin, 'cobranca_email': email_cob}
            cob  = {'valor': valor, 'descricao': descricao}

            try:
                # Verifica status atual no Asaas
                r = client.get(f"{ASAAS_BASE}/invoices/{inv_id}", headers=headers)
                if r.status_code != 200:
                    continue
                d         = r.json()
                nf_status = d.get('status')
                pdf_url   = d.get('pdfUrl') or pdf_url_banco

                # Atualiza status
                cur.execute(
                    "UPDATE cobrancas SET invoice_status=%s, invoice_pdf_url=%s WHERE id_cobranca=%s",
                    (nf_status, pdf_url, id_cob)
                )
                conn.commit()

                if nf_status != 'AUTHORIZED' or not pdf_url:
                    logger.info(f"   ⏳ #{id_cob} status={nf_status} — aguardando aprovação")
                    continue

                ok = email_so_nf(cond, cob, pdf_url)
                if ok:
                    cur.execute(
                        "UPDATE cobrancas SET nf_email_enviado=1 WHERE id_cobranca=%s",
                        (id_cob,)
                    )
                    conn.commit()

            except Exception as e:
                logger.error(f"   ❌ Erro #{id_cob}: {e}")

    conn.close()
    logger.info("=== Sync NFs concluído ===")


if __name__ == '__main__':
    main()
