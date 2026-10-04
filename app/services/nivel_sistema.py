# ============================================================================
# ARQUIVO: nivel_sistema.py
# PASTA: /home/visionlpr/app_subportaria_back/app/services/
# DESCRIÇÃO: Nível da equipe do sistema (role admin_sistema em mobile_operadores):
#            MASTER (acessa tudo, vê Leads no admin e cria usuários master/colaborador) ou
#            COLABORADOR (acessa tudo do master, MENOS Leads no admin e Usuários do sistema;
#            tudo o que faz fica no Relatório de Log com o nome e o nível).
#            Coluna mobile_operadores.nivel_sistema ('master'/'colaborador'; NULL em admin_sistema
#            antigo = master, compatibilidade). Se a coluna não existir, todo admin_sistema é master.
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-04 data alteração: 2026-10-04
# ============================================================================
import logging
from typing import Optional

from sqlalchemy import text

from app.database import SessionLocal

logger = logging.getLogger(__name__)

PAPEL_SISTEMA = "admin_sistema"


def _id_do_usuario(user: dict) -> Optional[int]:
    for k in ("id", "user_id", "sub", "username"):
        v = (user or {}).get(k)
        if str(v or "").isdigit():
            return int(v)
    return None


def nivel_do_id(user_id: Optional[int]) -> Optional[str]:
    """'master' / 'colaborador' para quem é admin_sistema; None para os demais (ou se não achar)."""
    if not user_id:
        return None
    db = SessionLocal()
    try:
        r = db.execute(text("SELECT role, nivel_sistema FROM mobile_operadores WHERE id = :i"), {"i": user_id}).fetchone()
        if not r or (r[0] or "").lower() != PAPEL_SISTEMA:
            return None
        return "colaborador" if r[1] == "colaborador" else "master"
    except Exception as e:  # coluna ainda não criada → compatibilidade: admin_sistema = master
        logger.warning("nivel_sistema: %s", e)
        try:
            db.rollback()
            r = db.execute(text("SELECT role FROM mobile_operadores WHERE id = :i"), {"i": user_id}).fetchone()
            return "master" if r and (r[0] or "").lower() == PAPEL_SISTEMA else None
        except Exception:
            return None
    finally:
        db.close()


def nivel_do_usuario(user: dict) -> Optional[str]:
    if (user or {}).get("role", "").lower() != PAPEL_SISTEMA:
        return None
    return nivel_do_id(_id_do_usuario(user)) or "master"


def eh_master_sistema(user: dict) -> bool:
    """Master do sistema: admin_sistema que NÃO é colaborador (sempre conferido no banco)."""
    return nivel_do_usuario(user) == "master"
