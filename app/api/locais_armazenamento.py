from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Dict, Any

from ..database import get_db
from .auth import get_current_user

router = APIRouter()

LOCAIS_PADRAO = [
    "Portaria Principal",
    "Portaria Social",
    "Sala de Correspondência",
    "Depósito de Encomendas",
    "Outro",
]

@router.get("/{id_condominio}")
def listar_locais_armazenamento(
    id_condominio: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT id_local, nome, padrao
            FROM locais_armazenamento
            WHERE id_condominio = :id_condominio AND ativo = 1
            ORDER BY ordem, id_local
            """
        ),
        {"id_condominio": id_condominio},
    ).fetchall()

    if rows:
        return [
            {"id_local": r.id_local, "nome": r.nome, "padrao": bool(r.padrao)}
            for r in rows
        ]

    # Fallback: condomínio sem lista personalizada usa a lista geral
    return [
        {"id_local": None, "nome": nome, "padrao": nome == "Portaria Principal"}
        for nome in LOCAIS_PADRAO
    ]

