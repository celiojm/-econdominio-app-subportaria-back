# ============================================================================
# ARQUIVO: unidades.py
# PASTA: app/services/
# DESCRIÇÃO: Cadastro de unidades (bloco + apartamento) — carga inicial a partir dos
#            moradores ativos. Junta grafias equivalentes (blocos: app/services/blocos.py;
#            apartamento: sem espaço, sem AP/APTO/APT, sem zero à esquerda) e usa a grafia mais
#            usada. Marca revisar=1 no que parecer erro. Liga moradores.unidade_id.
#            Uso: python -m app.services.unidades --condominio 51            (prévia, não grava)
#                 python -m app.services.unidades --condominio 51 --aplicar  (grava)
#                 python -m app.services.unidades --todos [--aplicar]
# VERSÃO: 1.1.0 - limpa caracteres invisíveis de bloco/apto (2026-09-30)
#         1.0.0 - criação (2026-09-30)
# data criação: 2026-09-30 data alteração: 2026-09-30
# ============================================================================
import argparse
import collections
import re

from sqlalchemy import text

from app.services.blocos import chave_bloco, limpar_texto


def chave_apto(apto) -> str:
    s = re.sub(r"\s+", "", str(apto or "")).upper()
    s = re.sub(r"^(APTO|APT|AP)\.?(?=[0-9A-Z])", "", s)
    s = re.sub(r"^0+(?=\d)", "", s)
    return s


def _mais_usada(contador: collections.Counter) -> str:
    return max(contador.items(), key=lambda kv: (kv[1], kv[0]))[0]


def montar_unidades(db, condominio_id: int) -> dict:
    """Calcula (sem gravar) as unidades de um condomínio a partir dos moradores ativos."""
    moradores = db.execute(text("""
        SELECT id, TRIM(IFNULL(bloco,'')) AS bloco, TRIM(IFNULL(apartamento,'')) AS apto
        FROM moradores WHERE condominio_id = :c AND (ativo = 1 OR ativo IS NULL)
    """), {"c": condominio_id}).fetchall()

    grafia_bloco = collections.defaultdict(collections.Counter)
    moradores = [type("M", (), {"id": m.id, "bloco": limpar_texto(m.bloco), "apto": limpar_texto(m.apto)}) for m in moradores]
    for m in moradores:
        grafia_bloco[chave_bloco(m.bloco)][m.bloco] += 1
    bloco_de = {k: _mais_usada(c) for k, c in grafia_bloco.items()}

    unidades = collections.defaultdict(lambda: {"grafias_apto": collections.Counter(), "moradores": []})
    for m in moradores:
        kb = chave_bloco(m.bloco)
        u = unidades[(kb, chave_apto(m.apto))]
        u["grafias_apto"][m.apto] += 1
        u["moradores"].append(m.id)

    # blocos com poucas unidades num condomínio que tem blocos "grandes" = suspeitos
    unid_por_bloco = collections.Counter(kb for kb, _ in unidades)
    grandes = [n for n in unid_por_bloco.values() if n >= 5]
    # blocos equivalentes com/sem prefixo (ex.: "TORRE A" e "A"): o MENOR vai para revisão
    sem_pref = lambda k: re.sub(r"^(TORRE|BLOCO|BLOC|BL|QUADRA|QD|Q)(?=[0-9A-Z])", "", k)
    por_base = collections.defaultdict(list)
    for k in unid_por_bloco:
        if k:
            por_base[sem_pref(k)].append(k)
    irmaos = {}
    for ks in por_base.values():
        if len(ks) > 1:
            maior = max(ks, key=lambda k: unid_por_bloco[k])
            for k in ks:
                if k != maior:
                    irmaos[k] = maior
    lista = []
    for (kb, ka), u in unidades.items():
        motivos = []
        if not ka:
            motivos.append("apartamento vazio")
        elif not re.search(r"\d", ka):
            motivos.append("apartamento sem número")
        if len(ka) > 8:
            motivos.append("apartamento muito longo")
        if kb and len(grandes) >= 2 and unid_por_bloco[kb] <= 2:
            motivos.append(f"bloco com só {unid_por_bloco[kb]} unidade(s)")
        irmao = irmaos.get(kb)   # outro bloco equivalente sem/com TORRE/BLOCO/QUADRA, maior que este
        if irmao:
            motivos.append(f"talvez seja o mesmo que o bloco {bloco_de[irmao]!r}")
        lista.append({"bloco": bloco_de[kb], "apartamento": _mais_usada(u["grafias_apto"]),
                      "moradores": u["moradores"], "revisar": bool(motivos),
                      "motivo": "; ".join(motivos) or None,
                      "grafias": sorted({f"{b}/{a}" for b in grafia_bloco[kb] for a in u["grafias_apto"]})})
    lista.sort(key=lambda x: (x["bloco"], x["apartamento"]))
    return {"condominio_id": condominio_id, "moradores": len(moradores), "unidades": lista,
            "blocos": sorted(unid_por_bloco.items(), key=lambda kv: -kv[1])}


def aplicar_carga(db, condominio_id: int) -> dict:
    """Grava as unidades calculadas (sem duplicar as já existentes) e liga os moradores."""
    r = montar_unidades(db, condominio_id)
    criadas = ligados = 0
    for u in r["unidades"]:
        ex = db.execute(text("""
            SELECT id FROM unidades WHERE condominio_id = :c AND bloco = :b AND apartamento = :a
        """), {"c": condominio_id, "b": u["bloco"], "a": u["apartamento"]}).scalar()
        if not ex:
            ex = db.execute(text("""
                INSERT INTO unidades (condominio_id, bloco, apartamento, origem, revisar, motivo_revisar)
                VALUES (:c, :b, :a, 'carga_inicial', :r, :m)
            """), {"c": condominio_id, "b": u["bloco"], "a": u["apartamento"],
                   "r": 1 if u["revisar"] else 0, "m": u["motivo"]}).lastrowid
            criadas += 1
        if u["moradores"]:
            ligados += db.execute(text(
                f"UPDATE moradores SET unidade_id = :u WHERE id IN ({','.join(str(int(i)) for i in u['moradores'])})"
            ), {"u": ex}).rowcount
    db.commit()
    return {"condominio_id": condominio_id, "unidades_criadas": criadas, "moradores_ligados": ligados,
            "revisar": sum(1 for u in r["unidades"] if u["revisar"])}


if __name__ == "__main__":
    from app.database import SessionLocal
    p = argparse.ArgumentParser()
    p.add_argument("--condominio", type=int)
    p.add_argument("--todos", action="store_true")
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--detalhe", action="store_true")
    a = p.parse_args()
    db = SessionLocal()
    print("banco:", db.execute(text("SELECT DATABASE()")).scalar(), "| modo:", "APLICAR" if a.aplicar else "prévia")
    ids = [a.condominio] if a.condominio else [r[0] for r in db.execute(text(
        "SELECT id FROM condominios WHERE ativo = 1 ORDER BY id")).fetchall()] if a.todos else []
    tot = collections.Counter()
    for cid in ids:
        if a.aplicar:
            res = aplicar_carga(db, cid); print(res); tot.update({k: v for k, v in res.items() if k != "condominio_id"})
            continue
        r = montar_unidades(db, cid)
        nome = db.execute(text("SELECT nome FROM condominios WHERE id=:c"), {"c": cid}).scalar()
        rev = [u for u in r["unidades"] if u["revisar"]]
        print(f"#{cid} {nome[:40]}: {r['moradores']} moradores → {len(r['unidades'])} unidades, {len(rev)} p/ revisar | blocos: "
              + ", ".join(f"{b or '(único)'}={n}" for b, n in r["blocos"][:12]) + (" …" if len(r["blocos"]) > 12 else ""))
        tot.update({"moradores": r["moradores"], "unidades": len(r["unidades"]), "revisar": len(rev)})
        if a.detalhe:
            for u in rev[:30]:
                print(f"    revisar: bloco {u['bloco']!r} apto {u['apartamento']!r} — {u['motivo']} (grafias {u['grafias'][:4]})")
    print("TOTAL:", dict(tot))
