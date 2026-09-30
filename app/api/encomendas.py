# ~/encomenda_v2/backend/app/api/encomendas.py - V2.6.0 - SUPERMASTER
# ALTERAÇÃO 2026-09-27: régua de cobrança — bloqueio de recebimento (flag BLOQUEIO_ASSINATURA_ENABLED)
# CORREÇÕES V2.6.0:
# - ✅ FIX: _resolve_cond_id retorna None para admin_sistema sem filtro → vê tudo
# - ✅ FIX: list_encomendas sem WHERE condominio_id quando admin_sistema
# - ✅ FIX: dashboard/stats sem WHERE condominio_id quando admin_sistema
# - ✅ FIX: import http_status separado para não conflitar com parâmetro status
# MANTÉM V2.5.0:
# - ✅ FIX: admin_sistema pode filtrar por condominio_id em /encomendas e /dashboard/stats
# MANTÉM V2.4.0:
# - ✅ FIX: Dashboard stats retorna recebidas_ontem, recebidas_mes, encomendas_atrasadas
# MANTÉM V2.3.9:
# - 🔥 FIX: Aceita tanto ?status=pendente quanto ?status_filter=pendente
# - 🔥 FIX: Aceita ?condominio_id= para admin_sistema filtrar por condomínio
# - 🔥 FIX: Aceita ?q= como alias de ?search= para busca
# MANTÉM V2.3.8:
# - 🚀 WhatsApp no create_encomenda → background (asyncio.create_task)
# - 🚀 WhatsApp no entregar_encomenda → background (asyncio.create_task)
# - 🚀 Response volta instantâneo para o frontend
# MANTÉM V2.3.7:
# - 🔥 FIX CRÍTICO: list_encomendas não busca URLs de imagens no loop

from app.services.image_storage_service import image_storage_service
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import json
import asyncio
import random
import logging
from app.database import get_db
from app.services.assinatura_situacao import checar_bloqueio_recebimento
from app.config import settings
from app.services.whatsapp import whatsapp_service
from app.api.auth import get_current_user
from app.schemas import (
    EncomendaCreate, EncomendaResponse, EncomendaUpdate,
    EncomendaEntrega, DashboardStats
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# HELPER: resolve condominio_id correto
# Retorna None quando admin_sistema sem filtro → sem WHERE condominio_id
# ============================================================

def _resolve_cond_id(current_user, condominio_id_param: Optional[int]) -> Optional[int]:
    """
    Suporta current_user como dict ou objeto Pydantic.
    Retorna None quando admin_sistema sem filtro → sem WHERE condominio_id.
    """
    if isinstance(current_user, dict):
        role = current_user.get("role", "")
        cond_id = current_user.get("condominio_id") or current_user.get("id_condominio")
        nivel = current_user.get("nivel_id") or current_user.get("nivel")
    else:
        role = getattr(current_user, 'role', '')
        cond_id = getattr(current_user, 'id_condominio', None) or getattr(current_user, 'condominio_id', None)
        nivel = getattr(current_user, 'nivel_id', None)

    is_master = (role == "admin_sistema" or nivel == 1 or int(cond_id or 0) == 0)

    if is_master and condominio_id_param:
        return condominio_id_param  # filtra por condomínio específico
    if is_master:
        return None  # sem filtro → vê tudo
    return cond_id


def _extract_operador_id(current_user) -> Optional[int]:
    """
    Extrai o id do operador logado, cobrindo os dois formatos de token
    ja usados neste arquivo (ver blocos inline em create_encomenda /
    entregar_encomenda): claim 'user_id' (login legado) ou 'sub' /
    'username' numerico (login mobile).
    """
    for _c in ('user_id', 'id', 'sub', 'username'):
        _v = current_user.get(_c) if isinstance(current_user, dict) else getattr(current_user, _c, None)
        if _v is not None and str(_v).isdigit():
            return int(str(_v))
    return None


# ============================================================
# LISTAR ENCOMENDAS - V2.6.0
# ============================================================

@router.get("/")
async def list_encomendas(
    status_filter: Optional[str] = Query(None, description="Filtro de status (alias 1)"),
    status: Optional[str] = Query(None, alias="status", description="Filtro de status (alias 2)"),
    search: Optional[str] = Query(None, description="Busca por nome/apto/codigo"),
    q: Optional[str] = Query(None, description="Busca por nome/apto/codigo (alias)"),
    condominio_id: Optional[int] = Query(None, description="ID do condomínio (admin_sistema pode filtrar)"),
    skip: int = Query(0),
    limit: int = Query(100),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Listar encomendas - V2.6.0 - admin_sistema vê tudo"""
    try:
        cond_id = _resolve_cond_id(current_user, condominio_id)

        if cond_id is not None:
            query = "SELECT e.* FROM encomendas e WHERE e.condominio_id = :condominio_id"
            params = {"condominio_id": cond_id}
        else:
            query = "SELECT e.* FROM encomendas e WHERE 1=1"
            params = {}

        # Aceitar tanto status_filter quanto status
        effective_status = status_filter or status
        if effective_status and effective_status not in ["all", "Todas"]:
            query += " AND e.status = :status"
            params["status"] = effective_status

        # Aceitar tanto search quanto q
        effective_search = search or q
        if effective_search:
            query += """ AND (
                e.nome_destinatario LIKE :search
                OR e.apartamento LIKE :search
                OR e.codigo_rastreio LIKE :search
                OR e.bloco LIKE :search
            )"""
            params["search"] = f"%{effective_search}%"

        query += " ORDER BY e.data_recebimento DESC LIMIT :limit OFFSET :skip"
        params["limit"] = limit
        params["skip"] = skip

        result = db.execute(text(query), params)
        encomendas = []

        for row in result:
            encomenda = {
                "id": row.id,
                "condominio_id": row.condominio_id,
                "nome_destinatario": row.nome_destinatario,
                "apartamento": row.apartamento,
                "bloco": getattr(row, 'bloco', None),
                "codigo_rastreio": getattr(row, 'codigo_rastreio', None),
                "remetente": getattr(row, 'remetente', None),
                "status": row.status,
                "data_recebimento": row.data_recebimento.isoformat() if row.data_recebimento else None,
                "data_entrega": row.data_entrega.isoformat() if hasattr(row, 'data_entrega') and row.data_entrega else None,
                "observacoes": getattr(row, 'observacoes', None),
                "telefone_morador": getattr(row, 'telefone_morador', None),
                "morador_id": getattr(row, 'morador_id', None),
                "nome_retirou": getattr(row, 'nome_retirou', None),
                "img_etiqueta": getattr(row, 'img_etiqueta', None),
                "img_etiqueta_server": getattr(row, 'img_etiqueta_server', None),
                "codigo_retirada": getattr(row, 'codigo_retirada', None),
            }
            encomendas.append(encomenda)

        # Contar total
        if cond_id is not None:
            count_query = "SELECT COUNT(*) as total FROM encomendas WHERE condominio_id = :condominio_id"
            count_params = {"condominio_id": cond_id}
        else:
            count_query = "SELECT COUNT(*) as total FROM encomendas WHERE 1=1"
            count_params = {}

        if effective_status and effective_status not in ["all", "Todas"]:
            count_query += " AND status = :status"
            count_params["status"] = effective_status
        if effective_search:
            count_query += " AND (nome_destinatario LIKE :search OR apartamento LIKE :search OR codigo_rastreio LIKE :search)"
            count_params["search"] = f"%{effective_search}%"

        total = db.execute(text(count_query), count_params).scalar() or 0

        return {"total": total, "items": encomendas}

    except Exception as e:
        logger.error(f"Erro ao listar encomendas: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao listar encomendas: {str(e)}"
        )


# ============================================================
# DASHBOARD STATS - V2.6.0 COM FILTRO POR CONDOMINIO
# DEVE VIR ANTES DE /{encomenda_id}
# ============================================================

@router.get("/dashboard/stats")
async def get_dashboard_stats(
    condominio_id: Optional[int] = Query(None, description="ID do condomínio (admin_sistema pode filtrar)"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Obter estatísticas de encomendas - V2.6.0"""
    try:
        cond_id = _resolve_cond_id(current_user, condominio_id)

        w = "WHERE condominio_id = :c" if cond_id is not None else "WHERE 1=1"
        p = {"c": cond_id} if cond_id is not None else {}

        total = db.execute(text(f"SELECT COUNT(*) FROM encomendas {w}"), p).scalar() or 0
        pendentes = db.execute(text(f"SELECT COUNT(*) FROM encomendas {w} AND status = 'pendente'"), p).scalar() or 0
        entregues = db.execute(text(f"SELECT COUNT(*) FROM encomendas {w} AND status = 'entregue'"), p).scalar() or 0
        canceladas = db.execute(text(f"SELECT COUNT(*) FROM encomendas {w} AND status = 'cancelada'"), p).scalar() or 0
        recebidas_hoje = db.execute(text(f"SELECT COUNT(*) FROM encomendas {w} AND DATE(data_recebimento) = CURDATE()"), p).scalar() or 0
        entregues_hoje = db.execute(text(f"SELECT COUNT(*) FROM encomendas {w} AND status = 'entregue' AND DATE(data_entrega) = CURDATE()"), p).scalar() or 0
        recebidas_ontem = db.execute(text(f"SELECT COUNT(*) FROM encomendas {w} AND DATE(data_recebimento) = DATE_SUB(CURDATE(), INTERVAL 1 DAY)"), p).scalar() or 0
        recebidas_mes = db.execute(text(f"SELECT COUNT(*) FROM encomendas {w} AND YEAR(data_recebimento) = YEAR(CURDATE()) AND MONTH(data_recebimento) = MONTH(CURDATE())"), p).scalar() or 0

        encomendas_atrasadas = db.execute(text(f"SELECT COUNT(*) FROM encomendas {w} AND status = 'pendente' AND DATEDIFF(CURDATE(), DATE(data_recebimento)) > 3"), p).scalar() or 0

        # ── Tempo médio de retirada (horas), só entregues ──────────────
        tempo_medio_row = db.execute(text(f"""
            SELECT AVG(TIMESTAMPDIFF(HOUR, data_recebimento, data_entrega)) AS media
            FROM encomendas {w} AND status = 'entregue' AND data_entrega IS NOT NULL
        """), p).scalar()
        tempo_medio_entrega_horas = round(float(tempo_medio_row), 1) if tempo_medio_row else 0

        # ── Movimento diário últimos 30 dias (dataset completo, não só 100) ──
        recebidas_rows = db.execute(text(f"""
            SELECT DATE(data_recebimento) AS dia, COUNT(*) AS total
            FROM encomendas {w} AND data_recebimento >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY DATE(data_recebimento)
        """), p).fetchall()
        entregues_rows = db.execute(text(f"""
            SELECT DATE(data_entrega) AS dia, COUNT(*) AS total
            FROM encomendas {w} AND status = 'entregue' AND data_entrega >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY DATE(data_entrega)
        """), p).fetchall()
        mapa_receb = {str(r.dia): r.total for r in recebidas_rows}
        mapa_entr = {str(r.dia): r.total for r in entregues_rows}
        movimento_semanal = []
        for i in range(29, -1, -1):
            dia = (datetime.now() - timedelta(days=i)).date()
            dia_str = str(dia)
            movimento_semanal.append({
                "data": dia.strftime("%d/%m"),
                "recebidas": mapa_receb.get(dia_str, 0),
                "entregues": mapa_entr.get(dia_str, 0),
            })

        # ── Heatmap dia-da-semana x hora (últimos 90 dias) ──────────────
        heatmap_rows = db.execute(text(f"""
            SELECT (DAYOFWEEK(data_recebimento) - 1) AS dia_semana,
                   HOUR(data_recebimento) AS hora,
                   COUNT(*) AS total
            FROM encomendas {w} AND data_recebimento >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
            GROUP BY dia_semana, hora
        """), p).fetchall()
        heatmap_horario = [
            {"dia_semana": int(r.dia_semana), "hora": int(r.hora), "total": r.total}
            for r in heatmap_rows
        ]
        # ── Entregues nos últimos 90 dias (não altera o campo `entregues` original) ──
        # ── Taxa de confirmação WhatsApp (últimos 90 dias) ──────────────
        # Aproximação: mede se o morador da encomenda já confirmou o WhatsApp
        # alguma vez (moradores.whats_confirmado), não confirmação por notificação específica.
        if cond_id is not None:
            whats_where = "WHERE e.condominio_id = :c AND e.data_recebimento >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)"
        else:
            whats_where = "WHERE e.data_recebimento >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)"
        whats_row = db.execute(text(f"""
            SELECT
                COUNT(*) AS total_notificaveis,
                SUM(CASE WHEN m.whats_confirmado IS NOT NULL THEN 1 ELSE 0 END) AS confirmadas
            FROM encomendas e
            INNER JOIN moradores m ON e.morador_id = m.id
            {whats_where}
        """), p).fetchone()
        whats_total = whats_row.total_notificaveis or 0
        whats_confirmadas = whats_row.confirmadas or 0
        whats_taxa_confirmacao = round((whats_confirmadas / whats_total) * 100, 1) if whats_total > 0 else None

        # ── Entregues nos últimos 90 dias (não altera o campo `entregues` original) ──
        entregues_90d = db.execute(text(f"""
            SELECT COUNT(*) FROM encomendas {w}
            AND status = 'entregue' AND data_entrega >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
        """), p).scalar() or 0
        total_90d = db.execute(text(f"""
            SELECT COUNT(*) FROM encomendas {w}
            AND data_recebimento >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
        """), p).scalar() or 0

        # ── Ranking por bloco ────────────────────────────────────────────

        # ── Ranking por bloco ────────────────────────────────────────────



        ranking_rows = db.execute(text(f"""
            SELECT COALESCE(NULLIF(TRIM(bloco), ''), 'Sem bloco') AS bloco, COUNT(*) AS total
            FROM encomendas {w}
            GROUP BY bloco
            ORDER BY total DESC
            LIMIT 15
        """), p).fetchall()
        ranking_blocos = [{"bloco": r.bloco, "apartamento": None, "total": r.total} for r in ranking_rows]

        return {
            "total_encomendas": total,
            "pendentes": pendentes,
            "entregues": entregues,
            "canceladas": canceladas,
            "recebidas_hoje": recebidas_hoje,
            "entregues_hoje": entregues_hoje,
            "recebidas_ontem": recebidas_ontem,
            "recebidas_mes": recebidas_mes,
            "encomendas_atrasadas": encomendas_atrasadas,
            "tempo_medio_entrega_horas": tempo_medio_entrega_horas,
            "movimento_semanal": movimento_semanal,
            "distribuicao_status": {
                "pendente": pendentes,
                "entregue": entregues,
                "atrasada": encomendas_atrasadas,
                "cancelada": canceladas,
            },
            "heatmap_horario": heatmap_horario,

            "ranking_blocos": ranking_blocos,
            "whatsapp_confirmacao": {
                "total_notificaveis": whats_total,
                "confirmadas": whats_confirmadas,
                "taxa_percentual": whats_taxa_confirmacao,
            },
            "entregues_90dias": entregues_90d,
            "total_90dias": total_90d,

            "dias_periodo": 90,

        }
    except Exception as e:
        logger.error(f"Erro ao obter estatísticas: {str(e)}")
        return {
            "total_encomendas": 0,
            "pendentes": 0,
            "entregues": 0,
            "canceladas": 0,
            "recebidas_hoje": 0,
            "entregues_hoje": 0,
            "recebidas_ontem": 0,
            "recebidas_mes": 0,
            "encomendas_atrasadas": 0,
            "tempo_medio_entrega_horas": 0,
            "movimento_semanal": [],
            "distribuicao_status": {"pendente": 0, "entregue": 0, "atrasada": 0, "cancelada": 0},
            "heatmap_horario": [],
            "ranking_blocos": []
        }

# ============================================================
# BUSCAR ENCOMENDAS
# ============================================================

@router.get("/buscar")
async def buscar_encomendas(
    q: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Buscar encomendas por termo"""
    return await list_encomendas(search=q, current_user=current_user, db=db)


# ============================================================
# GET ENCOMENDA POR ID - COMPLETO COM IMAGENS
# ============================================================

@router.get("/{encomenda_id}")
async def get_encomenda(
    encomenda_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Obter detalhes completos de uma encomenda - V2.3.7"""
    try:
        cond_id = _resolve_cond_id(current_user, None)

        if cond_id is not None:
            query = text("SELECT e.* FROM encomendas e WHERE e.id = :id AND e.condominio_id = :condominio_id")
            result = db.execute(query, {"id": encomenda_id, "condominio_id": cond_id})
        else:
            query = text("SELECT e.* FROM encomendas e WHERE e.id = :id")
            result = db.execute(query, {"id": encomenda_id})

        encomenda = result.fetchone()

        if not encomenda:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Encomenda não encontrada"
            )

        # Processar OCR data
        ocr_data = None
        if hasattr(encomenda, 'ocr_data') and encomenda.ocr_data:
            try:
                ocr_data = json.loads(encomenda.ocr_data)
            except:
                ocr_data = None

        # Buscar URL da etiqueta
        etiqueta_url = None
        img_etiqueta_server = getattr(encomenda, 'img_etiqueta_server', None)

        if img_etiqueta_server:
            try:
                etiqueta_url = await image_storage_service.get_image_url(img_etiqueta_server)
            except Exception as e:
                logger.warning(f"Erro ao obter URL da etiqueta: {e}")

        if not etiqueta_url:
            try:
                mapeamento_etiqueta = db.execute(text("""
                    SELECT nome_servidor FROM imagens_mapeamento
                    WHERE encomenda_id = :encomenda_id AND tipo = 'etiqueta'
                    ORDER BY id DESC LIMIT 1
                """), {"encomenda_id": encomenda_id}).fetchone()

                if mapeamento_etiqueta and mapeamento_etiqueta.nome_servidor:
                    etiqueta_url = await image_storage_service.get_image_url(mapeamento_etiqueta.nome_servidor)
            except Exception as e:
                logger.warning(f"Erro ao buscar etiqueta no mapeamento: {e}")

        # Buscar URL da assinatura
        assinatura_url = None
        if getattr(encomenda, 'status', '') == 'entregue':
            try:
                mapeamento_assinatura = db.execute(text("""
                    SELECT nome_servidor FROM imagens_mapeamento
                    WHERE encomenda_id = :encomenda_id AND tipo = 'assinatura'
                    ORDER BY id DESC LIMIT 1
                """), {"encomenda_id": encomenda_id}).fetchone()

                if mapeamento_assinatura and mapeamento_assinatura.nome_servidor:
                    assinatura_url = await image_storage_service.get_image_url(mapeamento_assinatura.nome_servidor)
            except Exception as e:
                logger.warning(f"Erro ao obter URL da assinatura: {e}")
        # V2.4.1 - Operadores gravados direto nas colunas da encomenda
        operador_recebimento = None
        operador_entrega = None
        try:
            _ops = db.execute(text("""
                SELECT operador_recebimento, operador_entrega
                FROM encomendas WHERE id = :id
            """), {"id": encomenda_id}).fetchone()
            if _ops:
                if _ops.operador_recebimento:
                    operador_recebimento = {"id": None, "nome": _ops.operador_recebimento}
                if _ops.operador_entrega:
                    operador_entrega = {"id": None, "nome": _ops.operador_entrega}
        except Exception as _e:
            logger.warning(f"Erro ao buscar operadores da encomenda {encomenda_id}: {_e}")
        response = {
            "id": encomenda.id,
            "condominio_id": encomenda.condominio_id,
            "nome_destinatario": encomenda.nome_destinatario,
            "apartamento": encomenda.apartamento,
            "bloco": getattr(encomenda, 'bloco', None),
            "codigo_rastreio": getattr(encomenda, 'codigo_rastreio', None),
            "remetente": getattr(encomenda, 'remetente', None),
            "status": encomenda.status,
            "data_recebimento": encomenda.data_recebimento.isoformat() if encomenda.data_recebimento else None,
            "data_entrega": encomenda.data_entrega.isoformat() if hasattr(encomenda, 'data_entrega') and encomenda.data_entrega else None,
            "observacoes": getattr(encomenda, 'observacoes', None),
            "observacoes_entrega": getattr(encomenda, 'observacoes_entrega', None),
            "telefone_morador": getattr(encomenda, 'telefone_morador', None),
            "morador_id": getattr(encomenda, 'morador_id', None),
            "nome_retirou": getattr(encomenda, 'nome_retirou', None),
            "codigo_retirada": getattr(encomenda, 'codigo_retirada', None),
            "img_etiqueta": getattr(encomenda, 'img_etiqueta', None),
            "img_etiqueta_server": img_etiqueta_server,
            "imagem_etiqueta": etiqueta_url,
            "foto_url": etiqueta_url,
            "etiqueta_url": etiqueta_url,
            "assinatura_url": assinatura_url,
            "ocr_data": ocr_data,
            "operador_recebimento": operador_recebimento,
            "operador_entrega": operador_entrega,
        }

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar encomenda {encomenda_id}: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao buscar encomenda: {str(e)}"
        )


# ============================================================
# CRIAR ENCOMENDA - V2.3.8 COM WHATSAPP EM BACKGROUND
# ============================================================

@router.post("/")
async def create_encomenda(
    encomenda_data: Dict[str, Any],
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Registrar nova encomenda - V2.3.8 - WhatsApp em background"""

    encomenda_criada_id = None

    try:
        logger.info("=" * 60)
        logger.info("=== NOVA ENCOMENDA - V2.3.8 ASYNC ===")
        logger.info(f"morador_id do payload: {encomenda_data.get('morador_id')}")
        logger.info(f"telefone_morador do payload: {encomenda_data.get('telefone_morador')}")
        logger.info("=" * 60)

        required_fields = ["nome_destinatario", "apartamento"]
        for field in required_fields:
            if field not in encomenda_data or not encomenda_data[field]:
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail=f"Campo obrigatório ausente: {field}"
                )

        # Régua de cobrança: bloqueia só o RECEBIMENTO (flag BLOQUEIO_ASSINATURA_ENABLED)
        checar_bloqueio_recebimento(db, _resolve_cond_id(current_user, None))

        img_etiqueta_filename = None
        img_etiqueta_server = None
        imagem_etiqueta_base64 = encomenda_data.get("imagem_etiqueta")

        if imagem_etiqueta_base64:
            try:
                logger.info("Processando upload da imagem da etiqueta...")
                resultado_upload = await image_storage_service.process_and_upload_etiqueta(
                    imagem_etiqueta_base64,
                    identificador=encomenda_data.get("nome_destinatario"),
                    codigo_rastreio=encomenda_data.get("codigo_rastreio")
                )
                img_etiqueta_filename = resultado_upload["nome_personalizado"]
                img_etiqueta_server = resultado_upload["nome_servidor"]
                logger.info(f"✅ Imagem salva - Server: {img_etiqueta_server}")
            except Exception as e:
                logger.error(f"⚠️ Erro ao salvar imagem (continuando): {str(e)}")

        morador_id_payload = encomenda_data.get("morador_id")
        morador_id = None

        if morador_id_payload:
            try:
                morador_id = int(morador_id_payload)
                if morador_id <= 0:
                    morador_id = None
            except (ValueError, TypeError):
                morador_id = None

        telefone_whatsapp = encomenda_data.get("telefone_morador")
        if telefone_whatsapp:
            telefone_whatsapp = telefone_whatsapp.strip()
            if not telefone_whatsapp:
                telefone_whatsapp = None

        logger.info(f"📱 Telefone para WhatsApp: {telefone_whatsapp}")

        whats_confirmado = None
        condominio_id = _resolve_cond_id(current_user, None)

        if not morador_id:
            try:
                if condominio_id is not None:
                    check_morador_query = text("""
                        SELECT id, whats_confirmado, telefone FROM moradores
                        WHERE LOWER(TRIM(nome)) = LOWER(TRIM(:nome))
                        AND TRIM(apartamento) = TRIM(:apartamento)
                        AND condominio_id = :condominio_id
                        AND ativo = 1
                        LIMIT 1
                    """)
                    morador_params = {
                        "nome": encomenda_data.get("nome_destinatario", "").strip(),
                        "apartamento": encomenda_data.get("apartamento", "").strip(),
                        "condominio_id": condominio_id
                    }
                else:
                    check_morador_query = text("""
                        SELECT id, whats_confirmado, telefone FROM moradores
                        WHERE LOWER(TRIM(nome)) = LOWER(TRIM(:nome))
                        AND TRIM(apartamento) = TRIM(:apartamento)
                        AND ativo = 1
                        LIMIT 1
                    """)
                    morador_params = {
                        "nome": encomenda_data.get("nome_destinatario", "").strip(),
                        "apartamento": encomenda_data.get("apartamento", "").strip(),
                    }

                result = db.execute(check_morador_query, morador_params).fetchone()

                if result:
                    morador_id = result.id
                    whats_confirmado = result.whats_confirmado
                    logger.info(f"✅ Morador encontrado: ID={morador_id}")

                    if not telefone_whatsapp and result.telefone and whats_confirmado:
                        telefone_whatsapp = result.telefone
            except Exception as e:
                logger.error(f"⚠️ Erro ao buscar morador: {e}")

        else:
            try:
                check_whats_query = text("""
                    SELECT whats_confirmado, telefone, nome FROM moradores
                    WHERE id = :morador_id AND ativo = 1
                """)
                result = db.execute(check_whats_query, {"morador_id": morador_id}).fetchone()

                if result:
                    whats_confirmado = result.whats_confirmado
                    logger.info(f"✅ Morador encontrado: {result.nome}")

                    if not telefone_whatsapp and result.telefone and whats_confirmado:
                        telefone_whatsapp = result.telefone
                else:
                    morador_id = None
            except Exception as e:
                logger.error(f"⚠️ Erro ao buscar morador: {e}")

        ocr_data_json = None
        ocr_data = encomenda_data.get("ocr_data")
        if ocr_data and isinstance(ocr_data, dict):
            ocr_data_json = json.dumps(ocr_data)

        nome_destinatario = encomenda_data.get("nome_destinatario", "").strip()
        apartamento = encomenda_data.get("apartamento", "").strip()
        bloco = encomenda_data.get("bloco", "").strip() if encomenda_data.get("bloco") else None
        codigo_rastreio = encomenda_data.get("codigo_rastreio", "").strip() if encomenda_data.get("codigo_rastreio") else None
        remetente = encomenda_data.get("remetente", "").strip() if encomenda_data.get("remetente") else None
        observacoes = encomenda_data.get("observacoes", "").strip() if encomenda_data.get("observacoes") else None
        local_armazenamento = encomenda_data.get("local_armazenamento", "").strip() if encomenda_data.get("local_armazenamento") else None   

        # admin_sistema sem condominio: usa 0 como placeholder (não deve criar encomendas sem condomínio)
        condominio_id_insert = condominio_id if condominio_id is not None else 0

        # ------------------------------------------------------------------
        # SUBPORTARIA (secao 2 do SUBPORTARIA_DEV2.md)
        # Se o condominio usa subportaria: encomenda entra num lote aberto e
        # NAO dispara WhatsApp no cadastro (aviso so sai quando a segunda
        # portaria clicar "Enviar Msg aos Moradores" - implementado em lotes.py).
        # ------------------------------------------------------------------
        usa_subportaria_ativa = False
        envio_whatsapp_ativo = True
        lote_id_insert = None
        try:
            cond_flag = db.execute(
                text("SELECT usa_subportaria, envio_whatsapp FROM condominios WHERE id = :cid"),
                {"cid": condominio_id_insert}
            ).fetchone()
            usa_subportaria_ativa = bool(cond_flag and cond_flag.usa_subportaria)
            envio_whatsapp_ativo = bool(cond_flag and cond_flag.envio_whatsapp != 'N')
        except Exception as e:
            logger.error(f"Erro ao verificar usa_subportaria (condominio {condominio_id_insert}): {e}")
            usa_subportaria_ativa = False

        if usa_subportaria_ativa:
            lote_id_payload = encomenda_data.get("lote_id")

            if lote_id_payload:
                lote_valido = db.execute(
                    text("""
                        SELECT id_lote FROM lotes_encomendas
                        WHERE id_lote = :lid AND condominio_id = :cid AND status = 'aberto'
                    """),
                    {"lid": lote_id_payload, "cid": condominio_id_insert}
                ).fetchone()
                if not lote_valido:
                    raise HTTPException(
                        status_code=http_status.HTTP_400_BAD_REQUEST,
                        detail="lote_id informado nao corresponde a um lote aberto deste condominio"
                    )
                lote_id_insert = lote_valido.id_lote
            else:
                lote_aberto = db.execute(
                    text("""
                        SELECT id_lote FROM lotes_encomendas
                        WHERE condominio_id = :cid AND status = 'aberto'
                        ORDER BY id_lote DESC LIMIT 1
                    """),
                    {"cid": condominio_id_insert}
                ).fetchone()

                if lote_aberto:
                    lote_id_insert = lote_aberto.id_lote
                else:
                    prox_row = db.execute(
                        text("""
                            SELECT COALESCE(MAX(numero),0)+1 AS prox
                            FROM lotes_encomendas WHERE condominio_id = :cid
                            FOR UPDATE
                        """),
                        {"cid": condominio_id_insert}
                    ).fetchone()
                    prox = prox_row.prox

                    _op_abertura_id = None
                    for _c in ('user_id', 'id', 'sub', 'username'):
                        _v = current_user.get(_c) if isinstance(current_user, dict) else getattr(current_user, _c, None)
                        if _v is not None and str(_v).isdigit():
                            _op_abertura_id = int(str(_v))
                            break

                    novo_lote = db.execute(
                        text("""
                            INSERT INTO lotes_encomendas
                                (condominio_id, numero, status, operador_abertura_id)
                            VALUES
                                (:cid, :numero, 'aberto', :operador_id)
                        """),
                        {"cid": condominio_id_insert, "numero": prox, "operador_id": _op_abertura_id}
                    )
                    db.commit()
                    lote_id_insert = novo_lote.lastrowid
                    logger.info(
                        f"Lote aberto automaticamente (sem lote_id no payload): "
                        f"condominio={condominio_id_insert} numero={prox} lote_id={lote_id_insert}"
                    )

            logger.info(
                f"Condominio {condominio_id_insert} usa subportaria - "
                f"encomenda ira para lote_id={lote_id_insert}, WhatsApp de cadastro suprimido"
            )
        data_recebimento = datetime.now()
        data_recebimento_str = data_recebimento.strftime('%Y-%m-%d %H:%M:%S')

        # V2.4.1 - Operador logado que recebeu a encomenda
        operador_recebimento_nome = None
        try:
            _uid = None
            for _c in ('user_id', 'id', 'sub', 'username'):
                _v = current_user.get(_c) if isinstance(current_user, dict) else getattr(current_user, _c, None)
                if _v is not None and str(_v).isdigit():
                    _uid = int(str(_v))
                    break
            if _uid:
                _row = db.execute(text("SELECT nome FROM mobile_operadores WHERE id = :id"), {"id": _uid}).fetchone()
                if _row and _row.nome:
                    operador_recebimento_nome = _row.nome
                if not operador_recebimento_nome:
                    _row = db.execute(text("SELECT Nome AS nome FROM operadores WHERE id = :id"), {"id": _uid}).fetchone()
                    if _row and _row.nome:
                        operador_recebimento_nome = _row.nome
            if not operador_recebimento_nome:
                for _campo in ('nome', 'Nome', 'username', 'sub', 'email'):
                    _v = current_user.get(_campo) if isinstance(current_user, dict) else getattr(current_user, _campo, None)
                    if _v and not str(_v).isdigit():
                        operador_recebimento_nome = str(_v)
                        break
            logger.info(f"[OPERADOR-RECEB] tipo={type(current_user).__name__} resolvido={operador_recebimento_nome}")
        except Exception as _e:
            logger.warning(f"[OPERADOR-RECEB] falha ao identificar operador: {_e}")
        if operador_recebimento_nome:
            _obs_op = f"[{data_recebimento.strftime('%d/%m/%Y %H:%M')}] Operador que recebeu: {operador_recebimento_nome}"
            observacoes = f"{observacoes}\n{_obs_op}" if observacoes else _obs_op
        # 2026-09-30: bloco padronizado: com morador escolhido vale o apto/bloco do cadastro dele
        try:
            from app.services.blocos import normalizar_bloco, dados_do_morador
            _cad = dados_do_morador(db, morador_id, condominio_id_insert)
            if _cad:
                apartamento = _cad["apartamento"] or apartamento
                bloco = _cad["bloco"] or bloco
            bloco = normalizar_bloco(db, condominio_id_insert, bloco)
        except Exception as _e:
            logger.warning(f"[BLOCO] padronização falhou: {_e}")
        insert_query = text("""
            INSERT INTO encomendas (
                condominio_id, nome_destinatario, apartamento, bloco, codigo_rastreio,
                remetente, status, data_recebimento, observacoes, local_armazenamento,
                telefone_morador, img_etiqueta, img_etiqueta_server, ocr_data, morador_id,
                codigo_retirada, operador_recebimento, lote_id
            ) VALUES (
                :condominio_id, :nome_destinatario, :apartamento, :bloco, :codigo_rastreio,
                :remetente, 'pendente', :data_recebimento, :observacoes, :local_armazenamento,
                :telefone_morador, :img_etiqueta, :img_etiqueta_server, :ocr_data, :morador_id,
                :codigo_retirada, :operador_recebimento, :lote_id
            )
        """)
        # Gera codigo_retirada unico entre as encomendas PENDENTES do mesmo condominio
        codigo_retirada = str(random.randint(1000, 9999))
        for _ in range(50):
            existe = db.execute(
                text("""
                    SELECT 1 FROM encomendas
                    WHERE condominio_id = :cid
                      AND status = 'pendente'
                      AND codigo_retirada = :cod
                    LIMIT 1
                """),
                {"cid": condominio_id_insert, "cod": codigo_retirada}
            ).first()
            if not existe:
                break
            codigo_retirada = str(random.randint(1000, 9999))
        else:
            logger.warning(
                f"codigo_retirada: 50 tentativas sem codigo unico no condominio {condominio_id_insert}"
            )

        result = db.execute(insert_query, {
            "condominio_id": condominio_id_insert,
            "nome_destinatario": nome_destinatario,
            "apartamento": apartamento,
            "bloco": bloco,
            "codigo_rastreio": codigo_rastreio,
            "remetente": remetente,
            "data_recebimento": data_recebimento_str,
            "observacoes": observacoes,
            "local_armazenamento": local_armazenamento,
            "telefone_morador": telefone_whatsapp,    


            "img_etiqueta": img_etiqueta_filename,
            "img_etiqueta_server": img_etiqueta_server,
            "ocr_data": ocr_data_json,
            "morador_id": morador_id,
            "codigo_retirada": codigo_retirada,
            "operador_recebimento": operador_recebimento_nome,
            "lote_id": lote_id_insert
        })


        encomenda_id = None

        try:
            if hasattr(result, 'lastrowid') and result.lastrowid:
                encomenda_id = result.lastrowid
        except:
            pass

        if not encomenda_id:
            try:
                if hasattr(result, 'inserted_primary_key') and result.inserted_primary_key:
                    encomenda_id = result.inserted_primary_key[0]
            except:
                pass

        db.commit()

        if not encomenda_id:
            try:
                result_id = db.execute(text("SELECT LAST_INSERT_ID() as id")).fetchone()
                if result_id:
                    encomenda_id = result_id[0] if isinstance(result_id, tuple) else result_id.id
            except:
                pass

        if not encomenda_id:
            try:
                busca_query = text("""
                    SELECT id FROM encomendas
                    WHERE condominio_id = :condominio_id
                    AND nome_destinatario = :nome
                    AND data_recebimento = :data_recebimento
                    ORDER BY id DESC LIMIT 1
                """)
                result_busca = db.execute(busca_query, {
                    "condominio_id": condominio_id_insert,
                    "nome": nome_destinatario,
                    "data_recebimento": data_recebimento_str
                }).fetchone()

                if result_busca:
                    encomenda_id = result_busca[0] if isinstance(result_busca, tuple) else result_busca.id
            except:
                pass

        if not encomenda_id:
            return {
                "message": "Encomenda registrada, mas não foi possível obter o ID",
                "encomenda": {
                    "id": None,
                    "status": "pendente",
                    "aviso": "ID não obtido"
                }
            }

        encomenda_criada_id = encomenda_id
        logger.info(f"✅ Encomenda criada com ID: {encomenda_id}")

        if img_etiqueta_filename and img_etiqueta_server:
            try:
                db.execute(text("""
                    INSERT INTO imagens_mapeamento (encomenda_id, tipo, nome_personalizado, nome_servidor)
                    VALUES (:encomenda_id, 'etiqueta', :nome_personalizado, :nome_servidor)
                """), {
                    "encomenda_id": encomenda_id,
                    "nome_personalizado": img_etiqueta_filename,
                    "nome_servidor": img_etiqueta_server
                })
                db.commit()
            except Exception as e:
                logger.error(f"⚠️ Erro ao inserir mapeamento: {str(e)}")

        notificar = encomenda_data.get("notificar_whatsapp", True)

        if telefone_whatsapp and notificar and settings.WHATSAPP_ENABLED and not usa_subportaria_ativa and envio_whatsapp_ativo:
            _needs_confirmation = whats_confirmado is None
            _imagem_etiqueta = encomenda_data.get("imagem_etiqueta")
            _condominio_id_bg = condominio_id_insert
            _condominio_nome_bg = getattr(current_user, 'condominio_nome', '') if not isinstance(current_user, dict) else current_user.get('condominio_nome', '')

            async def enviar_whatsapp_background():
                try:
                    logger.info(f"🚀 [BACKGROUND] Enviando WhatsApp para {telefone_whatsapp}...")

                    nome_cond = _condominio_nome_bg
                    if not nome_cond:
                        try:
                            from app.database import SessionLocal
                            db_bg = SessionLocal()
                            try:
                                cond_result = db_bg.execute(text("SELECT nome FROM condominios WHERE id = :id"), {"id": _condominio_id_bg}).fetchone()
                                nome_cond = cond_result.nome if cond_result else ""
                            finally:
                                db_bg.close()
                        except:
                            nome_cond = ""
                    dados_notificacao = {  
                        "id": encomenda_id,
                        "nome_destinatario": nome_destinatario,
                        "apartamento": apartamento,
                        "bloco": bloco,
                        "codigo_rastreio": codigo_rastreio,
                        "remetente": remetente,
                        "telefone_morador": telefone_whatsapp,
                        "imagem_etiqueta": _imagem_etiqueta,
                        "nome_condominio": nome_cond,
                        "whats_confirmado": whats_confirmado,
                        "morador_id": morador_id,
                        "condominio_id": _condominio_id_bg,
                        "codigo_retirada": codigo_retirada,
                        "local_armazenamento": local_armazenamento,
                    }
                    success, msg = await asyncio.to_thread(
                        whatsapp_service.send_package_notification,
                        dados_notificacao,
                        _needs_confirmation
                    )

                    logger.info(f"🚀 [BACKGROUND] WhatsApp resultado: {msg}")

                except Exception as e:
                    logger.error(f"❌ [BACKGROUND] Erro ao enviar WhatsApp: {e}")

            asyncio.create_task(enviar_whatsapp_background())
            logger.info(f"🚀 WhatsApp disparado em background para {telefone_whatsapp}")

        return {
            "message": "Encomenda registrada com sucesso",
            "encomenda": {
                "id": encomenda_id,
                "condominio_id": condominio_id_insert,
                "nome_destinatario": nome_destinatario,
                "apartamento": apartamento,
                "bloco": bloco,
                "codigo_rastreio": codigo_rastreio,
                "status": "pendente",
                "data_recebimento": data_recebimento.isoformat(),
                "lote_id": lote_id_insert,
                "usa_subportaria": usa_subportaria_ativa,
                "whatsapp_enviado": bool(
                    telefone_whatsapp and notificar and settings.WHATSAPP_ENABLED and not usa_subportaria_ativa and envio_whatsapp_ativo
                )
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao receber encomenda: {str(e)}")
        db.rollback()

        if encomenda_criada_id:
            return {
                "message": "Encomenda registrada com aviso",
                "encomenda": {"id": encomenda_criada_id, "aviso": str(e)}
            }

        raise HTTPException(status_code=500, detail=f"Erro ao receber encomenda: {str(e)}")


# ============================================================
# ENTREGAR ENCOMENDA - V2.3.8 COM WHATSAPP EM BACKGROUND
# ============================================================

@router.put("/{encomenda_id}/entregar")
async def entregar_encomenda(
    encomenda_id: int,
    entrega_data: Dict[str, Any],
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Registrar entrega - V2.3.8 WhatsApp em background"""
    try:
        cond_id = _resolve_cond_id(current_user, None)

        if cond_id is not None:
            query = text("""
                SELECT e.*, m.whats_confirmado, m.telefone as telefone_morador_atual
                FROM encomendas e
                LEFT JOIN moradores m ON e.morador_id = m.id
                WHERE e.id = :id AND e.condominio_id = :condominio_id
            """)
            encomenda = db.execute(query, {"id": encomenda_id, "condominio_id": cond_id}).fetchone()
        else:
            query = text("""
                SELECT e.*, m.whats_confirmado, m.telefone as telefone_morador_atual
                FROM encomendas e
                LEFT JOIN moradores m ON e.morador_id = m.id
                WHERE e.id = :id
            """)
            encomenda = db.execute(query, {"id": encomenda_id}).fetchone()

        if not encomenda:
            raise HTTPException(status_code=404, detail="Encomenda não encontrada")

        assinatura_server = None
        if entrega_data.get("assinatura_base64"):
            try:
                assinatura_server = await image_storage_service.process_and_upload_assinatura(
                    entrega_data["assinatura_base64"], encomenda_id
                )

                db.execute(text("""
                    INSERT INTO imagens_mapeamento (encomenda_id, tipo, nome_personalizado, nome_servidor)
                    VALUES (:encomenda_id, 'assinatura', :nome_personalizado, :nome_servidor)
                """), {
                    "encomenda_id": encomenda_id,
                    "nome_personalizado": f"ass_{encomenda_id}.jpg",
                    "nome_servidor": assinatura_server
                })
            except Exception as e:
                logger.error(f"Erro ao salvar assinatura: {e}")
        nome_retirou = entrega_data.get("nome_retirou", encomenda.nome_destinatario)

        # V2.4.1 - Operador logado que registrou a entrega
        operador_entrega_nome = None
        try:
            _uid = None
            for _c in ('user_id', 'id', 'sub', 'username'):
                _v = current_user.get(_c) if isinstance(current_user, dict) else getattr(current_user, _c, None)
                if _v is not None and str(_v).isdigit():
                    _uid = int(str(_v))
                    break

            if _uid:
                _row = db.execute(text("SELECT nome FROM mobile_operadores WHERE id = :id"), {"id": _uid}).fetchone()
                if _row and _row.nome:
                    operador_entrega_nome = _row.nome
                if not operador_entrega_nome:
                    _row = db.execute(text("SELECT Nome AS nome FROM operadores WHERE id = :id"), {"id": _uid}).fetchone()
                    if _row and _row.nome:
                        operador_entrega_nome = _row.nome
            if not operador_entrega_nome:
                for _campo in ('nome', 'Nome', 'username', 'sub', 'email'):
                    _v = current_user.get(_campo) if isinstance(current_user, dict) else getattr(current_user, _campo, None)
                    if _v and not str(_v).isdigit():
                        operador_entrega_nome = str(_v)
                        break

            logger.info(f"[OPERADOR-ENTREGA] tipo={type(current_user).__name__} resolvido={operador_entrega_nome}")
        except Exception as _e:
            logger.warning(f"[OPERADOR-ENTREGA] falha ao identificar operador: {_e}")
        operador_entrega_nome = operador_entrega_nome or "nao identificado"

        # V2.4.2 - Nota extra quando a entrega admin foi confirmada sem comprovante
        # (código de retirada deixado em branco no modal do admin)
        nota_sem_comprovante = ""
        if entrega_data.get("sem_comprovante"):
            agora_str = datetime.now().strftime('%d/%m/%Y %H:%M')
            nota_sem_comprovante = f"\nEntrega administrativa por {operador_entrega_nome} em {agora_str}"

        db.execute(text("""
            UPDATE encomendas
            SET status = 'entregue', data_entrega = NOW(), nome_retirou = :nome_retirou,
                operador_entrega = :operador_entrega,
                observacoes = CONCAT(IFNULL(observacoes, ''), '\n[', DATE_FORMAT(NOW(), '%d/%m/%Y %H:%i'), '] Entregue para: ', :nome_retirou, ' (Entrega registrada administrativamente)', '\n[', DATE_FORMAT(NOW(), '%d/%m/%Y %H:%i'), '] Operador que entregou: ', :operador_entrega, :nota_sem_comprovante)
            WHERE id = :id
        """), {"id": encomenda_id, "nome_retirou": nome_retirou, "operador_entrega": operador_entrega_nome, "nota_sem_comprovante": nota_sem_comprovante})
        if encomenda.morador_id and not encomenda.whats_confirmado:
            try:
                db.execute(text("UPDATE moradores SET whats_confirmado = NOW() WHERE id = :id"),
                          {"id": encomenda.morador_id})
            except:
                pass

        db.commit()

        telefone = encomenda.telefone_morador or encomenda.telefone_morador_atual
        if telefone and entrega_data.get("notificar_whatsapp", True) and encomenda.whats_confirmado:
            _codigo_rastreio = encomenda.codigo_rastreio or 'S/N'
            _nome_retirou = nome_retirou
            _condominio_id_bg = cond_id or encomenda.condominio_id
            _condominio_nome_bg = getattr(current_user, 'condominio_nome', '') if not isinstance(current_user, dict) else current_user.get('condominio_nome', '')
            _telefone = telefone

            async def enviar_whatsapp_entrega_background():
                try:
                    logger.info(f"🚀 [BACKGROUND] Enviando WhatsApp entrega para {_telefone}...")

                    nome_cond = _condominio_nome_bg
                    if not nome_cond:
                        try:
                            from app.database import SessionLocal
                            db_bg = SessionLocal()
                            try:
                                cond_result = db_bg.execute(text("SELECT nome FROM condominios WHERE id = :id"), {"id": _condominio_id_bg}).fetchone()
                                nome_cond = cond_result.nome if cond_result else ""
                            finally:
                                db_bg.close()
                        except:
                            nome_cond = ""

                    mensagem = f"✅ Encomenda {_codigo_rastreio},\nretirada por {_nome_retirou}!\n\n🏢 {nome_cond.title() if nome_cond else ''}\n\n*e-Condomínio*\nhttps://econdominio.com.br"

                    await asyncio.to_thread(
                        whatsapp_service.send_notification,
                        phone=_telefone,
                        message=mensagem
                    )

                    logger.info(f"🚀 [BACKGROUND] WhatsApp entrega enviado!")

                except Exception as e:
                    logger.error(f"❌ [BACKGROUND] Erro WhatsApp entrega: {e}")

            asyncio.create_task(enviar_whatsapp_entrega_background())

        return {"message": "Entrega registrada", "id": encomenda_id, "status": "entregue"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# CANCELAR ENCOMENDA
# ============================================================

@router.put("/{encomenda_id}/cancelar")
async def cancelar_encomenda(
    encomenda_id: int,
    cancelamento_data: Dict[str, Any] = {},
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Cancelar encomenda"""
    try:
        cond_id = _resolve_cond_id(current_user, None)

        if cond_id is not None:
            check = db.execute(text("SELECT id, status FROM encomendas WHERE id = :id AND condominio_id = :cond"),
                               {"id": encomenda_id, "cond": cond_id}).fetchone()
        else:
            check = db.execute(text("SELECT id, status FROM encomendas WHERE id = :id"),
                               {"id": encomenda_id}).fetchone()

        if not check:
            raise HTTPException(status_code=404, detail="Encomenda não encontrada")
        if check.status == "cancelada":
            raise HTTPException(status_code=400, detail="Já está cancelada")

        motivo = cancelamento_data.get("motivo", "Cancelada pelo operador")

        db.execute(text("""
            UPDATE encomendas
            SET status = 'cancelada',
                observacoes = CONCAT(IFNULL(observacoes, ''), '\n[', DATE_FORMAT(NOW(), '%d/%m/%Y %H:%i'), '] CANCELADA: ', :motivo)
            WHERE id = :id
        """), {"id": encomenda_id, "motivo": motivo})
        db.commit()

        return {"message": "Cancelada", "id": encomenda_id}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# EDITAR ENCOMENDA (2026-09-30)
# Campos editáveis pelo condomínio. "Observações" é exclusivo do sistema: o que vier
# no payload é ignorado e cada alteração ACRESCENTA uma linha com o estado anterior
# (nunca apaga o que já existe).
# ============================================================

_CAMPOS_EDITAVEIS = {
    "nome_destinatario": ("Destinatário", 200),
    "apartamento":       ("Apartamento", 50),
    "bloco":             ("Bloco", 10),
    "codigo_rastreio":   ("Código de rastreio", 100),
    "remetente":         ("Remetente", 200),
}
_PAPEIS = {"admin_sistema": "admin do sistema", "admin_condominio": "admin", "sindico": "síndico",
           "operador": "operador", "porteiro": "porteiro"}


@router.put("/{encomenda_id}")
async def editar_encomenda(
    encomenda_id: int,
    dados: Dict[str, Any],
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Edita dados da encomenda e registra o estado anterior em Observações (só acrescenta)."""
    try:
        cond_id = _resolve_cond_id(current_user, None)
        sql = ("SELECT id, status, observacoes, " + ", ".join(_CAMPOS_EDITAVEIS)
               + " FROM encomendas WHERE id = :id")
        params = {"id": encomenda_id}
        if cond_id is not None:
            sql += " AND condominio_id = :cond"
            params["cond"] = cond_id
        atual = db.execute(text(sql), params).mappings().fetchone()
        if not atual:
            raise HTTPException(status_code=404, detail="Encomenda não encontrada")
        if atual["status"] == "cancelada":
            raise HTTPException(status_code=400, detail="Encomenda cancelada não pode ser editada")

        novos, antes = {}, []
        for campo, (rotulo, limite) in _CAMPOS_EDITAVEIS.items():
            if campo not in dados:
                continue
            novo = "" if dados[campo] is None else str(dados[campo]).strip()
            if campo == "bloco" and novo:  # 2026-09-30: bloco padronizado
                from app.services.blocos import normalizar_bloco
                novo = normalizar_bloco(db, cond_id or current_user.get("condominio_id"), novo) or ""
            velho = "" if atual[campo] is None else str(atual[campo]).strip()
            if novo == velho:
                continue
            if campo in ("nome_destinatario", "apartamento") and not novo:
                raise HTTPException(status_code=422, detail=f"{rotulo} é obrigatório")
            if len(novo) > limite:
                raise HTTPException(status_code=422, detail=f"{rotulo}: máximo de {limite} caracteres")
            novos[campo] = novo or None
            antes.append(f"{rotulo}: {velho or '(vazio)'}")

        if not novos:
            return {"message": "Nenhuma alteração", "id": encomenda_id, "alterado": False}

        quem = current_user.get("nome") or current_user.get("username") or "usuário"
        if str(quem).isdigit():  # login novo (mobile_operadores): o token traz o id, não o nome
            quem = db.execute(text("SELECT nome FROM mobile_operadores WHERE id = :i"),
                              {"i": int(quem)}).scalar() or f"operador #{quem}"
        role = (current_user.get("role") or "").lower()
        papel = _PAPEIS.get(role) or role or "usuário"
        registro = (f"Alterado em {datetime.now():%d/%m/%Y %H:%M} por {quem} ({papel}). "
                    f"Como estava antes: " + "; ".join(antes))
        separador = "\n" if (atual["observacoes"] or "").strip() else ""

        sets = ", ".join(f"{c} = :{c}" for c in novos)
        db.execute(text(f"""
            UPDATE encomendas
            SET {sets}, observacoes = CONCAT(IFNULL(observacoes, ''), :sep, :registro)
            WHERE id = :id
        """), {**novos, "sep": separador, "registro": registro, "id": encomenda_id})
        db.commit()
        logger.info(f"Encomenda {encomenda_id} editada por {quem} ({papel}): {list(novos)}")
        return {"message": "Encomenda atualizada", "id": encomenda_id, "alterado": True, "registro": registro}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# DELETE ENCOMENDA
# ============================================================

@router.delete("/{encomenda_id}")
async def delete_encomenda(
    encomenda_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Excluir encomenda"""
    try:
        cond_id = _resolve_cond_id(current_user, None)

        if cond_id is not None:
            exists = db.execute(text("SELECT id FROM encomendas WHERE id = :id AND condominio_id = :cond"),
                                {"id": encomenda_id, "cond": cond_id}).fetchone()
        else:
            exists = db.execute(text("SELECT id FROM encomendas WHERE id = :id"),
                                {"id": encomenda_id}).fetchone()

        if not exists:
            raise HTTPException(status_code=404, detail="Não encontrada")

        db.execute(text("DELETE FROM imagens_mapeamento WHERE encomenda_id = :id"), {"id": encomenda_id})
        db.execute(text("DELETE FROM encomendas WHERE id = :id"), {"id": encomenda_id})
        db.commit()

        return {"message": "Excluída"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# IMAGENS DA ENCOMENDA
# ============================================================

@router.get("/{encomenda_id}/imagens")
async def get_encomenda_imagens(
    encomenda_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Obter URLs das imagens"""
    try:
        cond_id = _resolve_cond_id(current_user, None)

        if cond_id is not None:
            encomenda = db.execute(text("SELECT id, img_etiqueta_server, status FROM encomendas WHERE id = :id AND condominio_id = :cond"),
                                   {"id": encomenda_id, "cond": cond_id}).fetchone()
        else:
            encomenda = db.execute(text("SELECT id, img_etiqueta_server, status FROM encomendas WHERE id = :id"),
                                   {"id": encomenda_id}).fetchone()

        if not encomenda:
            raise HTTPException(status_code=404, detail="Não encontrada")

        response = {"encomenda_id": encomenda_id, "etiqueta_url": None, "assinatura_url": None}

        if encomenda.img_etiqueta_server:
            try:
                response["etiqueta_url"] = await image_storage_service.get_image_url(encomenda.img_etiqueta_server)
            except:
                pass

        if encomenda.status == "entregue":
            try:
                mapeamento = db.execute(text("""
                    SELECT nome_servidor FROM imagens_mapeamento
                    WHERE encomenda_id = :id AND tipo = 'assinatura'
                    ORDER BY id DESC LIMIT 1
                """), {"id": encomenda_id}).fetchone()

                if mapeamento and mapeamento.nome_servidor:
                    response["assinatura_url"] = await image_storage_service.get_image_url(mapeamento.nome_servidor)
            except:
                pass

        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# CONFIRMAR WHATSAPP
# ============================================================

@router.put("/{encomenda_id}/confirmar-whatsapp")
async def confirmar_whatsapp_morador(
    encomenda_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Confirmar WhatsApp"""
    try:
        cond_id = _resolve_cond_id(current_user, None)

        if cond_id is not None:
            result = db.execute(text("SELECT morador_id FROM encomendas WHERE id = :id AND condominio_id = :cond"),
                                {"id": encomenda_id, "cond": cond_id}).fetchone()
        else:
            result = db.execute(text("SELECT morador_id FROM encomendas WHERE id = :id"),
                                {"id": encomenda_id}).fetchone()

        if not result or not result.morador_id:
            raise HTTPException(status_code=404, detail="Não encontrado")

        db.execute(text("UPDATE moradores SET whats_confirmado = NOW() WHERE id = :id"), {"id": result.morador_id})
        db.commit()

        return {"message": "Confirmado"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# OCR
# ============================================================

@router.post("/ocr")
async def process_ocr(ocr_data: dict, current_user=Depends(get_current_user)):
    return {"success": True, "data": ocr_data}


# ==============================================================================
# UPLOAD DE ASSINATURA
# ==============================================================================

from pydantic import BaseModel

class UploadAssinaturaRequest(BaseModel):
    image: str
    encomenda_id: int

@router.post("/storage/upload/assinatura")
async def upload_assinatura(
    dados: UploadAssinaturaRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Upload de assinatura digital para o storage"""
    try:
        logger.info(f"📸 === UPLOAD DE ASSINATURA - Encomenda {dados.encomenda_id} ===")

        query = text("""
            SELECT id, condominio_id, status
            FROM encomendas
            WHERE id = :encomenda_id
        """)

        result = db.execute(query, {"encomenda_id": dados.encomenda_id})
        encomenda = result.fetchone()

        if not encomenda:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Encomenda não encontrada"
            )

        cond_id = _resolve_cond_id(current_user, None)
        if cond_id is not None and encomenda.condominio_id != cond_id:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Sem permissão para esta encomenda"
            )

        logger.info(f"📤 Enviando assinatura para storage...")

        assinatura_filename = await image_storage_service.process_and_upload_assinatura(
            dados.image,
            dados.encomenda_id
        )

        if not assinatura_filename:
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Erro ao salvar assinatura no storage"
            )

        logger.info(f"✅ Assinatura salva: {assinatura_filename}")

        try:
            insert_query = text("""
                INSERT INTO imagens_mapeamento
                (encomenda_id, tipo, nome_personalizado, nome_servidor)
                VALUES (:encomenda_id, 'assinatura', :nome_personalizado, :nome_servidor)
                ON DUPLICATE KEY UPDATE
                nome_servidor = VALUES(nome_servidor),
                created_at = CURRENT_TIMESTAMP
            """)

            db.execute(insert_query, {
                "encomenda_id": dados.encomenda_id,
                "nome_personalizado": assinatura_filename,
                "nome_servidor": assinatura_filename
            })

            db.commit()
            logger.info(f"✅ Assinatura registrada no mapeamento")

        except Exception as e:
            logger.error(f"❌ Erro ao registrar no mapeamento: {str(e)}")

        return {
            "success": True,
            "message": "Assinatura salva com sucesso",
            "filename": assinatura_filename,
            "encomenda_id": dados.encomenda_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao processar upload de assinatura: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao processar upload: {str(e)}"
        )
