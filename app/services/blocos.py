# ============================================================================
# ARQUIVO: blocos.py
# PASTA: app/services/
# DESCRIÇÃO: Padronização do bloco na gravação de morador/encomenda. Grafias equivalentes
#            ("3", "03", "BL3", "BL 3", "Bloco 03", "b3") viram a grafia MAIS USADA no cadastro de
#            moradores ativos do mesmo condomínio. Bloco sem equivalente é mantido (só espaços
#            normalizados) — nunca recusa. Letras/torres/quadras só juntam variação de
#            maiúscula/minúscula e espaço.
#            dados_do_morador: apto/bloco do cadastro quando a encomenda tem morador escolhido.
# VERSÃO: 1.1.0 - caractere invisível (ex. U+3164, hífen suave) ou só pontuação = bloco vazio (2026-09-30)
#         1.0.0 - criação (2026-09-30)
# data criação: 2026-09-30 data alteração: 2026-09-30
# ============================================================================
import re
from typing import Optional

from sqlalchemy import text


# caracteres invisíveis comuns em teclado de celular: hífen suave, preenchimentos hangul (ex. 0x3164),
# espaços de largura zero, marcas de direção, BOM
_INVISIVEIS = re.compile("[%s]" % "".join(
    [chr(c) for c in (0x00AD, 0x115F, 0x1160, 0x180E, 0x3164, 0xFEFF, 0xFFA0)]
    + [chr(c) for c in range(0x200B, 0x2010)] + [chr(c) for c in range(0x2028, 0x2030)]
    + [chr(c) for c in range(0x205F, 0x2070)]))


def limpar_texto(v) -> str:
    """Tira caracteres invisíveis, junta espaços; só pontuação vira vazio."""
    s = " ".join(_INVISIVEIS.sub("", str(v or "")).split())
    return s if re.search(r"\w", s) else ""


def chave_bloco(bloco) -> str:
    """Forma de comparação: sem espaço, maiúscula, sem prefixo BLOCO/BL/B antes de número, sem zero à esquerda."""
    s = re.sub(r"\s+", "", limpar_texto(bloco)).upper()
    s = re.sub(r"^(BLOCO|BLOC|BL|B)(?=\d)", "", s)
    s = re.sub(r"^BLOCO(?=[A-Z])", "", s)
    s = re.sub(r"^0+(?=\d)", "", s)
    return s


def normalizar_bloco(db, condominio_id, bloco) -> Optional[str]:
    if bloco is None:
        return None
    b = limpar_texto(bloco)
    if not b or not condominio_id:
        return b or None
    k = chave_bloco(b)
    if not k:
        return b
    rows = db.execute(text("""
        SELECT TRIM(bloco) AS b, COUNT(*) AS n FROM moradores
        WHERE condominio_id = :c AND (ativo = 1 OR ativo IS NULL)
          AND bloco IS NOT NULL AND TRIM(bloco) <> ''
        GROUP BY TRIM(bloco)
    """), {"c": condominio_id}).fetchall()
    candidatos = [(r.n, limpar_texto(r.b)) for r in rows if chave_bloco(r.b) == k and limpar_texto(r.b)]
    return max(candidatos)[1] if candidatos else b


def dados_do_morador(db, morador_id, condominio_id) -> Optional[dict]:
    """Apartamento e bloco do cadastro do morador (mesmo condomínio), ou None."""
    if not morador_id or not condominio_id:
        return None
    r = db.execute(text("SELECT apartamento, bloco FROM moradores WHERE id = :i AND condominio_id = :c"),
                   {"i": morador_id, "c": condominio_id}).fetchone()
    return {"apartamento": r.apartamento, "bloco": r.bloco} if r else None


def normalizar_tmp_importacao(condominio_id) -> int:
    """Padroniza o bloco das linhas pendentes de moradores_tmp antes da cópia para moradores."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        alterados = 0
        for (bloco,) in db.execute(text("""
            SELECT DISTINCT bloco FROM moradores_tmp
            WHERE condominio_id = :c AND status_revisao = 'pendente' AND bloco IS NOT NULL
        """), {"c": condominio_id}).fetchall():
            novo = normalizar_bloco(db, condominio_id, bloco)
            if novo != bloco:
                alterados += db.execute(text("""
                    UPDATE moradores_tmp SET bloco = :novo
                    WHERE condominio_id = :c AND status_revisao = 'pendente' AND bloco = :antigo
                """), {"novo": novo, "c": condominio_id, "antigo": bloco}).rowcount
        db.commit()
        return alterados
    finally:
        db.close()
