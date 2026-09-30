# ============================================================================
# ARQUIVO: financeiro_preco_routes.py
# PASTA: /home/visionlpr/backend/financeiro/
# DESCRIÇÃO: Endpoint centralizado de cálculo de preço — lê tabela_precos e
#            tabela_precos_planos do banco. Substitui fórmulas hardcoded nos
#            frontends e scripts.
# VERSÃO: 1.1.0 - Aplica piso valor_minimo da tabela_precos (tabela vigente 2026-07)
# data criação: 2026-06-08    data alteração: 2026-07-26
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db

router = APIRouter()

def _calcular_valor_mensal_base(qtd: int, faixas: list) -> float:
    """Calcula o valor mensal base para a quantidade de unidades informada.
    Aplica o piso `valor_minimo` da faixa — impede que 26 unidades custem
    menos que 25 (degrau invertido da tabela)."""
    if qtd <= 0:
        qtd = 1
    for faixa in faixas:
        qtd_min  = faixa["qtd_min"]
        qtd_max  = faixa["qtd_max"]   # None = sem limite
        tipo     = faixa["tipo"]
        piso     = float(faixa.get("valor_minimo") or 0)

        dentro = (qtd >= qtd_min) and (qtd_max is None or qtd <= qtd_max)
        if not dentro:
            continue

        if tipo == "fixo":
            valor_fixo = float(faixa["valor_fixo"])
            # faixa > 2000: valor_fixo é preço UNITÁRIO
            if qtd_min > 25:
                return round(max(qtd * valor_fixo, piso), 2)
            # faixa 1-25: valor_fixo é valor TOTAL
            return round(max(valor_fixo, piso), 2)

        # tipo == linear
        coef_a     = float(faixa["coef_a"])
        coef_b     = float(faixa["coef_b"])
        fator_min  = float(faixa["fator_min"])
        fator_max  = float(faixa["fator_max"])
        fator      = (qtd - fator_min) / (fator_max - fator_min)
        preco_unit = coef_a - (coef_b * fator)
        return round(max(qtd * preco_unit, piso), 2)

    # Fallback — não deveria chegar aqui se tabela estiver completa
    raise HTTPException(status_code=500, detail=f"Faixa não encontrada para qtd={qtd}")

@router.get("/preco/calcular")
def calcular_preco(
    qtd:   int            = Query(..., ge=1, description="Quantidade de unidades"),
    plano: Optional[str]  = Query("mensal", description="mensal|trimestral|semestral|anual"),
    db:    Session        = Depends(get_db),
):
    """
    Calcula o valor do plano para a quantidade de unidades informada.

    Retorna:
    - valor_mensal_base  : valor mensal sem desconto
    - desconto_pct       : desconto aplicado (%)
    - valor_mensal       : valor mensal com desconto
    - valor_total        : valor total a cobrar (mensal × meses)
    - meses              : número de meses do plano
    - preco_unitario     : valor por unidade/mês
    - plano              : código do plano
    - plano_nome         : nome do plano
    """
    # Carregar faixas ativas ordenadas por qtd_min
    rows = db.execute(text("""
        SELECT qtd_min, qtd_max, tipo, valor_fixo, valor_minimo, coef_a, coef_b, fator_min, fator_max
        FROM tabela_precos
        WHERE ativo = 1
        ORDER BY qtd_min ASC
    """)).fetchall()

    if not rows:
        raise HTTPException(status_code=500, detail="Tabela de preços não configurada")

    faixas = [dict(r._mapping) for r in rows]

    # Carregar plano
    plano_id = (plano or "mensal").lower()
    plano_row = db.execute(text("""
        SELECT codigo, nome, meses, desconto_pct
        FROM tabela_precos_planos
        WHERE codigo = :codigo AND ativo = 1
    """), {"codigo": plano_id}).fetchone()

    if not plano_row:
        raise HTTPException(status_code=400, detail=f"Plano '{plano_id}' não encontrado")

    meses        = plano_row.meses
    desconto_pct = float(plano_row.desconto_pct)

    # Calcular
    valor_mensal_base = _calcular_valor_mensal_base(qtd, faixas)
    valor_mensal      = round(valor_mensal_base * (1 - desconto_pct / 100), 2)
    valor_total       = round(valor_mensal * meses, 2) if meses > 1 else valor_mensal
    preco_unitario    = round(valor_total / qtd / meses, 4) if qtd > 0 else 0

    return {
        "qtd":               qtd,
        "plano":             plano_row.codigo,
        "plano_nome":        plano_row.nome,
        "meses":             meses,
        "desconto_pct":      desconto_pct,
        "valor_mensal_base": valor_mensal_base,
        "valor_mensal":      valor_mensal,
        "valor_total":       valor_total,
        "preco_unitario":    preco_unitario,
    }

@router.get("/preco/planos")
def listar_planos(
    qtd:            Optional[int] = Query(None, ge=1, description="Se informado, retorna valores calculados para cada plano"),
    condominio_id:  Optional[int] = Query(None, description="Se informado, respeita valor_plano_final travado do condominio"),
    db:             Session       = Depends(get_db),
):
    """
    Lista todos os planos disponíveis.
    Se qtd for informado, retorna os valores calculados para cada plano.
    Se condominio_id for informado e o condominio tiver valor_plano_final > 0,
    usa esse valor como base em vez de recalcular pela tabela_precos —
    protege clientes ja contratados de reajustes automaticos.
    """
    planos = db.execute(text("""
        SELECT codigo, nome, meses, desconto_pct
        FROM tabela_precos_planos
        WHERE ativo = 1
        ORDER BY meses ASC
    """)).fetchall()

    # Valor travado do condominio (se houver) tem prioridade sobre a tabela_precos
    valor_travado = 0.0
    qtd_efetivo   = qtd
    if condominio_id:
        cond = db.execute(text("""
            SELECT total_apartamentos, valor_plano_final
            FROM condominios WHERE id = :id
        """), {"id": condominio_id}).fetchone()
        if cond:
            valor_travado = float(cond.valor_plano_final or 0)
            if not qtd_efetivo:
                qtd_efetivo = cond.total_apartamentos or 0

    faixas = []
    if qtd_efetivo and valor_travado <= 0:
        rows = db.execute(text("""
            SELECT qtd_min, qtd_max, tipo, valor_fixo, valor_minimo, coef_a, coef_b, fator_min, fator_max
            FROM tabela_precos
            WHERE ativo = 1
            ORDER BY qtd_min ASC
        """)).fetchall()
        faixas = [dict(r._mapping) for r in rows]

    resultado = []
    for p in planos:
        item = {
            "codigo":       p.codigo,
            "nome":         p.nome,
            "meses":        p.meses,
            "desconto_pct": float(p.desconto_pct),
        }
        if valor_travado > 0:
            desc       = float(p.desconto_pct)
            val_mensal = round(valor_travado * (1 - desc / 100), 2)
            val_total  = round(val_mensal * p.meses, 2) if p.meses > 1 else val_mensal
            item["valor_mensal_base"] = valor_travado
            item["valor_mensal"]      = val_mensal
            item["valor_total"]       = val_total
        elif qtd_efetivo and faixas:
            base       = _calcular_valor_mensal_base(qtd_efetivo, faixas)
            desc       = float(p.desconto_pct)
            val_mensal = round(base * (1 - desc / 100), 2)
            val_total  = round(val_mensal * p.meses, 2) if p.meses > 1 else val_mensal
            item["valor_mensal_base"] = base
            item["valor_mensal"]      = val_mensal
            item["valor_total"]       = val_total
            item["preco_unitario"]    = round(val_total / qtd_efetivo / p.meses, 4)
        resultado.append(item)

    return {"planos": resultado, "qtd": qtd_efetivo, "condominio_id": condominio_id}
