# ============================================================================
# ARQUIVO: moradores.py
# PASTA: /home/visionlpr/backend/app/api/
# DESCRIÇÃO: API de Moradores
# VERSÃO: 2.1.0 - Correção acesso admin_sistema (condominio_id null)
# CRIAÇÃO: anterior   ALTERAÇÃO: 2025-05-28
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from app.database import get_db
from app.api.auth import get_current_user
from app.services.whatsapp import whatsapp_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _is_master(current_user: dict) -> bool:
    """Retorna True se o usuário tem acesso global (admin_sistema ou nivel 1)"""
    return (
        current_user.get("role") == "admin_sistema" or
        current_user.get("nivel_id") == 1 or
        current_user.get("nivel") == 1 or
        int(current_user.get("condominio_id") or 0) == 0
    )


@router.get("/buscar")
async def buscar_moradores(
    q: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Buscar moradores por nome ou apartamento"""
    try:
        if len(q) < 2:
            return []

        is_master = _is_master(current_user)
        search_term = f"%{q}%"

        if is_master:
            query = text("""
                SELECT id, nome, apartamento, bloco, telefone, whats_confirmado, email,
                       condominio_id
                FROM moradores
                WHERE ativo = 1
                AND (
                    LOWER(nome) LIKE LOWER(:search)
                    OR apartamento LIKE :search_apt
                )
                ORDER BY nome
                LIMIT 50
            """)
            result = db.execute(query, {
                "search": search_term,
                "search_apt": search_term
            })
        else:
            query = text("""
                SELECT id, nome, apartamento, bloco, telefone, whats_confirmado, email,
                       condominio_id
                FROM moradores
                WHERE condominio_id = :condominio_id
                AND ativo = 1
                AND (
                    LOWER(nome) LIKE LOWER(:search)
                    OR apartamento LIKE :search_apt
                )
                ORDER BY nome
                LIMIT 20
            """)
            result = db.execute(query, {
                "condominio_id": current_user["condominio_id"],
                "search": search_term,
                "search_apt": search_term
            })

        moradores = []
        for row in result:
            moradores.append({
                "id": row.id,
                "nome": row.nome,
                "apartamento": row.apartamento,
                "bloco": row.bloco,
                "telefone": row.telefone,
                "whats_confirmado": row.whats_confirmado.isoformat() if row.whats_confirmado else None,
                "email": row.email
            })

        return moradores

    except Exception as e:
        logger.error(f"Erro ao buscar moradores: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao buscar moradores: {str(e)}"
        )


@router.get("/{morador_id}")
async def get_morador(
    morador_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Obter dados de um morador específico"""
    try:
        is_master = _is_master(current_user)

        if is_master:
            query = text("""
                SELECT id, nome, apartamento, bloco, telefone, whats_confirmado, email, condominio_id, ativo
                FROM moradores
                WHERE id = :id
            """)
            result = db.execute(query, {"id": morador_id}).fetchone()
        else:
            query = text("""
                SELECT id, nome, apartamento, bloco, telefone, whats_confirmado, email, condominio_id, ativo
                FROM moradores
                WHERE id = :id AND condominio_id = :condominio_id
            """)
            result = db.execute(query, {
                "id": morador_id,
                "condominio_id": current_user["condominio_id"]
            }).fetchone()

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Morador não encontrado"
            )

        return {
            "id": result.id,
            "nome": result.nome,
            "apartamento": result.apartamento,
            "bloco": result.bloco,
            "telefone": result.telefone,
            "whats_confirmado": result.whats_confirmado.isoformat() if result.whats_confirmado else None,
            "email": result.email,
            "condominio_id": result.condominio_id,
            "ativo": result.ativo
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter morador: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao obter morador: {str(e)}"
        )


@router.post("/{morador_id}/confirmar-whatsapp")
async def confirmar_whatsapp(
    morador_id: int,
    data: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Confirmar WhatsApp do morador"""
    try:
        is_master = _is_master(current_user)

        check_query = text("""
            SELECT id, nome, apartamento, bloco, condominio_id
            FROM moradores
            WHERE id = :id
        """)

        morador = db.execute(check_query, {"id": morador_id}).fetchone()

        if not morador:
            logger.error(f"Morador não encontrado: ID {morador_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Morador não encontrado"
            )

        # admin_sistema pode operar em qualquer condomínio
        if not is_master and morador.condominio_id != current_user["condominio_id"]:
            logger.error(f"Morador {morador_id} não pertence ao condomínio {current_user['condominio_id']}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Morador não pertence ao seu condomínio"
            )

        telefone = data.get("telefone")
        if not telefone:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Telefone é obrigatório"
            )

        telefone_limpo = ''.join(filter(str.isdigit, telefone))

        update_query = text("""
            UPDATE moradores
            SET telefone = :telefone,
                whats_confirmado = NOW()
            WHERE id = :id
        """)

        db.execute(update_query, {
            "id": morador_id,
            "telefone": telefone_limpo
        })
        db.commit()

        logger.info(f"WhatsApp confirmado para morador {morador_id} - Tel: {telefone_limpo}")

        if data.get("send_confirmation_message", False):
            try:
                condominio_nome = current_user.get("condominio_nome", "Condomínio")
                await whatsapp_service.notify_whatsapp_confirmed(
                    nome=morador.nome,
                    condominio=condominio_nome,
                    telefone=telefone_limpo
                )
                logger.info(f"Mensagem de confirmação enviada para {telefone_limpo}")
            except Exception as e:
                logger.error(f"Erro ao enviar mensagem de confirmação: {str(e)}")

        return {
            "success": True,
            "message": "WhatsApp confirmado com sucesso",
            "morador": {
                "id": morador_id,
                "nome": morador.nome,
                "apartamento": morador.apartamento,
                "bloco": morador.bloco,
                "telefone": telefone_limpo,
                "whats_confirmado": datetime.now().isoformat()
            }
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao confirmar WhatsApp para morador {morador_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao confirmar WhatsApp: {str(e)}"
        )


@router.put("/{morador_id}/confirmar-whatsapp")
async def confirmar_whatsapp_put(
    morador_id: int,
    data: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Confirmar WhatsApp do morador (PUT method for compatibility)"""
    return await confirmar_whatsapp(morador_id, data, current_user, db)


@router.post("/")
async def create_morador(
    morador_data: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Criar novo morador"""
    try:
        # Para admin_sistema criando morador, exige condominio_id no payload
        condominio_id = morador_data.get("condominio_id") or current_user["condominio_id"]
        # 2026-09-30: bloco padronizado (app/services/blocos.py)
        from app.services.blocos import normalizar_bloco
        if morador_data.get("bloco") is not None:
            morador_data["bloco"] = normalizar_bloco(db, condominio_id, morador_data.get("bloco"))

        check_query = text("""
            SELECT id FROM moradores
            WHERE condominio_id = :condominio_id
            AND LOWER(nome) = LOWER(:nome)
            AND apartamento = :apartamento
        """)

        params = {
            "condominio_id": condominio_id,
            "nome": morador_data.get("nome"),
            "apartamento": morador_data.get("apartamento")
        }

        if morador_data.get("bloco"):
            check_query = text("""
                SELECT id FROM moradores
                WHERE condominio_id = :condominio_id
                AND LOWER(nome) = LOWER(:nome)
                AND apartamento = :apartamento
                AND bloco = :bloco
            """)
            params["bloco"] = morador_data.get("bloco")

        exists = db.execute(check_query, params).fetchone()

        if exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Morador já cadastrado"
            )

        insert_query = text("""
            INSERT INTO moradores (
                nome, apartamento, bloco, telefone, email,
                condominio_id, condominio_nome, data_cadastro, ativo
            ) VALUES (
                :nome, :apartamento, :bloco, :telefone, :email,
                :condominio_id, :condominio_nome, NOW(), 1
            )
        """)

        db.execute(insert_query, {
            "nome": morador_data.get("nome"),
            "apartamento": morador_data.get("apartamento"),
            "bloco": morador_data.get("bloco"),
            "telefone": morador_data.get("telefone"),
            "email": morador_data.get("email"),
            "condominio_id": condominio_id,
            "condominio_nome": current_user.get("condominio_nome", "")
        })
        db.commit()

        return {
            "success": True,
            "message": "Morador cadastrado com sucesso"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao criar morador: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao criar morador: {str(e)}"
        )


@router.get("/")
async def list_moradores(
    search: Optional[str] = None,
    status_filtro: Optional[str] = Query(default="ativo", alias="status"),
    order_by: Optional[str] = "nome",
    skip: int = 0,
    limit: int = 100,
    condominio_id: Optional[int] = None,  # 2026-10-08: master/colaborador listam os moradores de UM condomínio
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Listar moradores do condomínio"""
    try:
        is_master = _is_master(current_user)

        if status_filtro == "inativo":
            status_clause = "ativo = 0"
        elif status_filtro == "todos":
            status_clause = "1=1"
        else:
            status_clause = "ativo = 1"

        if is_master:
            query = f"""
                SELECT id, nome, apartamento, bloco, telefone, whats_confirmado, email, condominio_id, ativo,
                    (SELECT COUNT(*) FROM encomendas e WHERE e.morador_id = moradores.id AND e.status = 'pendente') AS pendentes
                FROM moradores
                WHERE {status_clause}
            """
            params = {"limit": limit, "skip": skip}

            count_query = f"SELECT COUNT(*) as total FROM moradores WHERE {status_clause}"
            count_params = {}
            if condominio_id:  # 2026-10-08: só os moradores do condomínio escolhido
                query += " AND condominio_id = :cid"
                count_query += " AND condominio_id = :cid"
                params["cid"] = condominio_id
                count_params["cid"] = condominio_id
        else:
            query = f"""
                SELECT id, nome, apartamento, bloco, telefone, whats_confirmado, email, condominio_id, ativo,
                    (SELECT COUNT(*) FROM encomendas e WHERE e.morador_id = moradores.id AND e.status = 'pendente') AS pendentes
                FROM moradores
                WHERE condominio_id = :condominio_id AND {status_clause}
            """
            params = {
                "condominio_id": current_user["condominio_id"],
                "limit": limit,
                "skip": skip
            }

            count_query = f"SELECT COUNT(*) as total FROM moradores WHERE condominio_id = :condominio_id AND {status_clause}"
            count_params = {"condominio_id": current_user["condominio_id"]}

        if search and len(search) >= 2:
            search_term = f"%{search}%"
            filter_clause = """
                AND (
                    LOWER(nome) LIKE LOWER(:search)
                    OR apartamento LIKE :search_apt
                )
            """
            query += filter_clause
            count_query += filter_clause
            params["search"] = search_term
            params["search_apt"] = search_term
            count_params["search"] = search_term
            count_params["search_apt"] = search_term

        order_clause = "apartamento, nome" if order_by == "apartamento" else "nome, apartamento"
        query += f" ORDER BY {order_clause} LIMIT :limit OFFSET :skip"
        result = db.execute(text(query), params)

        moradores = []
        for row in result:
            moradores.append({
                "id": row.id,
                "nome": row.nome,
                "apartamento": row.apartamento,
                "bloco": row.bloco,
                "telefone": row.telefone,
                "whats_confirmado": row.whats_confirmado.isoformat() if row.whats_confirmado else None,
                "email": row.email,
                "condominio_id": row.condominio_id,
                "ativo": row.ativo,
                "pendentes": row.pendentes if hasattr(row, "pendentes") else 0
            })

        total_result = db.execute(text(count_query), count_params).fetchone()
        total = total_result.total if total_result else 0

        return {
            "items": moradores,
            "total": total
        }

    except Exception as e:
        logger.error(f"Erro ao listar moradores: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao listar moradores: {str(e)}"
        )


@router.put("/{morador_id}")
async def update_morador(
    morador_id: int,
    morador_data: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Atualizar morador"""
    try:
        is_master = _is_master(current_user)

        check_query = text("""
            SELECT id, condominio_id FROM moradores
            WHERE id = :id
        """)

        exists = db.execute(check_query, {"id": morador_id}).fetchone()

        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Morador não encontrado"
            )

        # admin_sistema pode editar qualquer morador
        if not is_master and exists.condominio_id != current_user["condominio_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Morador não pertence ao seu condomínio"
            )

        # 2026-09-30: bloco padronizado (app/services/blocos.py)
        from app.services.blocos import normalizar_bloco
        if morador_data.get("bloco") is not None:
            morador_data["bloco"] = normalizar_bloco(db, exists.condominio_id, morador_data.get("bloco"))

        # 2026-09-30: cadastro igual a outro (uk_morador: condomínio+nome+apto+bloco) — antes dava 500.
        # Outro INATIVO: ganha "(inativo #id)" no nome e libera; outro ATIVO: 409 com a explicação.
        igual = db.execute(text("""
            SELECT id, nome, ativo FROM moradores
            WHERE condominio_id = :c AND nome = :n AND apartamento = :a AND bloco <=> :b AND id <> :id
            LIMIT 1
        """), {"c": exists.condominio_id, "n": morador_data.get("nome"), "a": morador_data.get("apartamento"),
               "b": morador_data.get("bloco"), "id": morador_id}).fetchone()
        if igual and igual.ativo == 0:
            db.execute(text("""
                UPDATE moradores SET nome = LEFT(CONCAT(nome, ' (inativo #', id, ')'), 200) WHERE id = :i
            """), {"i": igual.id})
        elif igual:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(f"Já existe um cadastro ativo de {igual.nome} no apto {morador_data.get('apartamento')}"
                        f" / bloco {morador_data.get('bloco') or '(único)'} (#{igual.id}). "
                        "Junte os dois em Unidades (Mesclar) ou inative um deles.")
            )

        update_query = text("""
            UPDATE moradores
            SET nome = :nome,
                apartamento = :apartamento,
                bloco = :bloco,
                telefone = :telefone,
                email = :email,
                ativo = :ativo,
                updated_at = NOW()
            WHERE id = :id
        """)

        db.execute(update_query, {
            "id": morador_id,
            "nome": morador_data.get("nome"),
            "apartamento": morador_data.get("apartamento"),
            "bloco": morador_data.get("bloco"),
            "telefone": morador_data.get("telefone"),
            "email": morador_data.get("email"),
            "ativo": morador_data.get("ativo", True)
        })
        db.commit()

        return await get_morador(morador_id, current_user, db)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao atualizar morador: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao atualizar morador: {str(e)}"
        )


@router.delete("/{morador_id}")
async def delete_morador(
    morador_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Desativar morador (soft delete)"""
    try:
        is_master = _is_master(current_user)

        if is_master:
            check_query = text("SELECT id FROM moradores WHERE id = :id")
            exists = db.execute(check_query, {"id": morador_id}).fetchone()
        else:
            check_query = text("SELECT id FROM moradores WHERE id = :id AND condominio_id = :condominio_id")
            exists = db.execute(check_query, {
                "id": morador_id,
                "condominio_id": current_user["condominio_id"]
            }).fetchone()

        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Morador não encontrado"
            )

        update_query = text("UPDATE moradores SET ativo = 0 WHERE id = :id")
        db.execute(update_query, {"id": morador_id})
        db.commit()

        return {"message": "Morador desativado com sucesso"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao desativar morador: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao desativar morador: {str(e)}"
        )


@router.patch("/{morador_id}/reativar")
async def reativar_morador(
    morador_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Reativar morador (reverte soft delete)"""
    try:
        is_master = _is_master(current_user)

        if is_master:
            check_query = text("SELECT id FROM moradores WHERE id = :id")
            exists = db.execute(check_query, {"id": morador_id}).fetchone()
        else:
            check_query = text("SELECT id FROM moradores WHERE id = :id AND condominio_id = :condominio_id")
            exists = db.execute(check_query, {
                "id": morador_id,
                "condominio_id": current_user["condominio_id"]
            }).fetchone()

        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Morador não encontrado"
            )

        update_query = text("UPDATE moradores SET ativo = 1 WHERE id = :id")
        db.execute(update_query, {"id": morador_id})
        db.commit()

        return {"message": "Morador reativado com sucesso"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao reativar morador {morador_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao reativar morador: {str(e)}"
        )


@router.post("/check")
async def check_morador(
    params: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Verificar se morador existe"""
    try:
        nome = params.get("nome", "")
        apartamento = params.get("apartamento", "")
        bloco = params.get("bloco", "")

        if not nome or not apartamento:
            return {"existe": False}

        condominio_id = params.get("condominio_id") or current_user["condominio_id"]

        query = text("""
            SELECT id, nome, telefone, whats_confirmado FROM moradores
            WHERE condominio_id = :condominio_id
            AND LOWER(nome) = LOWER(:nome)
            AND apartamento = :apartamento
            AND (bloco = :bloco OR (:bloco = '' AND bloco IS NULL))
            AND ativo = 1
        """)

        result = db.execute(query, {
            "condominio_id": condominio_id,
            "nome": nome,
            "apartamento": apartamento,
            "bloco": bloco
        }).fetchone()

        if result:
            return {
                "existe": True,
                "id": result.id,
                "nome": result.nome,
                "telefone": result.telefone,
                "whats_confirmado": result.whats_confirmado is not None
            }
        else:
            return {"existe": False}

    except Exception as e:
        logger.error(f"Erro ao verificar morador: {str(e)}")
        return {"existe": False, "error": str(e)}
