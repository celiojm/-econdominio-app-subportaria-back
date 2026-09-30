# FINANCEIRO.md — Módulo Financeiro eCondomínio

> Documentação completa do sistema financeiro da plataforma eCondomínio.
> Atualizado em: 2026-05-20

---

## 1. Visão Geral

O módulo financeiro é composto por **dois painéis distintos**:

| Painel | URL | Público-alvo |
|--------|-----|--------------|
| Admin do condomínio | `admin.econdominio.com.br/financeiro` | Síndico / Admin do condomínio |
| Financeiro interno | `financeiro.econdominio.com.br` | Equipe interna da eCondomínio |

Integrações externas:
- **Asaas** — geração de boletos, cobrança via PIX, emissão de NFS-e
- **Brevo (SMTP)** — envio de emails de confirmação de pagamento e nota fiscal

---

## 2. Infraestrutura

```
Frontend admin:    APPs FPOLIS — 10.3.1.4  ~/desenvolvimento/admin
Frontend financeiro: APPs FPOLIS — 10.3.1.4  ~/desenvolvimento/financeiro
Backend:           BACKEND FPOLIS — 10.3.1.6  /home/visionlpr/backend
Banco:             MySQL 8 — 10.3.1.3:6033 (ProxySQL) — AdmGeral
```

---

## 3. Painel do Condomínio — admin.econdominio.com.br/financeiro

### 3.1 Arquivo frontend
```
~/desenvolvimento/admin/src/pages/financeiro/Financeiro.tsx
```

### 3.2 O que exibe
- Cards: apartamentos, plano atual, validade da assinatura
- Se sem cobranças ou em período de teste: tela de geração do primeiro boleto
- Histórico de pagamentos com status, valor, data de pagamento
- Botão **Boleto** (pendentes) — baixa via Asaas
- Botão **NF** (pagos) — baixa PDF da nota fiscal via Asaas

### 3.3 Fluxo — primeiro boleto
1. Síndico seleciona o plano (Mensal / Trimestral / Semestral / Anual)
2. Sistema calcula o valor com desconto por período
3. Confirma: mostra condomínio, apartamentos, plano, valor, email de envio
4. Backend gera cobrança no Asaas → salva em `cobrancas` → enfileira WhatsApp

### 3.4 Descontos por período
| Período | Desconto | Ciclo |
|---------|----------|-------|
| Mensal | 0% | 30 dias |
| Trimestral | 5% | 90 dias |
| Semestral | 10% | 180 dias |
| Anual | 15% | 360 dias |

### 3.5 Backend — rotas
```
GET  /painel/financeiro/condominio              → dados do condomínio autenticado
GET  /painel/financeiro/cobrancas              → histórico de cobranças
POST /painel/financeiro/gerar-primeiro-boleto  → gera boleto no Asaas
GET  /painel/financeiro/boleto/{id}/pdf        → retorna URL do boleto
GET  /painel/financeiro/nota-fiscal/{id}       → retorna URL do PDF da NF
```

### 3.6 Arquivo backend
```
/home/visionlpr/backend/financeiro/financeiro_condominio_routes.py
```
Registrado em `main.py`:
```python
from financeiro.financeiro_condominio_routes import router as financeiro_cond_router
app.include_router(financeiro_cond_router, prefix="/painel/financeiro", tags=["financeiro-condominio"])
```

---

## 4. Painel Financeiro Interno — financeiro.econdominio.com.br

### 4.1 Arquivos frontend
```
~/desenvolvimento/financeiro/src/pages/
├── Dashboard.jsx         — visão geral
├── Cobrancas.jsx         — lista de condomínios com cobranças e ajuste de validade
├── Pagamentos.jsx        — histórico de pagamentos recebidos
├── Assinatura.jsx        — gestão de assinaturas
├── CondominiosAdmin.jsx  — administração de condomínios
├── ContasPagar.jsx       — contas a pagar internas
└── Previsao.jsx          — previsão de receita
```

### 4.2 Cobranças (`Cobrancas.jsx`)

**Filtros disponíveis:**
- Busca por nome/CNPJ
- Status: Todos / ✅ Ativos / 🔵 Em teste / 🔴 Inadimplentes / ⚠️ Validade vencida

**Ações por condomínio:**
- `$` — Nova cobrança (modal com seleção de plano, valor, vencimento, forma)
- 📄 — Histórico de cobranças
- 📅 — Ajuste manual de validade

**Modal Nova Cobrança:**
- Seleção de plano com cálculo automático de valor
- Forma de pagamento padrão: **Boleto Bancário**
- Cria cobrança no Asaas e salva em `cobrancas`

**Modal Ajuste de Validade:**
- Presets: +7d, +15d, +30d, +90d, +365d, -7d, -15d, -30d
- Valor manual com preview da nova data
- Campo de motivo e operador
- Endpoint: `POST /condominios/{id}/ajustar-validade`

### 4.3 Pagamentos (`Pagamentos.jsx`)

**Filtros:** mês/ano, forma de pagamento, nome do cliente

**Colunas:** data pagamento, condomínio, descrição, forma (PIX/Boleto/Cartão), validade da assinatura, valor

**Botão Sincronizar Asaas:** chama `POST /api/financeiro/pagamentos/sincronizar`

### 4.4 Backend — rotas principais
```
GET  /api/financeiro/condominios-com-validade   → lista condomínios com validade
GET  /api/financeiro/pagamentos                 → lista pagamentos recebidos
GET  /api/financeiro/pagamentos/totalizadores   → totais por forma de pagamento
POST /api/financeiro/pagamentos/sincronizar     → sincroniza com Asaas
POST /api/financeiro/sync/pagamentos            → sync completo (buscar_todos=true/false)
POST /api/financeiro/condominios/{id}/ajustar-validade → ajuste manual de validade
PUT  /api/financeiro/condominios/{id}           → atualiza dados do condomínio
POST /api/financeiro/cobrancas                  → cria nova cobrança
POST /api/financeiro/cobrancas/{id}/gerar-nf   → gera NFS-e via Asaas
GET  /api/financeiro/cobrancas/{id}/nf          → consulta NFS-e
POST /api/financeiro/cobrancas/{id}/enviar-nf   → envia NF por email
```

### 4.5 Arquivos backend
```
/home/visionlpr/backend/financeiro/
├── financeiro_condominios.py      — CRUD condomínios + ajuste validade
├── financeiro_pagamentos.py       — listagem de pagamentos + totalizadores
├── financeiro_sync.py             — sincronização Asaas + condominios-com-validade
├── financeiro_cobrancas.py        — cobranças (CRUD completo)
├── financeiro_cobrancas_painel.py — criar cobrança via painel interno
├── financeiro_nfe.py              — emissão e envio de NFS-e
├── financeiro_asaas_service.py    — service layer Asaas
├── financeiro_rotas.py            — router principal (agrega sub-routers)
└── financeiro_condominio_routes.py — rotas painel do condomínio (síndico)
```

---

## 5. Banco de Dados

### 5.1 Tabela `cobrancas`
```sql
id_cobranca               INT PK AUTO_INCREMENT
id_assinatura             INT (NULL se avulsa)
id_condominio             INT NOT NULL
valor                     DECIMAL(10,2)
valor_pago                DECIMAL(10,2)
data_vencimento           DATE
data_pagamento            DATE
status                    ENUM(pendente|pago|vencido|PENDING|RECEIVED|CONFIRMED|
                               OVERDUE|RECEIVED_IN_CASH|CANCELLED|cancelada|estornada)
forma_pagamento           ENUM(pix|boleto|cartao|transferencia|dinheiro)
descricao                 TEXT
asaas_payment_id          VARCHAR(100)   — ID do pagamento no Asaas
asaas_invoice_id          VARCHAR(50)    — ID da NFS-e no Asaas
invoice_status            VARCHAR(30)    — SCHEDULED|AUTHORIZED|ERROR|CANCELED
invoice_pdf_url           VARCHAR(500)   — URL do PDF da NFS-e
invoice_created_at        DATETIME
email_confirmacao_enviado TINYINT(1) DEFAULT 0  — email de pagamento enviado
nf_email_enviado          TINYINT(1) DEFAULT 0  — email da NF enviado
nf_tentativas             INT DEFAULT 0          — tentativas de geração de NF
nf_erro                   VARCHAR(200)           — último erro de NF
asaas_customer_id         VARCHAR(100)
data_criacao              TIMESTAMP
data_atualizacao          TIMESTAMP
```

### 5.2 Tabela `assinaturas`
```sql
id_assinatura         INT PK
id_condominio         INT
tipo_plano            VARCHAR — mensal|trimestral|semestral|anual
valor                 DECIMAL(10,2)
ciclo                 VARCHAR
data_inicio           DATE
validade_ate          DATE      — data de expiração da assinatura
status                ENUM(ativa|suspensa|cancelada|inadimplente)
renovacao_automatica  TINYINT(1)
asaas_customer_id     VARCHAR(50)
asaas_subscription_id VARCHAR(50)
data_criacao          TIMESTAMP
data_atualizacao      TIMESTAMP
```

### 5.3 Tabela `condominios` — campos financeiros relevantes
```sql
asaas_customer_id     VARCHAR(50)    — ID do cliente no Asaas
validade_ate          DATE           — validade da assinatura
assinatura_status     VARCHAR(20)    — ativa|trial|inadimplente
bonificado            TINYINT(1)     — em período bonificado
data_fim_bonificado   DATE
valor_mensal_base     DECIMAL(10,2)  — valor base p/ cálculo do plano
valor_plano_final     DECIMAL(10,2)
plano_selecionado     VARCHAR(20)    — mensal|trimestral|semestral|anual
forma_pagamento       VARCHAR(20)
cobranca_responsavel  VARCHAR(100)   — nome do responsável financeiro
cobranca_email        VARCHAR(150)   — email para cobranças (prioritário)
cobranca_whats        VARCHAR(20)    — WhatsApp para cobranças
email_financeiro      VARCHAR(150)   — email financeiro alternativo
```

---

## 6. Scripts Automáticos (Cron)

### 6.1 sync_pagamentos_e_nf.py — 07:00 diário
```
/home/visionlpr/backend/sync_pagamentos_e_nf.py
Log: /home/visionlpr/backend/sync_pagamentos_e_nf.log
```

**O que faz:**
1. Chama `/api/financeiro/sync/pagamentos?buscar_todos=false`
2. Para cada pagamento novo: envia email de confirmação ao condomínio
3. Busca cobranças pagas sem NF (`asaas_invoice_id IS NULL`, `nf_tentativas < 3`)
4. Para cada uma: chama `/api/financeiro/cobrancas/{id}/gerar-nf`
5. Registra tentativas e erros por cobrança

**Email de confirmação inclui:** valor pago, forma, data, nova validade da assinatura

### 6.2 sync_nf_aprovadas.py — 08:30 diário
```
/home/visionlpr/backend/sync_nf_aprovadas.py
Log: /home/visionlpr/backend/sync_nf_aprovadas.log
```

**O que faz:**
1. Busca cobranças com `asaas_invoice_id` e `nf_email_enviado = 0`
2. Consulta status atual da NF no Asaas
3. Se `AUTHORIZED`: envia email com link PDF da NF
4. Marca `nf_email_enviado = 1`

**Crontab:**
```
0 7 * * * flock -n /tmp/sync_nf.lock /home/visionlpr/backend/venv/bin/python3 /home/visionlpr/backend/sync_pagamentos_e_nf.py >> /home/visionlpr/backend/sync_pagamentos_e_nf.log 2>&1
30 8 * * * flock -n /tmp/sync_nf_aprov.lock /home/visionlpr/backend/venv/bin/python3 /home/visionlpr/backend/sync_nf_aprovadas.py >> /home/visionlpr/backend/sync_nf_aprovadas.log 2>&1
```

---

## 7. Integração Asaas

### 7.1 Variáveis de ambiente
```
ASAAS_API_KEY       — chave da API (nunca commitar)
ASAAS_BASE_URL      — https://api.asaas.com/v3
ASAAS_AMBIENTE      — production
ASAAS_WEBHOOK_SECRET
```

### 7.2 Endpoints Asaas utilizados
```
POST /payments                    — criar cobrança (boleto/PIX)
GET  /payments/{id}               — consultar cobrança
DELETE /payments/{id}             — cancelar cobrança
GET  /payments?customer={id}      — listar cobranças por cliente
GET  /payments?status=RECEIVED    — listar pagamentos recebidos
POST /customers                   — criar cliente
GET  /customers/{id}              — consultar cliente
GET  /subscriptions               — listar assinaturas
POST /invoices                    — emitir NFS-e
GET  /invoices/{id}               — consultar NFS-e
DELETE /invoices/{id}             — cancelar NFS-e
GET  /invoices?payment={id}       — buscar NFS-e por pagamento
```

### 7.3 Código de serviço NFS-e por município
```python
CODIGOS_SERVICO = {
    "FLORIANOPOLIS": "1.05.01",   # portal próprio — sem zero
    "FLORIANÓPOLIS": "1.05.01",
    "SAO PAULO":     "01.05.01",  # portal nacional — com zero
    "SÃO PAULO":     "01.05.01",
    "JAGUARIUNA":    "01.05.01",
    "JAGUARIÚNA":    "01.05.01",
    "RIO VERDE":     "01.05.01",
    "DEFAULT":       "01.05.01",  # padrão seguro para demais
}
```

**Serviço:** Licenciamento de uso de software na modalidade SaaS — Sistema eCondomínio
**ISS:** 2%
**CNPJ emissor:** 64.931.933/0001-85
**IM:** 00030555/2026

### 7.4 Fluxo NFS-e
1. Cobrança paga → `sync_pagamentos_e_nf.py` chama `gerar-nf`
2. Backend envia `POST /invoices` ao Asaas com `payment`, `municipalServiceCode`, `taxes`
3. Asaas processa com a prefeitura → status vai para `AUTHORIZED` ou `ERROR`
4. `sync_nf_aprovadas.py` detecta `AUTHORIZED` → envia PDF por email

---

## 8. Email — Configuração SMTP

```
Servidor:  smtp-relay.brevo.com
Porta:     587 (STARTTLS)
Usuário:   a64388001@smtp-brevo.com
Remetente: financeiro@econdominio.com.br
Nome:      Financeiro eCondomínio
```

**Prioridade de destinatário por condomínio:**
1. `cobranca_email` (campo específico de cobrança)
2. `email_financeiro` (email financeiro)
3. `email` (email geral do condomínio)

---

## 9. Cálculo de Valores

### 9.1 Tabela de preços por apartamentos
```python
def calcularPreco(qtd):
    if qtd <= 25:   return max(qtd * 1.20, 29.80)
    if qtd <= 1000: p = 1.20 - (0.60 * ((qtd - 25) / 975))
    elif qtd <= 2000: p = 0.60 - (0.08 * ((qtd - 1000) / 1000))
    else: p = 0.52
    return qtd * p
```

### 9.2 Valor por plano
```
valor_mensal = calcularPreco(total_apartamentos)
valor_total  = valor_mensal * meses * (1 - desconto%)
```

---

## 10. Procedimentos Operacionais

### 10.1 Gerar boleto manualmente para um condomínio
1. Acesse `financeiro.econdominio.com.br/financeiro/cobrancas`
2. Localize o condomínio → clique em `$`
3. Selecione o período e ajuste o valor se necessário
4. Clique em **Criar Cobrança**

### 10.2 Ajustar validade manualmente
1. Acesse `financeiro.econdominio.com.br/financeiro/cobrancas`
2. Clique no ícone 📅 do condomínio
3. Use os presets (+7d, +30d, etc.) ou valor manual
4. Informe o motivo e confirme

### 10.3 Reprocessar NFs com erro
```sql
-- Ver NFs com erro
SELECT id_cobranca, nf_erro, asaas_invoice_id
FROM cobrancas WHERE invoice_status = 'ERROR' OR nf_tentativas >= 3;

-- Zerar para tentar novamente após corrigir no Asaas
UPDATE cobrancas
SET asaas_invoice_id = NULL, invoice_status = NULL,
    nf_tentativas = 0, nf_erro = NULL
WHERE id_cobranca IN (...);
```

### 10.4 Reenviar emails de NF
```sql
UPDATE cobrancas SET nf_email_enviado = 0
WHERE id_cobranca IN (...);
```
Depois rode manualmente:
```bash
cd /home/visionlpr/backend
venv/bin/python3 sync_nf_aprovadas.py
```

### 10.5 Sincronização manual completa
```bash
cd /home/visionlpr/backend
# Sync de pagamentos + geração de NFs
venv/bin/python3 sync_pagamentos_e_nf.py

# Envio de NFs aprovadas
venv/bin/python3 sync_nf_aprovadas.py
```

### 10.6 Ver logs
```bash
tail -50 /home/visionlpr/backend/sync_pagamentos_e_nf.log
tail -50 /home/visionlpr/backend/sync_nf_aprovadas.log
```

### 10.7 Deploy frontend financeiro (produção)
```bash
# No servidor APPs (10.3.1.4)
cd ~/desenvolvimento/financeiro
npm run build

scp -r ~/desenvolvimento/financeiro/build/* visionlpr@10.3.1.3:/tmp/financeiro_build_novo/
ssh -t visionlpr@10.3.1.3 "bash -lc 'sudo rm -rf /var/www/financeiro/* && sudo cp -r /tmp/financeiro_build_novo/* /var/www/financeiro/ && sudo chown -R www-data:www-data /var/www/financeiro && sudo systemctl reload nginx && echo ✅ OK'"
```

### 10.8 Deploy frontend admin — página Financeiro (produção)
```bash
# No servidor APPs (10.3.1.4)
cd ~/desenvolvimento/admin
npm run build

scp -r ~/desenvolvimento/admin/build/* visionlpr@10.3.1.3:/tmp/admin_build_novo/
ssh -t visionlpr@10.3.1.3 "bash -lc 'sudo rm -rf /var/www/admin/* && sudo cp -r /tmp/admin_build_novo/* /var/www/admin/ && sudo chown -R www-data:www-data /var/www/admin && sudo systemctl reload nginx && echo ✅ OK'"
```

---

## 11. Pendências e Observações

### Condomínios inadimplentes (sem pagamento no Asaas)
```
ID 14 — F&G DIGITAL LTDA           — vencido desde 03/04/2026
ID 18 — GUIBOR SERVICOS INTEGRADOS — vencido desde 01/04/2026
ID 23 — CONDOMINIO SAN SEBASTIAN   — trial
ID 24 — PEDRO RAFAEL LOPES GARCIA  — vencido desde 16/04/2026
ID 25 — COSTA ATLANTICA            — trial
```

### NFs com erro de endereço no Asaas
Alguns condomínios têm CEP inválido no cadastro do Asaas.
Após corrigir no painel Asaas, zere as tentativas:
```sql
UPDATE cobrancas SET nf_tentativas = 0, nf_erro = NULL
WHERE id_cobranca IN (56, 79, 82, 83);
```

### Municípios com Portal Nacional
Configurar em Asaas → Notas Fiscais → Informações Fiscais → "Emito NFS-e pelo portal nacional = Sim"

### Bloquear acesso de condomínios com validade vencida
Ainda não implementado — a verificar no backend de autenticação.

---

## 12. Estrutura de Arquivos — Resumo

```
Backend (/home/visionlpr/backend/financeiro/):
├── financeiro_condominio_routes.py  ← painel síndico
├── financeiro_condominios.py        ← CRUD + ajuste validade
├── financeiro_pagamentos.py         ← listagem + totalizadores
├── financeiro_sync.py               ← sync Asaas
├── financeiro_nfe.py                ← NFS-e
├── financeiro_cobrancas.py          ← cobranças (completo)
├── financeiro_cobrancas_painel.py   ← criar cobrança (painel interno)
└── financeiro_rotas.py              ← router principal

Scripts (/home/visionlpr/backend/):
├── sync_pagamentos_e_nf.py   ← cron 07:00
└── sync_nf_aprovadas.py      ← cron 08:30

Frontend admin (/home/visionlpr/desenvolvimento/admin/src/):
└── pages/financeiro/Financeiro.tsx

Frontend financeiro (/home/visionlpr/desenvolvimento/financeiro/src/pages/):
├── Cobrancas.jsx
├── Pagamentos.jsx
├── Dashboard.jsx
├── Assinatura.jsx
├── CondominiosAdmin.jsx
├── ContasPagar.jsx
└── Previsao.jsx
```
