# ============================================================================
# ARQUIVO: locais_armazenamento_admin.py
# PASTA: /home/visionlpr/desenvolvimento/app/api/
# DESCRIÇÃO: CRUD de locais de armazenamento por condomínio — uso exclusivo
#            do administrador do sistema (admin_sistema)
# VERSÃO: 1.0.0
# data criação: 2026-07-03    data alteração: -
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional

from ..database import get_db
from .auth import get_current_user

router = APIRouter()


def _is_master(current_user: dict) -> bool:
    """Retorna True se o usuário tem acesso global (admin_sistema ou nivel 1)"""
    return (
        current_user.get("role") == "admin_sistema" or
        current_user.get("nivel_id") == 1 or
        current_user.get("nivel") == 1 or
        int(current_user.get("condominio_id") or 0) == 0
    )


def _exigir_admin_sistema(current_user: dict):
    if not _is_master(current_user):
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador do sistema")


class LocalArmazenamentoCreate(BaseModel):
    condominio_id: int
    nome: str
    padrao: bool = False
    ordem: Optional[int] = None


class LocalArmazenamentoUpdate(BaseModel):
    nome: Optional[str] = None
    padrao: Optional[bool] = None
    ordem: Optional[int] = None
    ativo: Optional[bool] = None


@router.get("/{condominio_id}")
def listar_locais_admin(
    condominio_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _exigir_admin_sistema(current_user)

    rows = db.execute(
        text("""
            SELECT id_local, id_condominio, nome, padrao, ativo, ordem, criado_em
            FROM locais_armazenamento
            WHERE id_condominio = :condominio_id
            ORDER BY ativo DESC, ordem, id_local
        """),
        {"condominio_id": condominio_id},
    ).fetchall()

    return [
        {
            "id_local": r.id_local,
            "id_condominio": r.id_condominio,
            "nome": r.nome,
            "padrao": bool(r.padrao),
            "ativo": bool(r.ativo),
            "ordem": r.ordem,
            "criado_em": r.criado_em.isoformat() if r.criado_em else None,
        }
        for r in rows
    ]


@router.post("/")
def criar_local_admin(
    payload: LocalArmazenamentoCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _exigir_admin_sistema(current_user)

    nome = payload.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome do local é obrigatório")

    if payload.ordem is None:
        max_ordem = db.execute(
            text("SELECT COALESCE(MAX(ordem), 0) AS max_ordem FROM locais_armazenamento WHERE id_condominio = :cid"),
            {"cid": payload.condominio_id},
        ).scalar()
        ordem = (max_ordem or 0) + 1
    else:
        ordem = payload.ordem

    if payload.padrao:
        db.execute(
            text("UPDATE locais_armazenamento SET padrao = 0 WHERE id_condominio = :cid"),
            {"cid": payload.condominio_id},
        )

    db.execute(
        text("""
            INSERT INTO locais_armazenamento (id_condominio, nome, padrao, ativo, ordem)
            VALUES (:cid, :nome, :padrao, 1, :ordem)
        """),
        {"cid": payload.condominio_id, "nome": nome, "padrao": 1 if payload.padrao else 0, "ordem": ordem},
    )
    db.commit()

    return {"success": True, "message": "Local de armazenamento criado"}


@router.put("/{id_local}")
def editar_local_admin(
    id_local: int,
    payload: LocalArmazenamentoUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _exigir_admin_sistema(current_user)

    atual = db.execute(
        text("SELECT id_condominio FROM locais_armazenamento WHERE id_local = :id"),
        {"id": id_local},
    ).fetchone()

    if not atual:
        raise HTTPException(status_code=404, detail="Local não encontrado")

    campos = []
    params = {"id": id_local}

    if payload.nome is not None:
        nome = payload.nome.strip()
        if not nome:
            raise HTTPException(status_code=400, detail="Nome não pode ser vazio")
        campos.append("nome = :nome")
        params["nome"] = nome

    if payload.ordem is not None:
        campos.append("ordem = :ordem")
        params["ordem"] = payload.ordem

    if payload.ativo is not None:
        campos.append("ativo = :ativo")
        params["ativo"] = 1 if payload.ativo else 0

    if payload.padrao is not None:
        if payload.padrao:
            db.execute(
                text("UPDATE locais_armazenamento SET padrao = 0 WHERE id_condominio = :cid"),
                {"cid": atual.id_condominio},
            )
        campos.append("padrao = :padrao")
        params["padrao"] = 1 if payload.padrao else 0

    if not campos:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")

    db.execute(
        text(f"UPDATE locais_armazenamento SET {', '.join(campos)} WHERE id_local = :id"),
        params,
    )
    db.commit()

    return {"success": True, "message": "Local de armazenamento atualizado"}


@router.delete("/{id_local}")
def desativar_local_admin(
    id_local: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _exigir_admin_sistema(current_user)

    atual = db.execute(
        text("SELECT id_local FROM locais_armazenamento WHERE id_local = :id"),
        {"id": id_local},
    ).fetchone()

    if not atual:
        raise HTTPException(status_code=404, detail="Local não encontrado")

    db.execute(
        text("UPDATE locais_armazenamento SET ativo = 0 WHERE id_local = :id"),
        {"id": id_local},
    )
    db.commit()

    return {"success": True, "message": "Local de armazenamento desativado"}
