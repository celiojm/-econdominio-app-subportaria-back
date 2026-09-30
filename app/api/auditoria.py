# ============================================================================
# ARQUIVO: auditoria.py
# PASTA: app/api/
# DESCRIÇÃO: Relatório de log da plataforma (GET /api/auditoria). Lê mobile_audit_logs
#            (gravado por app/services/auditoria.py). Master vê todos os condomínios (ou
#            filtra); síndico/admin do condomínio vê só o próprio; demais papéis: 403.
# VERSÃO: 1.0.0 - criação (2026-09-30)
# data criação: 2026-09-30 data alteração: 2026-09-30
# ============================================================================
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.api.condominio import is_admin_master
from app.database import get_db
from app.services.auditoria import ROTULOS

router = APIRouter()
PAPEIS_RELATORIO = ("sindico", "admin_condominio")


@router.get("")
async def relatorio_auditoria(
    condominio_id: Optional[int] = Query(None),
    data_inicio: Optional[str] = Query(None),   # AAAA-MM-DD
    data_fim: Optional[str] = Query(None),
    acao: Optional[str] = Query(None),
    busca: Optional[str] = Query(None),
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

    where, p = ["l.acao <> 'login'"], {}
    if condominio_id:
        where.append("l.condominio_id = :c"); p["c"] = condominio_id
    if data_inicio:
        where.append("l.criado_em >= :di"); p["di"] = data_inicio
    if data_fim:
        where.append("l.criado_em < DATE_ADD(:df, INTERVAL 1 DAY)"); p["df"] = data_fim
    if acao:
        where.append("l.acao = :a"); p["a"] = acao
    if busca:
        where.append("CAST(l.detalhes AS CHAR) LIKE :b"); p["b"] = f"%{busca}%"
    w = " AND ".join(where)

    total = db.execute(text(f"SELECT COUNT(*) FROM mobile_audit_logs l WHERE {w}"), p).scalar() or 0
    rows = db.execute(text(f"""
        SELECT l.id, l.criado_em, l.condominio_id, c.nome AS condominio_nome, l.acao, l.entidade,
               l.entidade_id, l.detalhes, l.ip
        FROM mobile_audit_logs l
        LEFT JOIN condominios c ON c.id = l.condominio_id
        WHERE {w}
        ORDER BY l.criado_em DESC, l.id DESC
        LIMIT :lim OFFSET :off
    """), {**p, "lim": limit, "off": (page - 1) * limit}).fetchall()

    itens = []
    for r in rows:
        try:
            d = json.loads(r.detalhes) if isinstance(r.detalhes, str) else (r.detalhes or {})
        except Exception:
            d = {}
        itens.append({
            "id": r.id, "data_hora": r.criado_em.isoformat() if r.criado_em else None,
            "condominio_id": r.condominio_id, "condominio_nome": r.condominio_nome,
            "acao": r.acao, "acao_rotulo": ROTULOS.get(r.acao, r.acao),
            "entidade": r.entidade, "entidade_id": r.entidade_id,
            "descricao": d.get("descricao"), "usuario_nome": d.get("usuario_nome"),
            "papel": d.get("papel"), "origem": d.get("origem"), "campos": d.get("campos"),
            "senha_alterada": d.get("senha_alterada"), "resultado": d.get("resultado"),
            "antes": d.get("antes"),
            "ip": r.ip if master else None,
        })
    return {"items": itens, "total": total, "page": page, "limit": limit,
            "total_pages": (total + limit - 1) // limit,
            "acoes": [{"valor": k, "rotulo": v} for k, v in ROTULOS.items()]}
