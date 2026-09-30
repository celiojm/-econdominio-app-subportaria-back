#!/bin/bash

# ================================================================================
# SCRIPT DE INSTALAÇÃO AUTOMÁTICA - ROTA DASHBOARD
# ================================================================================
# Este script adiciona automaticamente a rota /dashboard no backend
# ================================================================================

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color
BOLD='\033[1m'

echo ""
echo -e "${BLUE}${BOLD}======================================================================${NC}"
echo -e "${BLUE}${BOLD}  🚀 INSTALAÇÃO AUTOMÁTICA - ROTA DASHBOARD${NC}"
echo -e "${BLUE}${BOLD}======================================================================${NC}"
echo ""

# Diretório base
BACKEND_DIR="$HOME/backendV2/financeiro"

# Verificar se o diretório existe
if [ ! -d "$BACKEND_DIR" ]; then
    echo -e "${RED}❌ Diretório não encontrado: $BACKEND_DIR${NC}"
    echo -e "${YELLOW}Ajuste a variável BACKEND_DIR no script.${NC}"
    exit 1
fi

cd "$BACKEND_DIR"
echo -e "${GREEN}✓${NC} Diretório encontrado: $BACKEND_DIR"

# ================================================================================
# PASSO 1: Criar backup
# ================================================================================
echo ""
echo -e "${BOLD}📦 Criando backups...${NC}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="backup_dashboard_$TIMESTAMP"
mkdir -p "$BACKUP_DIR"

if [ -f "financeiro_rotas.py" ]; then
    cp financeiro_rotas.py "$BACKUP_DIR/"
    echo -e "${GREEN}✓${NC} Backup de financeiro_rotas.py criado"
fi

if [ -f "financeiro_dashboard.py" ]; then
    cp financeiro_dashboard.py "$BACKUP_DIR/"
    echo -e "${GREEN}✓${NC} Backup de financeiro_dashboard.py criado"
fi

# ================================================================================
# PASSO 2: Verificar se a rota já existe
# ================================================================================
echo ""
echo -e "${BOLD}🔍 Verificando se a rota já existe...${NC}"

if grep -q "route('/dashboard'" financeiro_rotas.py 2>/dev/null; then
    echo -e "${YELLOW}⚠${NC} Rota /dashboard já existe em financeiro_rotas.py"
    echo -e "${YELLOW}Pulando adição da rota...${NC}"
    ROTA_EXISTE=true
else
    echo -e "${GREEN}✓${NC} Rota /dashboard não encontrada, será adicionada"
    ROTA_EXISTE=false
fi

# ================================================================================
# PASSO 3: Criar/atualizar financeiro_dashboard.py
# ================================================================================
echo ""
echo -e "${BOLD}📝 Criando função obter_dados_dashboard...${NC}"

cat > financeiro_dashboard.py << 'DASHBOARD_EOF'
"""
Módulo de Dashboard Financeiro
Fornece dados consolidados para o dashboard
"""

from datetime import datetime, timedelta
from sqlalchemy import func, extract
from flask import current_app
import logging

logger = logging.getLogger(__name__)

def obter_dados_dashboard():
    """
    Obtém dados consolidados para o dashboard financeiro
    
    Returns:
        dict: Dados do dashboard incluindo métricas, gráficos e listas
    """
    try:
        from financeiro_modelos import (
            db, Assinatura, Cobranca, Condominio, Plano
        )
        
        hoje = datetime.now().date()
        
        # Calcular últimos 6 meses
        meses_atras = []
        for i in range(6):
            data = hoje - timedelta(days=30 * i)
            meses_atras.append(data.replace(day=1))
        meses_atras.reverse()
        
        logger.info("Calculando resumo geral...")
        
        # RESUMO GERAL
        resumo = {
            'assinaturas_total': Assinatura.query.count(),
            'assinaturas_ativas': Assinatura.query.filter_by(status='ativa').count(),
            'assinaturas_suspensas': Assinatura.query.filter_by(status='suspensa').count(),
            'assinaturas_canceladas': Assinatura.query.filter_by(status='cancelada').count(),
            'assinaturas_inadimplentes': Assinatura.query.filter_by(inadimplente=True).count(),
            'condominios_total': Condominio.query.filter_by(ativo=True).count(),
        }
        
        # MRR
        mrr_query = db.session.query(
            func.coalesce(func.sum(Plano.valor), 0)
        ).join(Assinatura).filter(
            Assinatura.status == 'ativa'
        ).scalar()
        mrr = float(mrr_query) if mrr_query else 0.0
        
        # MÊS ATUAL
        receita_recebida = db.session.query(
            func.coalesce(func.sum(Cobranca.valor), 0)
        ).filter(
            Cobranca.status == 'pago',
            extract('month', Cobranca.vencimento) == hoje.month,
            extract('year', Cobranca.vencimento) == hoje.year
        ).scalar()
        
        receita_pendente = db.session.query(
            func.coalesce(func.sum(Cobranca.valor), 0)
        ).filter(
            Cobranca.status.in_(['pendente', 'aguardando']),
            extract('month', Cobranca.vencimento) == hoje.month,
            extract('year', Cobranca.vencimento) == hoje.year
        ).scalar()
        
        total_cobrancas_mes = Cobranca.query.filter(
            extract('month', Cobranca.vencimento) == hoje.month,
            extract('year', Cobranca.vencimento) == hoje.year
        ).count()
        
        mes_atual = {
            'receita_recebida': float(receita_recebida) if receita_recebida else 0.0,
            'receita_pendente': float(receita_pendente) if receita_pendente else 0.0,
            'total_cobrancas': total_cobrancas_mes
        }
        
        # VENCENDO EM 7 DIAS
        sete_dias = hoje + timedelta(days=7)
        vencendo_query = db.session.query(
            func.count(Cobranca.id),
            func.coalesce(func.sum(Cobranca.valor), 0)
        ).filter(
            Cobranca.status.in_(['pendente', 'aguardando']),
            Cobranca.vencimento >= hoje,
            Cobranca.vencimento <= sete_dias
        ).first()
        
        vencendo_7_dias = {
            'quantidade': vencendo_query[0] if vencendo_query else 0,
            'valor_total': float(vencendo_query[1]) if vencendo_query and vencendo_query[1] else 0.0
        }
        
        # INADIMPLÊNCIA
        inadimplentes_query = db.session.query(
            func.count(Cobranca.id),
            func.coalesce(func.sum(Cobranca.valor), 0)
        ).filter(
            Cobranca.status == 'vencido',
            Cobranca.vencimento < hoje
        ).first()
        
        inadimplencia = {
            'quantidade': inadimplentes_query[0] if inadimplentes_query else 0,
            'valor_total': float(inadimplentes_query[1]) if inadimplentes_query and inadimplentes_query[1] else 0.0
        }
        
        # RECEITA MENSAL
        receita_mensal = []
        meses_pt = {
            1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun',
            7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'
        }
        
        for data_mes in meses_atras:
            mes_num = data_mes.month
            ano_num = data_mes.year
            
            recebido = db.session.query(
                func.coalesce(func.sum(Cobranca.valor), 0)
            ).filter(
                Cobranca.status == 'pago',
                extract('month', Cobranca.vencimento) == mes_num,
                extract('year', Cobranca.vencimento) == ano_num
            ).scalar()
            
            pendente = db.session.query(
                func.coalesce(func.sum(Cobranca.valor), 0)
            ).filter(
                Cobranca.status.in_(['pendente', 'aguardando']),
                extract('month', Cobranca.vencimento) == mes_num,
                extract('year', Cobranca.vencimento) == ano_num
            ).scalar()
            
            receita_mensal.append({
                'mes': meses_pt.get(mes_num, str(mes_num)),
                'recebido': float(recebido) if recebido else 0.0,
                'pendente': float(pendente) if pendente else 0.0
            })
        
        # DISTRIBUIÇÃO DE STATUS
        distribuicao = db.session.query(
            Cobranca.status,
            func.count(Cobranca.id).label('quantidade')
        ).filter(
            extract('month', Cobranca.vencimento) == hoje.month,
            extract('year', Cobranca.vencimento) == hoje.year
        ).group_by(Cobranca.status).all()
        
        distribuicao_status = [
            {'status': status, 'quantidade': qtd}
            for status, qtd in distribuicao
        ]
        
        # COBRANÇAS RECENTES
        cobrancas_recentes_query = db.session.query(
            Cobranca.id,
            Cobranca.valor,
            Cobranca.vencimento,
            Cobranca.status,
            Condominio.nome.label('condominio')
        ).join(
            Assinatura, Cobranca.assinatura_id == Assinatura.id
        ).join(
            Condominio, Assinatura.condominio_id == Condominio.id
        ).order_by(
            Cobranca.criado_em.desc()
        ).limit(10).all()
        
        cobrancas_recentes = [
            {
                'id': c.id,
                'condominio': c.condominio,
                'valor': float(c.valor),
                'vencimento': c.vencimento.isoformat() if c.vencimento else None,
                'status': c.status
            }
            for c in cobrancas_recentes_query
        ]
        
        # TOP CONDOMÍNIOS
        top_condominios_query = db.session.query(
            Condominio.id,
            Condominio.nome,
            func.count(Cobranca.id).label('total_cobrancas'),
            func.coalesce(func.sum(Cobranca.valor), 0).label('total_pago')
        ).join(
            Assinatura, Condominio.id == Assinatura.condominio_id
        ).join(
            Cobranca, Assinatura.id == Cobranca.assinatura_id
        ).filter(
            Cobranca.status == 'pago'
        ).group_by(
            Condominio.id, Condominio.nome
        ).order_by(
            func.sum(Cobranca.valor).desc()
        ).limit(5).all()
        
        top_condominios = [
            {
                'id': c.id,
                'nome': c.nome,
                'total_cobrancas': c.total_cobrancas,
                'total_pago': float(c.total_pago)
            }
            for c in top_condominios_query
        ]
        
        logger.info("Dashboard compilado com sucesso!")
        
        return {
            'resumo': resumo,
            'mrr': mrr,
            'mes_atual': mes_atual,
            'vencendo_7_dias': vencendo_7_dias,
            'inadimplencia': inadimplencia,
            'receita_mensal': receita_mensal,
            'distribuicao_status': distribuicao_status,
            'cobrancas_recentes': cobrancas_recentes,
            'top_condominios': top_condominios,
            'validades': [],
            'assinaturas_expirando': [],
            'gerado_em': datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Erro ao gerar dashboard: {str(e)}")
        logger.exception(e)
        raise
DASHBOARD_EOF

echo -e "${GREEN}✓${NC} Arquivo financeiro_dashboard.py criado/atualizado"

# ================================================================================
# PASSO 4: Adicionar rota em financeiro_rotas.py (se não existir)
# ================================================================================

if [ "$ROTA_EXISTE" = false ]; then
    echo ""
    echo -e "${BOLD}📝 Adicionando rota /dashboard em financeiro_rotas.py...${NC}"
    
    # Código da rota a ser adicionado
    cat >> financeiro_rotas.py << 'ROTA_EOF'


# ================================================================================
# DASHBOARD
# ================================================================================

@financeiro_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def obter_dashboard():
    """
    Obtém dados consolidados do dashboard financeiro
    GET /api/financeiro/dashboard
    
    Returns:
        JSON com métricas, resumos e dados do dashboard
    """
    try:
        from financeiro_dashboard import obter_dados_dashboard
        
        logger.info("Carregando dados do dashboard...")
        dados = obter_dados_dashboard()
        
        logger.info("Dashboard carregado com sucesso")
        return jsonify(dados), 200
        
    except Exception as e:
        logger.error(f"Erro ao obter dashboard: {str(e)}")
        logger.exception(e)
        return jsonify({
            'erro': 'Erro ao carregar dashboard',
            'detalhes': str(e)
        }), 500
ROTA_EOF

    echo -e "${GREEN}✓${NC} Rota /dashboard adicionada em financeiro_rotas.py"
fi

# ================================================================================
# PASSO 5: Reiniciar o backend
# ================================================================================
echo ""
echo -e "${BOLD}🔄 Reiniciando backend...${NC}"

if sudo systemctl restart backend-encomenda.service; then
    echo -e "${GREEN}✓${NC} Backend reiniciado com sucesso"
else
    echo -e "${RED}❌ Erro ao reiniciar backend${NC}"
    echo -e "${YELLOW}Execute manualmente: sudo systemctl restart backend-encomenda.service${NC}"
fi

# ================================================================================
# RESUMO FINAL
# ================================================================================
echo ""
echo -e "${BLUE}${BOLD}======================================================================${NC}"
echo -e "${BLUE}${BOLD}  ✅ INSTALAÇÃO CONCLUÍDA!${NC}"
echo -e "${BLUE}${BOLD}======================================================================${NC}"
echo ""
echo -e "${GREEN}✓${NC} Função obter_dados_dashboard criada"
echo -e "${GREEN}✓${NC} Rota /dashboard adicionada"
echo -e "${GREEN}✓${NC} Backend reiniciado"
echo -e "${GREEN}✓${NC} Backup criado em: $BACKUP_DIR"
echo ""
echo -e "${BOLD}📋 Próximos passos:${NC}"
echo -e "1. Teste a rota no navegador (limpe o cache e faça login)"
echo -e "2. Verifique os logs: ${BLUE}sudo journalctl -u backend-encomenda.service -f${NC}"
echo -e "3. Se houver erro, restaure o backup: ${YELLOW}cp $BACKUP_DIR/* .${NC}"
echo ""
echo -e "${BOLD}🧪 Testar a rota:${NC}"
echo -e "   ${BLUE}curl -H 'Authorization: Bearer SEU_TOKEN' \\${NC}"
echo -e "   ${BLUE}     https://backend.inforseg.com.br/api/financeiro/dashboard${NC}"
echo ""
