"""
SCRIPT: Correção do campo `apartamento` - moradores condominio_id = 11
BANCO:  AdmGeral (MySQL)
REGRAS:
  - Só atualiza apartamento
  - Só para condominio_id = 11
  - Não insere, não apaga, não altera outros campos
  - Matching por nome (normalizado) + bloco como desempate
  - Casos ambíguos → revisão manual
  - Gera backup antes de qualquer UPDATE
  - Gera preview antes de executar
"""

import pandas as pd
import unicodedata
import re
import mysql.connector
from datetime import datetime
import os
from pathlib import Path
from dotenv import load_dotenv

# ─────────────────────────────────────────────
# CONFIGURAÇÃO — lida do .env automaticamente
# ─────────────────────────────────────────────

# Procura o .env no mesmo diretório do script ou no diretório atual
_env_path = Path(__file__).parent / ".env"
if not _env_path.exists():
    _env_path = Path(".env")
load_dotenv(dotenv_path=_env_path, override=False)

DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     os.getenv("DATABASE_HOST",     "localhost")),
    "port":     int(os.getenv("DB_PORT", os.getenv("DATABASE_PORT",     3306))),
    "user":     os.getenv("DB_USER",     os.getenv("DATABASE_USER",     "")),
    "password": os.getenv("DB_PASSWORD", os.getenv("DATABASE_PASSWORD", "")),
    "database": os.getenv("DB_NAME",     "AdmGeral"),
    "charset":  "utf8mb4",
}

# Validação mínima para falhar cedo com mensagem clara
_missing = [k for k in ("host", "user", "password", "database") if not DB_CONFIG.get(k)]
if _missing:
    raise EnvironmentError(
        f"Variáveis de ambiente ausentes no .env: {_missing}\n"
        f"Arquivo .env procurado em: {_env_path.resolve()}"
    )

print(f"Conectando em {DB_CONFIG['user']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")

XLSX_PATH    = "moradores.xlsx"
CONDOMINIO_ID = 11

# Mapeamento: prefixo do APTO (planilha) → TORRE
PREFIX_TO_TORRE = {
    "CG": "A", "KB": "A", "OD": "A", "PB": "A",
    "BR": "B", "BS": "B", "FM": "B", "KW": "B",
    "VG": "VG",
}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def normalizar_nome(nome: str) -> str:
    """Remove acentos, espaços extras, caixa alta."""
    if not isinstance(nome, str):
        return ""
    nome = nome.strip().upper()
    nome = unicodedata.normalize("NFD", nome)
    nome = "".join(c for c in nome if unicodedata.category(c) != "Mn")
    nome = re.sub(r"\s+", " ", nome)
    return nome

def apto_ja_correto(apto_banco: str) -> bool:
    """Retorna True se o apartamento já está no formato esperado (ex: CG0021)."""
    return bool(re.match(r'^[A-Z]{2}\d{4}$', str(apto_banco).strip()))

def extrair_prefix_banco(bloco_banco: str) -> str | None:
    """Tenta extrair o prefixo de 2 letras a partir do bloco salvo no banco.
    Aceita: 'CG', 'A', 'B' etc.
    """
    if not bloco_banco:
        return None
    b = str(bloco_banco).strip().upper()
    if len(b) == 2 and b.isalpha():          # ex: 'CG', 'KB'
        return b if b in PREFIX_TO_TORRE else None
    if b in ("A", "B", "VG"):                # ex: torre 'A' ou 'B'
        # inverter mapa: torre → lista de prefixos
        inv = {}
        for p, t in PREFIX_TO_TORRE.items():
            inv.setdefault(t, []).append(p)
        prefixos = inv.get(b)
        return prefixos  # pode ser lista → ambíguo se >1
    return None

# ─────────────────────────────────────────────
# 1. LER PLANILHA
# ─────────────────────────────────────────────

df_xl = pd.read_excel(XLSX_PATH)
df_xl["NOME_NORM"] = df_xl["NOME"].apply(normalizar_nome)
df_xl["PREFIX"]    = df_xl["APTO"].str[:2]
df_xl["TORRE"]     = df_xl["TORRE"].str.strip().str.upper()

# Nomes duplicados na planilha → revisão automática
nomes_dup_planilha = set(
    df_xl[df_xl.duplicated("NOME_NORM", keep=False)]["NOME_NORM"].unique()
)

# Index planilha: nome_norm → lista de rows
from collections import defaultdict
idx_planilha = defaultdict(list)
for _, row in df_xl.iterrows():
    idx_planilha[row["NOME_NORM"]].append(row)

# ─────────────────────────────────────────────
# 2. LER BANCO
# ─────────────────────────────────────────────

conn = mysql.connector.connect(**DB_CONFIG)
cur  = conn.cursor(dictionary=True)

cur.execute("""
    SELECT id, nome, apartamento, bloco
    FROM   moradores
    WHERE  condominio_id = %s
    ORDER  BY nome
""", (CONDOMINIO_ID,))

moradores_banco = cur.fetchall()
print(f"Moradores no banco (condominio_id={CONDOMINIO_ID}): {len(moradores_banco)}")

# ─────────────────────────────────────────────
# 3. MATCHING E CLASSIFICAÇÃO
# ─────────────────────────────────────────────

updates_ok        = []   # (id, nome, apto_atual, apto_novo, motivo)
ja_corretos       = []   # já no formato correto
sem_match         = []   # nome não encontrado na planilha
revisao_manual    = []   # ambíguos

for m in moradores_banco:
    id_banco   = m["id"]
    nome_banco = m["nome"]
    apto_banco = m["apartamento"]
    bloco_banco= m["bloco"] or ""
    nome_norm  = normalizar_nome(nome_banco)

    # ── A) Apartamento já correto? ──────────────────────────────────
    if apto_ja_correto(apto_banco):
        ja_corretos.append({
            "id": id_banco, "nome": nome_banco,
            "apartamento_atual": apto_banco, "motivo": "formato já correto"
        })
        continue

    # ── B) Nome duplicado na planilha? ──────────────────────────────
    if nome_norm in nomes_dup_planilha:
        revisao_manual.append({
            "id": id_banco, "nome": nome_banco,
            "apartamento_atual": apto_banco, "bloco_banco": bloco_banco,
            "motivo": "nome duplicado na planilha — múltiplos APTOs possíveis"
        })
        continue

    # ── C) Não encontrado na planilha ───────────────────────────────
    if nome_norm not in idx_planilha:
        sem_match.append({
            "id": id_banco, "nome": nome_banco,
            "apartamento_atual": apto_banco, "bloco_banco": bloco_banco,
            "motivo": "nome não encontrado na planilha"
        })
        continue

    candidatos = idx_planilha[nome_norm]

    # ── D) Match único sem ambiguidade ──────────────────────────────
    if len(candidatos) == 1:
        row_xl   = candidatos[0]
        apto_novo = row_xl["APTO"]
        updates_ok.append({
            "id": id_banco, "nome": nome_banco,
            "apartamento_atual": apto_banco,
            "apartamento_novo": apto_novo,
            "motivo": "match único por nome"
        })
        continue

    # ── E) Múltiplos candidatos → tentar desempatar por bloco ───────
    prefix_banco = extrair_prefix_banco(bloco_banco)

    if isinstance(prefix_banco, str):
        # bloco salvo como prefixo de 2 letras (ex: 'CG')
        filtrados = [r for r in candidatos if r["PREFIX"] == prefix_banco]
    elif isinstance(prefix_banco, list):
        # bloco salvo como torre (ex: 'A') → filtra pelos prefixos dessa torre
        filtrados = [r for r in candidatos if r["PREFIX"] in prefix_banco]
    else:
        filtrados = []

    if len(filtrados) == 1:
        row_xl    = filtrados[0]
        apto_novo  = row_xl["APTO"]
        updates_ok.append({
            "id": id_banco, "nome": nome_banco,
            "apartamento_atual": apto_banco,
            "apartamento_novo": apto_novo,
            "motivo": f"match por nome + bloco ({bloco_banco})"
        })
    else:
        # Ainda ambíguo → revisão manual
        revisao_manual.append({
            "id": id_banco, "nome": nome_banco,
            "apartamento_atual": apto_banco, "bloco_banco": bloco_banco,
            "candidatos_planilha": [r["APTO"] for r in candidatos],
            "motivo": f"ambíguo após filtro de bloco ({len(filtrados)} candidatos)"
        })

# ─────────────────────────────────────────────
# 4. PREVIEW
# ─────────────────────────────────────────────

print("\n" + "="*70)
print("PREVIEW — ATUALIZAÇÕES PROPOSTAS")
print("="*70)
print(f"  Já corretos (não serão tocados):    {len(ja_corretos)}")
print(f"  Sem match na planilha:               {len(sem_match)}")
print(f"  Revisão manual necessária:           {len(revisao_manual)}")
print(f"  UPDATES seguros (prontos p/ aplicar): {len(updates_ok)}")
print()

if updates_ok:
    print("── UPDATES QUE SERÃO APLICADOS ──────────────────────────────────")
    print(f"  {'ID':>6}  {'NOME':<45}  {'DE':<10}  {'PARA':<10}  MOTIVO")
    print(f"  {'-'*6}  {'-'*45}  {'-'*10}  {'-'*10}  {'-'*30}")
    for u in updates_ok:
        print(f"  {u['id']:>6}  {u['nome']:<45}  {u['apartamento_atual']:<10}  {u['apartamento_novo']:<10}  {u['motivo']}")

if revisao_manual:
    print()
    print("── REVISÃO MANUAL NECESSÁRIA ────────────────────────────────────")
    print(f"  {'ID':>6}  {'NOME':<45}  {'APTO ATUAL':<10}  MOTIVO")
    for r in revisao_manual:
        cands = r.get("candidatos_planilha", [])
        cands_str = ", ".join(cands) if cands else ""
        print(f"  {r['id']:>6}  {r['nome']:<45}  {r['apartamento_atual']:<10}  {r['motivo']}")
        if cands_str:
            print(f"  {'':>6}  {'':>45}  Candidatos: {cands_str}")

if sem_match:
    print()
    print("── SEM MATCH NA PLANILHA ────────────────────────────────────────")
    for s in sem_match:
        print(f"  ID={s['id']:>6}  {s['nome']}")

# ─────────────────────────────────────────────
# 5. CONFIRMAÇÃO DO USUÁRIO
# ─────────────────────────────────────────────

print()
resp = input(f"Confirma aplicar {len(updates_ok)} UPDATE(s)? (sim/nao): ").strip().lower()
if resp != "sim":
    print("Abortado. Nenhuma alteração foi feita.")
    cur.close()
    conn.close()
    exit(0)

# ─────────────────────────────────────────────
# 6. BACKUP
# ─────────────────────────────────────────────

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_table = f"moradores_backup_cond11_{ts}"

print(f"\nCriando backup: {backup_table} ...", end=" ")
cur.execute(f"""
    CREATE TABLE `{backup_table}` AS
    SELECT * FROM moradores WHERE condominio_id = %s
""", (CONDOMINIO_ID,))
conn.commit()
print("OK")

# ─────────────────────────────────────────────
# 7. EXECUTAR UPDATES
# ─────────────────────────────────────────────

print(f"\nAplicando {len(updates_ok)} update(s)...")
erros = []

for u in updates_ok:
    try:
        cur.execute("""
            UPDATE moradores
               SET apartamento = %s
             WHERE id = %s
               AND condominio_id = %s
        """, (u["apartamento_novo"], u["id"], CONDOMINIO_ID))
    except Exception as e:
        erros.append({"id": u["id"], "nome": u["nome"], "erro": str(e)})

conn.commit()

print(f"\n{'='*70}")
print("RESULTADO FINAL")
print(f"{'='*70}")
print(f"  Updates aplicados com sucesso: {len(updates_ok) - len(erros)}")
print(f"  Erros durante execução:        {len(erros)}")
print(f"  Backup salvo em tabela:        {backup_table}")
print(f"  Revisão manual pendente:       {len(revisao_manual)} registro(s)")

if erros:
    print("\nERROS:")
    for e in erros:
        print(f"  ID={e['id']} {e['nome']}: {e['erro']}")

# ─────────────────────────────────────────────
# 8. SALVAR RELATÓRIO DE REVISÃO MANUAL
# ─────────────────────────────────────────────

if revisao_manual:
    df_rev = pd.DataFrame(revisao_manual)
    rev_file = f"revisao_manual_cond11_{ts}.xlsx"
    df_rev.to_excel(rev_file, index=False)
    print(f"\nArquivo de revisão manual salvo: {rev_file}")

cur.close()
conn.close()
print("\nConexão encerrada. Script finalizado.")
