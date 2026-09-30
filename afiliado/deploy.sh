#!/bin/bash
set -e

echo "🚀 Deploy Afiliado Frontend"
echo "============================"

# Build
echo "📦 Building..."
npm run build

# Restart Nginx
echo "🔄 Restarting Nginx..."
sudo systemctl restart nginx

# Testar
echo "✅ Testando..."
HTTP_CODE=$(curl -o /dev/null -s -w "%{http_code}" https://afiliado.econdominio.app.br/cadastro)

if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Deploy concluído! Frontend acessível (HTTP $HTTP_CODE)"
    echo "🌐 https://afiliado.econdominio.app.br/cadastro"
else
    echo "❌ Erro: Frontend retornou HTTP $HTTP_CODE"
    exit 1
fi
