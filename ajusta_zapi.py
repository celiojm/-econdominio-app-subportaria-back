#!/usr/bin/env python3
# ============================================================================
# ARQUIVO: ajusta_zapi.py
# PASTA: ~/ (rodar no servidor BACKEND 10.3.1.6)
# DESCRICAO: Troca o endereco do servidor Z-API (boleto/nota/relatorio) nos
#            backends do eCondominio. NAO toca em nada do Meta/encomendas.
#            - corrige PRIMARY_URL hardcoded em app/routers/gmail_webhook.py
#            - atualiza defaults os.getenv("ZAPI_API_URL", "<antigo>")
#            - opcionalmente atualiza as variaveis ZAPI_* do .env
#            Dry-run por padrao. Backup .bak_<timestamp> de tudo que altera.
# VERSAO: 1.0.0
# data criacao: 2026-09-06   data alteracao: 2026-09-06
# ============================================================================

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from getpass import getpass
from pathlib import Path

URL_NOVA = "http://191.252.221.192:8080"

# enderecos antigos que devem ser substituidos (com porta, sem barra final)
URLS_ANTIGAS = [
    "http://191.252.221.192:8080",
    "http://191.252.221.192:8080",
    "http://191.252.221.192:8080",
    "http://191.252.221.192:8080",
]

# diretorios ignorados na varredura
DIRS_IGNORADOS = {"venv", ".git", "__pycache__", "node_modules", "_arquivo_morto"}

# sufixos de arquivo que sao copia/backup e nao devem ser alterados
def eh_copia(nome: str) -> bool:
    if nome.endswith(".py"):
        base = nome[:-3]
        if re.search(r"\.(bak|old|backup|save)$", base):
            return True
        return False
    return True  # so mexemos em .py


def eh_backup_datado(caminho: Path) -> bool:
    return bool(re.search(r"\.(bak|backup)[_-]?\d{6,}", caminho.name))


TS = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(caminho: Path, aplicar: bool) -> Path:
    destino = caminho.with_name(f"{caminho.name}.bak_{TS}")
    if aplicar:
        shutil.copy2(caminho, destino)
    return destino


def compila(caminho: Path) -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(caminho)],
        capture_output=True, text=True,
    )
    return r.returncode == 0, (r.stderr or "").strip()


def varre_py(raiz: Path):
    for dirpath, dirnames, filenames in os.walk(raiz):
        dirnames[:] = [d for d in dirnames if d not in DIRS_IGNORADOS]
        for nome in filenames:
            if not nome.endswith(".py"):
                continue
            p = Path(dirpath) / nome
            if eh_copia(nome) or eh_backup_datado(p):
                continue
            # ignora copias com sufixo de data tipo arquivo.py-270326
            if re.search(r"\.py-\d{4,}$", nome):
                continue
            yield p


def ajusta_arquivo(caminho: Path, aplicar: bool) -> list[str]:
    """Retorna lista de descricoes das mudancas feitas (ou que seriam feitas)."""
    try:
        texto = caminho.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError) as e:
        return [f"  !! nao foi possivel ler: {e}"]

    original = texto
    mudancas = []

    # 1) caso especial: PRIMARY_URL hardcoded (gmail_webhook.py)
    #    PRIMARY_URL   = 'http://191.252.221.192:8080'
    padrao_primary = re.compile(
        r"""^(?P<ind>\s*)PRIMARY_URL(?P<esp>\s*)=(?P<esp2>\s*)['"](?P<url>https?://[^'"]+)['"]\s*$""",
        re.MULTILINE,
    )

    def troca_primary(m):
        url_atual = m.group("url").rstrip("/")
        if url_atual not in [u.rstrip("/") for u in URLS_ANTIGAS]:
            return m.group(0)
        mudancas.append(
            f"  PRIMARY_URL hardcoded -> os.getenv('ZAPI_API_URL', '{URL_NOVA}')"
        )
        return (
            f"{m.group('ind')}PRIMARY_URL{m.group('esp')}={m.group('esp2')}"
            f"os.getenv('ZAPI_API_URL', '{URL_NOVA}')"
        )

    texto = padrao_primary.sub(troca_primary, texto)

    # 2) defaults dentro de os.getenv(...) e qualquer literal remanescente
    for antiga in URLS_ANTIGAS:
        if antiga in texto:
            n = texto.count(antiga)
            texto = texto.replace(antiga, URL_NOVA)
            mudancas.append(f"  {antiga} -> {URL_NOVA}  ({n}x)")

    if texto == original:
        return []

    # garante import os quando inserimos os.getenv
    if "os.getenv('ZAPI_API_URL'" in texto and not re.search(
        r"^\s*(import os\b|from os import)", texto, re.MULTILINE
    ):
        linhas = texto.split("\n")
        pos = 0
        for i, l in enumerate(linhas[:40]):
            if l.startswith("import ") or l.startswith("from "):
                pos = i
        linhas.insert(pos + 1, "import os")
        texto = "\n".join(linhas)
        mudancas.append("  + import os (adicionado)")

    if aplicar:
        b = backup(caminho, True)
        caminho.write_text(texto, encoding="utf-8")
        ok, err = compila(caminho)
        if ok:
            mudancas.append(f"  backup: {b.name} | py_compile OK")
        else:
            shutil.copy2(b, caminho)
            mudancas.append(f"  !! py_compile FALHOU — REVERTIDO do backup. Erro: {err[:200]}")
    else:
        mudancas.append("  (dry-run — nada gravado)")

    return mudancas


VARS_ENV = ["ZAPI_API_URL", "ZAPI_INSTANCE_ID", "ZAPI_TOKEN", "ZAPI_CLIENT_TOKEN"]


def ajusta_env(raiz: Path, valores: dict, aplicar: bool) -> list[str]:
    env = raiz / ".env"
    if not env.exists():
        return [f"  (sem .env em {raiz})"]

    linhas = env.read_text(encoding="utf-8").split("\n")
    mudou = []
    for i, l in enumerate(linhas):
        for var, novo in valores.items():
            if novo is None:
                continue
            if l.startswith(f"{var}="):
                atual = l.split("=", 1)[1]
                if atual == novo:
                    mudou.append(f"  {var}: ja estava correto")
                else:
                    linhas[i] = f"{var}={novo}"
                    # nunca imprime o valor de token
                    if "TOKEN" in var or "INSTANCE" in var:
                        mudou.append(f"  {var}: atualizado (valor nao exibido)")
                    else:
                        mudou.append(f"  {var}: {atual} -> {novo}")

    if not mudou:
        return ["  (nenhuma variavel ZAPI_* encontrada)"]

    if aplicar:
        shutil.copy2(env, env.with_name(f".env.bak_{TS}"))
        env.write_text("\n".join(linhas), encoding="utf-8")
        mudou.append(f"  backup: .env.bak_{TS}")
    else:
        mudou.append("  (dry-run — nada gravado)")
    return mudou


def main():
    ap = argparse.ArgumentParser(
        description="Troca o endereco do servidor Z-API nos backends do eCondominio."
    )
    ap.add_argument("dirs", nargs="+", help="diretorios dos backends")
    ap.add_argument("--apply", action="store_true",
                    help="aplica de verdade (sem isso, roda em dry-run)")
    ap.add_argument("--env", action="store_true",
                    help="tambem atualiza as variaveis ZAPI_* do .env (pede os valores)")
    ap.add_argument("--permitir-producao", action="store_true",
                    help="necessario para tocar em /home/visionlpr/backend (zona proibida)")
    args = ap.parse_args()

    modo = "APLICANDO" if args.apply else "DRY-RUN (nada sera gravado)"
    print(f"\n=== ajusta_zapi.py — {modo} ===")
    print(f"    novo endereco: {URL_NOVA}\n")

    raizes = []
    for d in args.dirs:
        p = Path(d).expanduser().resolve()
        if not p.is_dir():
            print(f"!! {p} nao existe — pulando")
            continue
        if p == Path("/home/visionlpr/backend") and not args.permitir_producao:
            print(f"!! {p} e producao (zona proibida). Use --permitir-producao se for intencional.\n")
            continue
        raizes.append(p)

    valores = {}
    if args.env:
        print("Valores para o .env (Enter em branco = nao alterar essa variavel):")
        valores["ZAPI_API_URL"] = URL_NOVA
        print(f"  ZAPI_API_URL sera {URL_NOVA}")
        inst = input("  ZAPI_INSTANCE_ID: ").strip()
        valores["ZAPI_INSTANCE_ID"] = inst or None
        tok = getpass("  ZAPI_TOKEN (nao aparece na tela): ").strip()
        valores["ZAPI_TOKEN"] = tok or None
        cli = getpass("  ZAPI_CLIENT_TOKEN (Enter para manter): ").strip()
        valores["ZAPI_CLIENT_TOKEN"] = cli or None
        print()

    total_arquivos = 0
    for raiz in raizes:
        print(f"=========== {raiz}")
        for p in varre_py(raiz):
            res = ajusta_arquivo(p, args.apply)
            if res:
                total_arquivos += 1
                print(f"{p}")
                for l in res:
                    print(l)
        if args.env:
            print("--- .env")
            for l in ajusta_env(raiz, valores, args.apply):
                print(l)
        print()

    print(f"=== {total_arquivos} arquivo(s) .py com alteracao ===")
    if not args.apply:
        print("Nada foi gravado. Repita com --apply para aplicar.\n")
    else:
        print("Aplicado. Reinicie os servicos afetados e confira os logs.\n")


if __name__ == "__main__":
    main()
