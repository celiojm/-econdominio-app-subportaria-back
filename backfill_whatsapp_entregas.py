# ============================================================================
# ARQUIVO: backfill_whatsapp_entregas.py
# PASTA: /home/visionlpr/app_subportaria_back/
# DESCRIÇÃO: Preenche whatsapp_entregas (e o resumo whatsapp_* das encomendas) com o passado,
#            a partir dos logs: envios em send_grouped_whatsapp.log (desde 26/09/2026) e retornos
#            da Meta ("Statuses recebidos") em logs/app_subportaria_back-error.log.
#            SÓ LÊ logs e banco; gera ~/backfill_whatsapp_entregas.sql para rodar no AdmGeral.
#            Repetível: INSERT IGNORE + UPDATE que só preenche/avança.
# USO: venv/bin/python backfill_whatsapp_entregas.py
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-01 data alteração: 2026-10-01
# ============================================================================
import ast, os, re, sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from sqlalchemy import text
from app.database import SessionLocal

WORKER_LOG = os.path.join(BASE, "send_grouped_whatsapp.log")
STATUS_LOG = os.path.join(BASE, "logs", "app_subportaria_back-error.log")
SAIDA = os.path.expanduser("~/backfill_whatsapp_entregas.sql")

re_env = re.compile(r"^(\S+ \S+),\d+ \| INFO \| Enviando \| tel=(\d+) \| tipo=(\w+) \| msgs=(\d+) \| enc_id=(\w+)")
re_ok = re.compile(r"^(\S+ \S+),\d+ \| INFO \| Enviado \| tel=(\d+) \| provider=(\w+) \| id=(\S+)")

# 1) envios: wamid -> (data, telefone, tipo, encomenda_id, provider)
envios, atual = {}, {}
with open(WORKER_LOG, encoding="utf-8", errors="replace") as f:
    for linha in f:
        m = re_env.match(linha)
        if m:
            atual[m.group(2)] = (m.group(3), None if m.group(5) == "None" else int(m.group(5)), int(m.group(4)))
            continue
        m = re_ok.match(linha)
        if m and m.group(4) not in ("None", "") and m.group(2) in atual:
            tipo, enc, n = atual.pop(m.group(2))
            envios[m.group(4)] = (m.group(1), m.group(2), tipo, enc, m.group(3), n)
print("envios no log do worker:", len(envios))

# 2) retornos da Meta para esses wamids
ret = {}
with open(STATUS_LOG, encoding="utf-8", errors="replace") as f:
    for linha in f:
        i = linha.find("Statuses recebidos: ")
        if i < 0:
            continue
        try:
            lista = ast.literal_eval(linha[i + 20:].strip())
        except Exception:
            continue
        for st in lista:
            mid = st.get("id")
            if mid not in envios:
                continue
            r = ret.setdefault(mid, {})
            ts = int(st.get("timestamp") or 0)
            s = st.get("status")
            if ts and (s not in r or ts < r[s]):
                r[s] = ts
            if s == "failed":
                e = (st.get("errors") or [{}])[0]
                r["erro"] = (e.get("code"), (e.get("title") or e.get("message") or "")[:200])
print("envios com retorno da Meta:", len(ret))

# 3) fila: (encomenda_id, tipo) -> queue_id, morador, condomínio
db = SessionLocal()
fila = {}
for row in db.execute(text("""SELECT id, encomenda_id, tipo_evento, morador_id, condominio_id FROM whatsapp_message_queue
                               WHERE criado_em >= '2026-09-20' AND encomenda_id IS NOT NULL""")).fetchall():
    fila[(row.encomenda_id, row.tipo_evento)] = row


def q(v):
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


def dt(ts):
    return q(datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")) if ts else "NULL"


linhas = ["-- gerado por backfill_whatsapp_entregas.py em " + datetime.now().strftime("%d/%m/%Y %H:%M"),
          "SELECT DATABASE() AS banco_atual;", "START TRANSACTION;"]
cont = {"enviado": 0, "entregue": 0, "lido": 0, "falhou": 0}
for mid, (quando, tel, tipo, enc, prov, n) in envios.items():
    fq = fila.get((enc, tipo)) if enc else None
    r = ret.get(mid, {})
    status = ("lido" if "read" in r else "entregue" if "delivered" in r
              else "falhou" if "failed" in r else "enviado")
    cont[status] += 1
    entregue = r.get("delivered") or r.get("read")
    cod, tit = r.get("erro", (None, None))
    linhas.append(
        "INSERT IGNORE INTO whatsapp_entregas (message_id, queue_id, encomenda_id, morador_id, condominio_id, telefone, "
        "tipo_evento, provider, status, enviado_em) VALUES ("
        f"{q(mid)}, {q(fq.id if fq else None)}, {q(enc)}, {q(fq.morador_id if fq else None)}, "
        f"{q(fq.condominio_id if fq else None)}, {q(tel)}, {q(tipo)}, {q(prov)}, 'enviado', {q(quando)});")
    linhas.append(
        f"UPDATE whatsapp_entregas SET entregue_em=COALESCE(entregue_em,{dt(entregue)}), lido_em=COALESCE(lido_em,{dt(r.get('read'))}), "
        f"falhou_em=COALESCE(falhou_em,{dt(r.get('failed'))}), erro_codigo=COALESCE(erro_codigo,{q(cod)}), "
        f"erro_titulo=COALESCE(erro_titulo,{q(tit)}), status=CASE "
        f"WHEN {q(status)}='lido' OR status='lido' THEN 'lido' "
        f"WHEN {q(status)}='entregue' OR status='entregue' THEN 'entregue' "
        f"WHEN {q(status)}='falhou' THEN 'falhou' ELSE status END WHERE message_id={q(mid)};")
linhas += [
    "-- resumo do aviso de chegada: última mensagem ENCOMENDA_RECEBIDA de cada encomenda",
    "UPDATE encomendas e JOIN (SELECT w.encomenda_id, w.status, w.enviado_em, w.entregue_em, w.lido_em,"
    " CASE WHEN w.erro_codigo IS NULL THEN NULL ELSE LEFT(CONCAT(w.erro_codigo,' ',COALESCE(w.erro_titulo,'')),255) END AS erro"
    " FROM whatsapp_entregas w JOIN (SELECT encomenda_id, MAX(id) mx FROM whatsapp_entregas"
    " WHERE tipo_evento='ENCOMENDA_RECEBIDA' AND encomenda_id IS NOT NULL GROUP BY encomenda_id) u ON u.mx=w.id) x"
    " ON x.encomenda_id=e.id SET e.whatsapp_status=x.status, e.whatsapp_enviado_em=x.enviado_em,"
    " e.whatsapp_entregue_em=x.entregue_em, e.whatsapp_lido_em=x.lido_em, e.whatsapp_erro=x.erro;",
    "COMMIT;",
    "SELECT status, COUNT(*) FROM whatsapp_entregas GROUP BY status;",
    "SELECT whatsapp_status, COUNT(*) FROM encomendas WHERE whatsapp_status IS NOT NULL GROUP BY whatsapp_status;",
]
open(SAIDA, "w", encoding="utf-8").write("\n".join(linhas) + "\n")
print("status calculado:", cont)
print("arquivo:", SAIDA, "| linhas:", len(linhas))
