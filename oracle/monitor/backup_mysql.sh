#!/bin/bash
# ============================================================================
# ARQUIVO: backup_mysql.sh
# PASTA: /home/ubuntu/backend/monitor/ (oracle_backend)
# DESCRIÇÃO: Backup diário (cron 00:01) do MySQL de produção na Oracle (AdmGeral, com procedures), comprimido,
#            enviado ao STORAGE pelo WireGuard (visionlpr@10.250.0.2, chave ~/.ssh/id_backup_storage restrita ao
#            receptor ~/mysql_bkp/receber_backup.sh, que grava AdmGeral_AAAAMMDD_HHMM.sql.gz e mantém os 5 últimos).
#            Usa o usuário de backup só leitura (~/.my_backup.cnf). Falha → WhatsApp pelo monitor_servidores.py.
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-03 data alteração: 2026-10-03
# ============================================================================
set -o pipefail
CNF=/home/ubuntu/.my_backup.cnf
LOG=/home/ubuntu/backend/monitor/backup_mysql.log
ERR=$(mktemp)
falha() {
  echo "$(date '+%d/%m %H:%M') FALHA: $1" >> "$LOG"
  cd /home/ubuntu/backend/monitor && MSG="🔴 *Backup do MySQL (Oracle) FALHOU* — $(date '+%d/%m %H:%M')
$1" /usr/bin/python3 -c "import os, monitor_servidores as m; m.whatsapp(os.environ['MSG'])" >/dev/null 2>&1
  rm -f "$ERR"; exit 1
}
[ -f "$CNF" ] || falha "credencial de backup ausente ($CNF)"
T0=$(date +%s)
RESP=$(mysqldump --defaults-file="$CNF" --single-transaction --quick --routines --triggers --events --no-tablespaces \
         --set-gtid-purged=OFF --hex-blob --default-character-set=utf8mb4 AdmGeral 2>"$ERR" \
       | gzip -6 \
       | ssh -i /home/ubuntu/.ssh/id_backup_storage -o BatchMode=yes -o ConnectTimeout=20 visionlpr@10.250.0.2 2>>"$ERR")
ST=("${PIPESTATUS[@]}")
if [ "${ST[0]}" != "0" ] || [[ "$RESP" != OK* ]]; then
  falha "mysqldump=${ST[0]} envio=${ST[2]} | $(echo "$RESP" | tail -1) | $(head -c 200 "$ERR" | tr '\n' ' ')"
fi
echo "$(date '+%d/%m %H:%M') $RESP ($(( $(date +%s) - T0 ))s)" >> "$LOG"
rm -f "$ERR"
