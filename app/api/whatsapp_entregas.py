# ============================================================================
# ARQUIVO: whatsapp_entregas.py
# PASTA: app/api/
# DESCRIÇÃO: Comprovante e relatório de entrega do WhatsApp (tabela whatsapp_entregas, gravada por
#            app/services/whatsapp_entregas.py).
#            GET /api/whatsapp-entregas/encomenda/{id} — todas as mensagens da encomenda (qualquer
#                usuário do condomínio da encomenda, ou master).
#            GET /api/whatsapp-entregas/relatorio — totais, por condomínio (master) e lista paginada,
#                filtros: condomínio, período, tipo, status. Master: todos; síndico/admin_condominio:
#                só o próprio; demais papéis: 403.
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-01 data alteração: 2026-10-01
# ============================================================================
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.api.condominio import is_admin_master
from app.database import get_db

router = APIRouter()
PAPEIS_RELATORIO = ("sindico", "admin_condominio")

TIPO_ROTULO = {
    "ENCOMENDA_RECEBIDA": "Aviso de chegada",
    "LEMBRETE_ENCOMENDA_24H": "Lembrete 24h",
    "LEMBRETE_ENCOMENDA_48H": "Lembrete 48h",
    "LEMBRETE_ENCOMENDA_72H": "Lembrete 72h",
    "CADASTRO_MORADOR": "Confirmação de cadastro",
    "CONFIRMACAO_WHATSAPP": "Confirmação de WhatsApp",
    "MENSAGEM_GENERICA": "Mensagem",
}
# códigos de erro da Meta mais comuns, em português
ERRO_ROTULO = {
    131042: "Conta WhatsApp Business com pagamento pendente na Meta",
    131026: "Número sem WhatsApp ou que não pode receber mensagens",
    131047: "Fora da janela de 24h de conversa",
    131049: "Meta limitou mensagens para este número (engajamento)",
    131050: "Morador bloqueou mensagens desta empresa",
    131051: "Tipo de mensagem não suportado",
    131053: "Erro ao enviar a mídia",
    130472: "Número em experimento da Meta (não recebe)",
    131000: "Erro interno da Meta",
}

COLS = """w.id, w.message_id, w.encomenda_id, w.condominio_id, w.telefone, w.tipo_evento, w.provider, w.status,
          w.enviado_em, w.entregue_em, w.lido_em, w.falhou_em, w.erro_codigo, w.erro_titulo"""


def _iso(v):
    return v.isoformat() if v else None


def _item(r):
    return {
        "id": r.id, "encomenda_id": r.encomenda_id, "condominio_id": r.condominio_id,
        "telefone": r.telefone, "tipo_evento": r.tipo_evento,
        "tipo_rotulo": TIPO_ROTULO.get(r.tipo_evento or "", r.tipo_evento or "—"),
        "provider": r.provider, "status": r.status,
        "enviado_em": _iso(r.enviado_em), "entregue_em": _iso(r.entregue_em),
        "lido_em": _iso(r.lido_em), "falhou_em": _iso(r.falhou_em),
        "erro_codigo": r.erro_codigo,
        "erro_descricao": (ERRO_ROTULO.get(r.erro_codigo) or r.erro_titulo) if r.erro_codigo else None,
    }


@router.get("/encomenda/{encomenda_id}")
async def comprovante_encomenda(encomenda_id: int, current_user: dict = Depends(get_current_user),
                                db: Session = Depends(get_db)):
    enc = db.execute(text("SELECT id, condominio_id FROM encomendas WHERE id = :i"), {"i": encomenda_id}).fetchone()
    if not enc:
        raise HTTPException(status_code=404, detail="Encomenda não encontrada")
    if not is_admin_master(current_user) and current_user.get("condominio_id") != enc.condominio_id:
        raise HTTPException(status_code=403, detail="Encomenda de outro condomínio")
    rows = db.execute(text(f"SELECT {COLS} FROM whatsapp_entregas w WHERE w.encomenda_id = :i "
                           "ORDER BY COALESCE(w.enviado_em, w.criado_em), w.id"), {"i": encomenda_id}).fetchall()
    return {"encomenda_id": encomenda_id, "mensagens": [_item(r) for r in rows]}


@router.get("/relatorio")
async def relatorio(
    condominio_id: Optional[int] = Query(None),
    data_inicio: Optional[str] = Query(None),   # AAAA-MM-DD
    data_fim: Optional[str] = Query(None),
    tipo_evento: Optional[str] = Query(None),
    status: Optional[str] = Query(None),        # enviado / entregue / lido / falhou
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    master = is_admin_master(current_user)
    if not master:
        if (current_user.get("role") or "").lower() not in PAPEIS_RELATORIO:
            raise HTTPException(status_code=403, detail="Relatório disponível para síndico e administrador")
        condominio_id = current_user.get("condominio_id")
        if not condominio_id:
            raise HTTPException(status_code=403, detail="Usuário sem condomínio")

    where, p = ["w.condominio_id IS NOT NULL"], {}
    if condominio_id:
        where.append("w.condominio_id = :c"); p["c"] = condominio_id
    if data_inicio:
        where.append("w.enviado_em >= :di"); p["di"] = data_inicio
    if data_fim:
        where.append("w.enviado_em < DATE_ADD(:df, INTERVAL 1 DAY)"); p["df"] = data_fim
    if tipo_evento:
        where.append("w.tipo_evento = :t"); p["t"] = tipo_evento
    w_base = " AND ".join(where)
    w_lista = w_base + (" AND w.status = :s" if status else "")
    if status:
        p["s"] = status

    tot = db.execute(text(f"""
        SELECT COUNT(*) total, SUM(w.status='enviado') enviado, SUM(w.status='entregue') entregue,
               SUM(w.status='lido') lido, SUM(w.status='falhou') falhou
        FROM whatsapp_entregas w WHERE {w_base}"""), p).fetchone()
    resumo = {k: int(getattr(tot, k) or 0) for k in ("total", "enviado", "entregue", "lido", "falhou")}
    resumo["taxa_entrega"] = round(100 * (resumo["entregue"] + resumo["lido"]) / resumo["total"], 1) if resumo["total"] else None

    erros = [{"erro_codigo": r.erro_codigo, "erro_descricao": ERRO_ROTULO.get(r.erro_codigo) or r.erro_titulo, "qtd": int(r.qtd)}
             for r in db.execute(text(f"""SELECT w.erro_codigo, MAX(w.erro_titulo) erro_titulo, COUNT(*) qtd
                 FROM whatsapp_entregas w WHERE {w_base} AND w.status='falhou' GROUP BY w.erro_codigo ORDER BY qtd DESC"""), p)]

    por_cond = []
    if master and not condominio_id:
        por_cond = [{"condominio_id": r.condominio_id, "condominio_nome": r.nome, "total": int(r.total),
                     "entregues": int(r.entregues or 0), "falhou": int(r.falhou or 0),
                     "taxa_entrega": round(100 * int(r.entregues or 0) / int(r.total), 1) if r.total else None}
                    for r in db.execute(text(f"""
                        SELECT w.condominio_id, c.nome, COUNT(*) total,
                               SUM(w.status IN ('entregue','lido')) entregues, SUM(w.status='falhou') falhou
                        FROM whatsapp_entregas w LEFT JOIN condominios c ON c.id = w.condominio_id
                        WHERE {w_base} GROUP BY w.condominio_id, c.nome ORDER BY falhou DESC, total DESC"""), p)]

    total_lista = db.execute(text(f"SELECT COUNT(*) FROM whatsapp_entregas w WHERE {w_lista}"), p).scalar() or 0
    rows = db.execute(text(f"""
        SELECT {COLS}, c.nome AS condominio_nome, e.nome_destinatario, e.apartamento, e.bloco
        FROM whatsapp_entregas w
        LEFT JOIN condominios c ON c.id = w.condominio_id
        LEFT JOIN encomendas e ON e.id = w.encomenda_id
        WHERE {w_lista}
        ORDER BY w.enviado_em DESC, w.id DESC
        LIMIT :lim OFFSET :off"""), {**p, "lim": limit, "off": (page - 1) * limit}).fetchall()
    itens = []
    for r in rows:
        it = _item(r)
        it.update({"condominio_nome": r.condominio_nome, "destinatario": r.nome_destinatario,
                   "apartamento": r.apartamento, "bloco": r.bloco})
        itens.append(it)
    return {"resumo": resumo, "erros": erros, "por_condominio": por_cond,
            "total": total_lista, "page": page, "limit": limit, "itens": itens,
            "tipos": TIPO_ROTULO}
