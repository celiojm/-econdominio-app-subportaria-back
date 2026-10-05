# oracle/ — cópia das rotinas da produção na Oracle

Cópia de referência (somente versionamento) dos scripts que rodam no **crontab do ubuntu no oracle_backend**
(`~/backend`), que não existem no código do servidor antigo. Atualizar esta pasta copiando da Oracle sempre
que um desses scripts mudar lá.

- `crontab_oracle.txt` — lista das rotinas de produção (worker WhatsApp, syncs, renovação, régua, lembretes,
  monitor Meta, estatísticas, relatórios, folha, despesas, monitor dos servidores, backup).
- `monitor/` — relatórios diário/semanal/mensal, gerar/pagar folha, despesas Asaas, monitor dos servidores e
  backup diário do MySQL (caminhos da Oracle, `/home/ubuntu/backend`).

Ficaram DE FORA por terem senha/token escritos no código: `monitor/diagnostico_smtp.py`,
`monitor/monitor_zapi.py`, `monitor/teste_email_relatorio.py`. Também fora: `senha.py` (credencial).
Segredos ficam só nos `.env`/`.env.worker` da Oracle — nunca neste repositório.

Cópia feita em 05/10/2026.
