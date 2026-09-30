#!/bin/bash
# ============================================================
#  sync_prod_to_dev.sh
#  Compara scripts .py entre produção e desenvolvimento.
#  Se o arquivo em ~/backend for MAIS NOVO que em ~/desenvolvimento,
#  copia para ~/desenvolvimento (com backup automático).
# ============================================================

PROD="$HOME/backend"
DEV="$HOME/desenvolvimento"
BACKUP_DIR="$DEV/.backup_sync_$(date +%Y%m%d_%H%M%S)"
COPIADOS=0
IGNORADOS=0
NOVOS=0

echo "=================================================="
echo "  COMPARAÇÃO: produção  →  desenvolvimento"
echo "  PROD : $PROD"
echo "  DEV  : $DEV"
echo "=================================================="
echo ""

# Cria pasta de backup (só se houver algo para copiar)
mkdir -p "$BACKUP_DIR"

# Itera sobre todos os .py na raiz de produção
for prod_file in "$PROD"/*.py; do
    [ -f "$prod_file" ] || continue
    filename=$(basename "$prod_file")
    dev_file="$DEV/$filename"

    prod_ts=$(stat -c %Y "$prod_file")
    prod_date=$(stat -c "%y" "$prod_file" | cut -d'.' -f1)

    if [ -f "$dev_file" ]; then
        dev_ts=$(stat -c %Y "$dev_file")
        dev_date=$(stat -c "%y" "$dev_file" | cut -d'.' -f1)

        if [ "$prod_ts" -gt "$dev_ts" ]; then
            echo "🔄 ATUALIZAR  : $filename"
            echo "   DEV (antigo): $dev_date"
            echo "   PROD (novo) : $prod_date"
            # Faz backup do arquivo de dev antes de sobrescrever
            cp "$dev_file" "$BACKUP_DIR/$filename.bak"
            cp -p "$prod_file" "$dev_file"
            echo "   ✅ Copiado  (backup salvo em .backup_sync_*/)"
            COPIADOS=$((COPIADOS + 1))
        else
            echo "✅ OK (dev igual ou mais novo): $filename"
            echo "   DEV : $dev_date"
            echo "   PROD: $prod_date"
            IGNORADOS=$((IGNORADOS + 1))
        fi
    else
        echo "🆕 NOVO em PROD (não existe no dev): $filename"
        echo "   PROD: $prod_date"
        cp -p "$prod_file" "$dev_file"
        echo "   ✅ Copiado para dev"
        NOVOS=$((NOVOS + 1))
    fi
    echo ""
done

# Remove backup dir se vazio (nada foi sobrescrito)
rmdir "$BACKUP_DIR" 2>/dev/null

echo "=================================================="
echo "  RESUMO"
echo "  Arquivos atualizados (prod → dev) : $COPIADOS"
echo "  Arquivos novos copiados            : $NOVOS"
echo "  Arquivos ignorados (dev ok/igual)  : $IGNORADOS"
if [ -d "$BACKUP_DIR" ]; then
    echo "  Backups dos arquivos antigos em    : $BACKUP_DIR"
fi
echo "=================================================="
