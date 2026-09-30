#!/bin/bash
# ================================================================================
#  SCRIPT DE INSTALAÇÃO - CORREÇÃO DO SISTEMA DE EMAIL
#  Versão: 2.0
#  Data: 21/12/2024
#  Descrição: Instala os arquivos corrigidos e testa o sistema de email
# ================================================================================

echo ""
echo "================================================================================"
echo "  🔧 INSTALAÇÃO DA CORREÇÃO DO SISTEMA DE EMAIL - ECONDOMÍNIO"
echo "  Versão 2.0 - 21/12/2024"
echo "================================================================================"
echo ""

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Função para mostrar mensagens
msg_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

msg_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

msg_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

msg_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Diretórios
BACKEND_DIR="/home/visionlpr/backendV2"
FINANCEIRO_DIR="$BACKEND_DIR/financeiro"
BACKUP_DIR="$BACKEND_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ================================================================================
# ETAPA 1: VERIFICAÇÕES INICIAIS
# ================================================================================

msg_info "Verificando estrutura de diretórios..."
echo ""

# Verificar se os diretórios existem
if [ ! -d "$BACKEND_DIR" ]; then
    msg_error "Diretório do backend não encontrado: $BACKEND_DIR"
    exit 1
fi

if [ ! -d "$FINANCEIRO_DIR" ]; then
    msg_error "Diretório financeiro não encontrado: $FINANCEIRO_DIR"
    exit 1
fi

msg_success "Diretórios verificados"
echo ""

# ================================================================================
# ETAPA 2: CRIAR DIRETÓRIO DE BACKUP
# ================================================================================

msg_info "Criando diretório de backup..."
mkdir -p "$BACKUP_DIR"
msg_success "Diretório de backup criado: $BACKUP_DIR"
echo ""

# ================================================================================
# ETAPA 3: FAZER BACKUP DOS ARQUIVOS ATUAIS
# ================================================================================

msg_info "Fazendo backup dos arquivos atuais..."
echo ""

# Backup do financeiro_email.py
if [ -f "$FINANCEIRO_DIR/financeiro_email.py" ]; then
    cp "$FINANCEIRO_DIR/financeiro_email.py" "$BACKUP_DIR/financeiro_email.py.backup_$TIMESTAMP"
    msg_success "Backup criado: financeiro_email.py.backup_$TIMESTAMP"
else
    msg_warning "Arquivo financeiro_email.py não encontrado (será criado)"
fi

echo ""

# ================================================================================
# ETAPA 4: VERIFICAR SE OS NOVOS ARQUIVOS EXISTEM
# ================================================================================

msg_info "Verificando arquivos de correção..."
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$SCRIPT_DIR/financeiro_email.py" ]; then
    msg_error "Arquivo financeiro_email.py não encontrado em $SCRIPT_DIR"
    msg_info "Execute este script a partir da pasta que contém os arquivos corrigidos"
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/testar_email_v2.py" ]; then
    msg_error "Arquivo testar_email_v2.py não encontrado em $SCRIPT_DIR"
    exit 1
fi

msg_success "Arquivos de correção encontrados"
echo ""

# ================================================================================
# ETAPA 5: INSTALAR ARQUIVOS CORRIGIDOS
# ================================================================================

msg_info "Instalando arquivos corrigidos..."
echo ""

# Copiar financeiro_email.py
cp "$SCRIPT_DIR/financeiro_email.py" "$FINANCEIRO_DIR/financeiro_email.py"
msg_success "Instalado: financeiro_email.py"

# Copiar testar_email_v2.py
cp "$SCRIPT_DIR/testar_email_v2.py" "$BACKEND_DIR/testar_email_v2.py"
chmod +x "$BACKEND_DIR/testar_email_v2.py"
msg_success "Instalado: testar_email_v2.py (com permissão de execução)"

echo ""

# ================================================================================
# ETAPA 6: VERIFICAR CONFIGURAÇÃO .ENV
# ================================================================================

msg_info "Verificando configuração do .env..."
echo ""

if [ ! -f "$BACKEND_DIR/.env" ]; then
    msg_error "Arquivo .env não encontrado em $BACKEND_DIR"
    exit 1
fi

# Verificar variáveis necessárias
echo "Configurações de email no .env:"
echo "--------------------------------"
grep "^EMAIL_" "$BACKEND_DIR/.env" || msg_warning "Nenhuma configuração EMAIL_* encontrada no .env"
echo ""

# Verificar se tem acento no FROM_NAME
if grep -q "EMAIL_FROM_NAME.*ô" "$BACKEND_DIR/.env"; then
    msg_warning "ATENÇÃO: EMAIL_FROM_NAME contém acento no .env!"
    msg_info "Corrigindo automaticamente..."
    sed -i 's/EMAIL_FROM_NAME=Financeiro Econdomínio/EMAIL_FROM_NAME=Financeiro Econdominio/' "$BACKEND_DIR/.env"
    msg_success "EMAIL_FROM_NAME corrigido no .env (acento removido)"
fi

echo ""

# ================================================================================
# ETAPA 7: EXECUTAR TESTE
# ================================================================================

msg_info "Deseja executar o teste de email agora? (s/n)"
read -r resposta

if [[ "$resposta" =~ ^[Ss]$ ]]; then
    echo ""
    msg_info "Executando teste de email..."
    echo ""
    echo "================================================================================"
    
    cd "$BACKEND_DIR"
    python3 testar_email_v2.py
    
    echo "================================================================================"
    echo ""
else
    msg_info "Teste pulado. Execute manualmente quando desejar:"
    echo "  cd $BACKEND_DIR"
    echo "  python3 testar_email_v2.py"
    echo ""
fi

# ================================================================================
# ETAPA 8: INSTRUÇÕES FINAIS
# ================================================================================

echo ""
echo "================================================================================"
echo "  ✅ INSTALAÇÃO CONCLUÍDA COM SUCESSO!"
echo "================================================================================"
echo ""
msg_success "Arquivos instalados e configurados"
echo ""
echo "📋 PRÓXIMOS PASSOS:"
echo ""
echo "1. Se ainda não testou, execute:"
echo "   cd $BACKEND_DIR"
echo "   python3 testar_email_v2.py"
echo ""
echo "2. Reinicie o backend:"
echo "   sudo systemctl restart backend-financeiro"
echo "   # OU"
echo "   pm2 restart backend-financeiro"
echo ""
echo "3. Teste pelo frontend:"
echo "   - Acesse a tela de Cobranças"
echo "   - Envie um email de teste"
echo ""
echo "📁 BACKUPS:"
echo "   Localização: $BACKUP_DIR"
echo "   Arquivo: financeiro_email.py.backup_$TIMESTAMP"
echo ""
echo "📖 DOCUMENTAÇÃO:"
echo "   Consulte README_CORRECAO_EMAIL.md para mais detalhes"
echo ""
echo "================================================================================"
echo ""
