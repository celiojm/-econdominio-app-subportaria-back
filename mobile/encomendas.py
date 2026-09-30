# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/mobile/encomendas.py
# DESCRIÇÃO: Endpoints de encomendas para API mobile - V2.9.7 COM ASSINATURA
# CORREÇÃO V2.9.7:
# - 🔥 NEW: Endpoint /storage/upload/assinatura para upload de assinatura
# - 🔥 FIX: Padronização com /storage/upload/etiqueta
# - 🔥 FIX: Melhor tratamento de erros no upload
# - 🎯 MELHORIA: Logs detalhados para debug
# ==============================================================================

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from datetime import datetime
import mysql.connector
from pydantic import BaseModel
from auth import get_current_user
from database import get_db_connection
import logging
import requests
import os
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()

# Logger
logger = logging.getLogger(__name__)

# Router
router = APIRouter(prefix="/encomendas", tags=["encomendas"])

# ==============================================================================
# CONFIGURAÇÕES DO STORAGE (DO .ENV)
# ==============================================================================
STORAGE_API_URL = os.getenv("IMAGE_STORAGE_BASE_URL", "http://10.3.1.7:4000")
STORAGE_API_KEY = os.getenv("IMAGE_STORAGE_API_KEY", "")  # 2026-09-28: sem chave fixa no código

logger.info(f"📦 Storage configurado: {STORAGE_API_URL}")

# ==============================================================================
# SCHEMAS
# ==============================================================================

class RegistrarEncomendaRequest(BaseModel):
    morador_id: int
    nome_destinatario: str
    apartamento: str
    bloco: Optional[str] = None
    tipo: str = "encomenda"
    codigo_rastreio: Optional[str] = None
    remetente: Optional[str] = None
    observacoes: Optional[str] = None
    telefone_morador: Optional[str] = None

class EntregarEncomendaRequest(BaseModel):
    nome_retirou: Optional[str] = None
    notificar_whatsapp: bool = True
    observacoes_entrega: Optional[str] = None

class UploadAssinaturaRequest(BaseModel):
    image: str
    encomenda_id: int

# ==============================================================================
# FUNÇÕES AUXILIARES PARA STORAGE
# ==============================================================================

def upload_imagem_para_storage(base64_image: str, filename: str) -> Optional[str]:
    """
    Upload genérico de imagem para o storage
    Retorna: nome do arquivo se sucesso, None se falha
    """
    try:
        # Limpar base64 se necessário
        if base64_image.startswith('data:image'):
            base64_image = base64_image.split(',')[1]

        payload = {
            "image": f"data:image/jpeg;base64,{base64_image}",
            "filename": filename
        }

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": STORAGE_API_KEY
        }

        logger.info(f"📤 Enviando imagem para storage: {STORAGE_API_URL}/upload/base64")
        logger.info(f"   Filename: {filename}")

        response = requests.post(
            f"{STORAGE_API_URL}/upload/base64",
            json=payload,
            headers=headers,
            timeout=30
        )

        logger.info(f"📥 Resposta do storage: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                logger.info(f"✅ Imagem salva com sucesso: {filename}")
                return filename
            else:
                logger.error(f"❌ Storage retornou success=false: {result}")
                return None

        logger.error(f"❌ Erro HTTP do storage: {response.status_code} - {response.text}")
        return None

    except requests.exceptions.Timeout:
        logger.error(f"❌ Timeout ao conectar no storage após 30s")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.error(f"❌ Erro de conexão com storage: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"❌ Exceção ao salvar imagem: {str(e)}")
        return None

# ==============================================================================
# 🔥 NOVO ENDPOINT: UPLOAD DE ASSINATURA (PADRÃO RECEBER.TSX)
# ==============================================================================

@router.post("/storage/upload/assinatura")
async def upload_assinatura(
    dados: UploadAssinaturaRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Upload de assinatura digital para o storage
    Endpoint específico seguindo padrão do /storage/upload/etiqueta
    """
    try:
        logger.info(f"📸 === INICIANDO UPLOAD DE ASSINATURA ===")
        logger.info(f"   Encomenda ID: {dados.encomenda_id}")
        logger.info(f"   Usuário: {current_user.get('nome', 'N/A')}")

        # Verificar se encomenda existe e pertence ao condomínio do usuário
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, condominio_id 
            FROM encomendas 
            WHERE id = %s
        """, (dados.encomenda_id,))

        encomenda = cursor.fetchone()

        if not encomenda:
            cursor.close()
            conn.close()
            logger.error(f"❌ Encomenda {dados.encomenda_id} não encontrada")
            raise HTTPException(status_code=404, detail="Encomenda não encontrada")

        if encomenda['condominio_id'] != current_user.get("condominio_id"):
            cursor.close()
            conn.close()
            logger.error(f"❌ Encomenda não pertence ao condomínio do usuário")
            raise HTTPException(status_code=403, detail="Sem permissão para esta encomenda")

        # Gerar nome do arquivo
        filename = f"ass_{dados.encomenda_id}.jpg"

        logger.info(f"📤 Enviando assinatura para storage: {filename}")

        # Fazer upload para o storage
        resultado = upload_imagem_para_storage(dados.image, filename)

        if not resultado:
            cursor.close()
            conn.close()
            logger.error(f"❌ Falha ao enviar para storage")
            raise HTTPException(
                status_code=500,
                detail="Erro ao salvar assinatura no storage"
            )

        # Registrar no mapeamento
        try:
            cursor.execute("""
                INSERT INTO imagens_mapeamento 
                (encomenda_id, tipo, nome_personalizado, nome_servidor)
                VALUES (%s, 'assinatura', %s, %s)
                ON DUPLICATE KEY UPDATE
                nome_servidor = VALUES(nome_servidor),
                data_upload = CURRENT_TIMESTAMP
            """, (dados.encomenda_id, filename, filename))

            conn.commit()
            logger.info(f"✅ Assinatura registrada no mapeamento")

        except mysql.connector.Error as e:
            logger.error(f"❌ Erro ao registrar no mapeamento: {str(e)}")
            # Não falhar se apenas o mapeamento falhou
            pass

        cursor.close()
        conn.close()

        logger.info(f"✅ === UPLOAD DE ASSINATURA CONCLUÍDO ===")

        return {
            "success": True,
            "message": "Assinatura salva com sucesso",
            "filename": filename,
            "encomenda_id": dados.encomenda_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao processar upload de assinatura: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao processar upload: {str(e)}"
        )

# ==============================================================================
# ENDPOINTS EXISTENTES
# ==============================================================================

@router.get("/")
async def list_encomendas(
    status_filter: Optional[str] = Query(None, description="Filtro de status"),
    status: Optional[str] = Query(None, description="Filtro de status (alias)"),
    search: Optional[str] = Query(None, description="Termo de busca"),
    q: Optional[str] = Query(None, description="Termo de busca (alias)"),
    condominio_id: Optional[int] = Query(None, description="ID do condomínio (opcional)"),
    skip: int = 0,
    limit: int = 100,
    current_user: dict = Depends(get_current_user)
):
    """Listar encomendas do condomínio - compatível com frontend"""
    try:
        status_param = status_filter or status
        search_param = search or q
        cond_id = current_user["condominio_id"]

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        query = """
            SELECT e.*
            FROM encomendas e
            WHERE e.condominio_id = %s
        """
        params = [cond_id]

        if status_param and status_param not in ["all", "Todas"]:
            query += " AND e.status = %s"
            params.append(status_param)

        if search_param:
            query += """ AND (
                e.nome_destinatario LIKE %s
                OR e.apartamento LIKE %s
                OR e.codigo_rastreio LIKE %s
                OR e.bloco LIKE %s
            )"""
            search_term = f"%{search_param}%"
            params.extend([search_term, search_term, search_term, search_term])

        query += " ORDER BY e.data_recebimento DESC LIMIT %s OFFSET %s"
        params.extend([limit, skip])

        cursor.execute(query, params)
        encomendas = []

        for row in cursor.fetchall():
            encomenda = {
                "id": row['id'],
                "condominio_id": row['condominio_id'],
                "nome_destinatario": row.get('nome_destinatario'),
                "apartamento": row.get('apartamento'),
                "bloco": row.get('bloco'),
                "codigo_rastreio": row.get('codigo_rastreio'),
                "remetente": row.get('remetente'),
                "status": row['status'],
                "data_recebimento": row['data_recebimento'].isoformat() if row.get('data_recebimento') else None,
                "data_entrega": row['data_entrega'].isoformat() if row.get('data_entrega') else None,
                "observacoes": row.get('observacoes'),
                "telefone_morador": row.get('telefone_morador'),
                "morador_id": row.get('morador_id'),
                "nome_retirou": row.get('nome_retirou'),
            }
            encomendas.append(encomenda)

        # Contar total
        count_query = "SELECT COUNT(*) as total FROM encomendas WHERE condominio_id = %s"
        count_params = [cond_id]

        if status_param and status_param not in ["all", "Todas"]:
            count_query += " AND status = %s"
            count_params.append(status_param)

        if search_param:
            count_query += " AND (nome_destinatario LIKE %s OR apartamento LIKE %s OR codigo_rastreio LIKE %s)"
            count_params.extend([search_term, search_term, search_term])

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['total']

        cursor.close()
        conn.close()

        return {"total": total, "items": encomendas}

    except mysql.connector.Error as e:
        logger.error(f"Erro ao listar encomendas: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")

@router.get("/dashboard/stats")
async def get_dashboard_stats(
    current_user: dict = Depends(get_current_user)
):
    """Obter estatísticas de encomendas"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cond_id = current_user["condominio_id"]

        cursor.execute("SELECT COUNT(*) as total FROM encomendas WHERE condominio_id = %s", (cond_id,))
        total = cursor.fetchone()['total']

        cursor.execute("SELECT COUNT(*) as total FROM encomendas WHERE condominio_id = %s AND status = 'pendente'", (cond_id,))
        pendentes = cursor.fetchone()['total']

        cursor.execute("SELECT COUNT(*) as total FROM encomendas WHERE condominio_id = %s AND status = 'entregue'", (cond_id,))
        entregues = cursor.fetchone()['total']

        cursor.execute("SELECT COUNT(*) as total FROM encomendas WHERE condominio_id = %s AND status = 'cancelada'", (cond_id,))
        canceladas = cursor.fetchone()['total']

        cursor.execute("SELECT COUNT(*) as total FROM encomendas WHERE condominio_id = %s AND DATE(data_recebimento) = CURDATE()", (cond_id,))
        recebidas_hoje = cursor.fetchone()['total']

        cursor.execute("SELECT COUNT(*) as total FROM encomendas WHERE condominio_id = %s AND status = 'entregue' AND DATE(data_entrega) = CURDATE()", (cond_id,))
        entregues_hoje = cursor.fetchone()['total']

        cursor.close()
        conn.close()

        return {
            "total_encomendas": total,
            "pendentes": pendentes,
            "entregues": entregues,
            "canceladas": canceladas,
            "recebidas_hoje": recebidas_hoje,
            "entregues_hoje": entregues_hoje
        }

    except Exception as e:
        logger.error(f"Erro ao obter estatísticas: {str(e)}")
        return {
            "total_encomendas": 0,
            "pendentes": 0,
            "entregues": 0,
            "canceladas": 0,
            "recebidas_hoje": 0,
            "entregues_hoje": 0
        }

@router.get("/{encomenda_id}")
async def get_encomenda(
    encomenda_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Obter detalhes de uma encomenda"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT e.* FROM encomendas e
            WHERE e.id = %s AND e.condominio_id = %s
        """, (encomenda_id, current_user["condominio_id"]))

        encomenda = cursor.fetchone()

        cursor.close()
        conn.close()

        if not encomenda:
            raise HTTPException(status_code=404, detail="Encomenda não encontrada")

        if encomenda.get('data_recebimento'):
            encomenda['data_recebimento'] = encomenda['data_recebimento'].isoformat()
        if encomenda.get('data_entrega'):
            encomenda['data_entrega'] = encomenda['data_entrega'].isoformat()

        return encomenda

    except mysql.connector.Error as e:
        logger.error(f"Erro ao obter encomenda: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")

@router.post("/")
async def create_encomenda(
    encomenda: RegistrarEncomendaRequest,
    current_user: dict = Depends(get_current_user)
):
    """Registrar nova encomenda"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT condominio_id FROM moradores WHERE id = %s AND ativo = 1
        """, (encomenda.morador_id,))

        morador = cursor.fetchone()

        if not morador:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Morador não encontrado")

        if morador['condominio_id'] != current_user["condominio_id"]:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=403, detail="Morador não pertence ao seu condomínio")

        cursor.execute("""
            INSERT INTO encomendas (
                condominio_id, morador_id, nome_destinatario, apartamento, bloco,
                codigo_rastreio, remetente, status, data_recebimento, observacoes,
                telefone_morador
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pendente', NOW(), %s, %s)
        """, (
            current_user["condominio_id"],
            encomenda.morador_id,
            encomenda.nome_destinatario,
            encomenda.apartamento,
            encomenda.bloco,
            encomenda.codigo_rastreio,
            encomenda.remetente,
            encomenda.observacoes,
            encomenda.telefone_morador
        ))

        conn.commit()
        encomenda_id = cursor.lastrowid

        cursor.close()
        conn.close()

        return {
            "success": True,
            "encomenda_id": encomenda_id,
            "message": "Encomenda registrada com sucesso"
        }

    except HTTPException:
        raise
    except mysql.connector.Error as e:
        logger.error(f"Erro ao registrar encomenda: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")

@router.put("/{encomenda_id}/entregar")
async def entregar_encomenda(
    encomenda_id: int,
    entrega: EntregarEncomendaRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Marcar encomenda como entregue - V2.9.7
    A assinatura é enviada separadamente via /storage/upload/assinatura
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Verificar se encomenda existe
        cursor.execute("""
            SELECT e.*, m.telefone, m.whats_confirmado
            FROM encomendas e
            LEFT JOIN moradores m ON e.morador_id = m.id
            WHERE e.id = %s AND e.condominio_id = %s
        """, (encomenda_id, current_user["condominio_id"]))

        encomenda = cursor.fetchone()

        if not encomenda:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Encomenda não encontrada")

        if encomenda['status'] == 'entregue':
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Encomenda já foi entregue")

        nome_retirou = entrega.nome_retirou or encomenda['nome_destinatario']
        obs_entrega = entrega.observacoes_entrega or f"Entregue para: {nome_retirou}"

        # Atualizar encomenda
        cursor.execute("""
            UPDATE encomendas
            SET status = 'entregue',
                data_entrega = NOW(),
                nome_retirou = %s,
                observacoes = CONCAT(IFNULL(observacoes, ''), '\n[', DATE_FORMAT(NOW(), '%%d/%%m/%%Y %%H:%%i'), '] ', %s)
            WHERE id = %s
        """, (nome_retirou, obs_entrega, encomenda_id))

        # Confirmar WhatsApp se for a primeira entrega
        if encomenda.get('morador_id') and not encomenda.get('whats_confirmado'):
            cursor.execute("""
                UPDATE moradores SET whats_confirmado = NOW() WHERE id = %s
            """, (encomenda['morador_id'],))

        conn.commit()

        cursor.close()
        conn.close()

        logger.info(f"✅ Entrega registrada - Encomenda {encomenda_id}")

        return {
            "success": True,
            "message": "Encomenda marcada como entregue",
            "id": encomenda_id,
            "status": "entregue"
        }

    except HTTPException:
        raise
    except mysql.connector.Error as e:
        logger.error(f"Erro ao entregar encomenda: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")

@router.put("/{encomenda_id}/cancelar")
async def cancelar_encomenda(
    encomenda_id: int,
    cancelamento_data: dict = {},
    current_user: dict = Depends(get_current_user)
):
    """Cancelar encomenda"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, status FROM encomendas WHERE id = %s AND condominio_id = %s
        """, (encomenda_id, current_user["condominio_id"]))

        encomenda = cursor.fetchone()

        if not encomenda:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Encomenda não encontrada")

        if encomenda['status'] == "cancelada":
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Encomenda já está cancelada")

        motivo = cancelamento_data.get("motivo", "Cancelada pelo operador")

        cursor.execute("""
            UPDATE encomendas
            SET status = 'cancelada',
                observacoes = CONCAT(IFNULL(observacoes, ''), '\n[', DATE_FORMAT(NOW(), '%%d/%%m/%%Y %%H:%%i'), '] CANCELADA: ', %s)
            WHERE id = %s
        """, (motivo, encomenda_id))

        conn.commit()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": "Encomenda cancelada",
            "id": encomenda_id
        }

    except HTTPException:
        raise
    except mysql.connector.Error as e:
        logger.error(f"Erro ao cancelar encomenda: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")

@router.delete("/{encomenda_id}")
async def delete_encomenda(
    encomenda_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Excluir encomenda"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT id FROM encomendas WHERE id = %s AND condominio_id = %s
        """, (encomenda_id, current_user["condominio_id"]))

        if not cursor.fetchone():
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Encomenda não encontrada")

        cursor.execute("DELETE FROM encomendas WHERE id = %s", (encomenda_id,))
        conn.commit()

        cursor.close()
        conn.close()

        return {"message": "Encomenda excluída com sucesso"}

    except HTTPException:
        raise
    except mysql.connector.Error as e:
        logger.error(f"Erro ao excluir encomenda: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {str(e)}")
