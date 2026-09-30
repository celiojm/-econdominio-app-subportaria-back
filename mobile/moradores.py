# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/mobile/moradores.py
# DESCRIÇÃO: Endpoints de moradores para API mobile
# ==============================================================================

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
import mysql.connector
from auth import get_current_user
from database import get_db_connection
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/moradores", tags=["moradores"])

@router.get("/buscar")
async def buscar_moradores(
    q: str = Query("", description="Termo de busca"),
    condominio_id: Optional[int] = Query(None, description="ID do condomínio (opcional, vem do JWT)"),
    current_user: dict = Depends(get_current_user)
):
    """Buscar moradores por nome ou apartamento - compatível com frontend"""
    try:
        # Frontend pode enviar condominio_id, mas usamos sempre o do JWT
        cond_id = current_user["condominio_id"]
        
        if not q or len(q) < 2:
            return []
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        query = """
            SELECT id, nome, apartamento, bloco, telefone, whats_confirmado, email
            FROM moradores
            WHERE condominio_id = %s
            AND ativo = 1
            AND (
                LOWER(nome) LIKE LOWER(%s)
                OR apartamento LIKE %s
            )
            ORDER BY nome
            LIMIT 20
        """
        
        search_term = f"%{q}%"
        cursor.execute(query, (cond_id, search_term, search_term))
        moradores = cursor.fetchall()
        
        # Formatar whats_confirmado
        for m in moradores:
            if m.get('whats_confirmado'):
                m['whats_confirmado'] = m['whats_confirmado'].isoformat()
        
        cursor.close()
        conn.close()
        
        return moradores
        
    except mysql.connector.Error as e:
        logger.error(f"Erro ao buscar moradores: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")

@router.get("/")
async def list_moradores(
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: dict = Depends(get_current_user)
):
    """Listar moradores do condomínio"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        query = """
            SELECT id, nome, apartamento, bloco, telefone, whats_confirmado, email, condominio_id, ativo
            FROM moradores
            WHERE condominio_id = %s AND ativo = 1
        """
        params = [current_user["condominio_id"]]
        
        # Adicionar busca se fornecida
        if search and len(search) >= 2:
            query += """ AND (
                LOWER(nome) LIKE LOWER(%s)
                OR apartamento LIKE %s
            )"""
            search_term = f"%{search}%"
            params.extend([search_term, search_term])
        
        query += " ORDER BY apartamento, nome LIMIT %s OFFSET %s"
        params.extend([limit, skip])
        
        cursor.execute(query, params)
        moradores = cursor.fetchall()
        
        # Formatar whats_confirmado
        for m in moradores:
            if m.get('whats_confirmado'):
                m['whats_confirmado'] = m['whats_confirmado'].isoformat()
        
        # Contar total
        count_query = "SELECT COUNT(*) as total FROM moradores WHERE condominio_id = %s AND ativo = 1"
        count_params = [current_user["condominio_id"]]
        
        if search and len(search) >= 2:
            count_query += " AND (LOWER(nome) LIKE LOWER(%s) OR apartamento LIKE %s)"
            count_params.extend([search_term, search_term])
        
        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['total']
        
        cursor.close()
        conn.close()
        
        return {
            "items": moradores,
            "total": total
        }
        
    except mysql.connector.Error as e:
        logger.error(f"Erro ao listar moradores: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")

@router.get("/{morador_id}")
async def get_morador(
    morador_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Obter dados de um morador específico"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        query = """
            SELECT id, nome, apartamento, bloco, telefone, whats_confirmado, email, condominio_id, ativo
            FROM moradores
            WHERE id = %s AND condominio_id = %s
        """
        
        cursor.execute(query, (morador_id, current_user["condominio_id"]))
        morador = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if not morador:
            raise HTTPException(status_code=404, detail="Morador não encontrado")
        
        # Formatar whats_confirmado
        if morador.get('whats_confirmado'):
            morador['whats_confirmado'] = morador['whats_confirmado'].isoformat()
        
        return morador
        
    except mysql.connector.Error as e:
        logger.error(f"Erro ao obter morador: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")

@router.post("/check")
async def check_morador(
    params: dict,
    current_user: dict = Depends(get_current_user)
):
    """Verificar se morador existe"""
    try:
        nome = params.get("nome", "")
        apartamento = params.get("apartamento", "")
        bloco = params.get("bloco", "")
        
        if not nome or not apartamento:
            return {"existe": False}
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        query = """
            SELECT id, nome, telefone, whats_confirmado FROM moradores
            WHERE condominio_id = %s
            AND LOWER(nome) = LOWER(%s)
            AND apartamento = %s
            AND (bloco = %s OR (%s = '' AND bloco IS NULL))
            AND ativo = 1
        """
        
        cursor.execute(query, (
            current_user["condominio_id"],
            nome,
            apartamento,
            bloco,
            bloco
        ))
        
        result = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if result:
            return {
                "existe": True,
                "id": result['id'],
                "nome": result['nome'],
                "telefone": result['telefone'],
                "whats_confirmado": result['whats_confirmado'] is not None
            }
        else:
            return {"existe": False}
            
    except Exception as e:
        logger.error(f"Erro ao verificar morador: {str(e)}")
        return {"existe": False, "error": str(e)}

@router.post("/")
async def create_morador(
    morador_data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Criar novo morador"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Verificar se já existe
        check_query = """
            SELECT id FROM moradores
            WHERE condominio_id = %s
            AND LOWER(nome) = LOWER(%s)
            AND apartamento = %s
            AND (bloco = %s OR (%s = '' AND bloco IS NULL))
        """
        
        bloco = morador_data.get("bloco", "")
        cursor.execute(check_query, (
            current_user["condominio_id"],
            morador_data.get("nome"),
            morador_data.get("apartamento"),
            bloco,
            bloco
        ))
        
        if cursor.fetchone():
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Morador já cadastrado")
        
        # Inserir novo morador
        insert_query = """
            INSERT INTO moradores (
                condominio_id, nome, apartamento, bloco, telefone, email,
                condominio_nome, data_cadastro, ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), 1)
        """
        
        cursor.execute(insert_query, (
            current_user["condominio_id"],
            morador_data.get("nome"),
            morador_data.get("apartamento"),
            morador_data.get("bloco", ""),
            morador_data.get("telefone", ""),
            morador_data.get("email", ""),
            current_user.get("condominio_nome", "")
        ))
        
        conn.commit()
        morador_id = cursor.lastrowid
        
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "id": morador_id,
            "message": "Morador criado com sucesso"
        }
        
    except HTTPException:
        raise
    except mysql.connector.Error as e:
        logger.error(f"Erro ao criar morador: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")

@router.put("/{morador_id}/confirmar-whatsapp")
@router.post("/{morador_id}/confirmar-whatsapp")
async def confirmar_whatsapp(
    morador_id: int,
    data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Confirmar WhatsApp do morador"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Verificar se morador existe e pertence ao condomínio
        cursor.execute("""
            SELECT id, condominio_id FROM moradores WHERE id = %s
        """, (morador_id,))
        
        morador = cursor.fetchone()
        
        if not morador:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Morador não encontrado")
        
        if morador['condominio_id'] != current_user["condominio_id"]:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=403, detail="Morador não pertence ao seu condomínio")
        
        telefone = data.get("telefone")
        if not telefone:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Telefone é obrigatório")
        
        # Limpar telefone
        telefone_limpo = ''.join(filter(str.isdigit, telefone))
        
        # Atualizar
        cursor.execute("""
            UPDATE moradores
            SET telefone = %s, whats_confirmado = NOW()
            WHERE id = %s
        """, (telefone_limpo, morador_id))
        
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "message": "WhatsApp confirmado com sucesso"
        }
        
    except HTTPException:
        raise
    except mysql.connector.Error as e:
        logger.error(f"Erro ao confirmar WhatsApp: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")
