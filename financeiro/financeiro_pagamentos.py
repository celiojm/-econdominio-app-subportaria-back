# ================================================================================
#  PATH: backend/financeiro/financeiro_pagamentos.py
#  DESCRIPTION: Endpoints de LISTAGEM de pagamentos (sincronização em financeiro_sync.py)
#  VERSÃO: 3.1.0 - validade_ate incluída na query + endpoint ajuste manual de validade
#  data criação: 2026-05-20
# ================================================================================

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import datetime, date, timedelta
from pydantic import BaseModel
import logging

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ================================================================================
#  LISTAR PAGAMENTOS RECEBIDOS
# ================================================================================

@router.get("/pagamentos")
async def listar_pagamentos(
    mes: Optional[str] = Query(None, description="Mês no formato YYYY-MM"),
    data_inicio: Optional[str] = Query(None, description="Data início (YYYY-MM-DD)"),
    data_fim: Optional[str] = Query(None, description="Data fim (YYYY-MM-DD)"),
    forma_pagamento: Optional[str] = Query(None, description="Filtrar por forma de pagamento"),
    id_condominio: Optional[int] = Query(None, description="Filtrar por condomínio"),
    cliente: Optional[str] = Query(None, description="Filtrar por nome ou ID do cliente"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    Lista pagamentos recebidos (cobranças com status 'pago').
    Inclui validade_ate do condomínio diretamente na resposta.
    """
    try:
        # Construir filtros de data
        if mes:
            ano, mes_num = mes.split("-")
            data_inicio = f"{ano}-{mes_num}-01"
            if int(mes_num) == 12:
                data_fim = f"{int(ano)+1}-01-01"
            else:
                data_fim = f"{ano}-{int(mes_num)+1:02d}-01"
        elif not data_inicio:
            hoje = date.today()
            data_inicio = hoje.replace(day=1).isoformat()
            if hoje.month == 12:
                data_fim = hoje.replace(year=hoje.year+1, month=1, day=1).isoformat()
            else:
                data_fim = hoje.replace(month=hoje.month+1, day=1).isoformat()

        # Query base
        where_clauses = ["c.status = 'pago'"]
        params = {}

        if data_inicio:
            where_clauses.append("c.data_pagamento >= :data_inicio")
            params["data_inicio"] = data_inicio

        if data_fim:
            where_clauses.append("c.data_pagamento < :data_fim")
            params["data_fim"] = data_fim

        if forma_pagamento and forma_pagamento.lower() != "todas":
            where_clauses.append("c.forma_pagamento = :forma_pagamento")
            params["forma_pagamento"] = forma_pagamento.lower()

        if id_condominio:
            where_clauses.append("c.id_condominio = :id_condominio")
            params["id_condominio"] = id_condominio

        if cliente:
            where_clauses.append("(cond.nome LIKE :cliente OR c.asaas_customer_id LIKE :cliente)")
            params["cliente"] = f"%{cliente}%"

        where_sql = " AND ".join(where_clauses)

        # Contar total
        count_sql = f"""
            SELECT COUNT(*) FROM cobrancas c
            LEFT JOIN condominios cond ON c.id_condominio = cond.id
            WHERE {where_sql}
        """
        result = db.execute(text(count_sql), params)
        total = result.scalar()

        # Buscar pagamentos com paginação — inclui validade_ate
        offset = (page - 1) * per_page
        params["limit"] = per_page
        params["offset"] = offset

        query_sql = f"""
            SELECT
                c.id_cobranca,
                c.id_condominio,
                cond.nome as condominio_nome,
                c.valor,
                c.valor_pago,
                c.data_vencimento,
                c.data_pagamento,
                c.forma_pagamento,
                c.descricao,
                c.asaas_payment_id,
                c.asaas_customer_id,
                c.data_criacao,
                COALESCE(a.validade_ate, cond.validade_ate) as validade_ate
            FROM cobrancas c
            LEFT JOIN condominios cond ON c.id_condominio = cond.id
            LEFT JOIN assinaturas a ON a.id_condominio = c.id_condominio
            WHERE {where_sql}
            ORDER BY c.data_pagamento DESC, c.id_cobranca DESC
            LIMIT :limit OFFSET :offset
        """

        result = db.execute(text(query_sql), params)
        rows = result.fetchall()

        pagamentos = []
        for row in rows:
            pagamentos.append({
                "id": row.id_cobranca,
                "id_condominio": row.id_condominio,
                "condominio": row.condominio_nome,
                "valor": float(row.valor) if row.valor else 0,
                "valor_pago": float(row.valor_pago) if row.valor_pago else float(row.valor) if row.valor else 0,
                "data_vencimento": row.data_vencimento.isoformat() if row.data_vencimento else None,
                "data_pagamento": row.data_pagamento.isoformat() if row.data_pagamento else None,
                "forma_pagamento": row.forma_pagamento.upper() if row.forma_pagamento else "UNDEFINED",
                "descricao": row.descricao,
                "asaas_payment_id": row.asaas_payment_id,
                "asaas_customer_id": row.asaas_customer_id,
                "data_criacao": row.data_criacao.isoformat() if row.data_criacao else None,
                "validade_ate": row.validade_ate.isoformat() if row.validade_ate else None,
            })

        return {
            "recebimentos": pagamentos,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page if total > 0 else 1,
            "periodo": {
                "inicio": data_inicio,
                "fim": data_fim
            }
        }

    except Exception as e:
        logger.error(f"Erro ao listar pagamentos: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================================
#  AJUSTE MANUAL DE VALIDADE DO CONDOMÍNIO
# ================================================================================

class AjusteValidadeRequest(BaseModel):
    nova_validade: str  # formato YYYY-MM-DD
    motivo: Optional[str] = None


@router.put("/condominios/{id_condominio}/validade")
async def ajustar_validade(
    id_condominio: int,
    body: AjusteValidadeRequest,
    db: Session = Depends(get_db)
):
    """
    Ajusta manualmente a validade da assinatura de um condomínio.
    Útil quando o condomínio atrasou o pagamento mas vai regularizar.
    """
    try:
        # Valida data
        nova_validade = date.fromisoformat(body.nova_validade)

        # Verifica se condomínio existe
        cond = db.execute(
            text("SELECT id, nome, validade_ate FROM condominios WHERE id = :id"),
            {"id": id_condominio}
        ).fetchone()

        if not cond:
            raise HTTPException(status_code=404, detail="Condomínio não encontrado.")

        validade_anterior = cond.validade_ate

        # Atualiza condominios
        db.execute(
            text("UPDATE condominios SET validade_ate = :v WHERE id = :id"),
            {"v": nova_validade.isoformat(), "id": id_condominio}
        )

        # Atualiza assinatura se existir
        db.execute(
            text("""
                UPDATE assinaturas
                SET validade_ate = :v, data_atualizacao = NOW()
                WHERE id_condominio = :id
            """),
            {"v": nova_validade.isoformat(), "id": id_condominio}
        )

        db.commit()

        logger.info(
            f"Validade ajustada manualmente: condomínio {id_condominio} ({cond.nome}) "
            f"{validade_anterior} → {nova_validade} | motivo: {body.motivo}"
        )

        return {
            "success": True,
            "condominio_id": id_condominio,
            "condominio_nome": cond.nome,
            "validade_anterior": validade_anterior.isoformat() if validade_anterior else None,
            "nova_validade": nova_validade.isoformat(),
            "motivo": body.motivo
        }

    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="Data inválida. Use o formato YYYY-MM-DD.")
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao ajustar validade: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================================
#  TOTALIZADORES DE PAGAMENTOS
# ================================================================================

@router.get("/pagamentos/totalizadores")
async def totalizadores_pagamentos(
    mes: Optional[str] = Query(None, description="Mês no formato YYYY-MM"),
    data_inicio: Optional[str] = Query(None, description="Data início (YYYY-MM-DD)"),
    data_fim: Optional[str] = Query(None, description="Data fim (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """
    Retorna totalizadores de pagamentos por forma de pagamento.
    """
    try:
        if mes:
            ano, mes_num = mes.split("-")
            data_inicio = f"{ano}-{mes_num}-01"
            if int(mes_num) == 12:
                data_fim = f"{int(ano)+1}-01-01"
            else:
                data_fim = f"{ano}-{int(mes_num)+1:02d}-01"
        elif not data_inicio:
            hoje = date.today()
            data_inicio = hoje.replace(day=1).isoformat()
            if hoje.month == 12:
                data_fim = hoje.replace(year=hoje.year+1, month=1, day=1).isoformat()
            else:
                data_fim = hoje.replace(month=hoje.month+1, day=1).isoformat()

        query = """
            SELECT
                COALESCE(forma_pagamento, 'outros') as forma,
                COUNT(*) as quantidade,
                SUM(COALESCE(valor_pago, valor)) as total
            FROM cobrancas
            WHERE status = 'pago'
            AND data_pagamento >= :data_inicio
            AND data_pagamento < :data_fim
            GROUP BY forma_pagamento
        """

        result = db.execute(text(query), {"data_inicio": data_inicio, "data_fim": data_fim})
        rows = result.fetchall()

        por_forma = {}
        total_geral = 0
        qtd_total = 0

        for row in rows:
            forma = (row.forma or "outros").upper()
            valor = float(row.total) if row.total else 0
            qtd = row.quantidade
            por_forma[forma] = {"quantidade": qtd, "total": valor}
            total_geral += valor
            qtd_total += qtd

        return {
            "periodo": {"inicio": data_inicio, "fim": data_fim},
            "total_geral": total_geral,
            "quantidade_total": qtd_total,
            "por_forma_pagamento": por_forma,
            "detalhes": {
                "boleto": por_forma.get("BOLETO", {"quantidade": 0, "total": 0}),
                "pix": por_forma.get("PIX", {"quantidade": 0, "total": 0}),
                "cartao": por_forma.get("CARTAO", {"quantidade": 0, "total": 0}),
                "outros": por_forma.get("OUTROS", {"quantidade": 0, "total": 0})
            }
        }

    except Exception as e:
        logger.error(f"Erro ao calcular totalizadores: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================================
#  STATUS DA SINCRONIZAÇÃO
# ================================================================================

@router.get("/pagamentos/status-sync")
async def status_sincronizacao(db: Session = Depends(get_db)):
    """
    Retorna informações sobre a última sincronização e status geral.
    """
    try:
        result = db.execute(text("""
            SELECT
                MAX(data_atualizacao) as ultima_atualizacao,
                COUNT(*) as total_pagos,
                SUM(COALESCE(valor_pago, valor)) as valor_total
            FROM cobrancas
            WHERE status = 'pago'
        """))
        row = result.fetchone()

        result2 = db.execute(text("""
            SELECT COUNT(*) as pendentes FROM cobrancas WHERE status = 'pendente'
        """))
        pendentes = result2.scalar()

        return {
            "ultima_sincronizacao": row.ultima_atualizacao.isoformat() if row.ultima_atualizacao else None,
            "total_pagamentos_recebidos": row.total_pagos or 0,
            "valor_total_recebido": float(row.valor_total) if row.valor_total else 0,
            "cobrancas_pendentes": pendentes or 0
        }

    except Exception as e:
        logger.error(f"Erro ao buscar status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
