#!/bin/bash

# ============================================================================
# SCRIPT FINAL - ATIVAR BOTÃO EMAIL
# Execute: bash ativar_botao_email.sh
# ============================================================================

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║         🚀 ATIVAR BOTÃO EMAIL - ÚLTIMA ETAPA 🚀           ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Cores
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ============================================================================
# 1. VERIFICAR ARQUIVOS
# ============================================================================
echo "📦 Verificando arquivos necessários..."
echo ""

cd ~/backendV2/financeiro

if [ -f "financeiro_email.py" ]; then
    echo -e "${GREEN}✅ financeiro_email.py${NC}"
else
    echo -e "${RED}❌ financeiro_email.py FALTANDO${NC}"
    exit 1
fi

if [ -f "financeiro_email_routes.py" ]; then
    echo -e "${GREEN}✅ financeiro_email_routes.py${NC}"
else
    echo -e "${RED}❌ financeiro_email_routes.py FALTANDO${NC}"
    exit 1
fi

if grep -q "email_router" financeiro_rotas.py; then
    echo -e "${GREEN}✅ financeiro_rotas.py (email_router registrado)${NC}"
else
    echo -e "${RED}❌ financeiro_rotas.py NÃO tem email_router${NC}"
    exit 1
fi

echo ""

# ============================================================================
# 2. REINICIAR BACKEND
# ============================================================================
echo "🔄 Reiniciando backend..."
echo ""

sudo systemctl restart backend-encomenda.service

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Backend reiniciado com sucesso${NC}"
else
    echo -e "${RED}❌ Erro ao reiniciar backend${NC}"
    exit 1
fi

echo ""
echo "⏳ Aguardando backend inicializar (5 segundos)..."
sleep 5

# ============================================================================
# 3. VERIFICAR STATUS
# ============================================================================
echo ""
echo "🔍 Verificando status do backend..."
echo ""

if systemctl is-active --quiet backend-encomenda.service; then
    echo -e "${GREEN}✅ Backend está rodando${NC}"
else
    echo -e "${RED}❌ Backend NÃO está rodando${NC}"
    echo ""
    echo "Ver logs:"
    echo "  sudo journalctl -u backend-encomenda.service -n 50"
    exit 1
fi

# ============================================================================
# 4. TESTAR ROTA
# ============================================================================
echo ""
echo "🧪 Testando rota de email..."
echo ""

TESTE=$(curl -s http://localhost:5000/email/teste 2>&1)

if echo "$TESTE" | grep -q "success"; then
    echo -e "${GREEN}✅ Rota /email/teste FUNCIONANDO!${NC}"
    echo ""
    echo "Resposta:"
    echo "$TESTE" | python3 -m json.tool 2>/dev/null || echo "$TESTE"
else
    echo -e "${RED}❌ Rota não está respondendo corretamente${NC}"
    echo ""
    echo "Resposta:"
    echo "$TESTE"
    echo ""
    echo "Ver logs:"
    echo "  sudo journalctl -u backend-encomenda.service -n 50"
    exit 1
fi

# ============================================================================
# 5. SUCESSO!
# ============================================================================
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║              ✅ INSTALAÇÃO CONCLUÍDA COM SUCESSO ✅        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "🎉 O botão Email está ATIVO e funcionando!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🧪 COMO TESTAR:"
echo ""
echo "  1. Acesse: https://financeiro.econdominio.app.br"
echo ""
echo "  2. Vá em: Cobranças → Histórico de um condomínio"
echo ""
echo "  3. Clique no botão: 📧 Email"
echo ""
echo "  4. Resultado esperado:"
echo "     - Botão mostra \"Enviando...\""
echo "     - Toast verde: \"✅ Email enviado para email@exemplo.com!\""
echo "     - Email chega em 1-2 minutos"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📊 ROTAS DISPONÍVEIS:"
echo ""
echo "  POST /email/reenviar/{id}  - Reenviar email de cobrança"
echo "  GET  /email/teste          - Testar envio de email"
echo "  GET  /email/config         - Ver configurações"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🐛 TROUBLESHOOTING:"
echo ""
echo "  # Ver logs em tempo real:"
echo "  sudo journalctl -u backend-encomenda.service -f"
echo ""
echo "  # Testar rota manualmente:"
echo "  curl http://localhost:5000/email/teste"
echo ""
echo "  # Testar com cobrança real:"
echo "  curl -X POST http://localhost:5000/email/reenviar/pay_abc123"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "✅ TUDO PRONTO! O sistema está funcionando!"
echo ""

exit 0
