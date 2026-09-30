#!/usr/bin/env python3
"""
Script de Verificação e Correção - Dashboard Financeiro
Verifica se a rota /dashboard existe e sugere correções
"""

import os
import sys
from pathlib import Path

# Cores para terminal
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header():
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'='*70}")
    print("  🔍 DIAGNÓSTICO - DASHBOARD FINANCEIRO")
    print(f"{'='*70}{Colors.END}\n")

def check_file_exists(filepath):
    """Verifica se arquivo existe"""
    if os.path.exists(filepath):
        print(f"{Colors.GREEN}✓{Colors.END} Arquivo encontrado: {filepath}")
        return True
    else:
        print(f"{Colors.RED}✗{Colors.END} Arquivo NÃO encontrado: {filepath}")
        return False

def check_route_in_file(filepath, route_name):
    """Verifica se rota existe no arquivo"""
    if not os.path.exists(filepath):
        return False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    patterns = [
        f"route('{route_name}'",
        f'route("{route_name}"',
        f"'{route_name}'",
        f'"{route_name}"'
    ]
    
    for pattern in patterns:
        if pattern in content:
            print(f"{Colors.GREEN}✓{Colors.END} Rota {route_name} ENCONTRADA em {filepath}")
            return True
    
    print(f"{Colors.RED}✗{Colors.END} Rota {route_name} NÃO ENCONTRADA em {filepath}")
    return False

def check_function_exists(filepath, function_name):
    """Verifica se função existe"""
    if not os.path.exists(filepath):
        return False
        
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if f"def {function_name}" in content:
        print(f"{Colors.GREEN}✓{Colors.END} Função {function_name} encontrada")
        return True
    else:
        print(f"{Colors.RED}✗{Colors.END} Função {function_name} NÃO encontrada")
        return False

def suggest_route_addition():
    """Sugere adição da rota"""
    print(f"\n{Colors.YELLOW}{Colors.BOLD}💡 SUGESTÃO DE CORREÇÃO:{Colors.END}\n")
    
    print(f"{Colors.BOLD}Adicione esta rota em financeiro_rotas.py:{Colors.END}\n")
    
    code = """
@financeiro_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def obter_dashboard():
    '''
    Obtém dados do dashboard financeiro
    GET /api/financeiro/dashboard
    '''
    try:
        from financeiro_dashboard import obter_dados_dashboard
        dados = obter_dados_dashboard()
        return jsonify(dados), 200
    except Exception as e:
        logger.error(f"Erro ao obter dashboard: {str(e)}")
        return jsonify({'erro': 'Erro ao carregar dashboard'}), 500
"""
    
    print(f"{Colors.BLUE}{code}{Colors.END}")

def suggest_dashboard_function():
    """Sugere criação da função obter_dados_dashboard"""
    print(f"\n{Colors.YELLOW}{Colors.BOLD}💡 SUGESTÃO:{Colors.END}\n")
    print("Crie ou verifique a função em financeiro_dashboard.py:\n")
    
    code = """
def obter_dados_dashboard():
    '''
    Obtém dados consolidados para o dashboard
    '''
    from datetime import datetime, timedelta
    from sqlalchemy import func, and_, or_
    
    hoje = datetime.now().date()
    inicio_mes = hoje.replace(day=1)
    
    # Resumo geral
    resumo = {
        'assinaturas_total': Assinatura.query.count(),
        'assinaturas_ativas': Assinatura.query.filter_by(status='ativa').count(),
        'assinaturas_suspensas': Assinatura.query.filter_by(status='suspensa').count(),
        'assinaturas_canceladas': Assinatura.query.filter_by(status='cancelada').count(),
        'assinaturas_inadimplentes': Assinatura.query.filter_by(inadimplente=True).count(),
        'condominios_total': Condominio.query.filter_by(ativo=True).count(),
    }
    
    # MRR - Receita Recorrente Mensal
    mrr = db.session.query(
        func.sum(Plano.valor)
    ).join(Assinatura).filter(
        Assinatura.status == 'ativa'
    ).scalar() or 0
    
    # Dados do mês atual
    mes_atual = {
        'receita_recebida': db.session.query(func.sum(Cobranca.valor)).filter(
            Cobranca.status == 'pago',
            func.extract('month', Cobranca.vencimento) == hoje.month,
            func.extract('year', Cobranca.vencimento) == hoje.year
        ).scalar() or 0,
        'receita_pendente': db.session.query(func.sum(Cobranca.valor)).filter(
            Cobranca.status == 'pendente',
            func.extract('month', Cobranca.vencimento) == hoje.month,
            func.extract('year', Cobranca.vencimento) == hoje.year
        ).scalar() or 0,
        'total_cobrancas': Cobranca.query.filter(
            func.extract('month', Cobranca.vencimento) == hoje.month,
            func.extract('year', Cobranca.vencimento) == hoje.year
        ).count()
    }
    
    return {
        'resumo': resumo,
        'mrr': float(mrr),
        'mes_atual': mes_atual,
        'gerado_em': datetime.now().isoformat()
    }
"""
    
    print(f"{Colors.BLUE}{code}{Colors.END}")

def main():
    print_header()
    
    # Possíveis localizações do backend
    possible_paths = [
        os.path.expanduser("~/backendV2/financeiro"),
        "/home/visionlpr/backendV2/financeiro",
        "/home/ubuntu/backendV2/financeiro",
        "./financeiro",
        "../financeiro"
    ]
    
    backend_path = None
    for path in possible_paths:
        if os.path.exists(path):
            backend_path = path
            print(f"{Colors.GREEN}✓{Colors.END} Backend encontrado em: {path}\n")
            break
    
    if not backend_path:
        print(f"{Colors.RED}✗{Colors.END} Backend não encontrado em nenhum dos caminhos esperados")
        print(f"{Colors.YELLOW}Caminhos verificados:{Colors.END}")
        for path in possible_paths:
            print(f"  - {path}")
        print(f"\n{Colors.YELLOW}Execute este script no servidor onde está o backend.{Colors.END}")
        return
    
    print(f"{Colors.BOLD}Verificando arquivos principais...{Colors.END}\n")
    
    # Verificar arquivos
    rotas_file = os.path.join(backend_path, "financeiro_rotas.py")
    dashboard_file = os.path.join(backend_path, "financeiro_dashboard.py")
    
    rotas_exists = check_file_exists(rotas_file)
    dashboard_exists = check_file_exists(dashboard_file)
    
    print(f"\n{Colors.BOLD}Verificando rota /dashboard...{Colors.END}\n")
    
    # Verificar rota
    if rotas_exists:
        route_exists = check_route_in_file(rotas_file, '/dashboard')
        
        if not route_exists:
            suggest_route_addition()
    
    print(f"\n{Colors.BOLD}Verificando função obter_dados_dashboard...{Colors.END}\n")
    
    # Verificar função
    if dashboard_exists:
        function_exists = check_function_exists(dashboard_file, "obter_dados_dashboard")
        
        if not function_exists:
            suggest_dashboard_function()
    else:
        print(f"{Colors.YELLOW}⚠{Colors.END} Arquivo financeiro_dashboard.py não existe")
        print(f"{Colors.YELLOW}Crie o arquivo:{Colors.END} {dashboard_file}\n")
        suggest_dashboard_function()
    
    # Resumo final
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'='*70}")
    print("  📋 RESUMO")
    print(f"{'='*70}{Colors.END}\n")
    
    print(f"{Colors.BOLD}Próximos passos:{Colors.END}")
    print(f"1. Verifique se a rota /dashboard existe em {rotas_file}")
    print(f"2. Verifique se a função obter_dados_dashboard existe em {dashboard_file}")
    print(f"3. Reinicie o backend após fazer alterações")
    print(f"4. Teste a rota usando curl ou o diagnóstico HTML")
    
    print(f"\n{Colors.BOLD}Comando para testar (substitua SEU_TOKEN):{Colors.END}")
    print(f"{Colors.BLUE}curl -H 'Authorization: Bearer SEU_TOKEN' https://backend.inforseg.com.br/api/financeiro/dashboard{Colors.END}")
    
    print(f"\n{Colors.BOLD}Reiniciar backend:{Colors.END}")
    print(f"{Colors.BLUE}sudo systemctl restart seu-servico-backend{Colors.END}\n")

if __name__ == "__main__":
    main()
