# ============================================================================
# ARQUIVO: financeiro_contratos_assinatura.py
# PASTA: /home/visionlpr/app_subportaria_back/financeiro/
# DESCRIÇÃO: Assinatura de contratos — FASE 1 (docs/DIAGNOSTICO_ASSINATURA_ECONDOMINIO.md, ~/dev2).
#            - Congelar o contrato: baixa o PDF gerado do storage, grava o SHA-256 (hash_rascunho), o id público
#              aleatório (uuid_publico) e muda o status para 'pronto'. Depois de congelado o PDF não muda mais
#              (o síndico aceita exatamente esse arquivo; a assinatura A1 da empresa é a última etapa).
#            - Trilha de eventos encadeada (contratos_eventos, só INSERT): cada evento guarda o hash do anterior;
#              verificar_cadeia() recalcula tudo e aponta o primeiro evento adulterado.
#            - Hashes de evento e de CPF são HMAC-SHA256 com CHAVE SECRETA FORA DO BANCO (sem ela não dá para refazer a
#              cadeia nem descobrir o CPF por força bruta). Chave: variável CONTRATOS_ASSINATURA_CHAVE ou arquivo
#              ~/.contratos_assinatura.key (600). Sem chave o módulo recusa operar. Trocar a chave invalida a verificação
#              das cadeias antigas — guardar com backup separado.
#            Rotas (equipe interna — master ou colaborador):
#              POST /api/financeiro/contratos-licenca/{id}/preparar   → congela (idempotente)
#              GET  /api/financeiro/contratos-licenca/{id}/assinatura → status, assinantes, eventos e verificação
# VERSÃO: 1.5.1 - WhatsApp do CONVITE também em segundo plano (a tela do financeiro desistia aos 30 s e mostrava erro
#                 mesmo com o link enviado) (2026-10-10)
#         1.5.0 - modo "empresa assina ANTES": no envio o contrato é assinado com o certificado (autorizado por quem
#                 envia); após o aceite as evidências viram um PDF separado, também assinado, e conclui sozinho
#         1.4.0 - WhatsApp lento: código por WhatsApp enviado em segundo plano (resposta na hora), timeout vira
#                 "não confirmado" (não FALHOU); confirmar aceita QUALQUER código válido do convite (o atrasado também)
#         1.3.0 - FASES 3/4: POST /{id}/assinar-empresa (master/colaborador autoriza → serviço ASSINADOR assina com o
#                 certificado e valida), página de evidências, PDF final + evidências no storage, downloads e
#                 verificação pública /api/assinatura/verificar/{uuid}
#         1.2.0 - FASE 2: convite (link 7 dias), portal público /api/assinatura/{token}, código por e-mail/WhatsApp,
#                 aceite e recusa; envio real só com CONTRATOS_ASSINATURA_ENVIO_REAL=true (senão modo teste/log;
#                 no teste, só os destinos de CONTRATOS_ASSINATURA_TESTE_DESTINOS recebem de verdade)
#         1.1.0 - HMAC com chave secreta fora do banco nos eventos e no CPF (achado da revisão de segurança)
#         1.0.0 - criação (fase 1)
# data criação: 2026-10-08 data alteração: 2026-10-08
# ============================================================================
import hashlib
import hmac
import io
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.protecao_financeiro import usuario_interno
from financeiro.financeiro_contratos_licenca import STORAGE_API_KEY, STORAGE_BASE_URL

logger = logging.getLogger(__name__)
router = APIRouter(tags=["contratos-assinatura"])

VERSAO_MODELO = "1.2.0"   # versão do texto do contrato (financeiro_contratos_licenca)
ESTADOS = ("gerado", "pronto", "convidado", "visualizado", "aguardando_codigo", "aceito", "assinando",
           "concluido", "recusado", "expirado", "cancelado", "falha")


# ─── utilidades ─────────────────────────────────────────────────────────────
def sha256(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


_CHAVE: Optional[bytes] = None


def _chave() -> bytes:
    """Chave secreta do HMAC (fora do banco). Sem chave, nada é registrado (falha alta, não silenciosa)."""
    global _CHAVE
    if _CHAVE is None:
        v = os.getenv("CONTRATOS_ASSINATURA_CHAVE", "").strip()
        if not v:
            arq = os.path.expanduser(os.getenv("CONTRATOS_ASSINATURA_CHAVE_ARQUIVO", "~/.contratos_assinatura.key"))
            try:
                v = open(arq).read().strip()
            except OSError:
                v = ""
        if len(v) < 32:
            raise HTTPException(status_code=503, detail="Assinatura de contratos não configurada (chave ausente)")
        _CHAVE = v.encode()
    return _CHAVE


def hmac_hex(dados: bytes) -> str:
    return hmac.new(_chave(), dados, hashlib.sha256).hexdigest()


def hash_cpf(cpf: str) -> str:
    """HMAC do CPF (só dígitos) — confere o CPF sem guardar o número nem permitir força bruta."""
    return hmac_hex(re.sub(r"\D", "", cpf or "").encode())


def mascarar_cpf(cpf: str) -> str:
    d = re.sub(r"\D", "", cpf or "")
    return f"***.{d[3:6]}.{d[6:9]}-**" if len(d) == 11 else "***"


def ip_cliente(request: Optional[Request]) -> Optional[str]:
    """IP real atrás do Cloudflare/NGINX (mesma regra do middleware de auditoria)."""
    if request is None:
        return None
    h = request.headers
    ip = h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
    return (ip or (request.client.host if request.client else None) or None)


def baixar_documento(arquivo: str) -> bytes:
    try:
        resp = httpx.get(f"{STORAGE_BASE_URL}/storage/documento/{arquivo}", headers={"x-api-key": STORAGE_API_KEY}, timeout=30)
    except Exception:
        raise HTTPException(status_code=424, detail="Storage indisponível no momento")
    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="PDF não encontrado no storage")
    return resp.content


# ─── trilha de eventos encadeada ────────────────────────────────────────────
def _conteudo_evento(contrato_id, tipo, ator_tipo, ator_id, ator_nome, momento, ip, ua, dados, hash_doc, anterior) -> str:
    """Texto canônico do evento (ordem fixa, JSON com chaves ordenadas) — base do hash."""
    return json.dumps({
        "contrato_id": contrato_id, "tipo": tipo, "ator_tipo": ator_tipo, "ator_id": ator_id, "ator_nome": ator_nome,
        "momento_utc": momento, "ip": ip, "user_agent": ua, "dados": dados, "hash_documento": hash_doc,
        "hash_anterior": anterior,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def registrar_evento(db: Session, contrato_id: int, tipo: str, ator_tipo: str, ator_id=None, ator_nome=None,
                     request: Optional[Request] = None, dados: Optional[dict] = None, hash_documento: Optional[str] = None) -> str:
    """Insere um evento na cadeia do contrato (não faz commit — vai junto com a transação de quem chama).
    Trava as linhas do contrato (SELECT ... FOR UPDATE) para dois eventos simultâneos não pegarem o mesmo anterior."""
    db.execute(text("SELECT id FROM contratos_condominio WHERE id = :c FOR UPDATE"), {"c": contrato_id})
    anterior = db.execute(text("SELECT hash_evento FROM contratos_eventos WHERE contrato_id = :c ORDER BY id DESC LIMIT 1"),
                          {"c": contrato_id}).scalar()
    momento = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    ip = ip_cliente(request)
    ua = (request.headers.get("user-agent") or "")[:255] if request else None
    ator_id = None if ator_id is None else str(ator_id)
    h = hmac_hex(_conteudo_evento(contrato_id, tipo, ator_tipo, ator_id, ator_nome, momento, ip, ua, dados or {},
                                  hash_documento, anterior).encode())
    db.execute(text("""
        INSERT INTO contratos_eventos (contrato_id, tipo, ator_tipo, ator_id, ator_nome, momento_utc, ip, user_agent, dados,
                                       hash_documento, hash_anterior, hash_evento)
        VALUES (:c, :t, :at, :ai, :an, :m, :ip, :ua, :d, :hd, :ha, :h)"""),
        {"c": contrato_id, "t": tipo, "at": ator_tipo, "ai": ator_id, "an": ator_nome, "m": momento, "ip": ip, "ua": ua,
         "d": json.dumps(dados or {}, ensure_ascii=False, sort_keys=True), "hd": hash_documento, "ha": anterior, "h": h})
    return h


def verificar_cadeia(db: Session, contrato_id: int) -> dict:
    """Recalcula a cadeia inteira. ok=False aponta o 1º evento com hash que não bate ou elo quebrado."""
    anterior = None
    total = 0
    for r in db.execute(text("""SELECT id, contrato_id, tipo, ator_tipo, ator_id, ator_nome, momento_utc, ip, user_agent, dados,
                                       hash_documento, hash_anterior, hash_evento
                                  FROM contratos_eventos WHERE contrato_id = :c ORDER BY id"""), {"c": contrato_id}):
        total += 1
        dados = r.dados if isinstance(r.dados, dict) else json.loads(r.dados or "{}")
        momento = r.momento_utc.strftime("%Y-%m-%d %H:%M:%S.%f")
        esperado = hmac_hex(_conteudo_evento(r.contrato_id, r.tipo, r.ator_tipo, r.ator_id, r.ator_nome, momento, r.ip,
                                             r.user_agent, dados, r.hash_documento, r.hash_anterior).encode())
        if not hmac.compare_digest(r.hash_evento or "", esperado):
            esperado = None
        if r.hash_anterior != anterior or esperado is None:
            return {"ok": False, "eventos": total, "evento_com_problema": r.id}
        anterior = r.hash_evento
    return {"ok": True, "eventos": total, "ultimo_hash": anterior}


# ─── rotas ──────────────────────────────────────────────────────────────────
@router.post("/contratos-licenca/{contrato_id}/preparar")
def preparar_contrato(contrato_id: int, request: Request, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    """Congela o contrato: SHA-256 do PDF + id público + status 'pronto'. Repetir não muda nada (idempotente)."""
    r = db.execute(text("SELECT id, status, arquivo, hash_rascunho, uuid_publico FROM contratos_condominio WHERE id = :i"),
                   {"i": contrato_id}).fetchone()
    if not r or not r.arquivo:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    if r.hash_rascunho:
        return {"success": True, "id": contrato_id, "status": r.status, "hash": r.hash_rascunho,
                "uuid_publico": r.uuid_publico, "ja_estava_congelado": True}
    if r.status != "gerado":
        raise HTTPException(status_code=409, detail=f"Contrato em estado '{r.status}' não pode ser congelado")
    _chave()  # recusa congelar sem a chave configurada (o evento não poderia ser registrado)
    h = sha256(baixar_documento(r.arquivo))
    publico = str(uuid.uuid4())
    try:
        n = db.execute(text("""UPDATE contratos_condominio
                                  SET status = 'pronto', hash_rascunho = :h, uuid_publico = :u, versao_modelo = :v,
                                      congelado_em = NOW(), versao = versao + 1
                                WHERE id = :i AND status = 'gerado' AND hash_rascunho IS NULL"""),
                       {"h": h, "u": publico, "v": VERSAO_MODELO, "i": contrato_id}).rowcount
        if n != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="O contrato foi alterado ao mesmo tempo por outra pessoa. Recarregue.")
        registrar_evento(db, contrato_id, "congelado", "equipe", quem.get("usuario"), quem.get("nome"), request,
                         {"versao_modelo": VERSAO_MODELO, "arquivo": r.arquivo}, h)
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("ASSINATURA: falha ao congelar contrato #%s: %s", contrato_id, e)
        raise HTTPException(status_code=400, detail="Não foi possível congelar o contrato")
    logger.info("ASSINATURA: contrato #%s congelado por %s (sha256 %s)", contrato_id, quem.get("nome"), h[:16])
    return {"success": True, "id": contrato_id, "status": "pronto", "hash": h, "uuid_publico": publico}


@router.get("/contratos-licenca/{contrato_id}/assinatura")
def status_assinatura(contrato_id: int, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    r = db.execute(text("""SELECT id, razao_social, status, hash_rascunho, hash_final, uuid_publico, congelado_em, assinado_em,
                                  assinado_por_nome, versao_modelo, dados
                             FROM contratos_condominio WHERE id = :i"""), {"i": contrato_id}).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    assinantes = [dict(a._mapping) for a in db.execute(text("""
        SELECT id, papel, nome, cpf_mascarado, email, telefone, status, aceito_em, metodo
          FROM contratos_assinantes WHERE contrato_id = :c ORDER BY id"""), {"c": contrato_id})]
    eventos = [dict(e._mapping) for e in db.execute(text("""
        SELECT id, tipo, ator_tipo, ator_nome, momento_utc, ip FROM contratos_eventos WHERE contrato_id = :c ORDER BY id"""),
        {"c": contrato_id})]
    def iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v
    return {
        "id": r.id, "razao_social": r.razao_social, "status": r.status, "hash_rascunho": r.hash_rascunho,
        "hash_final": r.hash_final, "uuid_publico": r.uuid_publico, "versao_modelo": r.versao_modelo,
        "congelado_em": iso(r.congelado_em), "assinado_em": iso(r.assinado_em), "assinado_por_nome": r.assinado_por_nome,
        "assinantes": [{k: iso(v) for k, v in a.items()} for a in assinantes],
        "eventos": [{k: iso(v) for k, v in e.items()} for e in eventos],
        "cadeia": verificar_cadeia(db, contrato_id),
        "modo_assinatura": modo_assinatura(r),
    }


# ════════════════════════════════════════════════════════════════════════════
# FASE 2 — convite, portal do síndico, código (OTP), aceite e recusa
# ════════════════════════════════════════════════════════════════════════════
import base64
import secrets
import smtplib
from datetime import timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from xml.sax.saxutils import escape as _esc

from pydantic import BaseModel, ConfigDict, Field

publico = APIRouter(tags=["assinatura-publica"])   # montado em /api/assinatura (sem login; protegido pelo token)

CONVITE_DIAS = int(os.getenv("CONTRATOS_CONVITE_DIAS", "7"))
OTP_MINUTOS = 10
OTP_MAX_TENTATIVAS = 5        # erros por código
OTP_INTERVALO_S = 60          # entre dois envios
OTP_MAX_POR_HORA = 5          # envios por convite
SESSAO_MINUTOS = 15           # depois do código certo, prazo para aceitar
PORTAL_URL = os.getenv("CONTRATOS_PORTAL_URL", "https://admin.econdominio.com.br/assinar")
TEXTO_ACEITE_VERSAO = "aceite-v1"
TEXTO_ACEITE = ("Declaro que li a íntegra do contrato acima, que represento o condomínio CONTRATANTE com poderes para contratar "
                "e que aceito eletronicamente este Contrato de Licença de Uso, Acesso e Suporte do Software eCondomínio.")
ESTADOS_PORTAL = ("convidado", "visualizado", "aguardando_codigo")
ERRO_LINK = "Link inválido ou expirado. Peça um novo convite à equipe e-Condomínio."


def _agora() -> datetime:
    return datetime.now()   # hora do servidor (America/Sao_Paulo), mesma das outras colunas DATETIME


def envio_real() -> bool:
    """Só envia de verdade com CONTRATOS_ASSINATURA_ENVIO_REAL=true (produção). Sem isso: modo teste (só log)."""
    return os.getenv("CONTRATOS_ASSINATURA_ENVIO_REAL", "").strip().lower() == "true"


def _destino_teste_liberado(destino: str) -> bool:
    """Modo teste: envia de verdade só para os destinos de CONTRATOS_ASSINATURA_TESTE_DESTINOS (e-mails/telefones
    separados por vírgula — ex.: o do Célio). Qualquer outro (ex.: síndicos reais da cópia do banco) não recebe nada."""
    lib = [x.strip().lower() for x in os.getenv("CONTRATOS_ASSINATURA_TESTE_DESTINOS", "").split(",") if x.strip()]
    d = (destino or "").strip().lower()
    dig = re.sub(r"\D", "", d)
    return any(d == x or (dig and len(dig) >= 10 and re.sub(r"\D", "", x).endswith(dig[-10:])) for x in lib)


ENVIOS_TESTE: list = []   # modo teste: o que teria sido enviado (para os testes automáticos)


def mascarar_email(e: str) -> str:
    if not e or "@" not in e:
        return ""
    u, d = e.split("@", 1)
    return f"{u[:1]}***@{d}"


def mascarar_tel(t: str) -> str:
    d = re.sub(r"\D", "", t or "")
    return f"(**) *****-{d[-4:]}" if len(d) >= 8 else ""


def cpf_valido(cpf: str) -> bool:
    d = re.sub(r"\D", "", cpf or "")
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        s = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        dv = (s * 10) % 11 % 10
        if dv != int(d[n]):
            return False
    return True


def _enviar_email(dest: str, assunto: str, html: str) -> bool:
    if not envio_real() and not _destino_teste_liberado(dest):
        ENVIOS_TESTE.append({"canal": "email", "para": dest, "assunto": assunto, "html": html})
        logger.info("ASSINATURA (modo teste, não enviado) e-mail para %s: %s", mascarar_email(dest), assunto)
        return True
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = f"{os.getenv('BREVO_FROM_NAME', 'eCondominio')} <{os.getenv('BREVO_FROM_ADDRESS', 'contato@econdominio.com.br')}>"
        msg["To"] = dest
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(os.getenv("BREVO_SMTP_HOST", "smtp-relay.brevo.com"), int(os.getenv("BREVO_SMTP_PORT", "587")), timeout=15) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(os.getenv("BREVO_SMTP_USER", ""), os.getenv("BREVO_SMTP_KEY", ""))
            s.sendmail(os.getenv("BREVO_FROM_ADDRESS", "contato@econdominio.com.br"), [dest], msg.as_string())
        return True
    except Exception as e:
        logger.warning("ASSINATURA: e-mail falhou (%s): %s", mascarar_email(dest), e)
        return False


def _enviar_whatsapp(tel: str, texto: str) -> Optional[bool]:
    d = re.sub(r"\D", "", tel or "")
    if len(d) < 10:
        return False
    if not d.startswith("55"):
        d = "55" + d
    if not envio_real() and not _destino_teste_liberado(d):
        ENVIOS_TESTE.append({"canal": "whatsapp", "para": d, "texto": texto})
        logger.info("ASSINATURA (modo teste, não enviado) WhatsApp para %s", mascarar_tel(d))
        return True
    try:
        url = (f"{os.getenv('ZAPI_API_URL', 'http://191.252.221.192:8080')}/instances/{os.getenv('ZAPI_INSTANCE_ID', '')}"
               f"/token/{os.getenv('ZAPI_TOKEN', '')}/send-text")
        r = httpx.post(url, json={"phone": d, "message": texto},
                       headers={"Client-Token": os.getenv("ZAPI_CLIENT_TOKEN", ""), "Content-Type": "application/json"}, timeout=30)
        return r.status_code < 300
    except httpx.TimeoutException:
        # o servidor de WhatsApp próprio às vezes entrega mas demora a confirmar — não é falha certa
        logger.warning("ASSINATURA: WhatsApp sem confirmação (%s): demorou mais de 30 s", mascarar_tel(d))
        return None
    except Exception as e:
        logger.warning("ASSINATURA: WhatsApp falhou (%s): %s", mascarar_tel(d), e)
        return False


def _html_basico(titulo: str, corpo: str) -> str:
    return (f'<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden">'
            f'<div style="background:#1e3a8a;padding:20px 24px;color:#fff"><b style="font-size:18px">{_esc(titulo)}</b></div>'
            f'<div style="padding:22px 24px;color:#374151;font-size:14px;line-height:1.5">{corpo}</div>'
            f'<div style="background:#f9fafb;padding:10px;text-align:center;color:#9ca3af;font-size:11px">'
            f'E-CONDOMINIO SISTEMAS DE GESTAO LTDA · contato@econdominio.com.br · WhatsApp (48) 92001-4309</div></div>')


def _hash_token(token: str) -> str:
    return sha256(token.encode())   # token tem 256 bits aleatórios: SHA-256 basta (não é adivinhável)


def _sessao_emitir(convite_id: int, otp_id: int) -> tuple:
    exp = int((_agora() + timedelta(minutes=SESSAO_MINUTOS)).timestamp())
    base = f"{convite_id}.{otp_id}.{exp}"
    return base64.urlsafe_b64encode(f"{base}.{hmac_hex(('sessao:' + base).encode())}".encode()).decode(), exp


def _sessao_validar(sessao: str, convite_id: int) -> int:
    """Devolve o otp_id da sessão; erro 401 se inválida/expirada/de outro convite."""
    try:
        cid, oid, exp, assin = base64.urlsafe_b64decode(sessao.encode()).decode().split(".")
        ok = hmac.compare_digest(assin, hmac_hex(f"sessao:{cid}.{oid}.{exp}".encode()))
    except Exception:
        ok = False
    if not ok or int(cid) != convite_id or int(exp) < int(_agora().timestamp()):
        raise HTTPException(status_code=401, detail="Confirmação expirada. Peça um novo código.")
    return int(oid)


class NovoConvite(BaseModel):
    model_config = ConfigDict(extra="ignore")
    nome: str = Field(..., min_length=3, max_length=150)
    email: Optional[str] = Field(None, max_length=150)
    telefone: Optional[str] = Field(None, max_length=20)
    empresa_assina_antes: bool = False   # 2026-10-10: enviar já assinado pela e-Condomínio


def _dados_json(c) -> dict:
    d = c.dados if hasattr(c, "dados") else None
    if isinstance(d, dict):
        return d
    try:
        return json.loads(d or "{}")
    except (TypeError, ValueError):
        return {}


def modo_assinatura(c) -> str:
    return _dados_json(c).get("modo_assinatura") or "empresa_depois"


def _assinar_contrato_antes(db: Session, contrato_id: int, request: Request, quem: dict):
    """Modo empresa_antes: assina o PDF gerado com o certificado ANTES de congelar/enviar. O PDF assinado passa a ser o
    documento do contrato (o síndico lê e aceita esse arquivo); o gerado original fica registrado em dados.arquivo_original."""
    c = _carregar_contrato(db, contrato_id)
    if c.status != "gerado":
        raise HTTPException(status_code=409, detail="Este contrato já foi preparado sem a assinatura da empresa. Gere um novo para enviar já assinado.")
    original = baixar_documento(c.arquivo)
    cert = _assinador("GET", "/saude", timeout=15)["certificado"]
    r = _assinador("POST", "/assinar", {"pdf_b64": base64.b64encode(original).decode(), "anexos_b64": [],
                                        "motivo": "Contratada — E-CONDOMINIO SISTEMAS DE GESTAO LTDA", "local": "Florianópolis/SC",
                                        "campo": "Assinatura_eCondominio"}, timeout=120)
    assinado = base64.b64decode(r["pdf_b64"])
    if sha256(assinado) != r["sha256"] or not (r["validacao"]["integra"] and r["validacao"]["valida"]):
        raise HTTPException(status_code=424, detail="A assinatura devolvida não passou na conferência")
    arq = _subir_pdf(assinado, f"contrato_assinado_{contrato_id}_{datetime.now():%Y%m%d}")
    n = db.execute(text("""UPDATE contratos_condominio
                              SET arquivo = :a, versao = versao + 1,
                                  dados = JSON_SET(dados, '$.modo_assinatura', 'empresa_antes', '$.arquivo_original', :o,
                                                   '$.assinatura_autorizada_por', :q)
                            WHERE id = :c AND status = 'gerado'"""),
                   {"a": arq, "o": c.arquivo, "q": quem.get("nome") or "equipe", "c": contrato_id}).rowcount
    if n != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="O contrato mudou ao mesmo tempo. Recarregue.")
    db.execute(text("""INSERT INTO contratos_assinantes (contrato_id, papel, nome, status, aceito_em, metodo, certificado_impressao)
                       VALUES (:c, 'empresa', 'E-CONDOMINIO SISTEMAS DE GESTAO LTDA', 'assinado', NOW(), 'certificado_a1', :imp)"""),
               {"c": contrato_id, "imp": cert.get("impressao_sha256")})
    registrar_evento(db, contrato_id, "assinado_antes", "equipe", quem.get("usuario"), quem.get("nome"), request,
                     {"autorizado_por": quem.get("nome"), "certificado_titular": cert.get("titular"), "certificado_serial": cert.get("serial"),
                      "certificado_sha256": cert.get("impressao_sha256"), "validacao": r["validacao"], "arquivo_original": c.arquivo},
                     r["sha256"])
    db.commit()
    logger.info("ASSINATURA: contrato #%s assinado pela empresa ANTES do envio (autorizado por %s)", contrato_id, quem.get("nome"))


def _carregar_contrato(db: Session, contrato_id: int):
    r = db.execute(text("""SELECT id, razao_social, cnpj, status, arquivo, hash_rascunho, uuid_publico, plano, valor,
                                  inicio_vigencia, primeiro_vencimento, dados
                             FROM contratos_condominio WHERE id = :i"""), {"i": contrato_id}).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    return r


# ─── equipe: enviar convite / cancelar ──────────────────────────────────────
def _convite_whatsapp_bg(tel: str, texto: str, contrato_id: int):
    ok = _enviar_whatsapp(tel, texto)
    logger.info("ASSINATURA: convite por WhatsApp do contrato #%s: %s", contrato_id,
                "enviado" if ok else ("sem confirmação" if ok is None else "FALHOU"))


@router.post("/contratos-licenca/{contrato_id}/convites")
def enviar_convite(contrato_id: int, dados: NovoConvite, request: Request, db: Session = Depends(get_db),
                   quem: dict = Depends(usuario_interno), tarefas: BackgroundTasks = None):
    """Congela (se ainda não estiver), cria/atualiza o signatário (síndico) e manda o link por e-mail e/ou WhatsApp.
    Reenviar = chamar de novo: o link anterior é revogado."""
    email = (dados.email or "").strip().lower()
    tel = re.sub(r"\D", "", dados.telefone or "")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="E-mail inválido")
    if not email and len(tel) < 10:
        raise HTTPException(status_code=400, detail="Informe o e-mail ou o WhatsApp do síndico")
    c = _carregar_contrato(db, contrato_id)
    if dados.empresa_assina_antes and modo_assinatura(c) != "empresa_antes":
        _assinar_contrato_antes(db, contrato_id, request, quem)   # assina com o certificado antes de congelar/enviar
        c = _carregar_contrato(db, contrato_id)
    if c.status == "gerado":
        preparar_contrato(contrato_id, request, db, quem)
        c = _carregar_contrato(db, contrato_id)
    ja_assinado = modo_assinatura(c) == "empresa_antes"
    if c.status not in ("pronto",) + ESTADOS_PORTAL + ("expirado",):
        raise HTTPException(status_code=409, detail=f"Contrato em estado '{c.status}' não pode receber convite")
    token = secrets.token_urlsafe(32)
    try:
        db.execute(text("UPDATE contratos_convites SET revogado_em = NOW() WHERE contrato_id = :c AND revogado_em IS NULL"),
                   {"c": contrato_id})
        a = db.execute(text("""SELECT id FROM contratos_assinantes WHERE contrato_id = :c AND papel = 'sindico'
                                ORDER BY id DESC LIMIT 1"""), {"c": contrato_id}).fetchone()
        if a:
            aid = a.id
            db.execute(text("UPDATE contratos_assinantes SET nome = :n, email = :e, telefone = :t, status = 'pendente' WHERE id = :i"),
                       {"n": dados.nome.strip(), "e": email or None, "t": tel or None, "i": aid})
        else:
            aid = db.execute(text("""INSERT INTO contratos_assinantes (contrato_id, papel, nome, email, telefone)
                                     VALUES (:c, 'sindico', :n, :e, :t)"""),
                             {"c": contrato_id, "n": dados.nome.strip(), "e": email or None, "t": tel or None}).lastrowid
        canais = "+".join(x for x, ok in (("email", bool(email)), ("whatsapp", len(tel) >= 10)) if ok)
        conv = db.execute(text("""INSERT INTO contratos_convites (contrato_id, assinante_id, token_hash, expira_em, canal)
                                  VALUES (:c, :a, :h, :exp, :canal)"""),
                          {"c": contrato_id, "a": aid, "h": _hash_token(token),
                           "exp": _agora() + timedelta(days=CONVITE_DIAS), "canal": canais}).lastrowid
        db.execute(text("UPDATE contratos_condominio SET status = 'convidado', versao = versao + 1 WHERE id = :c"), {"c": contrato_id})
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("ASSINATURA: falha ao criar convite do contrato #%s: %s", contrato_id, e)
        raise HTTPException(status_code=400, detail="Não foi possível criar o convite")

    link = f"{PORTAL_URL}/{token}"
    validade = (_agora() + timedelta(days=CONVITE_DIAS)).strftime("%d/%m/%Y")
    envio = {}
    if email:
        corpo = (f"Olá, <b>{_esc(dados.nome.strip())}</b>!<br><br>A e-Condomínio enviou o <b>Contrato de Licença de Uso</b> do "
                 f"<b>{_esc(c.razao_social)}</b> para sua assinatura eletrônica."
                 f"{' O contrato já está <b>assinado digitalmente pela e-Condomínio</b>.' if ja_assinado else ''}<br><br>"
                 f'<a href="{link}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 18px;border-radius:8px;'
                 f'text-decoration:none;font-weight:bold">Ler e assinar o contrato</a><br><br>'
                 f"O link vale até <b>{validade}</b> e é pessoal. Para assinar você vai informar seu CPF e um código que enviaremos.")
        envio["email"] = _enviar_email(email, f"Contrato e-Condomínio para assinatura — {c.razao_social}", _html_basico("Contrato para assinatura", corpo))
    if len(tel) >= 10:
        texto_wa = (f"Olá, {dados.nome.strip()}! A e-Condomínio enviou o contrato do *{c.razao_social}* para sua assinatura eletrônica."
                    f"{' O contrato já está assinado digitalmente pela e-Condomínio.' if ja_assinado else ''}"
                    f"\n\nLeia e assine aqui (vale até {validade}):\n{link}")
        if tarefas is not None:
            tarefas.add_task(_convite_whatsapp_bg, tel, texto_wa, contrato_id)   # servidor de WhatsApp pode levar minutos
            envio["whatsapp"] = None   # "não confirmado" na tela; o resultado vai para o log
        else:
            envio["whatsapp"] = _enviar_whatsapp(tel, texto_wa)
    registrar_evento(db, contrato_id, "convite_enviado", "equipe", quem.get("usuario"), quem.get("nome"), request,
                     {"convite_id": conv, "assinante": dados.nome.strip(), "email": mascarar_email(email),
                      "whatsapp": mascarar_tel(tel), "envio": envio, "expira": validade}, c.hash_rascunho)
    db.commit()
    return {"success": True, "convite_id": conv, "link": link, "expira": validade, "envio": envio, "modo_teste": not envio_real(),
            "ja_assinado_pela_empresa": ja_assinado}


@router.post("/contratos-licenca/{contrato_id}/cancelar")
def cancelar_fluxo(contrato_id: int, request: Request, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    c = _carregar_contrato(db, contrato_id)
    if c.status in ("concluido", "cancelado"):
        raise HTTPException(status_code=409, detail=f"Contrato já está '{c.status}'")
    db.execute(text("UPDATE contratos_convites SET revogado_em = NOW() WHERE contrato_id = :c AND revogado_em IS NULL"), {"c": contrato_id})
    db.execute(text("UPDATE contratos_condominio SET status = 'cancelado', versao = versao + 1 WHERE id = :c"), {"c": contrato_id})
    registrar_evento(db, contrato_id, "cancelado", "equipe", quem.get("usuario"), quem.get("nome"), request,
                     {"status_anterior": c.status}, c.hash_rascunho)
    db.commit()
    return {"success": True, "status": "cancelado"}


# ─── portal do síndico (público, só com o token do link) ────────────────────
def _convite(db: Session, token: str):
    """Convite válido + contrato + signatário. Qualquer problema → mesma mensagem genérica (não revela se existe)."""
    if not token or len(token) > 100:
        raise HTTPException(status_code=404, detail=ERRO_LINK)
    r = db.execute(text("""
        SELECT v.id AS convite_id, v.expira_em, v.revogado_em, v.aberto_em, v.canal,
               c.id AS contrato_id, c.razao_social, c.cnpj, c.status, c.arquivo, c.hash_rascunho, c.plano, c.valor,
               c.inicio_vigencia, c.primeiro_vencimento, c.dados,
               a.id AS assinante_id, a.nome, a.email, a.telefone, a.cpf_mascarado, a.cpf_hash, a.status AS assinante_status, a.aceito_em
          FROM contratos_convites v
          JOIN contratos_condominio c ON c.id = v.contrato_id
          JOIN contratos_assinantes a ON a.id = v.assinante_id
         WHERE v.token_hash = :h"""), {"h": _hash_token(token)}).fetchone()
    if not r or r.revogado_em:
        raise HTTPException(status_code=404, detail=ERRO_LINK)
    if r.expira_em < _agora():
        if r.status in ESTADOS_PORTAL:
            db.execute(text("UPDATE contratos_condominio SET status = 'expirado' WHERE id = :c AND status IN ('convidado','visualizado','aguardando_codigo')"),
                       {"c": r.contrato_id})
            db.commit()
        raise HTTPException(status_code=404, detail=ERRO_LINK)
    if r.status not in ESTADOS_PORTAL + ("aceito", "assinando", "concluido", "recusado"):
        raise HTTPException(status_code=404, detail=ERRO_LINK)
    return r


@publico.get("/{token}")
def portal_info(token: str, request: Request, db: Session = Depends(get_db)):
    r = _convite(db, token)
    if not r.aberto_em:
        db.execute(text("UPDATE contratos_convites SET aberto_em = NOW() WHERE id = :v"), {"v": r.convite_id})
        if r.status == "convidado":
            db.execute(text("UPDATE contratos_condominio SET status = 'visualizado' WHERE id = :c AND status = 'convidado'"), {"c": r.contrato_id})
        registrar_evento(db, r.contrato_id, "aberto", "sindico", r.assinante_id, r.nome, request, {"convite_id": r.convite_id}, r.hash_rascunho)
        db.commit()
    canais = []
    if r.email:
        canais.append({"canal": "email", "destino": mascarar_email(r.email)})
    if r.telefone:
        canais.append({"canal": "whatsapp", "destino": mascarar_tel(r.telefone)})
    return {
        "contratante": r.razao_social, "cnpj": r.cnpj, "plano": r.plano, "valor": float(r.valor or 0),
        "inicio_vigencia": r.inicio_vigencia.isoformat() if r.inicio_vigencia else None,
        "primeiro_vencimento": r.primeiro_vencimento.isoformat() if r.primeiro_vencimento else None,
        "contratada": "E-CONDOMINIO SISTEMAS DE GESTAO LTDA — CNPJ 64.931.933/0001-85",
        "signatario": r.nome, "cpf_informado": r.cpf_mascarado, "canais": canais, "hash": r.hash_rascunho,
        "situacao": "aceito" if r.assinante_status == "aceito" else ("recusado" if r.status == "recusado" else "pendente"),
        "aceito_em": r.aceito_em.isoformat() if r.aceito_em else None, "texto_aceite": TEXTO_ACEITE,
        "assinado_pela_empresa": modo_assinatura(r) == "empresa_antes",
        "expira_em": r.expira_em.isoformat(),
    }


@publico.get("/{token}/documento")
def portal_documento(token: str, db: Session = Depends(get_db)):
    from fastapi.responses import Response
    r = _convite(db, token)
    pdf = baixar_documento(r.arquivo)
    if sha256(pdf) != r.hash_rascunho:   # o arquivo do storage tem de ser exatamente o congelado
        logger.error("ASSINATURA: PDF do contrato #%s diferente do hash congelado!", r.contrato_id)
        raise HTTPException(status_code=409, detail="Documento indisponível. Avise a equipe e-Condomínio.")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="Contrato_eCondominio.pdf"', "Cache-Control": "no-store"})


class PedidoCodigo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    cpf: str = Field(..., max_length=20)
    canal: str = Field(..., pattern="^(email|whatsapp)$")


def _codigo_whatsapp_bg(destino: str, codigo: str, contrato_id: int):
    """Envio do código por WhatsApp fora da requisição (o servidor próprio pode levar minutos)."""
    ok = _enviar_whatsapp(destino, f"Seu código para assinar o contrato e-Condomínio é *{codigo}*.\nVale por {OTP_MINUTOS} minutos. Não compartilhe.")
    logger.info("ASSINATURA: código por WhatsApp do contrato #%s: %s", contrato_id,
                "enviado" if ok else ("sem confirmação" if ok is None else "FALHOU"))


@publico.post("/{token}/codigo")
def portal_pedir_codigo(token: str, dados: PedidoCodigo, request: Request, tarefas: BackgroundTasks, db: Session = Depends(get_db)):
    r = _convite(db, token)
    if r.status not in ESTADOS_PORTAL:
        raise HTTPException(status_code=409, detail="Este contrato não está mais aguardando assinatura")
    if not cpf_valido(dados.cpf):
        raise HTTPException(status_code=400, detail="CPF inválido")
    hcpf = hash_cpf(dados.cpf)
    if r.cpf_hash and not hmac.compare_digest(r.cpf_hash, hcpf):
        raise HTTPException(status_code=400, detail="O CPF não confere com o informado antes neste convite")
    destino = r.email if dados.canal == "email" else r.telefone
    if not destino:
        raise HTTPException(status_code=400, detail="Canal indisponível para este convite")
    ult = db.execute(text("""SELECT COUNT(*) AS n, MAX(criado_em) AS ultimo FROM contratos_otp
                              WHERE convite_id = :v AND criado_em > NOW() - INTERVAL 1 HOUR"""), {"v": r.convite_id}).fetchone()
    if ult.n >= OTP_MAX_POR_HORA:
        raise HTTPException(status_code=429, detail="Muitos códigos pedidos. Tente de novo em 1 hora.")
    if ult.ultimo and (_agora() - ult.ultimo).total_seconds() < OTP_INTERVALO_S:
        raise HTTPException(status_code=429, detail="Aguarde 1 minuto para pedir outro código.")
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    otp = db.execute(text("""INSERT INTO contratos_otp (convite_id, codigo_hash, expira_em, canal)
                             VALUES (:v, :h, :exp, :canal)"""),
                     {"v": r.convite_id, "h": hmac_hex(f"otp:{r.convite_id}:{codigo}".encode()),
                      "exp": _agora() + timedelta(minutes=OTP_MINUTOS), "canal": dados.canal}).lastrowid
    db.execute(text("UPDATE contratos_assinantes SET cpf_mascarado = :m, cpf_hash = :h WHERE id = :a"),
               {"m": mascarar_cpf(dados.cpf), "h": hcpf, "a": r.assinante_id})
    db.execute(text("UPDATE contratos_condominio SET status = 'aguardando_codigo' WHERE id = :c AND status IN ('convidado','visualizado')"),
               {"c": r.contrato_id})
    db.commit()
    if dados.canal == "email":
        ok = _enviar_email(destino, f"Código para assinar o contrato e-Condomínio: {codigo}",
                           _html_basico("Seu código de assinatura", f"Seu código é <b style='font-size:22px;letter-spacing:4px'>{codigo}</b>."
                                        f"<br><br>Vale por {OTP_MINUTOS} minutos. Não compartilhe com ninguém."))
    else:
        tarefas.add_task(_codigo_whatsapp_bg, destino, codigo, r.contrato_id)
        ok = True   # pedido aceito; a entrega pode demorar (o resultado vai para o log)
    registrar_evento(db, r.contrato_id, "codigo_enviado" if ok else "codigo_falhou", "sindico", r.assinante_id, r.nome, request,
                     {"otp_id": otp, "canal": dados.canal, "destino": mascarar_email(destino) if dados.canal == "email" else mascarar_tel(destino),
                      "cpf": mascarar_cpf(dados.cpf)}, r.hash_rascunho)
    db.commit()
    if not ok:
        raise HTTPException(status_code=424, detail="Não foi possível enviar o código agora. Tente o outro canal ou mais tarde.")
    return {"success": True, "canal": dados.canal, "expira_minutos": OTP_MINUTOS, "pode_demorar": dados.canal == "whatsapp"}


class ConfirmaCodigo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    codigo: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


@publico.post("/{token}/codigo/confirmar")
def portal_confirmar_codigo(token: str, dados: ConfirmaCodigo, request: Request, db: Session = Depends(get_db)):
    r = _convite(db, token)
    if r.status not in ESTADOS_PORTAL:
        raise HTTPException(status_code=409, detail="Este contrato não está mais aguardando assinatura")
    validos = db.execute(text("""SELECT id, codigo_hash, expira_em, tentativas, canal FROM contratos_otp
                                  WHERE convite_id = :v AND usado_em IS NULL AND expira_em > NOW() AND tentativas < :mx
                                  ORDER BY id DESC FOR UPDATE"""), {"v": r.convite_id, "mx": OTP_MAX_TENTATIVAS}).fetchall()
    if not validos:
        db.rollback()
        raise HTTPException(status_code=400, detail="Código expirado. Peça um novo código.")
    alvo = hmac_hex(f"otp:{r.convite_id}:{dados.codigo}".encode())
    certo = next((v for v in validos if hmac.compare_digest(v.codigo_hash, alvo)), None)   # o código atrasado também vale
    o = certo or validos[0]
    ok = certo is not None
    if not ok:
        db.execute(text("UPDATE contratos_otp SET tentativas = tentativas + 1 WHERE id = :o"), {"o": o.id})
        registrar_evento(db, r.contrato_id, "codigo_errado", "sindico", r.assinante_id, r.nome, request,
                         {"otp_id": o.id, "tentativa": o.tentativas + 1}, r.hash_rascunho)
        db.commit()
        restam = OTP_MAX_TENTATIVAS - o.tentativas - 1
        raise HTTPException(status_code=400, detail=f"Código incorreto. {'Restam ' + str(restam) + ' tentativa(s).' if restam > 0 else 'Peça um novo código.'}")
    db.execute(text("UPDATE contratos_otp SET usado_em = NOW() WHERE id = :o"), {"o": o.id})
    registrar_evento(db, r.contrato_id, "codigo_confirmado", "sindico", r.assinante_id, r.nome, request,
                     {"otp_id": o.id, "canal": o.canal}, r.hash_rascunho)
    db.commit()
    sessao, exp = _sessao_emitir(r.convite_id, o.id)
    return {"success": True, "sessao": sessao, "expira_em": exp}


class Aceite(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sessao: str = Field(..., max_length=400)
    hash_visto: str = Field(..., min_length=64, max_length=64)
    li_e_aceito: bool
    represento: bool


def _concluir_empresa_antes_bg(contrato_id: int):
    """Depois do aceite (modo empresa_antes): assina as evidências e conclui, em segundo plano."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        _finalizar_empresa_antes(db, contrato_id, None, {"nome": None})
    except Exception as e:
        logger.warning("ASSINATURA: conclusão automática do contrato #%s falhou: %s", contrato_id, getattr(e, "detail", e))
    finally:
        db.close()


@publico.post("/{token}/aceitar")
def portal_aceitar(token: str, dados: Aceite, request: Request, tarefas: BackgroundTasks, db: Session = Depends(get_db)):
    r = _convite(db, token)
    if r.assinante_status == "aceito":
        return {"success": True, "situacao": "aceito", "aceito_em": r.aceito_em.isoformat() if r.aceito_em else None}
    if r.status not in ESTADOS_PORTAL:
        raise HTTPException(status_code=409, detail="Este contrato não está mais aguardando assinatura")
    otp_id = _sessao_validar(dados.sessao, r.convite_id)
    o = db.execute(text("SELECT canal, usado_em FROM contratos_otp WHERE id = :o AND convite_id = :v"),
                   {"o": otp_id, "v": r.convite_id}).fetchone()
    if not o or not o.usado_em:
        raise HTTPException(status_code=401, detail="Confirmação expirada. Peça um novo código.")
    if not (dados.li_e_aceito and dados.represento):
        raise HTTPException(status_code=400, detail="Marque as duas declarações para assinar")
    if not hmac.compare_digest(dados.hash_visto, r.hash_rascunho or ""):
        raise HTTPException(status_code=409, detail="O documento mudou desde que foi aberto. Recarregue a página.")
    n = db.execute(text("""UPDATE contratos_condominio SET status = 'aceito', versao = versao + 1
                            WHERE id = :c AND status IN ('convidado','visualizado','aguardando_codigo')"""), {"c": r.contrato_id}).rowcount
    if n != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Este contrato não está mais aguardando assinatura")
    db.execute(text("UPDATE contratos_assinantes SET status = 'aceito', aceito_em = NOW(), metodo = :m WHERE id = :a"),
               {"m": f"otp_{o.canal}", "a": r.assinante_id})
    registrar_evento(db, r.contrato_id, "aceito", "sindico", r.assinante_id, r.nome, request,
                     {"otp_id": otp_id, "canal": o.canal, "cpf": r.cpf_mascarado, "texto_aceite": TEXTO_ACEITE_VERSAO,
                      "declarou_leitura": True, "declarou_representacao": True}, r.hash_rascunho)
    db.commit()
    logger.info("ASSINATURA: contrato #%s ACEITO pelo síndico (%s)", r.contrato_id, r.cpf_mascarado)
    if modo_assinatura(r) == "empresa_antes":
        tarefas.add_task(_concluir_empresa_antes_bg, r.contrato_id)   # empresa já assinou: falta só assinar as evidências
    return {"success": True, "situacao": "aceito"}


class Recusa(BaseModel):
    model_config = ConfigDict(extra="ignore")
    motivo: Optional[str] = Field(None, max_length=500)


@publico.post("/{token}/recusar")
def portal_recusar(token: str, dados: Recusa, request: Request, db: Session = Depends(get_db)):
    r = _convite(db, token)
    if r.status not in ESTADOS_PORTAL:
        raise HTTPException(status_code=409, detail="Este contrato não está mais aguardando assinatura")
    db.execute(text("UPDATE contratos_condominio SET status = 'recusado', versao = versao + 1 WHERE id = :c"), {"c": r.contrato_id})
    db.execute(text("UPDATE contratos_assinantes SET status = 'recusado' WHERE id = :a"), {"a": r.assinante_id})
    db.execute(text("UPDATE contratos_convites SET revogado_em = NOW() WHERE id = :v"), {"v": r.convite_id})
    registrar_evento(db, r.contrato_id, "recusado", "sindico", r.assinante_id, r.nome, request,
                     {"motivo": (dados.motivo or "").strip()[:500]}, r.hash_rascunho)
    db.commit()
    return {"success": True, "situacao": "recusado"}


# ════════════════════════════════════════════════════════════════════════════
# FASES 3/4 — assinatura A1 da empresa (serviço assinador) + página de evidências + PDF final + verificação
# ════════════════════════════════════════════════════════════════════════════
ASSINADOR_URL = os.getenv("ASSINADOR_URL", "http://127.0.0.1:8090")
VERIFICAR_URL = os.getenv("CONTRATOS_VERIFICAR_URL", "https://admin.econdominio.com.br/verificar")
NOMES_EVENTO = {
    "congelado": "Contrato congelado (hash registrado)", "convite_enviado": "Link de assinatura enviado", "aberto": "Signatário abriu o link",
    "codigo_enviado": "Código de confirmação enviado", "codigo_falhou": "Falha ao enviar código", "codigo_errado": "Código incorreto digitado",
    "codigo_confirmado": "Código confirmado", "aceito": "ACEITE ELETRÔNICO do contratante", "recusado": "Recusa do contratante",
    "cancelado": "Fluxo cancelado pela equipe", "assinatura_autorizada": "Assinatura da contratada autorizada", "falha": "Falha na assinatura",
    "assinado_antes": "Contrato ASSINADO pela contratada (certificado digital) antes do envio",
}


def _token_assinador() -> str:
    v = os.getenv("ASSINADOR_TOKEN", "").strip()
    if not v:
        arq = os.getenv("ASSINADOR_TOKEN_ARQUIVO", "")
        try:
            v = open(os.path.expanduser(arq)).read().strip() if arq else ""
        except OSError:
            v = ""
    if not v:
        raise HTTPException(status_code=503, detail="Serviço de assinatura não configurado")
    return v


def _assinador(metodo: str, caminho: str, corpo: Optional[dict] = None, timeout: int = 90) -> dict:
    try:
        r = httpx.request(metodo, f"{ASSINADOR_URL}{caminho}", json=corpo, timeout=timeout,
                          headers={"X-Assinador-Token": _token_assinador()})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=424, detail="Serviço de assinatura indisponível")
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code != 200:
        raise HTTPException(status_code=424, detail=f"Assinatura não concluída: {d.get('erro') or r.status_code}")
    return d


def _subir_pdf(pdf: bytes, prefixo: str) -> str:
    try:
        resp = httpx.post(f"{STORAGE_BASE_URL}/upload/documento", headers={"x-api-key": STORAGE_API_KEY},
                          json={"base64": base64.b64encode(pdf).decode(), "prefixo": prefixo}, timeout=60)
        resp.raise_for_status()
        return resp.json()["filename"]
    except Exception as e:
        logger.error("ASSINATURA: falha ao gravar PDF no storage: %s", e)
        raise HTTPException(status_code=424, detail="Não foi possível salvar o PDF no storage")


def montar_evidencias(db: Session, c, sindico, cert: dict, autorizado_por: str, modo: str = "empresa_depois") -> tuple:
    """Página(s) de evidências em PDF + JSON canônico (o hash do JSON vai no evento final)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    eventos = [dict(e._mapping) for e in db.execute(text("""
        SELECT id, tipo, ator_tipo, ator_nome, momento_utc, ip, user_agent, hash_evento FROM contratos_eventos
         WHERE contrato_id = :c ORDER BY id"""), {"c": c.id})]
    cadeia = verificar_cadeia(db, c.id)
    if not cadeia["ok"]:
        raise HTTPException(status_code=409, detail=f"Histórico com alteração detectada (evento #{cadeia.get('evento_com_problema')}) — assinatura bloqueada")
    aceite = next((e for e in reversed(eventos) if e["tipo"] == "aceito"), None)
    if not aceite:
        raise HTTPException(status_code=409, detail="Não há aceite do contratante registrado")

    def br(dt):
        return (dt.replace(tzinfo=timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M:%S") if dt else "—")

    evid = {
        "versao": "evidencias-v1", "contrato_id": c.id, "uuid_publico": c.uuid_publico, "versao_modelo": c.versao_modelo,
        "documento_aceito_sha256": c.hash_rascunho, "contratante": {"razao_social": c.razao_social, "cnpj": c.cnpj},
        "signatario": {"nome": sindico.nome, "cpf": sindico.cpf_mascarado, "email": mascarar_email(sindico.email or ""),
                       "whatsapp": mascarar_tel(sindico.telefone or ""), "metodo": sindico.metodo,
                       "aceito_em_utc": aceite["momento_utc"].isoformat(), "ip": aceite["ip"], "navegador": aceite["user_agent"],
                       "texto_aceite_versao": TEXTO_ACEITE_VERSAO, "texto_aceite": TEXTO_ACEITE},
        "eventos": [{"id": e["id"], "tipo": e["tipo"], "ator": e["ator_nome"], "momento_utc": e["momento_utc"].isoformat(),
                     "ip": e["ip"], "hash": e["hash_evento"]} for e in eventos],
        "cadeia": {"eventos": cadeia["eventos"], "ultimo_hash": cadeia.get("ultimo_hash")},
        "contratada": {"razao_social": "E-CONDOMINIO SISTEMAS DE GESTAO LTDA", "cnpj": "64.931.933/0001-85",
                       "certificado_titular": cert.get("titular"), "certificado_emissor": cert.get("emissor"),
                       "certificado_serial": cert.get("serial"), "certificado_valido_ate": cert.get("valido_ate"),
                       "certificado_sha256": cert.get("impressao_sha256"), "assinatura_autorizada_por": autorizado_por},
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(), "verificacao": f"{VERIFICAR_URL}/{c.uuid_publico}",
        "modo_assinatura": modo,
    }
    json_canonico = json.dumps(evid, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    base = getSampleStyleSheet()
    s_t = ParagraphStyle("t", parent=base["Title"], fontSize=13, leading=16)
    s_h = ParagraphStyle("h", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9.5, spaceBefore=8, spaceAfter=3)
    s_p = ParagraphStyle("p", parent=base["Normal"], fontSize=8.5, leading=11)
    s_c = ParagraphStyle("c", parent=base["Normal"], fontSize=7.5, leading=9.5)
    w = A4[0] - 3.4 * cm

    def tab(linhas, larg):
        t = Table([[Paragraph(_esc(str(x)), s_c) for x in l] for l in linhas], colWidths=larg)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9aa5b8")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f7"))]))
        return t

    hist = [["Data/hora (Brasília)", "Evento", "Por", "IP"]] + [
        [br(e["momento_utc"]), NOMES_EVENTO.get(e["tipo"], e["tipo"]), e["ator_nome"] or e["ator_tipo"], e["ip"] or "—"] for e in eventos]
    th = Table([[Paragraph(_esc(str(x)), s_c) for x in l] for l in hist], colWidths=[3.4 * cm, 6.2 * cm, 4.2 * cm, w - 13.8 * cm], repeatRows=1)
    th.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9aa5b8")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]))
    story = [
        Paragraph("PÁGINA DE EVIDÊNCIAS DA ASSINATURA", s_t),
        Paragraph(f"Contrato de Licença de Uso, Acesso e Suporte do Software eCondomínio — contrato nº {c.id}", s_p),
        Paragraph("CONTRATO ACEITO", s_h),
        tab([["Documento aceito (SHA-256)", c.hash_rascunho], ["ID público", c.uuid_publico], ["Versão do modelo", c.versao_modelo or "—"],
             ["Contratante", f"{c.razao_social} — CNPJ {c.cnpj or '—'}"]], [4.4 * cm, w - 4.4 * cm]),
        Paragraph("ASSINATURA ELETRÔNICA DO CONTRATANTE (aceite com código de confirmação)", s_h),
        tab([["Signatário", sindico.nome], ["CPF", sindico.cpf_mascarado or "—"],
             ["Autenticação", (sindico.metodo or "").replace("otp_email", "código enviado por e-mail").replace("otp_whatsapp", "código enviado por WhatsApp")],
             ["E-mail / WhatsApp", " / ".join(x for x in [mascarar_email(sindico.email or ""), mascarar_tel(sindico.telefone or "")] if x) or "—"],
             ["Aceite em", br(aceite["momento_utc"])], ["IP / navegador", f"{aceite['ip'] or '—'} — {(aceite['user_agent'] or '—')[:140]}"],
             ["Declarações aceitas", TEXTO_ACEITE]], [4.4 * cm, w - 4.4 * cm]),
        Paragraph("ASSINATURA DIGITAL DA CONTRATADA (certificado digital)", s_h),
        tab([["Contratada", "E-CONDOMINIO SISTEMAS DE GESTAO LTDA — CNPJ 64.931.933/0001-85"],
             ["Certificado (titular)", cert.get("titular", "—")], ["Emissor", cert.get("emissor", "—")],
             ["Série / validade", f"{cert.get('serial', '—')} — até {(cert.get('valido_ate') or '')[:10]}"],
             ["Autorizada por", autorizado_por]], [4.4 * cm, w - 4.4 * cm]),
        Paragraph("HISTÓRICO (trilha encadeada e protegida contra alteração)", s_h), th, Spacer(1, 4),
        Paragraph(f"Eventos: {cadeia['eventos']} — último elo da cadeia: {cadeia.get('ultimo_hash')}", s_c),
        Paragraph(f"Verificação: {VERIFICAR_URL}/{c.uuid_publico} — hash destas evidências (JSON): {sha256(json_canonico)}", s_c),
        Spacer(1, 6),
        Paragraph(("O contrato aceito (SHA-256 acima) foi assinado digitalmente pela CONTRATADA ANTES do envio ao contratante; esta "
                   "página de evidências é um documento separado, também assinado digitalmente pela CONTRATADA após o aceite. "
                   if modo == "empresa_antes" else
                   "Este documento reúne o contrato aceito e esta página de evidências e foi assinado digitalmente pela CONTRATADA em seguida. ")
                  + "Qualquer alteração posterior invalida a assinatura digital.", s_p),
    ]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.7 * cm, rightMargin=1.7 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                      title=f"Evidências — contrato {c.id}", author="E-CONDOMINIO SISTEMAS DE GESTAO LTDA").build(story)
    return buf.getvalue(), json_canonico


@router.post("/contratos-licenca/{contrato_id}/assinar-empresa")
def assinar_empresa(contrato_id: int, request: Request, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    """Master ou colaborador autoriza: monta evidências, o ASSINADOR assina com o certificado da empresa e valida,
    guarda o PDF final. Repetir não assina duas vezes (chave de idempotência por hash do documento aceito)."""
    c = db.execute(text("""SELECT id, razao_social, cnpj, status, arquivo, hash_rascunho, uuid_publico, versao_modelo, hash_final, dados
                             FROM contratos_condominio WHERE id = :i"""), {"i": contrato_id}).fetchone()
    if not c:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    if modo_assinatura(c) == "empresa_antes" and c.status != "concluido":
        return _finalizar_empresa_antes(db, contrato_id, request, quem)   # empresa já assinou: concluir (assinar evidências)
    if c.status == "concluido":
        return {"success": True, "status": "concluido", "hash_final": c.hash_final, "ja_estava_assinado": True}
    if c.status not in ("aceito", "falha"):
        raise HTTPException(status_code=409, detail="Só dá para assinar depois do aceite do contratante")
    sindico = db.execute(text("""SELECT id, nome, cpf_mascarado, email, telefone, metodo FROM contratos_assinantes
                                  WHERE contrato_id = :c AND papel = 'sindico' AND status = 'aceito' ORDER BY id DESC LIMIT 1"""),
                         {"c": contrato_id}).fetchone()
    if not sindico:
        raise HTTPException(status_code=409, detail="Não há aceite do contratante registrado")
    chave = f"assinar:{contrato_id}:{c.hash_rascunho}"
    if db.execute(text("UPDATE contratos_condominio SET status = 'assinando', versao = versao + 1 WHERE id = :c AND status IN ('aceito','falha')"),
                  {"c": contrato_id}).rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="A assinatura já está em andamento")
    db.execute(text("""INSERT INTO contratos_assinatura_jobs (contrato_id, chave_idempotencia, status, tentativas, solicitado_por, iniciado_em)
                       VALUES (:c, :k, 'assinando', 1, :q, NOW())
                       ON DUPLICATE KEY UPDATE status = 'assinando', tentativas = tentativas + 1, iniciado_em = NOW(), solicitado_por = :q, ultimo_erro = NULL"""),
               {"c": contrato_id, "k": chave, "q": quem.get("nome")})
    registrar_evento(db, contrato_id, "assinatura_autorizada", "equipe", quem.get("usuario"), quem.get("nome"), request,
                     {"autorizado_por": quem.get("nome"), "tipo_usuario": quem.get("tipo"), "master": bool(quem.get("master"))}, c.hash_rascunho)
    db.commit()
    try:
        cert = _assinador("GET", "/saude", timeout=15)["certificado"]
        original = baixar_documento(c.arquivo)
        if sha256(original) != c.hash_rascunho:
            raise HTTPException(status_code=409, detail="O PDF guardado não confere com o documento aceito")
        ev_pdf, ev_json = montar_evidencias(db, c, sindico, cert, quem.get("nome") or "equipe")
        r = _assinador("POST", "/assinar", {"pdf_b64": base64.b64encode(original).decode(), "anexos_b64": [base64.b64encode(ev_pdf).decode()],
                                            "motivo": "Contratada — E-CONDOMINIO SISTEMAS DE GESTAO LTDA", "local": "Florianópolis/SC",
                                            "campo": "Assinatura_eCondominio"}, timeout=120)
        final = base64.b64decode(r["pdf_b64"])
        if sha256(final) != r["sha256"] or not (r["validacao"]["integra"] and r["validacao"]["valida"]):
            raise HTTPException(status_code=424, detail="A assinatura devolvida não passou na conferência")
        arq_final = _subir_pdf(final, f"contrato_assinado_{contrato_id}_{datetime.now():%Y%m%d}")
        arq_evid = _subir_pdf(ev_pdf, f"evidencias_{contrato_id}_{datetime.now():%Y%m%d}")
    except HTTPException as e:
        _falha_assinatura(db, contrato_id, chave, e.detail, request, quem, c.hash_rascunho)
        raise
    except Exception as e:
        _falha_assinatura(db, contrato_id, chave, f"erro interno ({type(e).__name__})", request, quem, c.hash_rascunho)
        raise HTTPException(status_code=424, detail="Falha ao assinar — tente de novo")
    h_final, h_evid = r["sha256"], sha256(ev_pdf)
    db.execute(text("""UPDATE contratos_condominio SET status = 'concluido', hash_final = :hf, arquivo_final = :af, arquivo_evidencias = :ae,
                              hash_evidencias = :he, assinado_em = NOW(), assinado_por_nome = :q, versao = versao + 1 WHERE id = :c"""),
               {"hf": h_final, "af": arq_final, "ae": arq_evid, "he": h_evid, "q": quem.get("nome"), "c": contrato_id})
    db.execute(text("""INSERT INTO contratos_assinantes (contrato_id, papel, nome, status, aceito_em, metodo, certificado_impressao)
                       VALUES (:c, 'empresa', 'E-CONDOMINIO SISTEMAS DE GESTAO LTDA', 'assinado', NOW(), 'certificado_a1', :imp)"""),
               {"c": contrato_id, "imp": cert.get("impressao_sha256")})
    db.execute(text("UPDATE contratos_assinatura_jobs SET status = 'ok', finalizado_em = NOW() WHERE chave_idempotencia = :k"), {"k": chave})
    registrar_evento(db, contrato_id, "assinado", "sistema", None, "Serviço assinador", request,
                     {"hash_final": h_final, "hash_evidencias_pdf": h_evid, "hash_evidencias_json": sha256(ev_json),
                      "certificado_titular": cert.get("titular"), "certificado_serial": cert.get("serial"),
                      "certificado_sha256": cert.get("impressao_sha256"), "validacao": r["validacao"], "autorizado_por": quem.get("nome")}, h_final)
    db.commit()
    logger.info("ASSINATURA: contrato #%s CONCLUÍDO (assinado A1, autorizado por %s, sha256 %s)", contrato_id, quem.get("nome"), h_final[:16])
    return {"success": True, "status": "concluido", "hash_final": h_final, "validacao": r["validacao"], "certificado": cert.get("titular")}


def _finalizar_empresa_antes(db: Session, contrato_id: int, request: Optional[Request], quem: dict) -> dict:
    """Modo empresa_antes, após o aceite: evidências em PDF separado, assinadas com o certificado; o contrato (já assinado)
    é o arquivo final. Idempotente; em falha fica 'falha' e o botão da equipe tenta de novo."""
    c = db.execute(text("""SELECT id, razao_social, cnpj, status, arquivo, hash_rascunho, uuid_publico, versao_modelo, hash_final, dados
                             FROM contratos_condominio WHERE id = :i"""), {"i": contrato_id}).fetchone()
    if not c:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    if c.status == "concluido":
        return {"success": True, "status": "concluido", "hash_final": c.hash_final, "ja_estava_assinado": True}
    if c.status not in ("aceito", "falha"):
        raise HTTPException(status_code=409, detail="Só dá para concluir depois do aceite do contratante")
    sindico = db.execute(text("""SELECT id, nome, cpf_mascarado, email, telefone, metodo FROM contratos_assinantes
                                  WHERE contrato_id = :c AND papel = 'sindico' AND status = 'aceito' ORDER BY id DESC LIMIT 1"""),
                         {"c": contrato_id}).fetchone()
    if not sindico:
        raise HTTPException(status_code=409, detail="Não há aceite do contratante registrado")
    autorizado = _dados_json(c).get("assinatura_autorizada_por") or "equipe"
    chave = f"evidencias:{contrato_id}:{c.hash_rascunho}"
    if db.execute(text("UPDATE contratos_condominio SET status = 'assinando', versao = versao + 1 WHERE id = :c AND status IN ('aceito','falha')"),
                  {"c": contrato_id}).rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="A conclusão já está em andamento")
    db.execute(text("""INSERT INTO contratos_assinatura_jobs (contrato_id, chave_idempotencia, status, tentativas, solicitado_por, iniciado_em)
                       VALUES (:c, :k, 'assinando', 1, :q, NOW())
                       ON DUPLICATE KEY UPDATE status = 'assinando', tentativas = tentativas + 1, iniciado_em = NOW(), ultimo_erro = NULL"""),
               {"c": contrato_id, "k": chave, "q": quem.get("nome") or "automático (após o aceite)"})
    db.commit()
    try:
        cert = _assinador("GET", "/saude", timeout=15)["certificado"]
        contrato_pdf = baixar_documento(c.arquivo)
        if sha256(contrato_pdf) != c.hash_rascunho:
            raise HTTPException(status_code=409, detail="O PDF guardado não confere com o documento aceito")
        ev_pdf, ev_json = montar_evidencias(db, c, sindico, cert, autorizado, "empresa_antes")
        r = _assinador("POST", "/assinar", {"pdf_b64": base64.b64encode(ev_pdf).decode(), "anexos_b64": [],
                                            "motivo": "Evidências do aceite — E-CONDOMINIO SISTEMAS DE GESTAO LTDA", "local": "Florianópolis/SC",
                                            "campo": "Assinatura_eCondominio_Evidencias"}, timeout=120)
        ev_assinado = base64.b64decode(r["pdf_b64"])
        if sha256(ev_assinado) != r["sha256"] or not (r["validacao"]["integra"] and r["validacao"]["valida"]):
            raise HTTPException(status_code=424, detail="A assinatura das evidências não passou na conferência")
        arq_evid = _subir_pdf(ev_assinado, f"evidencias_assinadas_{contrato_id}_{datetime.now():%Y%m%d}")
    except HTTPException as e:
        _falha_assinatura(db, contrato_id, chave, e.detail, request, quem, c.hash_rascunho)
        raise
    except Exception as e:
        _falha_assinatura(db, contrato_id, chave, f"erro interno ({type(e).__name__})", request, quem, c.hash_rascunho)
        raise HTTPException(status_code=424, detail="Falha ao concluir — tente de novo")
    db.execute(text("""UPDATE contratos_condominio SET status = 'concluido', hash_final = :hf, arquivo_final = :af, arquivo_evidencias = :ae,
                              hash_evidencias = :he, assinado_em = NOW(), assinado_por_nome = :q, versao = versao + 1 WHERE id = :c"""),
               {"hf": c.hash_rascunho, "af": c.arquivo, "ae": arq_evid, "he": r["sha256"], "q": autorizado, "c": contrato_id})
    db.execute(text("UPDATE contratos_assinatura_jobs SET status = 'ok', finalizado_em = NOW() WHERE chave_idempotencia = :k"), {"k": chave})
    registrar_evento(db, contrato_id, "assinado", "sistema", None, "Serviço assinador", request,
                     {"modo": "empresa_antes", "hash_contrato_assinado": c.hash_rascunho, "hash_evidencias_assinadas": r["sha256"],
                      "hash_evidencias_json": sha256(ev_json), "certificado_titular": cert.get("titular"),
                      "certificado_serial": cert.get("serial"), "validacao": r["validacao"], "autorizado_por": autorizado}, r["sha256"])
    db.commit()
    logger.info("ASSINATURA: contrato #%s CONCLUÍDO (empresa assinou antes; evidências assinadas)", contrato_id)
    return {"success": True, "status": "concluido", "hash_final": c.hash_rascunho, "validacao": r["validacao"], "certificado": cert.get("titular")}


def _falha_assinatura(db: Session, contrato_id: int, chave: str, motivo: str, request: Request, quem: dict, hash_doc: Optional[str]):
    try:
        db.rollback()
        db.execute(text("UPDATE contratos_condominio SET status = 'falha', versao = versao + 1 WHERE id = :c AND status = 'assinando'"), {"c": contrato_id})
        db.execute(text("UPDATE contratos_assinatura_jobs SET status = 'falha', ultimo_erro = :e, finalizado_em = NOW() WHERE chave_idempotencia = :k"),
                   {"e": str(motivo)[:250], "k": chave})
        registrar_evento(db, contrato_id, "falha", "sistema", None, "Serviço assinador", request, {"motivo": str(motivo)[:250]}, hash_doc)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("ASSINATURA: não registrou a falha do contrato #%s: %s", contrato_id, e)
    logger.warning("ASSINATURA: contrato #%s falhou: %s", contrato_id, motivo)


def _baixar_arquivo(db: Session, contrato_id: int, coluna: str, nome: str):
    from fastapi.responses import Response
    r = db.execute(text(f"SELECT {coluna} AS arq, razao_social FROM contratos_condominio WHERE id = :i"), {"i": contrato_id}).fetchone()
    if not r or not r.arq:
        raise HTTPException(status_code=404, detail="Arquivo ainda não disponível")
    pdf = baixar_documento(r.arq)
    rs = re.sub(r"[^A-Za-z0-9]+", "_", r.razao_social or "condominio").strip("_")[:50]
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{nome}_{rs}.pdf"'})


@router.get("/contratos-licenca/{contrato_id}/final")
def baixar_final(contrato_id: int, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    return _baixar_arquivo(db, contrato_id, "arquivo_final", "Contrato_assinado")


@router.get("/contratos-licenca/{contrato_id}/evidencias")
def baixar_evidencias(contrato_id: int, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    return _baixar_arquivo(db, contrato_id, "arquivo_evidencias", "Evidencias")


@publico.get("/verificar/{uuid_publico}")
def verificar_publico(uuid_publico: str, db: Session = Depends(get_db)):
    """Verificação pública por ID não enumerável — sem dados pessoais (só a empresa contratante e os hashes)."""
    if not re.fullmatch(r"[0-9a-f-]{36}", uuid_publico or ""):
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    r = db.execute(text("""SELECT razao_social, status, hash_rascunho, hash_final, assinado_em, congelado_em
                             FROM contratos_condominio WHERE uuid_publico = :u"""), {"u": uuid_publico}).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    return {"contratante": r.razao_social, "contratada": "E-CONDOMINIO SISTEMAS DE GESTAO LTDA",
            "situacao": "assinado" if r.status == "concluido" else "em andamento", "hash_documento_aceito": r.hash_rascunho,
            "hash_documento_final": r.hash_final, "assinado_em": r.assinado_em.isoformat() if r.assinado_em else None}
