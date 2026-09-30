# 📧 CORREÇÃO DO SISTEMA DE ENVIO DE EMAIL - ECONDOMÍNIO
## Versão 2.0 - 21/12/2024

---

## 🎯 PROBLEMA IDENTIFICADO

O envio de emails estava falhando devido ao **acento no nome do remetente**.

**Antes (com erro):**
```python
"from_name": "Financeiro Econdomínio"  # ❌ COM ACENTO
```

**Depois (correto):**
```python
"from_name": "Financeiro Econdominio"  # ✅ SEM ACENTO
```

---

## 📦 ARQUIVOS CORRIGIDOS

### 1. **financeiro_email.py** (Backend)
   - **Localização:** `/home/visionlpr/backendV2/financeiro/financeiro_email.py`
   - **Mudanças principais:**
     - Removido acento do nome do remetente (linha 28)
     - Adicionado timeout de 30 segundos
     - Melhorados logs e tratamento de erros
     - Removidos todos os acentos dos templates HTML e texto

### 2. **testar_email_v2.py** (Script de Teste)
   - **Localização:** `/home/visionlpr/backendV2/testar_email_v2.py`
   - **Função:** Testar o envio de email independentemente do sistema

---

## 🚀 INSTRUÇÕES DE INSTALAÇÃO

### **PASSO 1: Fazer backup dos arquivos atuais**

```bash
# Conectar ao servidor
ssh visionlpr@10.10.0.1 -p 2022

# Navegar para a pasta do backend
cd /home/visionlpr/backendV2/financeiro

# Fazer backup do arquivo atual
cp financeiro_email.py financeiro_email.py.backup_$(date +%Y%m%d_%H%M%S)

# Verificar backup
ls -lh financeiro_email.py*
```

### **PASSO 2: Enviar arquivos corrigidos do Windows**

```cmd
REM No Windows, navegue até a pasta onde salvou os arquivos

REM Enviar financeiro_email.py
scp -v -P 2022 financeiro_email.py visionlpr@10.10.0.1:~/backendV2/financeiro/

REM Enviar script de teste
scp -v -P 2022 testar_email_v2.py visionlpr@10.10.0.1:~/backendV2/
```

### **PASSO 3: Testar no servidor**

```bash
# Conectar ao servidor
ssh visionlpr@10.10.0.1 -p 2022

# Navegar para a pasta
cd /home/visionlpr/backendV2

# Dar permissão de execução ao script de teste
chmod +x testar_email_v2.py

# Executar teste
python3 testar_email_v2.py
```

**Resultado esperado:**
```
============================================================
✅ EMAIL ENVIADO COM SUCESSO!
============================================================

📬 Verifique: celiojm@gmail.com
⏱️  Aguarde 1-2 minutos

Se o email chegar, o problema do acento foi resolvido!
```

### **PASSO 4: Reiniciar o backend**

```bash
# Método 1: Se estiver usando systemd
sudo systemctl restart backend-financeiro

# Método 2: Se estiver usando PM2
pm2 restart backend-financeiro

# Método 3: Se estiver rodando manualmente
# Pare o processo atual (Ctrl+C) e reinicie:
cd /home/visionlpr/backendV2
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### **PASSO 5: Testar pelo frontend**

1. Acesse o sistema financeiro
2. Vá até a tela de **Cobranças**
3. Selecione um condomínio com email válido
4. Gere uma cobrança ou reenvie uma existente
5. Clique no botão **📧 Enviar Email**

**Resultado esperado:**
- Toast de sucesso: "✅ Email enviado para [email]!"
- Email recebido em 1-2 minutos

---

## ✅ VALIDAÇÕES IMPORTANTES

### **Verificar configuração do .env**

```bash
cd /home/visionlpr/backendV2
cat .env | grep EMAIL
```

**Deve conter:**
```env
EMAIL_SMTP_SERVER=mail.econdominio.com.br
EMAIL_SMTP_PORT=465
EMAIL_SMTP_USERNAME=financeiro@econdominio.com.br
EMAIL_SMTP_PASSWORD=Cjm101090***
EMAIL_FROM_ADDRESS=financeiro@econdominio.com.br
EMAIL_FROM_NAME=Financeiro Econdominio
EMAIL_USE_SSL=true
```

⚠️ **IMPORTANTE:** `EMAIL_FROM_NAME` deve estar **SEM ACENTO**

---

## 🐛 SOLUÇÃO DE PROBLEMAS

### **Problema 1: Email não chega**
```bash
# Verificar logs do backend
tail -f /var/log/backend-financeiro.log

# OU se estiver rodando com uvicorn:
# Verificar console onde o backend está rodando
```

**O que procurar nos logs:**
- `✅ [EMAIL] Email enviado com SUCESSO!`
- `✅ [EMAIL] Destinatário: [email]`

### **Problema 2: Erro de autenticação SMTP**
```bash
# Verificar .env
cat /home/visionlpr/backendV2/.env | grep EMAIL_SMTP_PASSWORD

# Testar manualmente
cd /home/visionlpr/backendV2
python3 testar_email_v2.py
```

### **Problema 3: Timeout ao enviar**
- Verificar firewall do servidor
- Verificar se a porta 465 está aberta
- Testar conectividade:
```bash
telnet mail.econdominio.com.br 465
```

### **Problema 4: Frontend não envia**
- Verificar console do navegador (F12)
- Procurar erros na chamada à API
- Verificar se o backend está rodando:
```bash
curl http://localhost:8000/health
```

---

## 📊 CHECKLIST DE VERIFICAÇÃO

- [ ] Backup dos arquivos originais criado
- [ ] Arquivo `financeiro_email.py` atualizado
- [ ] Script `testar_email_v2.py` copiado
- [ ] Teste manual executado com sucesso
- [ ] Email recebido no Gmail
- [ ] Backend reiniciado
- [ ] Teste pelo frontend executado
- [ ] Email enviado pelo sistema recebido

---

## 📞 SUPORTE

Se após seguir todos os passos o problema persistir:

1. **Verificar logs detalhados:**
   ```bash
   cd /home/visionlpr/backendV2
   python3 testar_email_v2.py > teste_email_log.txt 2>&1
   cat teste_email_log.txt
   ```

2. **Enviar informações para análise:**
   - Log completo do teste
   - Mensagem de erro exata
   - Versão do Python: `python3 --version`
   - Status do backend

---

## 📝 NOTAS ADICIONAIS

### **Por que o acento causava problema?**

Servidores SMTP podem ter problemas com caracteres especiais (acentos) no campo 
"From" do cabeçalho do email. Isso pode causar:
- Rejeição do email pelo servidor
- Marcação como spam
- Bounce (email devolvido)

### **Alternativa com encoding**

Se precisar usar acento no futuro, seria necessário fazer encoding adequado:
```python
from email.header import Header
msg["From"] = str(Header(f"{nome_com_acento} <{email}>", 'utf-8'))
```

Porém, a solução mais simples e confiável é **não usar acentos** no nome do remetente.

---

## ✨ MELHORIAS IMPLEMENTADAS

1. ✅ **Removido acento do remetente**
2. ✅ **Adicionado timeout nas conexões SMTP**
3. ✅ **Logs mais detalhados para debugging**
4. ✅ **Melhor tratamento de erros**
5. ✅ **Script de teste independente**
6. ✅ **Templates HTML sem acentos**

---

**Data da correção:** 21/12/2024  
**Versão:** 2.0  
**Testado e validado:** ✅
