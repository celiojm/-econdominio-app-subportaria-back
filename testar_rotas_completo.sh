#!/bin/bash

# ========================================
# Script de Teste Completo - Login + Rotas
# ========================================

echo "🔐 Teste Completo de Autenticação e Rotas"
echo "=========================================="
echo ""

# Cores
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BASE_URL="https://backend.econdominio.app.br"

# ========================================
# CONFIGURAÇÃO - AJUSTE AQUI!
# ========================================
EMAIL="lucas@inforseg.com.br"
read -sp "Digite a senha para $EMAIL: " PASSWORD
echo ""
echo ""

# ========================================
# PASSO 1: FAZER LOGIN
# ========================================
echo -e "${BLUE}[1/4]${NC} Fazendo login..."
echo "Email: $EMAIL"
echo ""

LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/mobile/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}")

# Verificar se login foi bem-sucedido
if echo "$LOGIN_RESPONSE" | grep -q "access_token"; then
    echo -e "${GREEN}✓ Login bem-sucedido!${NC}"
    
    # Extrair token usando jq (se disponível) ou grep
    if command -v jq &> /dev/null; then
        TOKEN=$(echo "$LOGIN_RESPONSE" | jq -r '.access_token')
    else
        TOKEN=$(echo "$LOGIN_RESPONSE" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)
    fi
    
    echo "Token obtido: ${TOKEN:0:50}..."
    echo ""
else
    echo -e "${RED}✗ Falha no login!${NC}"
    echo "Resposta do servidor:"
    echo "$LOGIN_RESPONSE" | head -20
    exit 1
fi

# ========================================
# PASSO 2: TESTAR ROTA MOBILE OTIMIZADA
# ========================================
echo -e "${BLUE}[2/4]${NC} Testando rota mobile otimizada (RECOMENDADA)..."
echo "Endpoint: /api/mobi/moradores/busca-incremental?q=ce&limit=50"
echo ""

TESTE1=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X GET "$BASE_URL/api/mobi/moradores/busca-incremental?q=ce&limit=50" \
  -H "Authorization: Bearer $TOKEN")

HTTP_CODE1=$(echo "$TESTE1" | grep "HTTP_CODE:" | cut -d':' -f2)
RESPONSE1=$(echo "$TESTE1" | sed '/HTTP_CODE:/d')

if [ "$HTTP_CODE1" == "200" ]; then
    echo -e "${GREEN}✓✓✓ SUCESSO! HTTP $HTTP_CODE1${NC}"
    echo "Resposta (primeiros 300 chars):"
    echo "$RESPONSE1" | head -c 300
    echo "..."
    echo ""
    echo -e "${GREEN}👉 USE ESTA ROTA NO APP MOBILE!${NC}"
else
    echo -e "${RED}✗ FALHA! HTTP $HTTP_CODE1${NC}"
    echo "Resposta:"
    echo "$RESPONSE1" | head -20
fi

echo ""
echo "=========================================="
echo ""

# ========================================
# PASSO 3: TESTAR ROTA DE LISTAGEM
# ========================================
echo -e "${BLUE}[3/4]${NC} Testando rota de listagem..."
echo "Endpoint: /api/moradores/?search=ce&limit=50"
echo ""

TESTE2=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X GET "$BASE_URL/api/moradores/?search=ce&limit=50" \
  -H "Authorization: Bearer $TOKEN")

HTTP_CODE2=$(echo "$TESTE2" | grep "HTTP_CODE:" | cut -d':' -f2)
RESPONSE2=$(echo "$TESTE2" | sed '/HTTP_CODE:/d')

if [ "$HTTP_CODE2" == "200" ]; then
    echo -e "${GREEN}✓ SUCESSO! HTTP $HTTP_CODE2${NC}"
    echo "Resposta (primeiros 300 chars):"
    echo "$RESPONSE2" | head -c 300
    echo "..."
else
    echo -e "${RED}✗ FALHA! HTTP $HTTP_CODE2${NC}"
    echo "Resposta:"
    echo "$RESPONSE2" | head -20
fi

echo ""
echo "=========================================="
echo ""

# ========================================
# PASSO 4: TESTAR ROTA DE BUSCA
# ========================================
echo -e "${BLUE}[4/4]${NC} Testando rota de busca..."
echo "Endpoint: /api/moradores/buscar?q=ce"
echo ""

TESTE3=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X GET "$BASE_URL/api/moradores/buscar?q=ce" \
  -H "Authorization: Bearer $TOKEN")

HTTP_CODE3=$(echo "$TESTE3" | grep "HTTP_CODE:" | cut -d':' -f2)
RESPONSE3=$(echo "$TESTE3" | sed '/HTTP_CODE:/d')

if [ "$HTTP_CODE3" == "200" ]; then
    echo -e "${GREEN}✓ SUCESSO! HTTP $HTTP_CODE3${NC}"
    echo "Resposta (primeiros 300 chars):"
    echo "$RESPONSE3" | head -c 300
    echo "..."
else
    echo -e "${RED}✗ FALHA! HTTP $HTTP_CODE3${NC}"
    echo "Resposta:"
    echo "$RESPONSE3" | head -20
fi

echo ""
echo "=========================================="

# ========================================
# RESUMO
# ========================================
echo ""
echo -e "${BLUE}📊 RESUMO DOS TESTES${NC}"
echo "=========================================="
echo ""

echo "1. Login: ${GREEN}✓ SUCESSO${NC}"
echo "2. Rota Mobile Otimizada: $([ "$HTTP_CODE1" == "200" ] && echo -e "${GREEN}✓ HTTP $HTTP_CODE1${NC}" || echo -e "${RED}✗ HTTP $HTTP_CODE1${NC}")"
echo "3. Rota de Listagem:      $([ "$HTTP_CODE2" == "200" ] && echo -e "${GREEN}✓ HTTP $HTTP_CODE2${NC}" || echo -e "${RED}✗ HTTP $HTTP_CODE2${NC}")"
echo "4. Rota de Busca:         $([ "$HTTP_CODE3" == "200" ] && echo -e "${GREEN}✓ HTTP $HTTP_CODE3${NC}" || echo -e "${RED}✗ HTTP $HTTP_CODE3${NC}")"

echo ""
echo "=========================================="
echo ""

# ========================================
# RECOMENDAÇÃO
# ========================================
if [ "$HTTP_CODE1" == "200" ]; then
    echo -e "${GREEN}✅ RECOMENDAÇÃO FINAL:${NC}"
    echo ""
    echo "Use esta rota no app mobile:"
    echo ""
    echo "  GET /api/mobi/moradores/busca-incremental"
    echo "  Parâmetros: q=TERMO&limit=50"
    echo ""
    echo "Exemplo de código:"
    echo "  const url = \`\${baseURL}/api/mobi/moradores/busca-incremental?q=\${termo}&limit=50\`"
    echo ""
elif [ "$HTTP_CODE2" == "200" ]; then
    echo -e "${YELLOW}⚠️ ALTERNATIVA:${NC}"
    echo ""
    echo "Use esta rota no app mobile:"
    echo ""
    echo "  GET /api/moradores/"
    echo "  Parâmetros: search=TERMO&limit=50"
    echo ""
else
    echo -e "${RED}❌ PROBLEMA:${NC}"
    echo "Nenhuma rota funcionou corretamente!"
    echo "Verifique os logs do backend."
fi

echo ""
echo "=========================================="
echo -e "${GREEN}✅ Teste concluído!${NC}"
echo "=========================================="
