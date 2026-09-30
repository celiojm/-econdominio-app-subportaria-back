# ================================================================================
# ARQUIVO: README_INSTALACAO.md
# PASTA:   ~/backend/afiliado/
# CAMINHO: visionlpr@vps60688:~/backend/afiliado/README_INSTALACAO.md
# ================================================================================

# Sistema de Afiliados e-Condomínio
## Manual de Instalação e Configuração

---

## 📋 Sumário
1. [Pré-requisitos](#pré-requisitos)
2. [Instalação do Banco de Dados](#instalação-do-banco-de-dados)
3. [Instalação do Backend](#instalação-do-backend)
4. [Configuração do Scheduler](#configuração-do-scheduler)
5. [Integração com Cadastro de Clientes](#integração-com-cadastro-de-clientes)
6. [Integração com Webhook](#integração-com-webhook)
7. [Testes](#testes)
8. [Manutenção](#manutenção)

---

## 🔧 Pré-requisitos

- MySQL 8.0+
- Python 3.8+
- FastAPI instalado
- Acesso ao servidor backend (vps60688)
- Permissões de root no MySQL

---

## 💾 Instalação do Banco de Dados

### Passo 1: Executar Script SQL

```bash
# Conectar ao MySQL
mysql -u root -p

# Executar o script de criação das tabelas
source ~/backend/afiliado/01_criar_tabelas_afiliados.sql
```

### Passo 2: Verificar Tabelas Criadas

```sql
USE AdmGeral;

SHOW TABLES LIKE 'afiliado%';

-- Deve mostrar:
-- afiliado_comissoes
-- afiliado_condominios
-- afiliado_logs
-- afiliado_saques
-- afiliados

-- Verificar permissões
SELECT * FROM permissoes WHERE nivel = 6;
```

---

## 🐍 Instalação do Backend

### Passo 1: Copiar Arquivos

Os arquivos do backend devem estar em: `~/backend/afiliado/`

```
backend/
└── afiliado/
    ├── __init__.py
    ├── afiliado_models.py
    ├── afiliado_service.py
    ├── afiliado_routes.py
    ├── afiliado_webhook.py
    ├── afiliado_cadastro_integration.py
    ├── afiliado_scheduler.py
    └── README_INSTALACAO.md
```

### Passo 2: Instalar Dependências

```bash
cd ~/backend
pip3 install schedule --break-system-packages
```

### Passo 3: Registrar Rotas no main.py

Editar `~/backend/app/main.py`:

```python
# Adicionar no início do arquivo
from afiliado import afiliado_router

# Na seção de routers, adicionar:
app.include_router(afiliado_router)
```

### Passo 4: Reiniciar Backend

```bash
sudo systemctl restart backend-v2.service
sudo systemctl status backend-v2.service
```

### Passo 5: Verificar Endpoints

```bash
curl http://localhost:8000/api/afiliados/admin/todos
```

---

## ⏰ Configuração do Scheduler

O scheduler é responsável por liberar comissões automaticamente após 7 dias.

### Criar Serviço Systemd

```bash
sudo nano /etc/systemd/system/afiliado-scheduler.service
```

Conteúdo:

```ini
[Unit]
Description=e-Condominio Afiliados Scheduler
After=network.target mysql.service

[Service]
Type=simple
User=visionlpr
WorkingDirectory=/home/visionlpr/backend
Environment="PYTHONPATH=/home/visionlpr/backend"
ExecStart=/usr/bin/python3 -m afiliado.afiliado_scheduler
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Ativar e Iniciar Serviço

```bash
sudo systemctl daemon-reload
sudo systemctl enable afiliado-scheduler
sudo systemctl start afiliado-scheduler
sudo systemctl status afiliado-scheduler
```

### Verificar Logs

```bash
sudo journalctl -u afiliado-scheduler -f
```

---

## 🔗 Integração com Cadastro de Clientes

### Localizar Arquivo de Cadastro

Arquivo: `~/backend/cliente/router.py` (ou similar)

### Adicionar Código de Integração

No endpoint de cadastro de cliente, após criar o condomínio:

```python
from afiliado.afiliado_cadastro_integration import vincular_afiliado_ao_cadastro

@router.post("/cadastro")
async def cadastrar_cliente(
    data: CadastroClienteRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    try:
        # ... código existente de cadastro ...
        
        # Criar condomínio
        condominio_id = criar_condominio(db, data)
        
        # ADICIONAR ESTA PARTE:
        # Vincular afiliado se houver código na URL
        codigo_afiliado = request.query_params.get('afiliado')
        if codigo_afiliado:
            try:
                ip_origem = request.client.host if request.client else None
                user_agent = request.headers.get('user-agent')
                
                vincular_afiliado_ao_cadastro(
                    db,
                    condominio_id,
                    codigo_afiliado,
                    ip_origem,
                    user_agent
                )
                logger.info(f"Afiliado vinculado ao condomínio {condominio_id}")
            except Exception as e:
                # Não quebrar o cadastro se o vínculo falhar
                logger.error(f"Erro ao vincular afiliado: {e}")
        
        # ... resto do código ...
        
        return {"success": True, "condominio_id": condominio_id}
        
    except Exception as e:
        logger.error(f"Erro no cadastro: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

---

## 🔔 Integração com Webhook

### Localizar Arquivo de Webhook

Arquivo: `~/backend/financeiro/financeiro_webhook.py`

### Adicionar Processamento de Comissões

No endpoint do webhook, adicionar:

```python
from afiliado.afiliado_webhook import processar_webhook_pagamento, processar_cancelamento_assinatura

@router.post("/webhook/asaas")
async def webhook_asaas(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
        evento = payload.get('event')
        
        # ... processamento existente ...
        
        # ADICIONAR ESTA PARTE:
        # Processar comissões de afiliado
        if evento in ['PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED']:
            try:
                resultado = processar_webhook_pagamento(db, payload)
                if resultado:
                    logger.info(f"Comissão processada: {resultado}")
            except Exception as e:
                logger.error(f"Erro ao processar comissão: {e}")
        
        elif evento == 'SUBSCRIPTION_CANCELLED':
            try:
                resultado = processar_cancelamento_assinatura(db, payload)
                if resultado:
                    logger.info(f"Comissões canceladas: {resultado}")
            except Exception as e:
                logger.error(f"Erro ao cancelar comissões: {e}")
        
        return {"received": True}
        
    except Exception as e:
        logger.error(f"Erro no webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

---

## 🧪 Testes

### 1. Testar Cadastro de Afiliado

```bash
curl -X POST http://localhost:8000/api/afiliados/cadastrar \
  -H "Content-Type: application/json" \
  -d '{
    "usuario_id": 1,
    "tipo_comissao": "primeiro_mes"
  }'
```

### 2. Testar Vínculo via URL

Acessar no navegador:
```
https://painel.econdominio.app.br/cadastro_cliente?afiliado=CODIGO_GERADO
```

### 3. Testar Cálculo de Comissão

```sql
-- Simular pagamento de cobrança
UPDATE cobrancas 
SET status = 'pago', data_pagamento = NOW() 
WHERE id_cobranca = X;

-- Executar cálculo manual
SELECT * FROM afiliado_comissoes WHERE cobranca_id = X;
```

### 4. Testar Dashboard

```bash
curl http://localhost:8000/api/afiliados/dashboard/1
```

---

## 🔧 Manutenção

### Comandos Úteis

```bash
# Ver logs do backend
sudo journalctl -u backend-v2.service -f

# Ver logs do scheduler
sudo journalctl -u afiliado-scheduler -f

# Reiniciar serviços
sudo systemctl restart backend-v2
sudo systemctl restart afiliado-scheduler

# Verificar status
sudo systemctl status backend-v2
sudo systemctl status afiliado-scheduler
```

### Queries Úteis

```sql
-- Ver todos os afiliados
SELECT * FROM afiliados;

-- Ver vínculos
SELECT 
    a.codigo_afiliado,
    a.tipo_comissao,
    c.nome as condominio,
    ac.data_vinculo
FROM afiliado_condominios ac
JOIN afiliados a ON a.id = ac.afiliado_id
JOIN condominios c ON c.id = ac.condominio_id
ORDER BY ac.data_vinculo DESC;

-- Ver comissões
SELECT 
    a.codigo_afiliado,
    c.nome as condominio,
    com.valor_comissao,
    com.status,
    com.tipo,
    com.data_criacao
FROM afiliado_comissoes com
JOIN afiliados a ON a.id = com.afiliado_id
JOIN condominios c ON c.id = com.condominio_id
ORDER BY com.data_criacao DESC;

-- Liberar comissões manualmente (caso necessário)
UPDATE afiliado_comissoes ac
JOIN cobrancas c ON c.id_cobranca = ac.cobranca_id
SET ac.status = 'liberada', ac.data_liberacao = NOW()
WHERE ac.status = 'pendente'
  AND c.status IN ('pago', 'RECEIVED', 'CONFIRMED')
  AND c.data_pagamento <= DATE_SUB(NOW(), INTERVAL 7 DAY);
```

---

## 📊 Monitoramento

### Métricas Importantes

1. **Afiliados Ativos**: `SELECT COUNT(*) FROM afiliados WHERE ativo = 1`
2. **Comissões Pendentes**: `SELECT COUNT(*), SUM(valor_comissao) FROM afiliado_comissoes WHERE status = 'pendente'`
3. **Saldo Total Liberado**: `SELECT SUM(valor_comissao) FROM afiliado_comissoes WHERE status = 'liberada'`
4. **Saques Pendentes**: `SELECT COUNT(*), SUM(valor_solicitado) FROM afiliado_saques WHERE status = 'solicitado'`

---

## ⚠️ Importante

1. **Backup**: Sempre faça backup do banco antes de executar scripts
2. **Testes**: Teste em ambiente de desenvolvimento primeiro
3. **Logs**: Monitore os logs durante os primeiros dias
4. **Performance**: O scheduler deve rodar em horários de baixo uso

---

## 📞 Suporte

Em caso de problemas:
1. Verificar logs do serviço
2. Verificar conexão com banco de dados
3. Verificar permissões de usuário
4. Consultar este manual

---

**Data de Criação**: 07/01/2026
**Versão**: 1.0
**Autor**: Sistema e-Condomínio
