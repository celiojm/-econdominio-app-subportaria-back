# ============================================================================
# ALTERAÇÃO 2026-09-27: cancelar-nf/autorizar-nf aceitam token do painel financeiro (usuario_interno)
# ARQUIVO: financeiro_nfe.py
# PASTA: financeiro/
# DESCRICAO: Modulo de NFS-e via Asaas - geracao, consulta e envio de nota fiscal
# VERSAO: 1.2.2 - Portadas rotas autorizar-nf e cancelar-nf do dev2_back (ja
#         testadas la) — front de producao ja tinha os botoes "Emitir agora"/
#         "Cancelar NF" ativos (mesmo build compartilhado), dando 404 real em
#         producao ate esta versao. Guard admin_sistema nos dois; import de
#         get_current_user adicionado (nao existia neste arquivo antes).
# VERSAO: 1.2.1 - http_status 502 -> 400 em emitir_nf/gerar_nf (2 pontos de
#         erro Asaas + fallback do wrapper): confirmado em producao que o
#         Cloudflare intercepta qualquer 502/504 da origem e substitui pela
#         propria pagina generica "error code: 502", escondendo o erro real
#         do usuario. 400 e sempre repassado sem alteracao. Nao e regressao
#         de hoje — o gerar_nf antigo ja usava 502 pra erro de Asaas; so
#         nunca tinha sido exercitado pelo dominio publico real ate o botao
#         "Emitir NF" existir na tela.
# VERSAO: 1.2.0 - Diff MINIMO da Etapa 3 (NFE_FINANCEIRO.md), aprovado
#         explicitamente sem refatoracao maior nem rotas novas (autorizar-nf/
#         cancelar-nf ficam pra depois). gerar_nf virou wrapper fino sobre
#         emitir_nf(conn, id_cobranca) — extracao necessaria so pro gatilho
#         de nfe_antes_pagamento (admin/cobrancas.py::criar_cobranca) poder
#         chamar sem HTTP; emitir_nf nunca levanta excecao. Regra de status
#         agora condicional a condominios.nfe_antes_pagamento (flag 0 mantem
#         "so cobranca paga", identico a v1.1.0; flag 1 permite pendente,
#         continua bloqueando cancelada/estornada/deletada). nf_erro e
#         nf_tentativas agora gravados em QUALQUER falha, inclusive quando a
#         Asaas responde 200 mas com status=ERROR (bug pre-existente: essa
#         combinacao era salva como sucesso — mesma classe do caso Neoville).
#         Confirmado antes de aplicar: nenhum cron em ~/backend chama esta
#         copia do arquivo (sync_pagamentos_e_nf.py usa localhost:5000,
#         backend classico, arquivo separado) — sem risco de duplo-incremento
#         de nf_tentativas nem de quebrar retry de cron.
#         v1.1.0 - Fix: codigo de servico de Florianopolis sem zero a esquerda
#         (1.05.01 -> 01.05.01), causava rejeicao "codigo de tributacao invalido"
#         no Portal Nacional NFS-e.
# data criacao: anterior   data alteracao: 2026-09-07
# ============================================================================
"""
financeiro_nfe.py — Módulo de NFS-e via Asaas
e-Condomínio Sistemas de Gestão LTDA
CNPJ: 64.931.933/0001-85 | IM: 00030555/2026

Todas as configurações são lidas do .env (~/desenvolvimento/.env)
"""

import os
import re
import httpx
import logging
import smtplib
import ssl
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import aiomysql
from dotenv import load_dotenv
from app.api.auth import get_current_user
from app.services.protecao_financeiro import usuario_interno

# Carrega .env — procura na pasta atual e no diretório pai
load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/financeiro", tags=["NFS-e"])

# ─── Configurações via .env ───────────────────────────────────────────────────

ASAAS_API_KEY  = os.getenv("ASAAS_API_KEY")
ASAAS_BASE_URL = os.getenv("ASAAS_BASE_URL", "https://api.asaas.com/v3")
ASAAS_HEADERS  = {
    "accept":       "application/json",
    "content-type": "application/json",
    "access_token": ASAAS_API_KEY,
}

ZAPI_API_URL      = os.getenv("ZAPI_API_URL",      "http://191.252.221.192:8080")
ZAPI_INSTANCE_ID  = os.getenv("ZAPI_INSTANCE_ID")
ZAPI_TOKEN        = os.getenv("ZAPI_TOKEN")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN")

SMTP_HOST = os.getenv("EMAIL_SMTP_SERVER",   "rbr16.dizinc.com")
SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "465"))
SMTP_USER = os.getenv("EMAIL_SMTP_USERNAME", "financeiro@econdominio.com.br")
SMTP_PASS = os.getenv("EMAIL_SMTP_PASSWORD")
SMTP_FROM = os.getenv("EMAIL_FROM_ADDRESS",  "financeiro@econdominio.com.br")
SMTP_NAME = os.getenv("EMAIL_FROM_NAME",     "Financeiro eCondomínio")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "10.3.1.5"),
    "port":     int(os.getenv("DB_PORT", "3306")),
    "user":     os.getenv("DB_USER",     "econdo"),
    "password": os.getenv("DB_PASSWORD", ""),
    "db":       os.getenv("DB_NAME",     "AdmGeral_dev"),
    "charset":  "utf8mb4",
}

# ─── Constantes NFS-e ────────────────────────────────────────────────────────

CODIGOS_SERVICO = {
    # Florianópolis — Portal Nacional NFS-e, COM zero (era "1.05.01" e causava rejeição — 2026-09-06)
    "FLORIANOPOLIS": "01.05.01",
    "FLORIANÓPOLIS": "01.05.01",
    # Todos os demais municípios — portal nacional (com zero)
    "SAO PAULO":        "01.05.01",
    "SÃO PAULO":        "01.05.01",
    "CAMPINAS":         "01.05.01",
    "GUARATINGUETA":    "01.05.01",
    "GUARULHOS":        "01.05.01",
    "JAGUARIUNA":       "01.05.01",
    "JAGUARIÚNA":       "01.05.01",
    "PRAIA GRANDE":     "01.05.01",
    "SANTO ANDRE":      "01.05.01",
    "SANTO ANDRÉ":      "01.05.01",
    "SUMARE":           "01.05.01",
    "SUMARÁ":           "01.05.01",
    "TABOAO DA SERRA":  "01.05.01",
    "TABÃO DA SERRA":   "01.05.01",
    "RIO DE JANEIRO":   "01.05.01",
    "NITEROI":          "01.05.01",
    "NITÉROI":          "01.05.01",
    "RIO VERDE":        "01.05.01",
    "BELO HORIZONTE":   "01.05.01",
    "RIO PARANAIBA":    "01.05.01",
    "RIO PARANAÍBA":    "01.05.01",
    "VARGINHA":         "01.05.01",
    "CAMPO GRANDE":     "01.05.01",
    "CURITIBA":         "01.05.01",
    "LONDRINA":         "01.05.01",
    "SAO JOSE":         "01.05.01",
    "SÃO JOSÉ":         "01.05.01",
    "MACEIO":           "01.05.01",
    "MACEIÓ":           "01.05.01",
    "SALVADOR":         "01.05.01",
    "FORTALEZA":        "01.05.01",
    "BRASILIA":         "01.05.01",
    "BRASÍLIA":         "01.05.01",
    "ITABAIANA":        "01.05.01",
    # Padrão seguro para novos municípios
    "DEFAULT":          "01.05.01",
}

DESCRICAO_SERVICO = (
    "Licenciamento de uso de software na modalidade SaaS (Software as a Service) "
    "– Sistema eCondomínio – mensalidade referente ao período {mes_ref}"
)

ISS_RATE = 2.0  # %


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def get_db():
    conn = await aiomysql.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


async def fetch_cobranca(conn, id_cobranca: int) -> dict:
    async with conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute(
            """
           SELECT c.*,
                   cond.cidade,        cond.estado AS uf,
                   cond.nome           AS nome_fantasia,
                   cond.cnpj           AS cond_cnpj,
                   cond.email          AS cond_email,
                   cond.cobranca_whats AS cond_whatsapp,
                   cond.nfe_antes_pagamento AS cond_nfe_antes_pagamento,
                   cond.asaas_customer_id AS cond_asaas_customer_id,
                   cond.cep AS cond_cep, cond.endereco AS cond_endereco,
                   cond.numero AS cond_numero, cond.complemento AS cond_complemento,
                   cond.bairro AS cond_bairro
            FROM   cobrancas   c
            JOIN   condominios cond ON cond.id = c.id_condominio
            WHERE  c.id_cobranca = %s  
            """,
            (id_cobranca,),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada")
    return row


async def update_cobranca_nf(conn, id_cobranca: int, **kwargs):
    cols = ", ".join(f"{k} = %s" for k in kwargs)
    vals = list(kwargs.values()) + [id_cobranca]
    async with conn.cursor() as cur:
        await cur.execute(
            f"UPDATE cobrancas SET {cols} WHERE id_cobranca = %s", vals
        )
    await conn.commit()


def formatar_mes_ref(venc) -> str:
    if isinstance(venc, (date, datetime)):
        return venc.strftime("%m/%Y")
    return str(venc)[:7] if venc else "referência"


def limpar_telefone(tel: str) -> str:
    for c in (" ", "-", "(", ")", ".", "+"):
        tel = tel.replace(c, "")
    return tel


def resolver_numero_endereco(numero: str, endereco: str) -> str:
    """Numero do condominio costuma vir vazio no cadastro (endereco foi
    digitado tudo junto num campo so, com o numero no final do texto).
    Tenta extrair a sequencia de digitos no fim do endereco antes de cair
    pro fallback 'S/N' — a Asaas exige addressNumber preenchido pra liberar
    emissao de NFS-e, mesmo com os outros campos de endereco completos."""
    numero = (numero or "").strip()
    if numero:
        return numero
    m = re.search(r"(\d+)\s*$", (endereco or "").strip())
    return m.group(1) if m else "S/N"


async def sincronizar_endereco_asaas(cob: dict, id_cobranca: int) -> None:
    """Atualiza o endereco do cliente no Asaas a partir dos dados atuais do
    condominio, best-effort, antes de emitir a NFS-e. Cobre clientes criados
    antes do endereco ser enviado na criacao (fix anterior so vale pra
    clientes novos) e o caso do numero vazio (ver resolver_numero_endereco).
    Nunca bloqueia a emissao da NF — falha aqui so vira warning no log."""
    customer_id = cob.get("cond_asaas_customer_id")
    if not customer_id:
        return
    payload = {
        "postalCode":    (cob.get("cond_cep") or "").replace("-", ""),
        "address":       cob.get("cond_endereco") or "",
        "addressNumber": resolver_numero_endereco(cob.get("cond_numero"), cob.get("cond_endereco")),
        "complement":    cob.get("cond_complemento") or "",
        "province":      cob.get("cond_bairro") or "",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{ASAAS_BASE_URL}/customers/{customer_id}",
                headers=ASAAS_HEADERS,
                json=payload,
            )
        if resp.status_code not in (200, 201):
            logger.warning(
                f"[NFS-e] Falha ao sincronizar endereco do cliente Asaas | "
                f"cobranca={id_cobranca} customer={customer_id}: {resp.text[:300]}"
            )
    except Exception as e:
        logger.warning(
            f"[NFS-e] Erro de conexao ao sincronizar endereco do cliente Asaas | "
            f"cobranca={id_cobranca} customer={customer_id}: {str(e)[:300]}"
        )


# ─── Gerar NFS-e ─────────────────────────────────────────────────────────────

# Status de COBRANÇA que nunca fazem sentido pra emitir NF, independente de
# nfe_antes_pagamento (cobre vocabulario PT-BR e o bruto vindo da Asaas).
STATUS_COBRANCA_BLOQUEIA_NF_SEMPRE = (
    "cancelado", "cancelled", "cancelada",
    "estornado", "estornada", "refunded",
    "deletado", "deletada", "deleted",
)
# Status que indicam pagamento confirmado — regra de hoje pra
# nfe_antes_pagamento=0 (100% da base antes deste deploy).
STATUS_COBRANCA_PAGO = ("received", "confirmed", "received_in_cash", "pago")


async def emitir_nf(conn, id_cobranca: int) -> dict:
    """Diff MINIMO (2026-09-07, Etapa 3 do NFE_FINANCEIRO.md) sobre a funcao
    que ja emite NF pra producao real (68 condominios hoje) — extraida de
    gerar_nf (abaixo, agora um wrapper fino) so pro gatilho de
    nfe_antes_pagamento (admin/cobrancas.py::criar_cobranca) poder chamar sem
    depender de HTTP. NUNCA levanta excecao: retorna sempre dict
    {ok, http_status, ...}. Comportamento HTTP de gerar_nf preservado
    (mesmos codigos 400/409/502/200 de antes). Reemissao sobre nota em
    ERROR/CANCELED, rotas autorizar-nf/cancelar-nf e outras refatoracoes NAO
    entram neste deploy — ficam pra depois, combinado explicitamente.

    Novo aqui: checagem de status agora le condominios.nfe_antes_pagamento
    (flag 0 = regra antiga, so paga; flag 1 = permite pendente, ainda bloqueia
    cancelada/estornada/deletada) e nf_erro/nf_tentativas passam a ser
    gravados em TODA falha (antes, uma nota que a Asaas devolvia como ERROR
    de forma sincrona era salva como se tivesse dado certo — bug real,
    mesma classe do caso Neoville)."""
    try:
        cob = await fetch_cobranca(conn, id_cobranca)
    except HTTPException as exc:
        return {"ok": False, "erro": exc.detail, "http_status": exc.status_code}

    status_bruto        = (cob.get("status") or "").lower()
    nfe_antes_pagamento = bool(cob.get("cond_nfe_antes_pagamento"))
    tentativas_atuais    = cob.get("nf_tentativas") or 0

    if status_bruto in STATUS_COBRANCA_BLOQUEIA_NF_SEMPRE:
        return {
            "ok": False,
            "erro": f"NFS-e não pode ser emitida para cobrança com status '{cob.get('status')}'.",
            "http_status": 400,
        }
    if not nfe_antes_pagamento and status_bruto not in STATUS_COBRANCA_PAGO:
        return {
            "ok": False,
            "erro": f"NFS-e só pode ser gerada para cobranças pagas. Status atual: {cob.get('status')}",
            "http_status": 400,
        }
    if cob.get("asaas_invoice_id"):
        return {
            "ok": False,
            "erro": f"NFS-e já existe. ID: {cob['asaas_invoice_id']} | Status: {cob.get('invoice_status')}",
            "http_status": 409,
        }
    if not cob.get("asaas_payment_id"):
        return {
            "ok": False,
            "erro": "Cobrança sem asaas_payment_id — necessário para emitir NFS-e.",
            "http_status": 400,
        }

    # Sincroniza endereco do cliente no Asaas antes de emitir — cobre clientes
    # criados antes do fix de endereco na criacao, e o caso do numero vazio
    # (ver resolver_numero_endereco). Best-effort: nunca bloqueia a emissao.
    await sincronizar_endereco_asaas(cob, id_cobranca)

    cidade      = (cob.get("cidade") or "").upper().strip()
    cod_servico = CODIGOS_SERVICO.get(cidade, CODIGOS_SERVICO["DEFAULT"])
    mes_ref     = formatar_mes_ref(cob.get("data_vencimento"))

    payload = {
        "payment":              cob["asaas_payment_id"],
        "serviceDescription":   DESCRICAO_SERVICO.format(mes_ref=mes_ref),
        "municipalServiceCode": cod_servico,
        "updatePayment":        False,
        "taxes": {
            "iss": ISS_RATE, "retainIss": False,
            "cofins": 0, "csll": 0, "inss": 0, "ir": 0, "pis": 0,
        },
    }

    logger.info(f"[NFS-e] Gerando | cobranca={id_cobranca} payment={cob['asaas_payment_id']} cod={cod_servico}")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{ASAAS_BASE_URL}/invoices",
                headers=ASAAS_HEADERS,
                json=payload,
            )
    except Exception as e:
        motivo = str(e)[:500]
        logger.error(f"[NFS-e] Erro de conexão com Asaas | cobranca={id_cobranca}: {motivo}")
        await update_cobranca_nf(conn, id_cobranca, nf_erro=motivo, nf_tentativas=tentativas_atuais + 1)
        # http_status 400 (nao 502) de proposito: confirmado em producao (2026-09-07)
        # que o Cloudflare intercepta qualquer 502/504 da origem e substitui pelo
        # proprio "error code: 502" generico, escondendo o detail real do erro do
        # usuario. 400 e sempre repassado sem alteracao.
        return {"ok": False, "erro": f"Erro de conexão com Asaas: {motivo}", "http_status": 400}

    if resp.status_code not in (200, 201):
        motivo = resp.text[:500]
        logger.error(f"[NFS-e] Asaas erro {resp.status_code}: {motivo}")
        await update_cobranca_nf(conn, id_cobranca, nf_erro=motivo, nf_tentativas=tentativas_atuais + 1)
        # Idem — 400 em vez de 502, mesmo motivo (Cloudflare intercepta 502/504).
        return {"ok": False, "erro": f"Erro Asaas: {motivo}", "http_status": 400}

    data           = resp.json()
    invoice_id     = data.get("id")
    invoice_status = data.get("status", "SCHEDULED")
    invoice_pdf    = data.get("pdfUrl") or data.get("pdf") or ""

    if invoice_status == "ERROR":
        motivo = data.get("statusDescription") or "Rejeitada pela Asaas (motivo não informado)."
        await update_cobranca_nf(
            conn, id_cobranca,
            asaas_invoice_id   = invoice_id,
            invoice_status     = invoice_status,
            invoice_pdf_url    = invoice_pdf,
            invoice_created_at = datetime.now(),
            nf_erro            = motivo,
            nf_tentativas      = tentativas_atuais + 1,
        )
        logger.error(f"[NFS-e] ERROR | cobranca={id_cobranca} invoice={invoice_id} motivo={motivo}")
        return {
            "ok": False,
            "asaas_invoice_id": invoice_id,
            "invoice_status":   invoice_status,
            "erro":             motivo,
            "http_status":      200,
        }

    await update_cobranca_nf(
        conn, id_cobranca,
        asaas_invoice_id   = invoice_id,
        invoice_status     = invoice_status,
        invoice_pdf_url    = invoice_pdf,
        invoice_created_at = datetime.now(),
        nf_erro            = None,
    )

    logger.info(f"[NFS-e] OK | invoice={invoice_id} status={invoice_status}")
    return {
        "ok": True,
        "asaas_invoice_id":     invoice_id,
        "invoice_status":       invoice_status,
        "invoice_pdf_url":      invoice_pdf,
        "municipalServiceCode": cod_servico,
        "cidade":               cob.get("cidade"),
        "mes_ref":              mes_ref,
        "http_status":          200,
    }


@router.post("/cobrancas/{id_cobranca}/gerar-nf")
async def gerar_nf(id_cobranca: int, conn=Depends(get_db)):
    """Wrapper HTTP fino sobre emitir_nf — preserva o contrato de status HTTP
    que ja existia (400/409/502/200), agora convertendo o dict que emitir_nf
    devolve em vez de montar a logica aqui dentro."""
    resultado = await emitir_nf(conn, id_cobranca)
    status = resultado.get("http_status", 200 if resultado.get("ok") else 400)
    if status >= 400:
        raise HTTPException(status_code=status, detail=resultado.get("erro"))
    return resultado


# ─── Consultar / sincronizar NFS-e ───────────────────────────────────────────

@router.get("/cobrancas/{id_cobranca}/nf")
async def consultar_nf(id_cobranca: int, conn=Depends(get_db)):
    cob = await fetch_cobranca(conn, id_cobranca)

    if not cob.get("asaas_invoice_id"):
        return {"tem_nf": False, "invoice_status": None}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{ASAAS_BASE_URL}/invoices/{cob['asaas_invoice_id']}",
            headers=ASAAS_HEADERS,
        )

    if resp.status_code == 200:
        data        = resp.json()
        novo_status = data.get("status", cob["invoice_status"])
        novo_pdf    = data.get("pdfUrl") or data.get("pdf") or cob.get("invoice_pdf_url") or ""

        if novo_status != cob.get("invoice_status") or novo_pdf != cob.get("invoice_pdf_url"):
            await update_cobranca_nf(conn, id_cobranca,
                invoice_status=novo_status, invoice_pdf_url=novo_pdf)

        return {
            "tem_nf":             True,
            "asaas_invoice_id":   cob["asaas_invoice_id"],
            "invoice_status":     novo_status,
            "invoice_pdf_url":    novo_pdf,
            "invoice_created_at": str(cob.get("invoice_created_at") or ""),
        }

    return {
        "tem_nf":             True,
        "asaas_invoice_id":   cob["asaas_invoice_id"],
        "invoice_status":     cob.get("invoice_status"),
        "invoice_pdf_url":    cob.get("invoice_pdf_url"),
        "invoice_created_at": str(cob.get("invoice_created_at") or ""),
    }


# ─── Enviar NFS-e (WhatsApp + Email) ─────────────────────────────────────────

class EnviarNFRequest(BaseModel):
    enviar_whatsapp: bool = True
    enviar_email:    bool = True
    telefone:        Optional[str] = None
    email_dest:      Optional[str] = None


@router.post("/cobrancas/{id_cobranca}/enviar-nf")
async def enviar_nf(id_cobranca: int, req: EnviarNFRequest, conn=Depends(get_db)):
    cob = await fetch_cobranca(conn, id_cobranca)

    if not cob.get("asaas_invoice_id"):
        raise HTTPException(status_code=400, detail="NFS-e ainda não gerada")
    if not cob.get("invoice_pdf_url"):
        raise HTTPException(status_code=400, detail="PDF ainda não disponível — aguarde autorização")

    resultados = {}
    nome_cond  = cob.get("nome_fantasia") or "Condomínio"
    pdf_url    = cob["invoice_pdf_url"]
    mes_ref    = formatar_mes_ref(cob.get("data_vencimento"))
    valor_fmt  = f"R$ {float(cob['valor']):.2f}".replace(".", ",")

    # ── WhatsApp ──────────────────────────────────────────────────────────────
    if req.enviar_whatsapp:
        tel = limpar_telefone(req.telefone or cob.get("cond_whatsapp") or "")
        if not tel:
            resultados["whatsapp"] = {"ok": False, "erro": "Telefone não informado"}
        else:
            msg_wa = (
                f"*{nome_cond}* — Nota Fiscal de Serviço\n\n"
                f"📄 NFS-e referente à mensalidade *{mes_ref}* — {valor_fmt}\n"
                f"🔗 PDF: {pdf_url}\n\n"
                f"_eCondomínio Sistemas · {SMTP_FROM}_"
            )
            url = f"{ZAPI_API_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.post(
                        url,
                        json={"phone": tel, "message": msg_wa},
                        headers={"Client-Token": ZAPI_CLIENT_TOKEN},
                    )
                ok = r.status_code in (200, 201)
                resultados["whatsapp"] = {"ok": ok, "status": r.status_code, "telefone": tel}
                logger.info(f"[NFS-e] WA {'OK' if ok else 'ERRO'} → {tel}")
            except Exception as e:
                logger.error(f"[NFS-e] WA exceção: {e}")
                resultados["whatsapp"] = {"ok": False, "erro": str(e)}

    # ── Email ─────────────────────────────────────────────────────────────────
    if req.enviar_email:
        dest = req.email_dest or cob.get("cond_email") or ""
        if not dest:
            resultados["email"] = {"ok": False, "erro": "Email não informado"}
        else:
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = f"NFS-e eCondomínio — {mes_ref} — {nome_cond}"
                msg["From"]    = f"{SMTP_NAME} <{SMTP_FROM}>"
                msg["To"]      = dest

                html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td align="center" style="padding:32px 16px">
<table width="560" cellpadding="0" cellspacing="0"
       style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
  <tr><td style="background:#1a3a5c;padding:24px 32px">
    <h1 style="margin:0;color:#fff;font-size:20px">🧾 Nota Fiscal de Serviço</h1>
    <p style="margin:4px 0 0;color:#a8c4e0;font-size:13px">eCondomínio Sistemas</p>
  </td></tr>
  <tr><td style="padding:28px 32px;color:#333">
    <p style="margin:0 0 16px">Prezado(a) gestor(a) do <strong>{nome_cond}</strong>,</p>
    <p style="margin:0 0 16px">
      Sua NFS-e referente à mensalidade de <strong>{mes_ref}</strong>
      no valor de <strong>{valor_fmt}</strong> foi emitida e está disponível.
    </p>
    <div style="text-align:center;margin:28px 0">
      <a href="{pdf_url}"
         style="background:#1a3a5c;color:#fff;padding:12px 28px;text-decoration:none;
                border-radius:6px;font-size:14px;font-weight:600;display:inline-block">
        📄 Baixar NFS-e (PDF)
      </a>
    </div>
    <p style="margin:0;font-size:11px;color:#999">Link direto: <a href="{pdf_url}">{pdf_url}</a></p>
  </td></tr>
  <tr><td style="background:#f4f6f9;padding:14px 32px;border-top:1px solid #e5e7eb;
                 color:#aaa;font-size:11px">
    eCondomínio Sistemas de Gestão · {SMTP_FROM}
  </td></tr>
</table></td></tr></table>
</body></html>"""

                msg.attach(MIMEText(html, "html", "utf-8"))
                ctx = ssl.create_default_context()
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
                    srv.ehlo()
                    srv.starttls()
                    srv.ehlo()
                    srv.login(SMTP_USER, SMTP_PASS)
                    srv.sendmail(SMTP_FROM, dest, msg.as_string())

                resultados["email"] = {"ok": True, "dest": dest}
                logger.info(f"[NFS-e] Email OK → {dest}")
            except Exception as e:
                logger.error(f"[NFS-e] Email exceção: {e}")
                resultados["email"] = {"ok": False, "erro": str(e)}

    return {"ok": True, "resultados": resultados}


# ─── Cancelar NFS-e ───────────────────────────────────────────────────────────

@router.post("/cobrancas/{id_cobranca}/cancelar-nf")
async def cancelar_nf(id_cobranca: int, conn=Depends(get_db), current_user: dict = Depends(usuario_interno)):
    """Rota nova (2026-09-07, portada do dev2_back apos testada la) — nao
    existia rota propria de cancelamento ate agora, so o uso interno em
    emitir_nf (reemissao sobre ERROR). Permite cancelar uma NFS-e via API sem
    depender do painel da Asaas.

    Idempotente: se a nota ja estiver CANCELED, retorna ok sem chamar a Asaas
    de novo. Nao mexe em nf_erro/nf_tentativas — cancelamento nao e uma
    falha de emissao.

    Guard de role: so admin_sistema pode cancelar NF."""
    if current_user.get("role") != "admin_sistema":
        raise HTTPException(status_code=403, detail="Apenas admin_sistema pode cancelar NFS-e")

    cob = await fetch_cobranca(conn, id_cobranca)

    invoice_id = cob.get("asaas_invoice_id")
    if not invoice_id:
        raise HTTPException(status_code=400, detail="Cobrança não tem NFS-e emitida para cancelar")

    if cob.get("invoice_status") == "CANCELED":
        return {"ok": True, "asaas_invoice_id": invoice_id, "invoice_status": "CANCELED", "ja_estava_cancelada": True}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{ASAAS_BASE_URL}/invoices/{invoice_id}/cancel",
            headers=ASAAS_HEADERS,
        )

    if resp.status_code not in (200, 201):
        motivo = resp.text[:500]
        logger.error(f"[NFS-e] Falha ao cancelar {invoice_id} | cobranca={id_cobranca}: {motivo}")
        # 400, nao 502 — Cloudflare intercepta 502/504 da origem e substitui
        # pela propria pagina generica (confirmado em producao 2026-09-07).
        raise HTTPException(status_code=400, detail=f"Erro Asaas ao cancelar: {motivo}")

    await update_cobranca_nf(conn, id_cobranca, invoice_status="CANCELED")
    logger.info(f"[NFS-e] Cancelada {invoice_id} | cobranca={id_cobranca}")

    return {"ok": True, "asaas_invoice_id": invoice_id, "invoice_status": "CANCELED"}


# ─── Autorizar (antecipar) NFS-e agendada ────────────────────────────────────

@router.post("/cobrancas/{id_cobranca}/autorizar-nf")
async def autorizar_nf(id_cobranca: int, conn=Depends(get_db), current_user: dict = Depends(usuario_interno)):
    """Rota nova (2026-09-07, portada do dev2_back apos testada la) —
    POST /v3/invoices/{id}/authorize, confirmado na doc oficial da Asaas. Nao
    cria nota nova: antecipa a emissao de uma nota ja existente com
    effectiveDate futura/hoje, em vez de esperar o processamento assincrono
    normal da Asaas (que pode levar minutos). So faz sentido sobre nota em
    SCHEDULED; sobre qualquer outro status, rejeita.

    A resposta da Asaas pra esse endpoint normalmente ainda nao vem
    AUTHORIZED — vira SYNCHRONIZED (processando) e so fecha em AUTHORIZED/
    ERROR depois, de forma assincrona (webhook ou proxima consulta via
    consultar_nf). Por isso so atualizamos invoice_status com o que a Asaas
    devolveu na hora, sem tratar como emissao concluida.

    Guard de role: so admin_sistema."""
    if current_user.get("role") != "admin_sistema":
        raise HTTPException(status_code=403, detail="Apenas admin_sistema pode autorizar NFS-e")

    cob = await fetch_cobranca(conn, id_cobranca)

    invoice_id = cob.get("asaas_invoice_id")
    if not invoice_id:
        raise HTTPException(status_code=400, detail="Cobrança não tem NFS-e emitida para autorizar")
    if cob.get("invoice_status") != "SCHEDULED":
        raise HTTPException(
            status_code=400,
            detail=f"Só é possível antecipar emissão de nota em SCHEDULED. Status atual: {cob.get('invoice_status')}",
        )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{ASAAS_BASE_URL}/invoices/{invoice_id}/authorize",
            headers=ASAAS_HEADERS,
        )

    if resp.status_code not in (200, 201):
        motivo = resp.text[:500]
        logger.error(f"[NFS-e] Falha ao autorizar {invoice_id} | cobranca={id_cobranca}: {motivo}")
        # 400, nao 502 — mesmo motivo (Cloudflare intercepta 502/504).
        raise HTTPException(status_code=400, detail=f"Erro Asaas ao autorizar: {motivo}")

    data = resp.json()
    novo_status = data.get("status", "SYNCHRONIZED")
    await update_cobranca_nf(conn, id_cobranca, invoice_status=novo_status)
    logger.info(f"[NFS-e] Autorização antecipada solicitada {invoice_id} | cobranca={id_cobranca} | status={novo_status}")

    return {"ok": True, "asaas_invoice_id": invoice_id, "invoice_status": novo_status}
