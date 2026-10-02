# ============================================================================
# ARQUIVO: leads_whatsapp.py
# PASTA: app/services/
# DESCRIÇÃO: Conversa do lead pelo painel (admin /leads e financeiro "Leads do site") e agendamentos
#            de lead (Agenda do financeiro). Fala com o servidor de WhatsApp do funil (vps60688,
#            whatsapp-web.js, número 5548920014309) pelas rotas chat-messages / message-media /
#            send-chat, com ZAPI_API_URL/ZAPI_INSTANCE_ID/ZAPI_TOKEN do .env.
#            Só envio individual (WhatsApp Web pode bloquear o número em envio em massa).
#            Quem chama já validou permissão (master no admin; usuário interno no financeiro).
# VERSÃO: 1.1.0 - conversa e mídia lidas da tabela leads_mensagens (getChats do WA Web quebrado) (2026-10-02)
#         1.0.0 - criação
# data criação: 2026-10-02 data alteração: 2026-10-02
# ============================================================================
import logging
import os
from datetime import datetime

import httpx
from fastapi import HTTPException
from sqlalchemy import text

from app.database import SessionLocal

logger = logging.getLogger(__name__)
STATUS_AGENDA = ("pendente", "realizado", "cancelado")


def _base():
    url, inst, tok = os.getenv("ZAPI_API_URL"), os.getenv("ZAPI_INSTANCE_ID"), os.getenv("ZAPI_TOKEN")
    if not (url and inst and tok):
        raise HTTPException(status_code=503, detail="Servidor de WhatsApp não configurado")
    return f"{url.rstrip('/')}/instances/{inst}/token/{tok}"


def _headers():
    return {"Client-Token": os.getenv("ZAPI_CLIENT_TOKEN", ""), "Content-Type": "application/json"}


def _chamar(metodo, caminho, **kw):
    try:
        # 75s: o servidor de WhatsApp espera até 60s pela confirmação do envio
        r = httpx.request(metodo, _base() + caminho, headers=_headers(), timeout=75, **kw)
    except httpx.HTTPError as e:
        logger.warning("leads_whatsapp: servidor de WhatsApp indisponível: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="Servidor de WhatsApp indisponível")
    try:
        j = r.json()
    except Exception:
        j = {}
    if r.status_code != 200 or not j.get("success"):
        # nunca repassa a URL (tem token); só a mensagem de erro do servidor
        detalhe = j.get("error") or f"erro {r.status_code}"
        codigo = 404 if r.status_code == 404 else 503 if r.status_code == 503 else 400
        raise HTTPException(status_code=codigo, detail=f"WhatsApp: {detalhe}")
    return j


def _lead(db, lead_id):
    row = db.execute(text("SELECT id, nome, whatsapp, whatsapp_chat_id, status, observacao FROM leads WHERE id = :i"),
                     {"i": lead_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    return row


def conversa(lead_id: int, limite: int = 200) -> dict:
    """v1.1: lê o histórico gravado pelo servidor de WhatsApp (tabela leads_mensagens)."""
    db = SessionLocal()
    try:
        lead = _lead(db, lead_id)
        if not lead.whatsapp_chat_id:
            return {"lead_id": lead_id, "chat_id": None, "mensagens": [],
                    "aviso": "Conversa registrada a partir de 02/10/2026 — aparece quando o cliente mandar a próxima mensagem."}
        rows = db.execute(text("""
            SELECT * FROM (SELECT msg_id, from_me, tipo, corpo, midia_mime IS NOT NULL AS tem_midia, enviado_em, id
                           FROM leads_mensagens WHERE chat_id = :c ORDER BY enviado_em DESC, id DESC LIMIT :lim) x
            ORDER BY enviado_em, id"""), {"c": lead.whatsapp_chat_id, "lim": limite}).fetchall()
        return {"lead_id": lead_id, "chat_id": lead.whatsapp_chat_id, "mensagens": [{
            "id": r.msg_id, "fromMe": bool(r.from_me), "type": r.tipo, "body": r.corpo or "",
            "hasMedia": bool(r.tem_midia), "data_hora": r.enviado_em.isoformat() if r.enviado_em else None,
        } for r in rows]}
    finally:
        db.close()


def midia(lead_id: int, msg_id: str) -> dict:
    db = SessionLocal()
    try:
        lead = _lead(db, lead_id)
        r = db.execute(text("SELECT midia_mime, midia_nome, midia_dados FROM leads_mensagens WHERE msg_id = :m AND chat_id = :c"),
                       {"m": msg_id, "c": lead.whatsapp_chat_id or ""}).fetchone()
        if not r or not r.midia_dados:
            raise HTTPException(status_code=404, detail="Mídia não encontrada nesta conversa")
        return {"mimetype": r.midia_mime, "filename": r.midia_nome, "data": r.midia_dados}
    finally:
        db.close()


def responder(lead_id: int, mensagem: str, operador: str) -> dict:
    texto = (mensagem or "").strip()
    if not texto:
        raise HTTPException(status_code=422, detail="Mensagem vazia")
    if len(texto) > 4000:
        raise HTTPException(status_code=422, detail="Mensagem longa demais")
    db = SessionLocal()
    try:
        lead = _lead(db, lead_id)
        j = _chamar("POST", "/send-chat", json={"chatId": lead.whatsapp_chat_id or "", "phone": lead.whatsapp, "message": texto})
        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        linha = f"[{agora}] Resposta pelo painel ({operador}): \"{texto[:300]}\""
        db.execute(text("""
            UPDATE leads SET observacao = TRIM(CONCAT(COALESCE(observacao, ''), '\n', :l)),
                   ultimo_contato_em = NOW(),
                   status = IF(status = 'novo', 'contatado', status),
                   whatsapp_chat_id = COALESCE(:c, whatsapp_chat_id)
            WHERE id = :i"""), {"l": linha, "c": j.get("chatId"), "i": lead_id})
        db.commit()
        return {"success": True, "id": j.get("id")}
    finally:
        db.close()


def agendar(lead_id: int, data_agendamento: str, anotacao: str, operador: str) -> dict:
    try:
        quando = datetime.fromisoformat(data_agendamento)
    except Exception:
        raise HTTPException(status_code=422, detail="Data/hora inválida")
    db = SessionLocal()
    try:
        _lead(db, lead_id)
        r = db.execute(text("""INSERT INTO leads_agendamentos (lead_id, operador_nome, data_agendamento, anotacao)
                               VALUES (:l, :o, :d, :a)"""),
                       {"l": lead_id, "o": operador, "d": quando, "a": (anotacao or "").strip()[:1000] or None})
        linha = f"[{datetime.now().strftime('%d/%m/%Y %H:%M')}] Agendado contato para {quando.strftime('%d/%m/%Y %H:%M')} ({operador})"
        if anotacao:
            linha += f": {anotacao.strip()[:200]}"
        db.execute(text("UPDATE leads SET observacao = TRIM(CONCAT(COALESCE(observacao, ''), '\n', :t)) WHERE id = :i"),
                   {"t": linha, "i": lead_id})
        db.commit()
        return {"success": True, "id": r.lastrowid}
    finally:
        db.close()


def agenda(status: str = "pendente") -> dict:
    db = SessionLocal()
    try:
        where, p = "", {}
        if status != "todos":
            if status not in STATUS_AGENDA:
                raise HTTPException(status_code=422, detail="Status inválido")
            where, p = "WHERE a.status = :s", {"s": status}
        rows = db.execute(text(f"""
            SELECT a.id, a.lead_id, a.operador_nome, a.data_agendamento, a.anotacao, a.status, a.criado_em,
                   l.nome, l.whatsapp, l.cidade, l.status AS lead_status
            FROM leads_agendamentos a JOIN leads l ON l.id = a.lead_id
            {where} ORDER BY a.data_agendamento ASC LIMIT 300"""), p).fetchall()
        return {"items": [{
            "id": r.id, "lead_id": r.lead_id, "operador_nome": r.operador_nome,
            "data_agendamento": r.data_agendamento.isoformat() if r.data_agendamento else None,
            "anotacao": r.anotacao, "status": r.status, "criado_em": r.criado_em.isoformat() if r.criado_em else None,
            "lead_nome": r.nome, "whatsapp": r.whatsapp, "cidade": r.cidade, "lead_status": r.lead_status,
        } for r in rows]}
    finally:
        db.close()


def atualizar_agendamento(ag_id: int, status: str, operador: str) -> dict:
    if status not in STATUS_AGENDA:
        raise HTTPException(status_code=422, detail="Status inválido")
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT lead_id FROM leads_agendamentos WHERE id = :i"), {"i": ag_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        db.execute(text("UPDATE leads_agendamentos SET status = :s WHERE id = :i"), {"s": status, "i": ag_id})
        db.execute(text("UPDATE leads SET observacao = TRIM(CONCAT(COALESCE(observacao, ''), '\n', :t)) WHERE id = :l"),
                   {"t": f"[{datetime.now().strftime('%d/%m/%Y %H:%M')}] Agendamento marcado como {status} ({operador})",
                    "l": row.lead_id})
        db.commit()
        return {"success": True}
    finally:
        db.close()
