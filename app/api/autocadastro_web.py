# ============================================================================
# ARQUIVO: autocadastro_web.py
# PASTA: backend/app/api/
# DESCRIÇÃO: Autocadastro de morador pela página web /auto-cadastro (QR code ou
#            link que o condomínio manda aos moradores). Substitui a conversa
#            pelo WhatsApp/webhook. Mesmas regras: cadastro novo; já cadastrado
#            (confirmar, corrigir, adicionar pessoa); número em outro condomínio.
#            O número 48 92001-4309 (WhatsApp próprio, não oficial) NUNCA inicia
#            conversa: o morador escreve primeiro (botão da página → mensagem
#            pronta), o robô do servidor chama /whatsapp-pedido e responde com um
#            link pessoal (?t=token, 24 h). O número fica confirmado porque foi o
#            próprio morador quem escreveu.
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-14 data alteração: 2026-10-14
# ============================================================================

import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal

logger = logging.getLogger(__name__)
router = APIRouter()

LINK_HORAS = 24
SESSAO_EXPIRADA = "Este link expirou. Abra de novo o QR code ou o link do seu condomínio e mande a mensagem pelo WhatsApp."
MAX_PEDIDOS_TELEFONE = 5      # por hora
PAGINA_URL = os.getenv("AUTOCADASTRO_PAGINA_URL", "https://admin.econdominio.com.br/auto-cadastro")

_PHONE_SQL = "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(telefone,''), ' ', ''), '-', ''), '(', ''), ')', ''), '+', '')"


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------

def _hash(valor: str) -> str:
    chave = (os.getenv("SECRET_KEY") or "").encode()
    return hmac.new(chave, valor.encode(), hashlib.sha256).hexdigest()


def _limpar(valor: Optional[str], maximo: int) -> str:
    v = re.sub(r"[\x00-\x1f\x7f<>]", " ", valor or "")
    v = re.sub(r"\s+", " ", v).strip()
    return v[:maximo]


def _telefone_canonico(raw: str) -> str:
    from leads.validators import normalizar_whatsapp_br
    tel = normalizar_whatsapp_br(raw or "")
    if not tel:
        raise HTTPException(status_code=400, detail="Informe um celular válido com DDD.")
    return tel


def _telefone_exibicao(tel: str) -> str:
    local = tel[2:]
    return f"({local[:2]}) {local[2:7]}-{local[7:]}"


def _candidatos(tel: str) -> List[str]:
    """Formas como o mesmo celular pode estar gravado (com/sem 55, com/sem o 9)."""
    local = tel[2:]
    sem9 = local[:2] + local[3:]
    return list({tel, local, "55" + sem9, sem9})


def _condominio(db, condominio_id: int) -> dict:
    r = db.execute(text("""SELECT id, nome, permite_auto_cadastro FROM condominios
                            WHERE id = :c AND ativo = 1"""), {"c": condominio_id}).mappings().first()
    if not r:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado.")
    if not r["permite_auto_cadastro"]:
        raise HTTPException(status_code=403, detail="O cadastro automático não está liberado para este condomínio. Fale com a portaria.")
    return dict(r)


def _moradores_do_telefone(db, condominio_id: int, tel: str) -> List[dict]:
    cands = _candidatos(tel)
    ph = ", ".join(f":p{i}" for i in range(len(cands)))
    rows = db.execute(text(f"""
        SELECT id, nome, apartamento, bloco, whats_confirmado FROM moradores
         WHERE condominio_id = :c AND COALESCE(ativo, 1) = 1 AND {_PHONE_SQL} IN ({ph})
         ORDER BY id"""), {"c": condominio_id, **{f"p{i}": v for i, v in enumerate(cands)}}).mappings().all()
    return [{"id": r["id"], "nome": r["nome"], "apartamento": r["apartamento"], "bloco": r["bloco"] or "",
             "confirmado": bool(r["whats_confirmado"])} for r in rows]


def _em_outro_condominio(db, condominio_id: int, tel: str) -> bool:
    cands = _candidatos(tel)
    ph = ", ".join(f":p{i}" for i in range(len(cands)))
    return bool(db.execute(text(f"""
        SELECT 1 FROM moradores WHERE condominio_id <> :c AND COALESCE(ativo, 1) = 1
           AND {_PHONE_SQL} IN ({ph}) LIMIT 1"""), {"c": condominio_id, **{f"p{i}": v for i, v in enumerate(cands)}}).first())


def _situacao(db, condominio_id: int, tel: str) -> dict:
    moradores = _moradores_do_telefone(db, condominio_id, tel)
    return {
        "situacao": "ja_cadastrado" if moradores else "novo",
        "moradores": moradores,
        "outro_condominio": (not moradores) and _em_outro_condominio(db, condominio_id, tel),
    }


def _sessao(db, token: str) -> dict:
    if not token or len(token) > 100:
        raise HTTPException(status_code=401, detail=SESSAO_EXPIRADA)
    r = db.execute(text("""SELECT condominio_id, telefone FROM autocadastro_codigos
                            WHERE token_hash = :t AND token_expira_em > NOW() LIMIT 1"""),
                   {"t": _hash("token:" + token)}).mappings().first()
    if not r:
        raise HTTPException(status_code=401, detail=SESSAO_EXPIRADA)
    return dict(r)


def _morador_da_sessao(db, sessao: dict, morador_id: int) -> dict:
    for m in _moradores_do_telefone(db, sessao["condominio_id"], sessao["telefone"]):
        if m["id"] == morador_id:
            return m
    raise HTTPException(status_code=404, detail="Cadastro não encontrado para este celular.")


def _bloco(db, condominio_id: int, bloco: Optional[str]) -> Optional[str]:
    b = _limpar(bloco, 10)
    if not b or b.lower() in ("sem bloco", "nao tem", "não tem", "-", "n/a"):
        return None
    try:
        from app.services.blocos import normalizar_bloco
        return (normalizar_bloco(db, condominio_id, b) or b)[:10]
    except Exception:
        return b


def _unidade_id(db, condominio_id: int, apartamento: str, bloco: Optional[str]) -> Optional[int]:
    try:
        r = db.execute(text("""SELECT id FROM unidades WHERE condominio_id = :c AND apartamento = :a
                                AND bloco = :b AND ativo = 1 LIMIT 1"""),
                       {"c": condominio_id, "a": apartamento, "b": bloco or ""}).first()
        return r[0] if r else None
    except Exception:
        return None


def _validar_nome(nome: str) -> str:
    n = _limpar(nome, 200)
    if len(n) < 3 or not re.search(r"[A-Za-zÀ-ÿ]", n):
        raise HTTPException(status_code=400, detail="Informe o nome completo.")
    return n


def _validar_apto(apto: str) -> str:
    a = _limpar(apto, 50)
    if not a:
        raise HTTPException(status_code=400, detail="Informe o apartamento/unidade.")
    return a


def _inserir_morador(db, condominio: dict, tel: str, nome: str, apto: str, bloco: Optional[str], obs: str) -> int:
    try:
        r = db.execute(text("""
            INSERT INTO moradores (nome, apartamento, bloco, telefone, condominio_id, condominio_nome,
                                   whats_confirmado, obs, unidade_id)
            VALUES (:nome, :apto, :bloco, :tel, :c, :cn, NOW(), :obs, :u)"""),
            {"nome": nome, "apto": apto, "bloco": bloco, "tel": _telefone_exibicao(tel), "c": condominio["id"],
             "cn": condominio["nome"], "obs": obs, "u": _unidade_id(db, condominio["id"], apto, bloco)})
        db.commit()
        return r.lastrowid
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Já existe um morador com este nome nesta unidade. Fale com a portaria.")


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PedidoWhatsapp(_Base):
    condominio_id: int = Field(..., ge=1, le=999999)
    telefone: str = Field(..., max_length=30)


class Sessao(_Base):
    token: str = Field(..., max_length=100)


class Cadastrar(_Base):
    token: str = Field(..., max_length=100)
    nome: str = Field(..., max_length=250)
    apartamento: str = Field(..., max_length=60)
    bloco: Optional[str] = Field(None, max_length=30)


class Confirmar(_Base):
    token: str = Field(..., max_length=100)
    morador_id: int


class Corrigir(Confirmar):
    nome: str = Field(..., max_length=250)
    apartamento: str = Field(..., max_length=60)
    bloco: Optional[str] = Field(None, max_length=30)


class Adicionar(Confirmar):
    nome: str = Field(..., max_length=250)


# ---------------------------------------------------------------------------
# Rotas (públicas — prefixo /api/autocadastro)
# ---------------------------------------------------------------------------

@router.post("/whatsapp-pedido")
def pedido_whatsapp(dados: PedidoWhatsapp, request: Request):
    """Só o robô do WhatsApp próprio chama (token + IP, igual aos leads), quando o
    morador manda a mensagem pronta da página. Devolve o texto que o robô responde."""
    from leads.router import LEADS_WHATSAPP_IP_PERMITIDO, LEADS_WHATSAPP_TOKEN
    from leads.validators import extrair_ip_real
    recebido = request.headers.get("x-lead-token", "")
    if not LEADS_WHATSAPP_TOKEN or not hmac.compare_digest(recebido, LEADS_WHATSAPP_TOKEN) \
            or extrair_ip_real(request) != LEADS_WHATSAPP_IP_PERMITIDO:
        raise HTTPException(status_code=403, detail="acesso negado")
    db = SessionLocal()
    try:
        try:
            cond = _condominio(db, dados.condominio_id)
        except HTTPException as e:
            return {"ok": False, "mensagem": f"{e.detail}"}
        try:
            tel = _telefone_canonico(dados.telefone)
        except HTTPException:
            return {"ok": False, "mensagem": "Não consegui identificar o seu número de celular. "
                                             "Fale com a portaria do seu condomínio para fazer o cadastro."}
        n = db.execute(text("""SELECT COUNT(*) FROM autocadastro_codigos
                                WHERE telefone = :t AND criado_em > NOW() - INTERVAL 1 HOUR"""), {"t": tel}).scalar()
        if n >= MAX_PEDIDOS_TELEFONE:
            return {"ok": False, "mensagem": "Você já pediu vários links na última hora. Use o último link que enviamos "
                                             "ou tente de novo daqui a pouco."}
        token = secrets.token_urlsafe(32)
        db.execute(text("""INSERT INTO autocadastro_codigos
                               (condominio_id, telefone, codigo_hash, expira_em, verificado_em, token_hash, token_expira_em, ip)
                           VALUES (:c, :t, :h, NOW(), NOW(), :th, NOW() + INTERVAL :hrs HOUR, 'whatsapp')"""),
                   {"c": cond["id"], "t": tel, "h": _hash("pedido:" + secrets.token_hex(16)),
                    "th": _hash("token:" + token), "hrs": LINK_HORAS})
        db.commit()
        logger.info("AUTOCADASTRO: link enviado pelo WhatsApp | cond=%s | tel=...%s", cond["id"], tel[-4:])
        return {"ok": True, "mensagem": (
            f"Olá! Recebemos seu pedido de cadastro no {cond['nome']}.\n\n"
            f"Toque no link abaixo para preencher seus dados (vale por {LINK_HORAS} horas):\n"
            f"{PAGINA_URL}?t={token}")}
    finally:
        db.close()


@router.post("/sessao")
def sessao(dados: Sessao):
    """A página aberta pelo link pessoal (?t=) carrega o condomínio e os cadastros do celular."""
    db = SessionLocal()
    try:
        s = _sessao(db, dados.token)
        cond = _condominio(db, s["condominio_id"])
        return {"condominio_nome": cond["nome"], "telefone": _telefone_exibicao(s["telefone"]),
                **_situacao(db, cond["id"], s["telefone"])}
    finally:
        db.close()


@router.post("/cadastrar")
def cadastrar(dados: Cadastrar):
    db = SessionLocal()
    try:
        s = _sessao(db, dados.token)
        cond = _condominio(db, s["condominio_id"])
        if _moradores_do_telefone(db, cond["id"], s["telefone"]):
            raise HTTPException(status_code=409, detail="Este celular já está cadastrado neste condomínio.")
        nome, apto = _validar_nome(dados.nome), _validar_apto(dados.apartamento)
        bloco = _bloco(db, cond["id"], dados.bloco)
        mid = _inserir_morador(db, cond, s["telefone"], nome, apto, bloco,
                               f"Cadastro pelo próprio morador na página de autocadastro em {datetime.now():%d/%m/%Y %H:%M}")
        logger.info("AUTOCADASTRO: morador #%s cadastrado | cond=%s", mid, cond["id"])
        return {"ok": True, "morador_id": mid, **_situacao(db, cond["id"], s["telefone"])}
    finally:
        db.close()


@router.post("/confirmar")
def confirmar(dados: Confirmar):
    db = SessionLocal()
    try:
        s = _sessao(db, dados.token)
        _condominio(db, s["condominio_id"])
        m = _morador_da_sessao(db, s, dados.morador_id)
        db.execute(text("UPDATE moradores SET whats_confirmado = COALESCE(whats_confirmado, NOW()) WHERE id = :id"),
                   {"id": m["id"]})
        db.commit()
        return {"ok": True, **_situacao(db, s["condominio_id"], s["telefone"])}
    finally:
        db.close()


@router.post("/corrigir")
def corrigir(dados: Corrigir):
    db = SessionLocal()
    try:
        s = _sessao(db, dados.token)
        _condominio(db, s["condominio_id"])
        m = _morador_da_sessao(db, s, dados.morador_id)
        nome, apto = _validar_nome(dados.nome), _validar_apto(dados.apartamento)
        bloco = _bloco(db, s["condominio_id"], dados.bloco)
        if (nome, apto, bloco or "") == (m["nome"], m["apartamento"], m["bloco"] or ""):
            return {"ok": True, **_situacao(db, s["condominio_id"], s["telefone"])}
        trilha = (f"\nCorrigido pelo próprio morador na página de autocadastro em {datetime.now():%d/%m/%Y %H:%M}. "
                  f"Como estava antes: nome: {m['nome']}; apartamento: {m['apartamento']}; bloco: {m['bloco'] or '-'}")
        try:
            db.execute(text("""UPDATE moradores SET nome = :n, apartamento = :a, bloco = :b, unidade_id = :u,
                                      whats_confirmado = COALESCE(whats_confirmado, NOW()),
                                      obs = CONCAT(COALESCE(obs, ''), :trilha) WHERE id = :id"""),
                       {"n": nome, "a": apto, "b": bloco, "u": _unidade_id(db, s["condominio_id"], apto, bloco),
                        "trilha": trilha, "id": m["id"]})
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Já existe um morador com este nome nesta unidade. Fale com a portaria.")
        logger.info("AUTOCADASTRO: morador #%s corrigido pelo próprio morador", m["id"])
        return {"ok": True, **_situacao(db, s["condominio_id"], s["telefone"])}
    finally:
        db.close()


@router.post("/adicionar")
def adicionar(dados: Adicionar):
    db = SessionLocal()
    try:
        s = _sessao(db, dados.token)
        cond = _condominio(db, s["condominio_id"])
        ref = _morador_da_sessao(db, s, dados.morador_id)
        nome = _validar_nome(dados.nome)
        mid = _inserir_morador(db, cond, s["telefone"], nome, ref["apartamento"], ref["bloco"] or None,
                               f"Adicionado pelo morador na página de autocadastro em {datetime.now():%d/%m/%Y %H:%M} "
                               f"(mesmo celular de {ref['nome']})")
        logger.info("AUTOCADASTRO: morador #%s adicionado (ref #%s)", mid, ref["id"])
        return {"ok": True, "morador_id": mid, **_situacao(db, cond["id"], s["telefone"])}
    finally:
        db.close()
