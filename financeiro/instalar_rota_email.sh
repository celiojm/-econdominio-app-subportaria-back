#!/bin/bash

# ============================================================================
# SCRIPT DE INSTALAÇÃO - ROTA DE EMAIL NO BACKEND
# ============================================================================
# Este script instala automaticamente a rota /email/reenviar no backend
# ============================================================================

echo ""
echo "========================================================================"
echo "🔧 INSTALAÇÃO - ROTA DE EMAIL NO BACKEND FINANCEIRO"
echo "========================================================================"
echo ""

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Diretórios
FINANCEIRO_DIR="/home/visionlpr/backendV2/financeiro"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Verificar se está rodando como visionlpr
if [ "$(whoami)" != "visionlpr" ]; then
    echo -e "${RED}❌ Este script deve ser executado como usuário visionlpr${NC}"
    exit 1
fi

# ============================================================================
# 1. VERIFICAR ARQUIVOS NECESSÁRIOS
# ============================================================================
echo "📦 Verificando arquivos necessários..."
echo ""

ARQUIVOS_OK=true

if [ ! -f "$SCRIPT_DIR/financeiro_email.py" ]; then
    echo -e "${RED}❌ Arquivo financeiro_email.py não encontrado${NC}"
    ARQUIVOS_OK=false
fi

if [ ! -f "$SCRIPT_DIR/financeiro_email_routes_FINAL.py" ]; then
    echo -e "${RED}❌ Arquivo financeiro_email_routes_FINAL.py não encontrado${NC}"
    ARQUIVOS_OK=false
fi

if [ "$ARQUIVOS_OK" = false ]; then
    echo ""
    echo -e "${RED}Arquivos necessários não encontrados no diretório atual${NC}"
    echo "Execute este script do diretório onde estão os arquivos"
    exit 1
fi

echo -e "${GREEN}✅ Todos os arquivos encontrados${NC}"
echo ""

# ============================================================================
# 2. COPIAR MÓDULO DE EMAIL
# ============================================================================
echo "📥 Copiando módulo de email..."

if [ -f "$FINANCEIRO_DIR/financeiro_email.py" ]; then
    echo -e "${YELLOW}⚠️  financeiro_email.py já existe. Fazendo backup...${NC}"
    cp "$FINANCEIRO_DIR/financeiro_email.py" "$FINANCEIRO_DIR/financeiro_email.py.backup_$(date +%Y%m%d_%H%M%S)"
fi

cp "$SCRIPT_DIR/financeiro_email.py" "$FINANCEIRO_DIR/"
chmod 644 "$FINANCEIRO_DIR/financeiro_email.py"
echo -e "${GREEN}✅ financeiro_email.py copiado${NC}"

# ============================================================================
# 3. COPIAR ROTAS DE EMAIL
# ============================================================================
echo "📥 Copiando rotas de email..."

if [ -f "$FINANCEIRO_DIR/financeiro_email_routes.py" ]; then
    echo -e "${YELLOW}⚠️  financeiro_email_routes.py já existe. Fazendo backup...${NC}"
    cp "$FINANCEIRO_DIR/financeiro_email_routes.py" "$FINANCEIRO_DIR/financeiro_email_routes.py.backup_$(date +%Y%m%d_%H%M%S)"
fi

cp "$SCRIPT_DIR/financeiro_email_routes_FINAL.py" "$FINANCEIRO_DIR/financeiro_email_routes.py"
chmod 644 "$FINANCEIRO_DIR/financeiro_email_routes.py"
echo -e "${GREEN}✅ financeiro_email_routes.py copiado${NC}"
echo ""

# ============================================================================
# 4. ENCONTRAR ARQUIVO DE ROTAS PRINCIPAL
# ============================================================================
echo "🔍 Procurando arquivo de rotas principal..."
echo ""

cd "$FINANCEIRO_DIR"

# Procurar por arquivo que tem FastAPI ou APIRouter
ARQUIVO_ROTAS=$(grep -l "FastAPI\|APIRouter" *.py 2>/dev/null | grep -v email_routes | head -1)

if [ -z "$ARQUIVO_ROTAS" ]; then
    echo -e "${RED}❌ Não foi possível encontrar o arquivo de rotas principal${NC}"
    echo ""
    echo "Arquivos Python no diretório:"
    ls -1 *.py
    echo ""
    echo -e "${YELLOW}Por favor, informe qual arquivo contém as rotas (ex: financeiro_rotas.py):${NC}"
    read -r ARQUIVO_ROTAS
    
    if [ ! -f "$ARQUIVO_ROTAS" ]; then
        echo -e "${RED}❌ Arquivo $ARQUIVO_ROTAS não encontrado${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}✅ Arquivo de rotas encontrado: $ARQUIVO_ROTAS${NC}"
echo ""

# ============================================================================
# 5. VERIFICAR SE ROTA JÁ ESTÁ REGISTRADA
# ============================================================================
echo "🔍 Verificando se rota já está registrada..."

if grep -q "email_router" "$ARQUIVO_ROTAS"; then
    echo -e "${YELLOW}⚠️  Rota email_router já está registrada em $ARQUIVO_ROTAS${NC}"
    echo ""
    echo "Deseja substituir o registro existente? (s/n):"
    read -r -n 1 RESPOSTA
    echo ""
    
    if [[ ! $RESPOSTA =~ ^[Ss]$ ]]; then
        echo "Instalação cancelada pelo usuário"
        exit 0
    fi
    
    # Fazer backup
    cp "$ARQUIVO_ROTAS" "${ARQUIVO_ROTAS}.backup_$(date +%Y%m%d_%H%M%S)"
    
    # Remover linhas antigas
    sed -i '/email_router/d' "$ARQUIVO_ROTAS"
    echo -e "${GREEN}✅ Linhas antigas removidas${NC}"
fi

# ============================================================================
# 6. ADICIONAR IMPORT DO EMAIL_ROUTER
# ============================================================================
echo "📝 Adicionando import do email_router..."

# Encontrar linha de imports
LINHA_IMPORTS=$(grep -n "^from\|^import" "$ARQUIVO_ROTAS" | tail -1 | cut -d: -f1)

if [ -z "$LINHA_IMPORTS" ]; then
    LINHA_IMPORTS=1
fi

# Adicionar import após última linha de imports
sed -i "${LINHA_IMPORTS}a\\
\\n# NOVO: Rotas de email\\nfrom financeiro_email_routes import router as email_router" "$ARQUIVO_ROTAS"

echo -e "${GREEN}✅ Import adicionado${NC}"

# ============================================================================
# 7. ADICIONAR REGISTRO DO ROUTER
# ============================================================================
echo "📝 Adicionando registro do router..."

# Procurar onde outros routers são registrados
if grep -q "include_router" "$ARQUIVO_ROTAS"; then
    # Adicionar após último include_router
    LINHA_INCLUDE=$(grep -n "include_router" "$ARQUIVO_ROTAS" | tail -1 | cut -d: -f1)
    sed -i "${LINHA_INCLUDE}a\\
\\n# NOVO: Incluir rotas de email\\napp.include_router(email_router)" "$ARQUIVO_ROTAS"
    echo -e "${GREEN}✅ Router registrado com app.include_router${NC}"
else
    # Procurar pelo app = FastAPI()
    if grep -q "app.*=.*FastAPI" "$ARQUIVO_ROTAS"; then
        LINHA_APP=$(grep -n "app.*=.*FastAPI" "$ARQUIVO_ROTAS" | head -1 | cut -d: -f1)
        sed -i "${LINHA_APP}a\\
\\n# NOVO: Incluir rotas de email\\napp.include_router(email_router)" "$ARQUIVO_ROTAS"
        echo -e "${GREEN}✅ Router registrado com app.include_router${NC}"
    else
        echo -e "${YELLOW}⚠️  Não foi possível adicionar automaticamente${NC}"
        echo ""
        echo "Por favor, adicione manualmente ao arquivo $ARQUIVO_ROTAS:"
        echo ""
        echo -e "${BLUE}app.include_router(email_router)${NC}"
        echo ""
    fi
fi

echo ""

# ============================================================================
# 8. VERIFICAR .ENV
# ============================================================================
echo "🔍 Verificando configuração de email no .env..."

ENV_FILE="/home/visionlpr/backendV2/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}❌ Arquivo .env não encontrado em $ENV_FILE${NC}"
else
    EMAIL_FROM_NAME=$(grep "^EMAIL_FROM_NAME=" "$ENV_FILE" | cut -d= -f2-)
    
    if [ -z "$EMAIL_FROM_NAME" ]; then
        echo -e "${YELLOW}⚠️  EMAIL_FROM_NAME não está definido no .env${NC}"
    elif echo "$EMAIL_FROM_NAME" | grep -q "ó\|í\|á\|é"; then
        echo -e "${RED}❌ EMAIL_FROM_NAME tem ACENTO: $EMAIL_FROM_NAME${NC}"
        echo "   Isto causa problemas no envio de email!"
        echo ""
        echo "Deseja corrigir automaticamente? (s/n):"
        read -r -n 1 CORRIGIR
        echo ""
        
        if [[ $CORRIGIR =~ ^[Ss]$ ]]; then
            cp "$ENV_FILE" "${ENV_FILE}.backup_$(date +%Y%m%d_%H%M%S)"
            sed -i 's/^EMAIL_FROM_NAME=.*/EMAIL_FROM_NAME=Financeiro Econdominio/' "$ENV_FILE"
            echo -e "${GREEN}✅ EMAIL_FROM_NAME corrigido (sem acento)${NC}"
        fi
    else
        echo -e "${GREEN}✅ EMAIL_FROM_NAME está correto: $EMAIL_FROM_NAME${NC}"
    fi
fi

echo ""

# ============================================================================
# 9. REINICIAR BACKEND
# ============================================================================
echo "🔄 Reiniciando backend..."
echo ""

sudo systemctl restart backend-encomenda.service
sleep 3

if systemctl is-active --quiet backend-encomenda.service; then
    echo -e "${GREEN}✅ Backend reiniciado com sucesso${NC}"
else
    echo -e "${RED}❌ Erro ao reiniciar backend${NC}"
    echo ""
    echo "Verifique os logs:"
    echo "  sudo journalctl -u backend-encomenda.service -n 50"
    exit 1
fi

echo ""

# ============================================================================
# 10. TESTAR ROTA
# ============================================================================
echo "🧪 Testando rota de email..."
echo ""

# Aguardar backend inicializar
echo "Aguardando backend inicializar..."
sleep 5

# Testar rota
TESTE=$(curl -s http://localhost:5000/email/teste 2>&1)

if echo "$TESTE" | grep -q "success"; then
    echo -e "${GREEN}✅ Rota /email/teste está funcionando!${NC}"
    echo ""
    echo "Resposta:"
    echo "$TESTE" | python3 -m json.tool 2>/dev/null || echo "$TESTE"
else
    echo -e "${RED}❌ Rota não está respondendo corretamente${NC}"
    echo ""
    echo "Resposta:"
    echo "$TESTE"
    echo ""
    echo "Verifique os logs:"
    echo "  sudo journalctl -u backend-encomenda.service -n 50"
fi

echo ""

# ============================================================================
# 11. RESUMO FINAL
# ============================================================================
echo "========================================================================"
echo "✅ INSTALAÇÃO CONCLUÍDA"
echo "========================================================================"
echo ""
echo "📋 Arquivos instalados:"
echo "  ✓ $FINANCEIRO_DIR/financeiro_email.py"
echo "  ✓ $FINANCEIRO_DIR/financeiro_email_routes.py"
echo "  ✓ $ARQUIVO_ROTAS (modificado)"
echo ""
echo "🔗 Rotas disponíveis:"
echo "  POST /email/reenviar/{cobranca_id}  - Reenviar email de cobrança"
echo "  GET  /email/teste                   - Testar envio de email"
echo "  GET  /email/config                  - Ver configurações de email"
echo ""
echo "📝 Próximos passos:"
echo "  1. Acesse o frontend: https://financeiro.econdominio.app.br"
echo "  2. Vá em Cobranças → Histórico de um condomínio"
echo "  3. Clique no botão '📧 Email' de uma cobrança"
echo "  4. Verifique se o toast verde aparece"
echo "  5. Confirme se o email chegou (1-2 minutos)"
echo ""
echo "🐛 Troubleshooting:"
echo "  # Ver logs do backend:"
echo "  sudo journalctl -u backend-encomenda.service -f"
echo ""
echo "  # Testar rota manualmente:"
echo "  curl http://localhost:5000/email/teste"
echo ""
echo "  # Testar com cobrança real:"
echo "  curl -X POST http://localhost:5000/email/reenviar/COB-TEST-002"
echo ""
echo "========================================================================"
echo ""

# Salvar log da instalação
LOG_FILE="$FINANCEIRO_DIR/email_install_$(date +%Y%m%d_%H%M%S).log"
cat > "$LOG_FILE" << EOF
Instalação da Rota de Email
============================
Data: $(date)
Usuário: $(whoami)
Hostname: $(hostname)

Arquivos instalados:
- $FINANCEIRO_DIR/financeiro_email.py
- $FINANCEIRO_DIR/financeiro_email_routes.py

Arquivo de rotas modificado:
- $ARQUIVO_ROTAS

Status do backend:
$(systemctl status backend-encomenda.service --no-pager | head -10)

Teste da rota:
$TESTE
EOF

echo "💾 Log salvo em: $LOG_FILE"
echo ""

exit 0
