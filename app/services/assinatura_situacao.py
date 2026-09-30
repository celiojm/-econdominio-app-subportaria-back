# ============================================================================
# ARQUIVO: assinatura_situacao.py
# PASTA: /home/visionlpr/app_subportaria_back/app/services/
# DESCRIÇÃO: Fonte única do STATUS do condomínio (padrão para app, admin,
#            financeiro e régua) e do que cada status permite:
#              ativo | em_teste | teste_estendido | cadastro_incompleto |
#              nao_convertido | cancelado | sistema
#            + alertas (não mudam o status): dados de cobrança incompletos,
#            pagamento em atraso. Inclui o guard de recebimento de encomendas.
# VERSÃO: 3.0.0 - padronização dos 7 status (2026-09-27)
#         2.x   - 5 estados da régua (em_teste/inativo/ativo/em_debito/desativado)
# data criação: 2026-09-26 data alteração: 2026-09-27
# ============================================================================

import logging
import os
from datetime import date
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import text

logger = logging.getLogger(__name__)

# --- Constantes ajustáveis ---------------------------------------------------
TRIAL_DIAS = 14                 # duração do teste no cadastro
AVISO_ANTES_DIAS = 6            # aviso/boleto: dias antes do fim da validade
BOLETO_VALIDADE_DIAS = 10       # boleto vence X dias após ser gerado
ATRASO_BLOQUEIA_RECEBER = 5     # pagante: dias após a validade até parar de receber
CANCELA_APOS_DIAS = 30          # pagante: dias após a validade até virar Cancelado
NAO_CONVERTIDO_CONSULTA_DIAS = 30  # não convertido: dias só consultando, depois só tela de ativação

STATUS = ("ativo", "em_teste", "teste_estendido", "cadastro_incompleto", "nao_convertido", "cancelado", "sistema")
ROTULOS = {
    "ativo": "Ativo", "em_teste": "Em teste", "teste_estendido": "Teste estendido",
    "cadastro_incompleto": "Cadastro incompleto", "nao_convertido": "Não convertido",
    "cancelado": "Cancelado", "sistema": "Sistema",
}

CAMPOS_BASICOS = {
    "total_apartamentos": "Total de unidades",
    "sindico": "Síndico / Responsável",
    "telefone": "Telefone",
    "email": "E-mail",
}
CAMPOS_COBRANCA = {
    "cobranca_email": "E-mail de cobrança",
    "cobranca_whats": "WhatsApp de cobrança",
}
# compatibilidade com quem importava o nome antigo
CAMPOS_CADASTRO_OBRIGATORIOS = {**CAMPOS_BASICOS, **CAMPOS_COBRANCA}

MSG_BONIFICADO = "Período de bonificação — Garanta seu acesso!"


def _fmt_data(d) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _faltando(dados: Optional[dict], campos: dict) -> list:
    if dados is None:
        return []
    out = []
    for campo, rotulo in campos.items():
        v = dados.get(campo)
        vazio = (not v or int(v) <= 0) if campo == "total_apartamentos" else not str(v or "").strip()
        if vazio:
            out.append(rotulo)
    return out


def calcular_situacao(
    validade_ate: Optional[date],
    assinatura_status: Optional[str],
    bonificado: bool,
    data_fim_bonificado: Optional[date],
    dados_cadastro: Optional[dict] = None,
    hoje: Optional[date] = None,
    ja_pagou: bool = False,
    n_moradores: Optional[int] = None,
) -> dict:
    """Função pura. Pagante = tem cobrança paga (ja_pagou). assinatura_status só importa para
    'sistema' e 're_teste' (teste estendido). Bonificação nunca sobrepõe a validade."""
    hoje = hoje or date.today()
    dias = (validade_ate - hoje).days if validade_ate else -999
    st_contrato = (assinatura_status or "").lower()
    bonificado_vigente = bool(bonificado and data_fim_bonificado and hoje <= data_fim_bonificado)
    faltam_basicos = _faltando(dados_cadastro, CAMPOS_BASICOS)
    faltam_cobranca = _faltando(dados_cadastro, CAMPOS_COBRANCA)
    alertas = []
    if faltam_cobranca:
        alertas.append("Complete os dados de cobrança: " + ", ".join(faltam_cobranca) + ".")

    # permissões padrão: tudo liberado (receber depende dos dados básicos)
    receber, entregar, leitura, tela_bloqueada = not faltam_basicos, True, False, False
    dias_limite = None

    if st_contrato == "sistema":
        status, msg = "sistema", "Condomínio interno (sem cobrança)."
        receber, alertas = True, []
    elif ja_pagou:
        if dias >= -CANCELA_APOS_DIAS:
            status = "ativo"
            if dias < -ATRASO_BLOQUEIA_RECEBER:
                receber = False
                dias_limite = dias + CANCELA_APOS_DIAS + 1
                alertas.append("Pagamento em atraso.")
                msg = "Pagamento em atraso. Você pode entregar encomendas, mas não receber novas até regularizar."
            elif dias < 0:
                alertas.append("Pagamento em atraso.")
                msg = f"Assinatura venceu em {_fmt_data(validade_ate)}. Regularize para não interromper o recebimento."
            else:
                msg = f"Assinatura válida até {_fmt_data(validade_ate)}"
        else:
            status = "cancelado"
            receber, entregar, leitura, tela_bloqueada = False, False, True, True
            msg = "Condomínio há mais de 30 dias sem pagamento. Deseja ativar?"
    elif dias < 0:
        status = "nao_convertido"
        receber, entregar, leitura = False, False, True
        if dias < -NAO_CONVERTIDO_CONSULTA_DIAS:
            tela_bloqueada = True
            msg = "Período de teste encerrado há mais de 30 dias. Deseja ativar?"
        else:
            dias_limite = dias + NAO_CONVERTIDO_CONSULTA_DIAS + 1
            msg = "Período de teste encerrado. Faça o pagamento para utilizar o sistema."
    else:
        if st_contrato == "re_teste":
            status = "teste_estendido"
        elif faltam_basicos or n_moradores == 0:
            status = "cadastro_incompleto"
        else:
            status = "em_teste"
        msg = f"Período de teste até {_fmt_data(validade_ate)} ({dias} dia(s) restantes)."
        if bonificado_vigente:
            msg = MSG_BONIFICADO

    mostrar_aviso = status in ("nao_convertido", "cancelado") or (status == "ativo" and dias < 0) or \
        (status in ("ativo", "em_teste", "teste_estendido", "cadastro_incompleto") and dias <= AVISO_ANTES_DIAS)
    return {
        "status": status,
        "rotulo": ROTULOS[status],
        "validade_ate": validade_ate,
        "dias_restantes": dias,
        "dias_para_desativar": dias_limite,
        "mensagem": msg,
        "bonificado_vigente": bonificado_vigente,
        "cadastro_incompleto": faltam_basicos if status != "sistema" else [],
        "alertas": alertas,
        "pode_receber": bool(receber),
        "pode_entregar": entregar,
        "somente_leitura": leitura,
        "tela_bloqueada": tela_bloqueada,
        "pode_continuar": not tela_bloqueada,
        "mostrar_aviso_pagamento": mostrar_aviso and status != "sistema",
    }


def situacao_assinatura(db, condominio_id: int, hoje: Optional[date] = None) -> Optional[dict]:
    """Lê o condomínio e devolve a situação; None se o condomínio não existir."""
    campos = ", ".join(CAMPOS_CADASTRO_OBRIGATORIOS)
    row = db.execute(
        text(
            f"SELECT validade_ate, assinatura_status, bonificado, data_fim_bonificado, {campos}, "
            "EXISTS(SELECT 1 FROM cobrancas b WHERE b.id_condominio = c.id AND b.status = 'pago') AS ja_pagou, "
            "(SELECT COUNT(*) FROM moradores m WHERE m.condominio_id = c.id AND m.ativo = 1) AS n_moradores "
            "FROM condominios c WHERE c.id = :id"
        ),
        {"id": condominio_id},
    ).fetchone()
    if not row:
        return None
    return calcular_situacao(
        row.validade_ate, row.assinatura_status, bool(row.bonificado), row.data_fim_bonificado,
        {c: getattr(row, c) for c in CAMPOS_CADASTRO_OBRIGATORIOS}, hoje, bool(row.ja_pagou), int(row.n_moradores),
    )


def bloqueio_habilitado() -> bool:
    return os.getenv("BLOQUEIO_ASSINATURA_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def checar_bloqueio_recebimento(db, condominio_id: Optional[int]) -> None:
    """Chamar antes de INSERT de encomenda. Flag desligada = só loga (dry-run);
    ligada = HTTP 402. Não afeta entrega, retirada, consulta nem listagem."""
    if not condominio_id:
        return
    sit = situacao_assinatura(db, int(condominio_id))
    if not sit or sit["pode_receber"]:
        return
    st = sit["status"]
    if st == "nao_convertido":
        detail = "Período de teste encerrado. Faça o pagamento para utilizar o sistema."
    elif st == "cancelado":
        detail = "Condomínio há mais de 30 dias sem pagamento. Faça o pagamento para reativar."
    elif st == "ativo":
        detail = "Cadastro de encomendas suspenso por pagamento em atraso. Avise o síndico."
    elif sit["cadastro_incompleto"]:
        detail = ("Cadastro do condomínio incompleto (faltam: " + ", ".join(sit["cadastro_incompleto"])
                  + "). Complete os dados para receber encomendas.")
    else:
        detail = "Cadastro de encomendas suspenso. Avise o síndico."
    if not bloqueio_habilitado():
        logger.warning("BLOQUEIO_DRYRUN condominio=%s situacao=%s teria sido bloqueado: %s", condominio_id, st, detail)
        return
    raise HTTPException(status_code=402, detail=detail)
