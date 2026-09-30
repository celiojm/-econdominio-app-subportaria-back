"""
================================================================================
ARQUIVO: financeiro_contas_pagar.py
PASTA:   ~/backend/financeiro/
ROTA BASE: /api/financeiro/contas-pagar
DESCRIÇÃO: CRUD de contas a pagar da E-CONDOMÍNIO SISTEMAS DE GESTAO LTDA
================================================================================
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import date, timedelta
from pydantic import BaseModel
from app.database import get_db
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Schemas ───────────────────────────────────────────────────────────────────

class ContaPagarCreate(BaseModel):
    descricao:          str
    fornecedor:         Optional[str]  = None
    categoria:          str            = 'outros'
    valor:              float
    data_vencimento:    str            # YYYY-MM-DD
    competencia:        Optional[str]  = None   # YYYY-MM
    forma_pagamento:    Optional[str]  = None
    conta_pagamento:    Optional[str]  = None
    recorrente:         bool           = False
    periodicidade:      Optional[str]  = 'mensal'
    dia_vencimento:     Optional[int]  = None
    observacoes:        Optional[str]  = None
    numero_documento:   Optional[str]  = None
    criado_por:         Optional[str]  = 'Admin'

class ContaPagarUpdate(BaseModel):
    descricao:          Optional[str]   = None
    fornecedor:         Optional[str]   = None
    categoria:          Optional[str]   = None
    valor:              Optional[float] = None
    data_vencimento:    Optional[str]   = None
    competencia:        Optional[str]   = None
    forma_pagamento:    Optional[str]   = None
    conta_pagamento:    Optional[str]   = None
    recorrente:         Optional[bool]  = None
    periodicidade:      Optional[str]   = None
    dia_vencimento:     Optional[int]   = None
    observacoes:        Optional[str]   = None
    numero_documento:   Optional[str]   = None

class MarcarPagoRequest(BaseModel):
    data_pagamento:  str            # YYYY-MM-DD
    valor_pago:      float
    forma_pagamento: Optional[str]  = None
    conta_pagamento: Optional[str]  = None
    observacoes:     Optional[str]  = None
    operador:        Optional[str]  = 'Admin'
    gerar_proxima:   bool           = True   # se recorrente, gera próxima


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    return {
        "id":               row[0],
        "descricao":        row[1],
        "fornecedor":       row[2],
        "categoria":        row[3],
        "valor":            float(row[4]) if row[4] else 0.0,
        "valor_pago":       float(row[5]) if row[5] else None,
        "data_vencimento":  row[6].isoformat() if row[6] else None,
        "data_pagamento":   row[7].isoformat() if row[7] else None,
        "competencia":      row[8],
        "status":           row[9],
        "recorrente":       bool(row[10]),
        "periodicidade":    row[11],
        "dia_vencimento":   row[12],
        "forma_pagamento":  row[13],
        "conta_pagamento":  row[14],
        "observacoes":      row[15],
        "numero_documento": row[16],
        "criado_por":       row[17],
        "data_criacao":     row[18].isoformat() if row[18] else None,
    }

SELECT_ALL = """
    SELECT id, descricao, fornecedor, categoria, valor, valor_pago,
           data_vencimento, data_pagamento, competencia, status,
           recorrente, periodicidade, dia_vencimento, forma_pagamento,
           conta_pagamento, observacoes, numero_documento, criado_por, data_criacao
    FROM contas_a_pagar
"""

def _proxima_data(dia: int, periodicidade: str, base: date) -> date:
    """Calcula a próxima data de vencimento para conta recorrente."""
    from calendar import monthrange
    if periodicidade == 'mensal':
        mes  = base.month + 1 if base.month < 12 else 1
        ano  = base.year if base.month < 12 else base.year + 1
    elif periodicidade == 'trimestral':
        mes  = base.month + 3
        ano  = base.year + (mes - 1) // 12
        mes  = ((mes - 1) % 12) + 1
    elif periodicidade == 'semestral':
        mes  = base.month + 6
        ano  = base.year + (mes - 1) // 12
        mes  = ((mes - 1) % 12) + 1
    else:  # anual
        mes, ano = base.month, base.year + 1

    max_dia = monthrange(ano, mes)[1]
    return date(ano, mes, min(dia, max_dia))


# ── GET /contas-pagar ─────────────────────────────────────────────────────────

@router.get("/contas-pagar")
async def listar_contas(
    status:     Optional[str] = None,
    categoria:  Optional[str] = None,
    competencia: Optional[str] = None,
    vencendo_em: Optional[int] = None,  # próximos N dias
    page:  int = Query(1,  ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    try:
        where, params = ["1=1"], {}

        if status:     where.append("status = :status");         params["status"]     = status
        if categoria:  where.append("categoria = :categoria");   params["categoria"]  = categoria
        if competencia: where.append("competencia = :comp");     params["comp"]       = competencia
        if vencendo_em is not None:
            params["hoje"]   = date.today()
            params["limite"] = date.today() + timedelta(days=vencendo_em)
            where.append("data_vencimento BETWEEN :hoje AND :limite AND status = 'pendente'")

        w = " AND ".join(where)
        total = db.execute(text(f"SELECT COUNT(*) FROM contas_a_pagar WHERE {w}"), params).scalar()

        # Atualiza status vencido automaticamente
        db.execute(text("""
            UPDATE contas_a_pagar
            SET status = 'vencido'
            WHERE status = 'pendente' AND data_vencimento < CURDATE()
        """))
        db.commit()

        offset = (page - 1) * limit
        rows = db.execute(text(f"""
            {SELECT_ALL} WHERE {w}
            ORDER BY
              FIELD(status,'vencido','pendente','pago','cancelado'),
              data_vencimento ASC
            LIMIT :limit OFFSET :offset
        """), {**params, "limit": limit, "offset": offset}).fetchall()

        # Resumo financeiro
        resumo = db.execute(text(f"""
            SELECT
              SUM(CASE WHEN status IN ('pendente','vencido') THEN valor ELSE 0 END) as total_pendente,
              SUM(CASE WHEN status = 'vencido'               THEN valor ELSE 0 END) as total_vencido,
              SUM(CASE WHEN status = 'pago'
                        AND MONTH(data_pagamento) = MONTH(CURDATE())
                        AND YEAR(data_pagamento)  = YEAR(CURDATE())  THEN valor_pago ELSE 0 END) as pago_mes,
              COUNT(CASE WHEN status = 'vencido' THEN 1 END) as qtd_vencidas
            FROM contas_a_pagar
        """)).fetchone()

        return {
            "success": True,
            "data": [_row_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
            "resumo": {
                "total_pendente": float(resumo[0] or 0),
                "total_vencido":  float(resumo[1] or 0),
                "pago_mes":       float(resumo[2] or 0),
                "qtd_vencidas":   int(resumo[3] or 0),
            }
        }
    except Exception as e:
        logger.error(f"Erro listar contas_a_pagar: {e}")
        raise HTTPException(500, str(e))


# ── GET /contas-pagar/:id ─────────────────────────────────────────────────────

@router.get("/contas-pagar/{conta_id}")
async def obter_conta(conta_id: int, db: Session = Depends(get_db)):
    row = db.execute(text(f"{SELECT_ALL} WHERE id = :id"), {"id": conta_id}).fetchone()
    if not row:
        raise HTTPException(404, "Conta não encontrada")
    return {"success": True, "data": _row_to_dict(row)}


# ── POST /contas-pagar ────────────────────────────────────────────────────────

@router.post("/contas-pagar")
async def criar_conta(body: ContaPagarCreate, db: Session = Depends(get_db)):
    try:
        comp = body.competencia
        if not comp and body.data_vencimento:
            comp = body.data_vencimento[:7]  # extrai YYYY-MM

        db.execute(text("""
            INSERT INTO contas_a_pagar
                (descricao, fornecedor, categoria, valor, data_vencimento, competencia,
                 forma_pagamento, conta_pagamento, recorrente, periodicidade, dia_vencimento,
                 observacoes, numero_documento, criado_por)
            VALUES
                (:descricao, :fornecedor, :categoria, :valor, :data_vencimento, :competencia,
                 :forma_pagamento, :conta_pagamento, :recorrente, :periodicidade, :dia_vencimento,
                 :observacoes, :numero_documento, :criado_por)
        """), {
            "descricao":        body.descricao,
            "fornecedor":       body.fornecedor,
            "categoria":        body.categoria,
            "valor":            body.valor,
            "data_vencimento":  body.data_vencimento,
            "competencia":      comp,
            "forma_pagamento":  body.forma_pagamento,
            "conta_pagamento":  body.conta_pagamento,
            "recorrente":       1 if body.recorrente else 0,
            "periodicidade":    body.periodicidade or 'mensal',
            "dia_vencimento":   body.dia_vencimento,
            "observacoes":      body.observacoes,
            "numero_documento": body.numero_documento,
            "criado_por":       body.criado_por or 'Admin',
        })
        db.commit()
        new_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        row = db.execute(text(f"{SELECT_ALL} WHERE id = :id"), {"id": new_id}).fetchone()
        return {"success": True, "message": "Conta criada com sucesso", "data": _row_to_dict(row)}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))


# ── PUT /contas-pagar/:id ─────────────────────────────────────────────────────

@router.put("/contas-pagar/{conta_id}")
async def atualizar_conta(conta_id: int, body: ContaPagarUpdate, db: Session = Depends(get_db)):
    try:
        check = db.execute(text("SELECT id FROM contas_a_pagar WHERE id = :id"), {"id": conta_id}).fetchone()
        if not check:
            raise HTTPException(404, "Conta não encontrada")

        campos = body.dict(exclude_none=True)
        if not campos:
            raise HTTPException(400, "Nenhum campo para atualizar")

        sets   = ", ".join([f"{k} = :{k}" for k in campos])
        campos["id"] = conta_id
        db.execute(text(f"UPDATE contas_a_pagar SET {sets} WHERE id = :id"), campos)
        db.commit()

        row = db.execute(text(f"{SELECT_ALL} WHERE id = :id"), {"id": conta_id}).fetchone()
        return {"success": True, "message": "Conta atualizada", "data": _row_to_dict(row)}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))


# ── POST /contas-pagar/:id/pagar ──────────────────────────────────────────────

@router.post("/contas-pagar/{conta_id}/pagar")
async def marcar_pago(conta_id: int, body: MarcarPagoRequest, db: Session = Depends(get_db)):
    """Marca conta como paga e opcionalmente gera a próxima recorrência."""
    try:
        row = db.execute(text(f"{SELECT_ALL} WHERE id = :id"), {"id": conta_id}).fetchone()
        if not row:
            raise HTTPException(404, "Conta não encontrada")
        if row[9] == 'pago':
            raise HTTPException(409, "Conta já foi marcada como paga")

        conta = _row_to_dict(row)

        # Marca como paga
        db.execute(text("""
            UPDATE contas_a_pagar
            SET status = 'pago', data_pagamento = :dp, valor_pago = :vp,
                forma_pagamento = COALESCE(:fp, forma_pagamento),
                conta_pagamento = COALESCE(:cp, conta_pagamento),
                observacoes     = COALESCE(:obs, observacoes)
            WHERE id = :id
        """), {
            "dp":  body.data_pagamento,
            "vp":  body.valor_pago,
            "fp":  body.forma_pagamento,
            "cp":  body.conta_pagamento,
            "obs": body.observacoes,
            "id":  conta_id,
        })
        db.commit()

        proxima = None
        # Gera próxima recorrência
        if body.gerar_proxima and conta["recorrente"] and conta["dia_vencimento"]:
            base_date = date.fromisoformat(conta["data_vencimento"])
            nova_data = _proxima_data(conta["dia_vencimento"], conta["periodicidade"] or "mensal", base_date)
            nova_comp = nova_data.strftime("%Y-%m")

            # Verifica se já existe para o próximo período
            existe = db.execute(text("""
                SELECT id FROM contas_a_pagar
                WHERE descricao = :desc AND competencia = :comp AND status != 'cancelado'
            """), {"desc": conta["descricao"], "comp": nova_comp}).fetchone()

            if not existe:
                db.execute(text("""
                    INSERT INTO contas_a_pagar
                        (descricao, fornecedor, categoria, valor, data_vencimento, competencia,
                         forma_pagamento, conta_pagamento, recorrente, periodicidade, dia_vencimento,
                         criado_por)
                    VALUES
                        (:descricao, :fornecedor, :categoria, :valor, :data_vencimento, :competencia,
                         :forma_pagamento, :conta_pagamento, :recorrente, :periodicidade, :dia_vencimento,
                         :criado_por)
                """), {
                    "descricao":       conta["descricao"],
                    "fornecedor":      conta["fornecedor"],
                    "categoria":       conta["categoria"],
                    "valor":           conta["valor"],
                    "data_vencimento": nova_data.isoformat(),
                    "competencia":     nova_comp,
                    "forma_pagamento": conta["forma_pagamento"],
                    "conta_pagamento": conta["conta_pagamento"],
                    "recorrente":      1,
                    "periodicidade":   conta["periodicidade"],
                    "dia_vencimento":  conta["dia_vencimento"],
                    "criado_por":      body.operador or "Sistema",
                })
                db.commit()
                proxima = nova_data.isoformat()

        return {
            "success":  True,
            "message":  "Conta marcada como paga",
            "proxima_gerada": proxima,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))


# ── DELETE /contas-pagar/:id ──────────────────────────────────────────────────

@router.delete("/contas-pagar/{conta_id}")
async def cancelar_conta(conta_id: int, db: Session = Depends(get_db)):
    try:
        check = db.execute(text("SELECT id FROM contas_a_pagar WHERE id = :id"), {"id": conta_id}).fetchone()
        if not check:
            raise HTTPException(404, "Conta não encontrada")
        db.execute(text("UPDATE contas_a_pagar SET status = 'cancelado' WHERE id = :id"), {"id": conta_id})
        db.commit()
        return {"success": True, "message": "Conta cancelada"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))


# ── GET /contas-pagar/relatorio/mensal ────────────────────────────────────────

@router.get("/contas-pagar/relatorio/mensal")
async def relatorio_mensal(
    ano:  int = Query(date.today().year),
    db: Session = Depends(get_db)
):
    """Resumo mensal de despesas por categoria para o ano informado."""
    try:
        rows = db.execute(text("""
            SELECT
                competencia,
                categoria,
                COUNT(*) as qtd,
                SUM(valor) as total_previsto,
                SUM(CASE WHEN status = 'pago' THEN valor_pago ELSE 0 END) as total_pago
            FROM contas_a_pagar
            WHERE competencia LIKE :ano AND status != 'cancelado'
            GROUP BY competencia, categoria
            ORDER BY competencia, categoria
        """), {"ano": f"{ano}%"}).fetchall()

        return {
            "success": True,
            "ano": ano,
            "data": [
                {"competencia": r[0], "categoria": r[1], "qtd": r[2],
                 "total_previsto": float(r[3] or 0), "total_pago": float(r[4] or 0)}
                for r in rows
            ]
        }
    except Exception as e:
        raise HTTPException(500, str(e))
