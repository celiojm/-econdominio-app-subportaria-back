# ============================================================================
# ARQUIVO: validators.py
# PASTA: /home/visionlpr/app_subportaria_back/leads/
# DESCRIÇÃO: Validação e normalização de campos do endpoint público de leads
#            (nunca confiar no navegador — allowlist por campo, rejeita o que
#            não casar). Também o rate-limiter em memória por IP.
# VERSÃO: 1.0.0
# data criação: 2026-09-15   data alteração: 2026-09-15
# ============================================================================

import re
import time
import unicodedata
from collections import defaultdict, deque
from threading import Lock
from typing import Optional

# ─── Normalização de texto ─────────────────────────────────────────────────

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏  ]")
_PERIGOSO_RE = re.compile(r"<|>|\{\{|\$\{|javascript:", re.IGNORECASE)


def limpar_texto(valor: str) -> str:
    """NFC + remove caracteres de controle/zero-width. Não faz validação de conteúdo."""
    if not isinstance(valor, str):
        raise ValueError("valor deve ser string")
    v = unicodedata.normalize("NFC", valor)
    v = _CONTROL_CHARS_RE.sub("", v)
    return v.strip()


def contem_padrao_perigoso(valor: str) -> bool:
    return bool(_PERIGOSO_RE.search(valor))


# ─── Allowlist por campo ────────────────────────────────────────────────────

_NOME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ' .\-]{2,80}$")
_UTM_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
_FBCLID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,255}$")
_PAGINA_RE = re.compile(r"^/[A-Za-z0-9_.\-/?=&%]{0,199}$")

ORIGENS_FORMULARIO_VALIDAS = ("formulario", "rodape")
ORIGEM_WHATSAPP = "whatsapp"


def validar_nome(valor: Optional[str]) -> Optional[str]:
    if valor is None:
        return None
    v = limpar_texto(valor)
    if not v:
        return None
    if not _NOME_RE.match(v):
        raise ValueError("nome inválido")
    return v


def validar_utm(valor: Optional[str], campo: str) -> Optional[str]:
    if valor is None or valor == "":
        return None
    v = limpar_texto(valor)
    if not _UTM_RE.match(v):
        raise ValueError(f"{campo} inválido")
    return v


def validar_fbclid(valor: Optional[str]) -> Optional[str]:
    if valor is None or valor == "":
        return None
    v = limpar_texto(valor)
    if not _FBCLID_RE.match(v):
        raise ValueError("fbclid inválido")
    return v


def validar_pagina(valor: Optional[str]) -> Optional[str]:
    if valor is None or valor == "":
        return None
    v = limpar_texto(valor)
    if not _PAGINA_RE.match(v):
        raise ValueError("pagina inválida")
    return v


def validar_mensagem(valor: Optional[str]) -> Optional[str]:
    if valor is None:
        return None
    v = limpar_texto(valor)[:500]
    if not v:
        return None
    if contem_padrao_perigoso(v):
        raise ValueError("mensagem contém padrão não permitido")
    return v


def validar_origem_formulario(valor: str) -> str:
    v = limpar_texto(valor or "")
    if v not in ORIGENS_FORMULARIO_VALIDAS:
        raise ValueError("origem inválida")
    return v


# ─── WhatsApp — normalização canônica (55 + DDD + 9 + 8 dígitos = 13) ──────

_WHATSAPP_CANONICO_RE = re.compile(r"^55\d{2}9\d{8}$")


def normalizar_whatsapp_br(raw: str) -> Optional[str]:
    """
    Regra combinada: celular sempre 13 dígitos (55+DDD+9+8). Se vier com 12
    dígitos (55+DDD+8, sem o 9) e o primeiro dígito do número for 6-9, insere
    o 9. Aceita também entrada sem o "55" (10 ou 11 dígitos), aplicando a
    mesma lógica antes de prefixar o código do país. Retorna None se não der
    pra normalizar num celular BR válido.
    """
    if not isinstance(raw, str):
        return None
    digitos = "".join(c for c in raw if c.isdigit())

    if digitos.startswith("55"):
        resto = digitos[2:]
    else:
        resto = digitos

    if len(resto) == 11:
        pass  # DDD + 9 + 8 já completo
    elif len(resto) == 10:
        if resto[2] in "6789":
            resto = resto[:2] + "9" + resto[2:]
        else:
            return None  # 10 dígitos sem cara de celular (fixo) — não é WhatsApp válido
    else:
        return None

    canonico = "55" + resto
    if not _WHATSAPP_CANONICO_RE.match(canonico):
        return None
    return canonico


# ─── CNPJ — dígitos verificadores ───────────────────────────────────────────

def _cnpj_digito_verificador(cnpj_parcial: str, pesos: list) -> int:
    soma = sum(int(d) * p for d, p in zip(cnpj_parcial, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def validar_cnpj(raw: Optional[str]) -> Optional[str]:
    if raw is None or raw == "":
        return None
    digitos = "".join(c for c in raw if c.isdigit())
    if len(digitos) != 14:
        raise ValueError("cnpj deve ter 14 dígitos")
    if digitos == digitos[0] * 14:
        raise ValueError("cnpj inválido (sequência repetida)")

    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    dv1 = _cnpj_digito_verificador(digitos[:12], pesos1)
    dv2 = _cnpj_digito_verificador(digitos[:12] + str(dv1), pesos2)
    if digitos[-2:] != f"{dv1}{dv2}":
        raise ValueError("cnpj inválido (dígito verificador)")
    return digitos


# ─── Rate limit em memória (processo único, sem Redis) ──────────────────────
# 5 envios / 10 min por IP; usado só pro endpoint público /api/leads.
# Best-effort: reinicia com o processo (restart do systemd), aceitável pro
# volume de tráfego de anúncio deste formulário.

_RATE_LIMIT_JANELA_SEGUNDOS = 600
_RATE_LIMIT_MAX_POR_JANELA = 5
_rate_limit_lock = Lock()
_rate_limit_hits: dict = defaultdict(deque)


def extrair_ip_real(request) -> str:
    """
    X-Forwarded-For pode conter uma cadeia "cliente, proxy1, proxy2" — o IP
    real do usuário é sempre o PRIMEIRO da lista (os demais são dos proxies
    intermediários, incluindo o NGINX local). Nunca usar o último.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        primeiro = xff.split(",")[0].strip()
        if primeiro:
            return primeiro
    return request.client.host if request.client else "desconhecido"


def rate_limit_excedido(ip: str) -> bool:
    agora = time.time()
    with _rate_limit_lock:
        hits = _rate_limit_hits[ip]
        while hits and agora - hits[0] > _RATE_LIMIT_JANELA_SEGUNDOS:
            hits.popleft()
        if len(hits) >= _RATE_LIMIT_MAX_POR_JANELA:
            return True
        hits.append(agora)
        return False
