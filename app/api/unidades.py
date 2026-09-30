# ============================================================================
# ARQUIVO: unidades.py
# PASTA: app/api/
# DESCRIÇÃO: Cadastro de unidades (bloco + apartamento) — /api/unidades. Master: todos os
#            condomínios; síndico/admin do condomínio: só o próprio; demais: 403.
#            Renomear atualiza bloco/apartamento (texto) dos moradores ligados. Mesclar leva os
#            moradores para outra unidade (conflito de morador repetido é pulado e informado).
#            Excluir só unidade sem moradores.
# VERSÃO: 1.0.0 - criação (2026-09-30)
# data criação: 2026-09-30 data alteração: 2026-09-30
# ============================================================================
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.api.condominio import is_admin_master
from app.database import get_db
from app.services.blocos import normalizar_bloco

router = APIRouter()


def _condominio_permitido(current_user: dict, condominio_id: Optional[int]) -> int:
    if is_admin_master(current_user):
        if not condominio_id:
            raise HTTPException(status_code=422, detail="Informe o condomínio")
        return int(condominio_id)
    if (current_user.get("role") or "").lower() not in ("sindico", "admin_condominio"):
        raise HTTPException(status_code=403, detail="Disponível para síndico e administrador")
    proprio = current_user.get("condominio_id")
    if not proprio:
        raise HTTPException(status_code=403, detail="Usuário sem condomínio")
    return int(proprio)


def _unidade(db, unidade_id: int, current_user: dict):
    u = db.execute(text("SELECT id, condominio_id, bloco, apartamento, ativo, revisar FROM unidades WHERE id = :i"),
                   {"i": unidade_id}).fetchone()
    if not u or not u.ativo:
        raise HTTPException(status_code=404, detail="Unidade não encontrada")
    if not is_admin_master(current_user) and u.condominio_id != _condominio_permitido(current_user, None):
        raise HTTPException(status_code=404, detail="Unidade não encontrada")
    return u


def _limpo(v, campo, limite) -> str:
    s = " ".join(str(v or "").split())
    if len(s) > limite:
        raise HTTPException(status_code=422, detail=f"{campo}: máximo de {limite} caracteres")
    return s


@router.get("")
async def listar_unidades(
    condominio_id: Optional[int] = Query(None),
    revisar: Optional[bool] = Query(None),
    bloco: Optional[str] = Query(None),
    busca: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cond = _condominio_permitido(current_user, condominio_id)
    where, p = ["u.condominio_id = :c", "u.ativo = 1"], {"c": cond}
    if revisar is not None:
        where.append("u.revisar = :r"); p["r"] = 1 if revisar else 0
    if bloco is not None:
        where.append("u.bloco = :b"); p["b"] = bloco
    if busca:
        where.append("(u.apartamento LIKE :q OR u.bloco LIKE :q OR EXISTS (SELECT 1 FROM moradores m2 "
                     "WHERE m2.unidade_id = u.id AND m2.nome LIKE :q))"); p["q"] = f"%{busca}%"
    rows = db.execute(text(f"""
        SELECT u.id, u.bloco, u.apartamento, u.revisar, u.motivo_revisar, u.origem,
               COUNT(m.id) AS n_moradores,
               SUBSTRING_INDEX(GROUP_CONCAT(m.nome ORDER BY m.nome SEPARATOR '||'), '||', 4) AS nomes
        FROM unidades u
        LEFT JOIN moradores m ON m.unidade_id = u.id AND (m.ativo = 1 OR m.ativo IS NULL)
        WHERE {' AND '.join(where)}
        GROUP BY u.id
        ORDER BY u.bloco, LENGTH(u.apartamento), u.apartamento
        LIMIT 3000
    """), p).fetchall()
    resumo = db.execute(text("""
        SELECT bloco, COUNT(*) n, SUM(revisar) r FROM unidades
        WHERE condominio_id = :c AND ativo = 1 GROUP BY bloco ORDER BY bloco
    """), {"c": cond}).fetchall()
    cad = db.execute(text("SELECT nome, total_apartamentos FROM condominios WHERE id = :c"), {"c": cond}).fetchone()
    return {
        "condominio_id": cond, "condominio_nome": cad.nome if cad else None,
        "total_apartamentos_cadastrado": cad.total_apartamentos if cad else None,
        "total_unidades": sum(r.n for r in resumo), "total_revisar": int(sum(r.r or 0 for r in resumo)),
        "blocos": [{"bloco": r.bloco, "unidades": r.n, "revisar": int(r.r or 0)} for r in resumo],
        "items": [{"id": r.id, "bloco": r.bloco, "apartamento": r.apartamento, "revisar": bool(r.revisar),
                   "motivo_revisar": r.motivo_revisar, "origem": r.origem, "n_moradores": r.n_moradores,
                   "moradores": (r.nomes or "").split("||") if r.nomes else []} for r in rows],
    }


@router.post("")
async def criar_unidade(dados: Dict[str, Any], current_user: dict = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    cond = _condominio_permitido(current_user, dados.get("condominio_id"))
    apto = _limpo(dados.get("apartamento"), "Apartamento", 50)
    if not apto:
        raise HTTPException(status_code=422, detail="Informe o apartamento")
    bloco = normalizar_bloco(db, cond, _limpo(dados.get("bloco"), "Bloco", 30)) or ""
    try:
        uid = db.execute(text("""
            INSERT INTO unidades (condominio_id, bloco, apartamento, origem) VALUES (:c, :b, :a, 'manual')
        """), {"c": cond, "b": bloco, "a": apto}).lastrowid
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"A unidade {bloco or '(bloco único)'} / {apto} já existe")
    return {"success": True, "id": uid, "bloco": bloco, "apartamento": apto}


@router.put("/{unidade_id}")
async def editar_unidade(unidade_id: int, dados: Dict[str, Any], current_user: dict = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    u = _unidade(db, unidade_id, current_user)
    bloco = normalizar_bloco(db, u.condominio_id, _limpo(dados.get("bloco", u.bloco), "Bloco", 30)) or ""
    apto = _limpo(dados.get("apartamento", u.apartamento), "Apartamento", 50)
    if not apto:
        raise HTTPException(status_code=422, detail="Informe o apartamento")
    try:
        db.execute(text("UPDATE unidades SET bloco = :b, apartamento = :a, revisar = 0, motivo_revisar = NULL WHERE id = :i"),
                   {"b": bloco, "a": apto, "i": unidade_id})
        db.execute(text("UPDATE moradores SET bloco = :b, apartamento = :a WHERE unidade_id = :i"),
                   {"b": bloco or None, "a": apto, "i": unidade_id})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Já existe essa unidade (ou morador repetido nela) — use Mesclar")
    return {"success": True, "id": unidade_id, "bloco": bloco, "apartamento": apto}


@router.patch("/{unidade_id}/conferida")
async def marcar_conferida(unidade_id: int, current_user: dict = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    _unidade(db, unidade_id, current_user)
    db.execute(text("UPDATE unidades SET revisar = 0, motivo_revisar = NULL WHERE id = :i"), {"i": unidade_id})
    db.commit()
    return {"success": True}


@router.post("/{unidade_id}/mesclar")
async def mesclar_unidade(unidade_id: int, dados: Dict[str, Any], current_user: dict = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    origem = _unidade(db, unidade_id, current_user)
    destino = _unidade(db, int(dados.get("destino_id") or 0), current_user)
    if destino.id == origem.id or destino.condominio_id != origem.condominio_id:
        raise HTTPException(status_code=422, detail="Escolha outra unidade do mesmo condomínio")
    movidos, pulados = 0, []
    for m in db.execute(text("SELECT id, nome FROM moradores WHERE unidade_id = :i"), {"i": origem.id}).fetchall():
        try:
            with db.begin_nested():
                db.execute(text("UPDATE moradores SET unidade_id = :d, bloco = :b, apartamento = :a WHERE id = :m"),
                           {"d": destino.id, "b": destino.bloco or None, "a": destino.apartamento, "m": m.id})
            movidos += 1
        except IntegrityError:
            pulados.append(m.nome)
    if not pulados:
        db.execute(text("UPDATE unidades SET ativo = 0 WHERE id = :i"), {"i": origem.id})
    db.commit()
    return {"success": True, "movidos": movidos, "pulados": pulados, "origem_desativada": not pulados}


@router.delete("/{unidade_id}")
async def excluir_unidade(unidade_id: int, current_user: dict = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    u = _unidade(db, unidade_id, current_user)
    n = db.execute(text("SELECT COUNT(*) FROM moradores WHERE unidade_id = :i AND (ativo = 1 OR ativo IS NULL)"),
                   {"i": u.id}).scalar()
    if n:
        raise HTTPException(status_code=409, detail=f"A unidade tem {n} morador(es) — use Mesclar ou mude os moradores antes")
    db.execute(text("UPDATE unidades SET ativo = 0 WHERE id = :i"), {"i": u.id})
    db.commit()
    return {"success": True}
