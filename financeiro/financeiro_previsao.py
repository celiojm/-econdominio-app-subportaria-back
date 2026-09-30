# ================================================================================
#  PATH: backend/financeiro/financeiro_previsao.py
#  DESCRIPTION: Endpoints para previsão financeira
# ================================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import logging

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/previsao")
async def obter_previsao(db: Session = Depends(get_db)):
    """
    Retorna previsão de receita dos próximos 12 meses.
    """
    try:
        hoje = date.today()
        
        # 1. Resumo de assinaturas por status
        result = db.execute(text("""
            SELECT 
                status,
                COUNT(*) as quantidade,
                SUM(valor) as valor_total
            FROM assinaturas
            GROUP BY status
        """))
        assinaturas_status = [
            {"status": r.status, "quantidade": r.quantidade, "valor_total": float(r.valor_total or 0)}
            for r in result.fetchall()
        ]
        
        # 2. Assinaturas ativas por plano
        result = db.execute(text("""
            SELECT 
                a.id_plano,
                COALESCE(p.codigo, a.tipo_plano) as plano_nome,
                COALESCE(p.dias_validade, 30) as dias_validade,
                COUNT(*) as quantidade,
                SUM(a.valor) as valor_total
            FROM assinaturas a
            LEFT JOIN planos p ON a.id_plano = p.id_plano
            WHERE a.status = 'ativa'
            GROUP BY a.id_plano, plano_nome, dias_validade
            ORDER BY quantidade DESC
        """))
        assinaturas_plano = [
            {
                "id_plano": r.id_plano,
                "plano_nome": r.plano_nome,
                "dias_validade": r.dias_validade,
                "quantidade": r.quantidade,
                "valor_total": float(r.valor_total or 0)
            }
            for r in result.fetchall()
        ]
        
        # 3. Calcular receita antecipada (assinaturas pagas com validade futura)
        result = db.execute(text("""
            SELECT 
                a.id_assinatura,
                a.valor,
                a.validade_ate,
                a.data_inicio,
                COALESCE(p.dias_validade, 30) as dias_validade
            FROM assinaturas a
            LEFT JOIN planos p ON a.id_plano = p.id_plano
            WHERE a.status = 'ativa'
        """))
        assinaturas_ativas = result.fetchall()
        
        # 4. Calcular previsão mês a mês (próximos 12 meses)
        previsao_mensal = []
        receita_antecipada_total = 0
        
        for i in range(12):
            mes_ref = hoje + relativedelta(months=i)
            primeiro_dia = mes_ref.replace(day=1)
            ultimo_dia = (primeiro_dia + relativedelta(months=1)) - relativedelta(days=1)
            
            receita_mes = 0
            receita_antecipada_mes = 0
            receita_nova_mes = 0
            
            for ass in assinaturas_ativas:
                valor_mensal = float(ass.valor)
                dias_validade = ass.dias_validade or 30
                
                # Calcular valor mensal proporcional
                if dias_validade == 30:
                    valor_mes = valor_mensal
                elif dias_validade == 90:
                    valor_mes = valor_mensal / 3
                elif dias_validade == 180:
                    valor_mes = valor_mensal / 6
                elif dias_validade == 360:
                    valor_mes = valor_mensal / 12
                else:
                    valor_mes = valor_mensal * 30 / dias_validade
                
                # Se tem validade_ate definida
                if ass.validade_ate:
                    validade = ass.validade_ate
                    if validade >= primeiro_dia:
                        # Ainda está coberto pelo pagamento antecipado
                        if validade >= ultimo_dia:
                            receita_antecipada_mes += valor_mes
                        else:
                            # Parte do mês coberto, parte precisa renovar
                            dias_cobertos = (validade - primeiro_dia).days + 1
                            dias_mes = (ultimo_dia - primeiro_dia).days + 1
                            receita_antecipada_mes += valor_mes * (dias_cobertos / dias_mes)
                            receita_nova_mes += valor_mes * ((dias_mes - dias_cobertos) / dias_mes)
                    else:
                        # Precisa renovar
                        receita_nova_mes += valor_mes
                else:
                    # Sem validade definida, assume renovação mensal
                    receita_nova_mes += valor_mes
                
                receita_mes = receita_antecipada_mes + receita_nova_mes
            
            previsao_mensal.append({
                "mes": mes_ref.strftime("%Y-%m"),
                "mes_nome": mes_ref.strftime("%b/%Y"),
                "receita_total": round(receita_mes, 2),
                "receita_antecipada": round(receita_antecipada_mes, 2),
                "receita_renovacao": round(receita_nova_mes, 2)
            })
            
            if i == 0:
                receita_antecipada_total = receita_antecipada_mes
        
        # 5. Totais gerais
        result = db.execute(text("""
            SELECT 
                COUNT(*) as total_assinaturas,
                SUM(CASE WHEN status = 'ativa' THEN 1 ELSE 0 END) as ativas,
                SUM(CASE WHEN status = 'suspensa' THEN 1 ELSE 0 END) as suspensas,
                SUM(CASE WHEN status = 'cancelada' THEN 1 ELSE 0 END) as canceladas,
                SUM(CASE WHEN status = 'inadimplente' THEN 1 ELSE 0 END) as inadimplentes,
                SUM(CASE WHEN status = 'ativa' THEN valor ELSE 0 END) as mrr
            FROM assinaturas
        """))
        totais = result.fetchone()
        
        # 6. Cobranças pendentes/pagas este mês
        result = db.execute(text("""
            SELECT 
                SUM(CASE WHEN status = 'pago' THEN valor_pago ELSE 0 END) as recebido_mes,
                SUM(CASE WHEN status = 'pendente' THEN valor ELSE 0 END) as pendente_mes,
                SUM(CASE WHEN status = 'vencido' THEN valor ELSE 0 END) as vencido_mes
            FROM cobrancas
            WHERE MONTH(data_vencimento) = MONTH(CURRENT_DATE())
            AND YEAR(data_vencimento) = YEAR(CURRENT_DATE())
            AND (descricao NOT LIKE '%Pix recebido%' AND descricao NOT LIKE '%gerada automaticamente%')
        """))
        cobrancas_mes = result.fetchone()
        
        return {
            "gerado_em": datetime.now().isoformat(),
            "resumo": {
                "total_assinaturas": totais.total_assinaturas or 0,
                "ativas": totais.ativas or 0,
                "suspensas": totais.suspensas or 0,
                "canceladas": totais.canceladas or 0,
                "inadimplentes": totais.inadimplentes or 0,
                "mrr": float(totais.mrr or 0),  # Monthly Recurring Revenue
                "arr": float((totais.mrr or 0) * 12),  # Annual Recurring Revenue
                "receita_antecipada": round(receita_antecipada_total, 2)
            },
            "mes_atual": {
                "recebido": float(cobrancas_mes.recebido_mes or 0) if cobrancas_mes else 0,
                "pendente": float(cobrancas_mes.pendente_mes or 0) if cobrancas_mes else 0,
                "vencido": float(cobrancas_mes.vencido_mes or 0) if cobrancas_mes else 0
            },
            "assinaturas_por_status": assinaturas_status,
            "assinaturas_por_plano": assinaturas_plano,
            "previsao_12_meses": previsao_mensal
        }
        
    except Exception as e:
        logger.error(f"Erro ao gerar previsão: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/previsao/detalhada")
async def previsao_detalhada(db: Session = Depends(get_db)):
    """
    Retorna previsão detalhada com breakdown por condomínio.
    """
    try:
        result = db.execute(text("""
            SELECT 
                a.id_assinatura,
                a.id_condominio,
                c.nome as condominio_nome,
                a.valor,
                a.status,
                a.data_inicio,
                a.validade_ate,
                COALESCE(p.codigo, a.tipo_plano) as plano,
                COALESCE(p.dias_validade, 30) as dias_validade
            FROM assinaturas a
            LEFT JOIN condominios c ON a.id_condominio = c.id
            LEFT JOIN planos p ON a.id_plano = p.id_plano
            WHERE a.status = 'ativa'
            ORDER BY a.valor DESC
        """))
        
        assinaturas = [
            {
                "id_assinatura": r.id_assinatura,
                "id_condominio": r.id_condominio,
                "condominio": r.condominio_nome or f"Condomínio #{r.id_condominio}",
                "plano": r.plano,
                "dias_validade": r.dias_validade,
                "valor": float(r.valor),
                "status": r.status,
                "data_inicio": r.data_inicio.isoformat() if r.data_inicio else None,
                "validade_ate": r.validade_ate.isoformat() if r.validade_ate else None
            }
            for r in result.fetchall()
        ]
        
        return {"assinaturas": assinaturas, "total": len(assinaturas)}
        
    except Exception as e:
        logger.error(f"Erro ao gerar previsão detalhada: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
