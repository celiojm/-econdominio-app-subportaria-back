# ================================================================================
#  PATH: backend/financeiro/financeiro_dashboard.py
#  DESCRIPTION: Endpoints para Dashboard Financeiro - VERSÃO 2.1 (robusto)
# ================================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
import logging

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/dashboard")
async def obter_dashboard(db: Session = Depends(get_db)):
    """
    Retorna dados consolidados para o Dashboard.
    """
    try:
        hoje = date.today()
        
        # 1. Total de condomínios
        result = db.execute(text("SELECT COUNT(*) as total FROM condominios"))
        total_condominios = result.fetchone().total or 0
        
        # 2. Assinaturas
        result = db.execute(text("""
            SELECT 
                COUNT(*) as total,
                COALESCE(SUM(CASE WHEN status = 'ativa' THEN 1 ELSE 0 END), 0) as ativas,
                COALESCE(SUM(CASE WHEN status = 'ativa' THEN valor ELSE 0 END), 0) as mrr,
                COALESCE(SUM(CASE WHEN status = 'suspensa' THEN 1 ELSE 0 END), 0) as suspensas,
                COALESCE(SUM(CASE WHEN status = 'cancelada' THEN 1 ELSE 0 END), 0) as canceladas,
                COALESCE(SUM(CASE WHEN status = 'inadimplente' THEN 1 ELSE 0 END), 0) as inadimplentes
            FROM assinaturas
        """))
        ass = result.fetchone()
        
        # 3. Cobranças do mês atual
        result = db.execute(text("""
            SELECT 
                COUNT(*) as total,
                COALESCE(SUM(valor), 0) as valor_total,
                COALESCE(SUM(CASE WHEN status = 'pago' THEN COALESCE(valor_pago, valor) ELSE 0 END), 0) as recebido,
                COALESCE(SUM(CASE WHEN status = 'pendente' THEN valor ELSE 0 END), 0) as pendente,
                COALESCE(SUM(CASE WHEN status = 'vencido' THEN valor ELSE 0 END), 0) as vencido,
                COALESCE(SUM(CASE WHEN status = 'pago' THEN 1 ELSE 0 END), 0) as qtd_pagas,
                COALESCE(SUM(CASE WHEN status = 'pendente' THEN 1 ELSE 0 END), 0) as qtd_pendentes,
                COALESCE(SUM(CASE WHEN status = 'vencido' THEN 1 ELSE 0 END), 0) as qtd_vencidas
            FROM cobrancas
            WHERE MONTH(data_vencimento) = MONTH(:hoje)
            AND YEAR(data_vencimento) = YEAR(:hoje)
        """), {"hoje": hoje})
        cob_mes = result.fetchone()
        
        # 4. Cobranças últimos 6 meses (para gráfico)
        receita_mensal = []
        for i in range(5, -1, -1):
            mes_ref = hoje - relativedelta(months=i)
            result = db.execute(text("""
                SELECT 
                    COALESCE(SUM(CASE WHEN status = 'pago' THEN COALESCE(valor_pago, valor) ELSE 0 END), 0) as recebido,
                    COALESCE(SUM(CASE WHEN status IN ('pendente', 'vencido') THEN valor ELSE 0 END), 0) as pendente
                FROM cobrancas
                WHERE MONTH(data_vencimento) = :mes
                AND YEAR(data_vencimento) = :ano
            """), {"mes": mes_ref.month, "ano": mes_ref.year})
            r = result.fetchone()
            receita_mensal.append({
                "mes": mes_ref.strftime("%b/%y"),
                "mes_num": mes_ref.strftime("%Y-%m"),
                "recebido": float(r.recebido or 0),
                "pendente": float(r.pendente or 0)
            })
        
        # 5. Cobranças recentes (últimas 10)
        result = db.execute(text("""
            SELECT 
                c.id_cobranca,
                c.id_condominio,
                COALESCE(cond.nome, CONCAT('Condomínio #', c.id_condominio)) as condominio,
                c.valor,
                c.valor_pago,
                c.data_vencimento,
                c.data_pagamento,
                c.status,
                c.forma_pagamento
            FROM cobrancas c
            LEFT JOIN condominios cond ON c.id_condominio = cond.id
            ORDER BY c.data_criacao DESC
            LIMIT 10
        """))
        cobrancas_recentes = [
            {
                "id": r.id_cobranca,
                "condominio": r.condominio,
                "valor": float(r.valor) if r.valor else 0,
                "valor_pago": float(r.valor_pago) if r.valor_pago else None,
                "vencimento": r.data_vencimento.isoformat() if r.data_vencimento else None,
                "pagamento": r.data_pagamento.isoformat() if r.data_pagamento else None,
                "status": r.status,
                "forma_pagamento": r.forma_pagamento
            }
            for r in result.fetchall()
        ]
        
        # 6. Top 5 condomínios por receita
        result = db.execute(text("""
            SELECT 
                c.id_condominio,
                COALESCE(cond.nome, CONCAT('Condomínio #', c.id_condominio)) as condominio,
                COALESCE(SUM(CASE WHEN c.status = 'pago' THEN COALESCE(c.valor_pago, c.valor) ELSE 0 END), 0) as total_pago,
                COUNT(*) as total_cobrancas
            FROM cobrancas c
            LEFT JOIN condominios cond ON c.id_condominio = cond.id
            GROUP BY c.id_condominio, condominio
            ORDER BY total_pago DESC
            LIMIT 5
        """))
        top_condominios = [
            {
                "id": r.id_condominio,
                "nome": r.condominio,
                "total_pago": float(r.total_pago or 0),
                "total_cobrancas": r.total_cobrancas
            }
            for r in result.fetchall()
        ]
        
        # 7. Distribuição por status de cobrança (para gráfico pizza)
        result = db.execute(text("""
            SELECT 
                status,
                COUNT(*) as quantidade,
                COALESCE(SUM(valor), 0) as valor_total
            FROM cobrancas
            WHERE MONTH(data_vencimento) = MONTH(:hoje)
            AND YEAR(data_vencimento) = YEAR(:hoje)
            GROUP BY status
        """), {"hoje": hoje})
        dist_status = [
            {"status": r.status, "quantidade": r.quantidade, "valor": float(r.valor_total or 0)}
            for r in result.fetchall()
        ]
        
        # 8. Cobranças vencendo em breve (próximos 7 dias)
        result = db.execute(text("""
            SELECT COUNT(*) as total, COALESCE(SUM(valor), 0) as valor
            FROM cobrancas
            WHERE status = 'pendente'
            AND data_vencimento BETWEEN :hoje AND :fim
        """), {"hoje": hoje, "fim": hoje + timedelta(days=7)})
        vencendo = result.fetchone()
        
        # 9. Inadimplência (cobranças vencidas)
        result = db.execute(text("""
            SELECT COUNT(*) as total, COALESCE(SUM(valor), 0) as valor
            FROM cobrancas
            WHERE status = 'vencido'
        """))
        inadimplencia = result.fetchone()
        
        # 10. Novas assinaturas este mês
        result = db.execute(text("""
            SELECT COUNT(*) as total
            FROM assinaturas
            WHERE MONTH(data_criacao) = MONTH(:hoje)
            AND YEAR(data_criacao) = YEAR(:hoje)
        """), {"hoje": hoje})
        novas_mes = result.fetchone().total or 0
        
        # 11. Assinaturas expirando (próximos 30 dias) - NOVO
        assinaturas_expirando = []
        validades = {"vencidas": 0, "vence_7_dias": 0, "vence_30_dias": 0, "ok": 0}
        try:
            result = db.execute(text("""
                SELECT 
                    c.id,
                    c.nome,
                    COALESCE(a.validade_ate, c.validade_ate) as validade,
                    DATEDIFF(COALESCE(a.validade_ate, c.validade_ate), :hoje) as dias_restantes,
                    a.tipo_plano,
                    a.valor
                FROM condominios c
                LEFT JOIN assinaturas a ON a.id_condominio = c.id
                WHERE COALESCE(a.validade_ate, c.validade_ate) IS NOT NULL
                ORDER BY validade ASC
            """), {"hoje": hoje})
            
            for r in result.fetchall():
                dias = r.dias_restantes if r.dias_restantes is not None else 999
                
                if dias < 0:
                    validades["vencidas"] += 1
                elif dias <= 7:
                    validades["vence_7_dias"] += 1
                elif dias <= 30:
                    validades["vence_30_dias"] += 1
                else:
                    validades["ok"] += 1
                
                if dias <= 30:
                    assinaturas_expirando.append({
                        "id": r.id,
                        "nome": r.nome,
                        "validade": r.validade.isoformat() if r.validade else None,
                        "dias_restantes": dias,
                        "tipo_plano": r.tipo_plano,
                        "valor": float(r.valor) if r.valor else None,
                        "status": "vencida" if dias < 0 else "expirando"
                    })
        except Exception as e:
            logger.warning(f"Erro ao buscar validades: {e}")
        
        # Calcular taxa de recebimento
        valor_esperado = float(cob_mes.valor_total or 0)
        valor_recebido = float(cob_mes.recebido or 0)
        taxa_recebimento = (valor_recebido / valor_esperado * 100) if valor_esperado > 0 else 0
        
        return {
            "gerado_em": datetime.now().isoformat(),
            "resumo": {
                "total_condominios": total_condominios,
                "assinaturas_ativas": ass.ativas or 0,
                "assinaturas_total": ass.total or 0,
                "assinaturas_suspensas": ass.suspensas or 0,
                "assinaturas_canceladas": ass.canceladas or 0,
                "assinaturas_inadimplentes": ass.inadimplentes or 0,
                "mrr": float(ass.mrr or 0),
                "arr": float((ass.mrr or 0) * 12),
                "novas_assinaturas_mes": novas_mes
            },
            "mes_atual": {
                "total_cobrancas": cob_mes.total or 0,
                "valor_esperado": valor_esperado,
                "recebido": valor_recebido,
                "pendente": float(cob_mes.pendente or 0),
                "vencido": float(cob_mes.vencido or 0),
                "taxa_recebimento": round(taxa_recebimento, 1),
                "qtd_pagas": cob_mes.qtd_pagas or 0,
                "qtd_pendentes": cob_mes.qtd_pendentes or 0,
                "qtd_vencidas": cob_mes.qtd_vencidas or 0
            },
            "vencendo_7_dias": {
                "quantidade": vencendo.total or 0,
                "valor": float(vencendo.valor or 0)
            },
            "inadimplencia": {
                "quantidade": inadimplencia.total or 0,
                "valor": float(inadimplencia.valor or 0)
            },
            "validades": validades,
            "assinaturas_expirando": assinaturas_expirando,
            "receita_mensal": receita_mensal,
            "distribuicao_status": dist_status,
            "cobrancas_recentes": cobrancas_recentes,
            "top_condominios": top_condominios
        }
        
    except Exception as e:
        logger.error(f"Erro ao gerar dashboard: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
