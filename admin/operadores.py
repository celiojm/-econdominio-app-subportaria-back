# ========================================
# ALTERAÇÃO 2026-09-27: situacao_assinatura na lista de condomínios (filtro por status no admin)
# ARQUIVO: operadores.py
# PASTA: admin/
# DESCRIÇÃO: Operadores CRUD - Módulo Admin
# Gerencia tabela mobile_operadores
# VERSÃO: 4.1.0 - Adiciona nfe_antes_pagamento em CondominioUpdate,
#         SELECT_CONDOMINIO/_C, montar_condominio_dict() e no UPDATE de
#         atualizar_condominio, com guard de role (so admin_sistema altera,
#         comparando valor novo vs. atual pra nao quebrar salvamento normal
#         de outros campos por sindico). Etapa 3, NFE_FINANCEIRO.md. Testado
#         e provado antes deste deploy em dev2_back (arquitetura identica).
# VERSÃO: 4.0.0 - Limpo, sem remendos
# 2026-10-04: nível 'colaborador' (admin_sistema + nivel_sistema); só o master dá/altera níveis de sistema
# Roles: admin_sistema, sindico, operador, porteiro
# ========================================

import re
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, validator

from .database import get_db_connection
from .auth_service import auth_service
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

DIAS_BONIFICADOS = 7
ROLES_VALIDOS = ['admin_sistema', 'colaborador', 'sindico', 'operador', 'porteiro']
# 2026-10-04: 'colaborador' = equipe do sistema. No banco fica role='admin_sistema' + nivel_sistema='colaborador'
# (acessa o mesmo que o admin do sistema; rotinas só do colaborador usam nivel_sistema). Só o MASTER dá ou
# altera os níveis admin_sistema/colaborador.
NIVEIS_SISTEMA = ('admin_sistema', 'colaborador')


def _role_publico(op: dict) -> str:
    if op.get('role') == 'admin_sistema' and op.get('nivel_sistema') == 'colaborador':
        return 'colaborador'
    return op.get('role')


def _exigir_master_para(current_user: dict, motivo: str):
    from app.services.nivel_sistema import eh_master_sistema
    if not eh_master_sistema(current_user):
        raise HTTPException(status_code=403, detail=f"Só o master pode {motivo}")


# ============================================================================
# Schemas
# ============================================================================

class OperadorCreate(BaseModel):
    email: EmailStr
    nome: str
    telefone: Optional[str] = None
    senha: str
    condominio_id: int
    role: str = "operador"
    nivel_id: int = 3
    ativo: bool = True

    @validator('telefone')
    def normalizar_telefone(cls, v):
        if v:
            return re.sub(r'\D', '', v)
        return v

    @validator('role')
    def validar_role(cls, v):
        if v not in ROLES_VALIDOS:
            raise ValueError(f'Role inválido. Use: {ROLES_VALIDOS}')
        return v


class OperadorUpdate(BaseModel):
    email: Optional[EmailStr] = None
    nome: Optional[str] = None
    telefone: Optional[str] = None
    senha: Optional[str] = None
    condominio_id: Optional[int] = None
    role: Optional[str] = None
    nivel_id: Optional[int] = None
    ativo: Optional[bool] = None

    @validator('telefone')
    def normalizar_telefone(cls, v):
        if v:
            return re.sub(r'\D', '', v)
        return v

    @validator('role')
    def validar_role(cls, v):
        if v and v not in ROLES_VALIDOS:
            raise ValueError(f'Role inválido. Use: {ROLES_VALIDOS}')
        return v


class CondominioUpdate(BaseModel):
    nome: Optional[str] = None
    cnpj: Optional[str] = None
    sindico: Optional[str] = None
    endereco: Optional[str] = None
    numero: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None
    cep: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None
    total_apartamentos: Optional[int] = None
    ativo: Optional[bool] = None
    observacoes: Optional[str] = None
    cobranca_responsavel: Optional[str] = None
    cobranca_email: Optional[str] = None
    cobranca_whats: Optional[str] = None
    email_financeiro: Optional[str] = None
    plano_selecionado: Optional[str] = None
    valor_mensal_base: Optional[float] = None
    valor_plano_final: Optional[float] = None
    plano_desconto: Optional[float] = None
    usa_subportaria: Optional[bool] = None
    envio_whatsapp: Optional[str] = None
    permite_auto_cadastro: Optional[bool] = None
    nfe_antes_pagamento: Optional[bool] = None
    lembrete_encomenda_ativo: Optional[bool] = None
# ============================================================================
# Funções auxiliares
# ============================================================================

def get_email_base(email: str) -> str:
    """Extrai o email base sem sufixo +condX"""
    if not email:
        return ''
    parte_local = email.split('@')[0]
    if '+cond' in parte_local:
        parte_local = parte_local.split('+cond')[0]
    elif '+' in parte_local:
        parte_local = parte_local.split('+')[0]
    dominio = email.split('@')[1] if '@' in email else ''
    return f"{parte_local}@{dominio}"


def get_condominios_do_sindico(cursor, user_email: str) -> List[int]:
    """Retorna lista de IDs de condomínios que o síndico gerencia"""
    email_base = get_email_base(user_email)
    cursor.execute("""
        SELECT DISTINCT condominio_id
        FROM mobile_operadores
        WHERE email = %s
           OR email LIKE %s
           OR email = %s
    """, (user_email, f"{email_base.split('@')[0]}+cond%@{email_base.split('@')[1]}", email_base))
    return [r['condominio_id'] for r in cursor.fetchall()]


def verificar_permissao_criar(current_user: dict, condominio_destino: int, cursor=None):
    role = current_user.get('role', '')
    if role == 'admin_sistema':
        return True
    if role == 'sindico':
        if cursor:
            permitidos = get_condominios_do_sindico(cursor, current_user.get('email', ''))
            if condominio_destino in permitidos:
                return True
            raise HTTPException(status_code=403, detail="Você não tem permissão para este condomínio")
        else:
            if condominio_destino == current_user.get('condominio_id'):
                return True
            raise HTTPException(status_code=403, detail="Você só pode criar operadores no seu condomínio")
    raise HTTPException(status_code=403, detail="Você não tem permissão para criar operadores")


def verificar_permissao_editar(current_user: dict, operador_condominio: int, cursor=None):
    role = current_user.get('role', '')
    if role == 'admin_sistema':
        return True
    if role == 'sindico':
        if cursor:
            permitidos = get_condominios_do_sindico(cursor, current_user.get('email', ''))
            if operador_condominio in permitidos:
                return True
            raise HTTPException(status_code=403, detail="Você só pode editar operadores dos seus condomínios")
        else:
            if operador_condominio == current_user.get('condominio_id'):
                return True
            raise HTTPException(status_code=403, detail="Você só pode editar operadores do seu condomínio")
    raise HTTPException(status_code=403, detail="Você não tem permissão para editar operadores")


def montar_condominio_dict(c: dict, prefixo: str = '') -> dict:
    """Monta dicionário padrão de condomínio a partir de um row do banco"""
    p = prefixo  # vazio ou 'c.' já removido pelo cursor dict
    return {
        "id": c['id'],
        "nome": c['nome'],
        "cnpj": c.get('cnpj'),
        "endereco": c.get('endereco'),
        "numero": c.get('numero'),
        "complemento": c.get('complemento'),
        "bairro": c.get('bairro'),
        "cidade": c.get('cidade'),
        "estado": c.get('estado'),
        "cep": c.get('cep'),
        "telefone": c.get('telefone'),
        "email": c.get('email'),
        "sindico": c.get('sindico'),
        "total_apartamentos": c.get('total_apartamentos', 0),
        "observacoes": c.get('observacoes'),
        "ativo": bool(c.get('ativo', True)),
        "cobranca_responsavel": c.get('cobranca_responsavel'),
        "cobranca_email": c.get('cobranca_email'),
        "cobranca_whats": c.get('cobranca_whats'),
        "email_financeiro": c.get('email_financeiro'),
        "validade_ate": str(c['validade_ate']) if c.get('validade_ate') else None,
        "assinatura_status": c.get('assinatura_status'),
        "data_cadastro": str(c['data_cadastro']) if c.get('data_cadastro') else None,
        "periodo_bonificado": c.get('periodo_bonificado', 0),
        "data_fim_bonificado": str(c['data_fim_bonificado']) if c.get('data_fim_bonificado') else None,
        "plano_selecionado": c.get('plano_selecionado') or 'mensal',
        "valor_mensal_base": float(c.get('valor_mensal_base') or 0),
        "valor_plano_final": float(c.get('valor_plano_final') or 0),
        "plano_desconto": int(c.get('plano_desconto') or 0),
        "usa_subportaria": bool(c.get('usa_subportaria', False)),
        "envio_whatsapp": c.get('envio_whatsapp') or 'S',
        "permite_auto_cadastro": bool(c.get('permite_auto_cadastro', False)),
        "nfe_antes_pagamento": bool(c.get('nfe_antes_pagamento', False)),
        "situacao_assinatura": c.get('situacao_assinatura'),
        "lembrete_encomenda_ativo": bool(c.get('lembrete_encomenda_ativo', False)),
    }


# ============================================================================
# SELECT padrão de condomínio (reutilizado em todas as queries)
# ============================================================================



SELECT_CONDOMINIO = """
    id, nome, cnpj, endereco, numero, complemento, bairro, cidade, estado, cep,
    telefone, email, sindico, total_apartamentos, observacoes, ativo,
    cobranca_responsavel, cobranca_email, cobranca_whats, email_financeiro,
    validade_ate, assinatura_status, data_cadastro,
    periodo_bonificado, data_fim_bonificado,
    plano_selecionado, valor_mensal_base, valor_plano_final, plano_desconto,
    usa_subportaria, envio_whatsapp, permite_auto_cadastro, nfe_antes_pagamento,
    lembrete_encomenda_ativo,
    situacao_assinatura
"""


SELECT_CONDOMINIO_C = """
    c.id, c.nome, c.cnpj, c.endereco, c.numero, c.complemento, c.bairro, c.cidade, c.estado, c.cep,
    c.telefone, c.email, c.sindico, c.total_apartamentos, c.observacoes, c.ativo,
    c.cobranca_responsavel, c.cobranca_email, c.cobranca_whats, c.email_financeiro,
    c.validade_ate, c.assinatura_status, c.data_cadastro,
    c.periodo_bonificado, c.data_fim_bonificado,
    c.plano_selecionado, c.valor_mensal_base, c.valor_plano_final, c.plano_desconto,
    c.usa_subportaria, c.envio_whatsapp, c.permite_auto_cadastro, c.nfe_antes_pagamento,
    c.lembrete_encomenda_ativo,
    c.situacao_assinatura
"""


# ============================================================================
# Rotas - Condomínios
# ============================================================================

@router.get("/condominios-disponiveis")
async def listar_condominios_disponiveis(current_user: dict = Depends(get_current_user)):
    """Lista condomínios disponíveis baseado na permissão do usuário"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            role = current_user.get('role', '')
            user_email = current_user.get('email', '')
            logger.info(f"Listando condomínios para role={role}, email={user_email}")

            if role == 'admin_sistema':
                cursor.execute(f"""
                    SELECT {SELECT_CONDOMINIO}
                    FROM condominios
                    WHERE ativo = 1
                    ORDER BY nome
                """)

            elif role == 'sindico':
                email_base = get_email_base(user_email)
                cursor.execute(f"""
                    SELECT DISTINCT {SELECT_CONDOMINIO_C}
                    FROM condominios c
                    INNER JOIN mobile_operadores op ON op.condominio_id = c.id
                    WHERE c.ativo = 1 AND (
                        op.email = %s
                        OR op.email LIKE %s
                        OR op.email = %s
                    )
                    ORDER BY c.nome
                """, (user_email, f"{email_base.split('@')[0]}+cond%@{email_base.split('@')[1]}", email_base))

            else:
                return []

            condominios = cursor.fetchall()
            logger.info(f"Encontrados {len(condominios)} condomínios")
            return [montar_condominio_dict(c) for c in condominios]

    finally:
        conn.close()


@router.get("/condominios/meus")
async def listar_meus_condominios(current_user: dict = Depends(get_current_user)):
    """Lista todos os condomínios que o usuário gerencia"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            user_email = current_user.get('email', '')
            email_base = get_email_base(user_email)

            if current_user.get('role') == 'admin_sistema':
                cursor.execute(f"""
                    SELECT {SELECT_CONDOMINIO}
                    FROM condominios
                    WHERE ativo = 1
                    ORDER BY nome
                """)
            else:
                cursor.execute(f"""
                    SELECT DISTINCT {SELECT_CONDOMINIO_C}
                    FROM condominios c
                    INNER JOIN mobile_operadores op ON op.condominio_id = c.id
                    WHERE c.ativo = 1 AND (
                        op.email = %s
                        OR op.email LIKE %s
                        OR op.email = %s
                    )
                    ORDER BY c.nome
                """, (user_email, f"{email_base.split('@')[0]}+cond%@{email_base.split('@')[1]}", email_base))

            condominios = cursor.fetchall()
            return {
                "success": True,
                "total": len(condominios),
                "condominios": [montar_condominio_dict(c) for c in condominios]
            }

    except Exception as e:
        logger.error(f"Erro ao listar condomínios: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.post("/condominios/sindico-novo")
async def sindico_cadastrar_condominio(
    dados: dict,
    current_user: dict = Depends(get_current_user)
):
    """Permite que um síndico profissional cadastre um novo condomínio"""
    if current_user.get('role') not in ['sindico', 'admin_sistema']:
        raise HTTPException(status_code=403, detail="Apenas síndicos podem cadastrar novos condomínios")

    cnpj = dados.get('cnpj', '').replace('.', '').replace('/', '').replace('-', '')
    nome = dados.get('nome', '').strip()

    if not cnpj or len(cnpj) != 14:
        raise HTTPException(status_code=400, detail="CNPJ inválido")
    if not nome:
        raise HTTPException(status_code=400, detail="Nome do condomínio é obrigatório")

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, nome FROM condominios WHERE REPLACE(REPLACE(REPLACE(cnpj, '.', ''), '/', ''), '-', '') = %s",
                (cnpj,)
            )
            existente = cursor.fetchone()
            if existente:
                raise HTTPException(status_code=400, detail=f"CNPJ já cadastrado: {existente['nome']}")

            data_hoje = date.today()
            data_validade = data_hoje + timedelta(days=DIAS_BONIFICADOS)

            cursor.execute("""
                INSERT INTO condominios (
                    nome, cnpj, endereco, numero, complemento, bairro, cidade, estado, cep,
                    telefone, email, sindico, total_apartamentos,
                    validade_ate, assinatura_status, periodo_bonificado,
                    data_inicio_bonificado, data_fim_bonificado, ativo,
                    plano_selecionado, valor_mensal_base, valor_plano_final, plano_desconto
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, 'ativa', 1,
                    %s, %s, 1,
                    %s, %s, %s, %s
                )
            """, (
                nome, cnpj,
                dados.get('endereco', ''), dados.get('numero', ''),
                dados.get('complemento', ''), dados.get('bairro', ''),
                dados.get('cidade', ''), dados.get('estado', 'SC'),
                dados.get('cep', '').replace('-', ''),
                dados.get('telefone', '').replace('(', '').replace(')', '').replace('-', '').replace(' ', ''),
                dados.get('email', ''),
                current_user.get('nome', ''),
                dados.get('total_apartamentos', 0),
                data_validade, data_hoje, data_validade,
                dados.get('plano_selecionado', 'mensal'),
                dados.get('valor_mensal_base', 0),
                dados.get('valor_plano_final', 0),
                dados.get('plano_desconto', 0),
            ))

            novo_condominio_id = cursor.lastrowid

            # Vincular síndico ao novo condomínio
            cursor.execute(
                "SELECT id, senha_hash FROM mobile_operadores WHERE id = %s",
                (current_user['id'],)
            )
            operador_existente = cursor.fetchone()

            if operador_existente:
                email_original = current_user.get('email', '')
                parte_local = email_original.split('@')[0]
                dominio = email_original.split('@')[1] if '@' in email_original else 'econdominio.com.br'
                if '+cond' in parte_local:
                    parte_local = parte_local.split('+cond')[0]
                email_alternativo = f"{parte_local}+cond{novo_condominio_id}@{dominio}"

                cursor.execute("""
                    INSERT INTO mobile_operadores (
                        condominio_id, nome, email, senha_hash, role, nivel_id, ativo
                    ) VALUES (%s, %s, %s, %s, 'sindico', 2, 1)
                """, (
                    novo_condominio_id,
                    current_user.get('nome'),
                    email_alternativo,
                    operador_existente['senha_hash']
                ))

            conn.commit()
            logger.info(f"Síndico {current_user.get('email')} cadastrou condomínio: {nome} (ID: {novo_condominio_id})")

            return {
                "success": True,
                "message": f"Condomínio cadastrado! Você tem {DIAS_BONIFICADOS} dias de teste gratuito.",
                "condominio": {
                    "id": novo_condominio_id,
                    "nome": nome,
                    "cnpj": cnpj,
                    "validade_ate": data_validade.isoformat(),
                    "dias_bonificados": DIAS_BONIFICADOS
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"Erro ao cadastrar condomínio: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.put("/condominios/{condominio_id}")
async def atualizar_condominio(
    condominio_id: int,
    data: CondominioUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Atualiza dados de um condomínio (admin ou síndico vinculado)"""
    conn = get_db_connection()
    try:
        role = current_user.get('role', '')
        user_email = current_user.get('email', '')

        with conn.cursor() as cursor:
            cursor.execute("SELECT id, nome, usa_subportaria, nfe_antes_pagamento, lembrete_encomenda_ativo, total_apartamentos FROM condominios WHERE id = %s", (condominio_id,))
            cond = cursor.fetchone()
            if not cond:
                raise HTTPException(status_code=404, detail="Condomínio não encontrado")

            if role == 'admin_sistema':
                pass
            elif role == 'sindico':
                permitidos = get_condominios_do_sindico(cursor, user_email)
                if condominio_id not in permitidos:
                    raise HTTPException(status_code=403, detail="Sem permissão para editar este condomínio")
            else:
                raise HTTPException(status_code=403, detail="Sem permissão")

            # Kill switch: bloqueia desativar subportaria com lote em andamento
            # (status != notificado e != cancelado). Ver SUBPORTARIA_DEV2.md secao 8.5.
            if data.usa_subportaria is False and bool(cond.get("usa_subportaria")):
                cursor.execute(
                    "SELECT COUNT(*) AS n FROM lotes_encomendas WHERE condominio_id = %s AND status NOT IN ('notificado', 'cancelado')",
                    (condominio_id,)
                )
                pendentes = cursor.fetchone()["n"]
                if pendentes > 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Nao e possivel desativar a subportaria: existem {pendentes} lote(s) em andamento. Feche, transfira e notifique todos os lotes antes de desativar."
                    )

            # 2026-10-05: total de unidades so pode ser ALTERADO pela equipe do sistema (master/colaborador) —
            # mesma comparacao com o valor atual (o front manda o formulario inteiro).
            if data.total_apartamentos is not None and int(data.total_apartamentos or 0) != int(cond.get("total_apartamentos") or 0):
                if role != "admin_sistema":
                    raise HTTPException(status_code=403, detail="Só a equipe do sistema pode alterar o total de unidades")

            # nfe_antes_pagamento so pode ser ALTERADO por admin_sistema. Compara
            # com o valor atual (nao so a presenca do campo) porque o front manda
            # o formulario inteiro no PUT, inclusive pra sindico salvando outro
            # campo qualquer — um 403 seco so pela presenca quebraria esse fluxo.
            if data.nfe_antes_pagamento is not None and bool(data.nfe_antes_pagamento) != bool(cond.get("nfe_antes_pagamento")):
                if role != 'admin_sistema':
                    raise HTTPException(
                        status_code=403,
                        detail="Apenas admin_sistema pode alterar nfe_antes_pagamento",
                    )

            # lembrete_encomenda_ativo so pode ser ALTERADO por admin_sistema — mesma
            # logica de comparacao com o valor atual usada em nfe_antes_pagamento.
            if data.lembrete_encomenda_ativo is not None and bool(data.lembrete_encomenda_ativo) != bool(cond.get("lembrete_encomenda_ativo")):
                if role != 'admin_sistema':
                    raise HTTPException(
                        status_code=403,
                        detail="Apenas admin_sistema pode alterar lembrete_encomenda_ativo",
                    )

            # Montar UPDATE apenas com campos enviados (não None)
            campos = {
                'nome': data.nome,
                'cnpj': data.cnpj,
                'sindico': data.sindico,
                'endereco': data.endereco,
                'numero': data.numero,
                'complemento': data.complemento,
                'bairro': data.bairro,
                'cidade': data.cidade,
                'estado': data.estado,
                'cep': data.cep,
                'telefone': data.telefone,
                'email': data.email,
                'total_apartamentos': data.total_apartamentos,
                'ativo': data.ativo,
                'observacoes': data.observacoes,
                'cobranca_responsavel': data.cobranca_responsavel,
                'cobranca_email': data.cobranca_email,
                'cobranca_whats': data.cobranca_whats,
                'email_financeiro': data.email_financeiro,
                'plano_selecionado': data.plano_selecionado,
                'valor_mensal_base': data.valor_mensal_base,
                'valor_plano_final': data.valor_plano_final,
                'plano_desconto': data.plano_desconto,
                'usa_subportaria': data.usa_subportaria,
                'envio_whatsapp': data.envio_whatsapp,
                'permite_auto_cadastro': data.permite_auto_cadastro,
                'nfe_antes_pagamento': data.nfe_antes_pagamento,
                'lembrete_encomenda_ativo': data.lembrete_encomenda_ativo,
            }

            updates = [(k, v) for k, v in campos.items() if v is not None]

            if not updates:
                return {"success": True, "message": "Nada a atualizar"}

            sql = f"UPDATE condominios SET {', '.join(f'{k} = %s' for k, v in updates)} WHERE id = %s"
            cursor.execute(sql, [v for k, v in updates] + [condominio_id])
            conn.commit()

            logger.info(f"Condomínio {condominio_id} atualizado por {user_email}: {[k for k,v in updates]}")
            return {
                "success": True,
                "message": f"'{cond['nome']}' atualizado com sucesso",
                "condominio_id": condominio_id
            }

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"Erro ao atualizar condomínio {condominio_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


# ============================================================================
# Rotas - Operadores
# ============================================================================

@router.get("/operadores")
async def listar_operadores(current_user: dict = Depends(get_current_user)):
    """Lista operadores filtrado por permissão"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            role = current_user.get('role', '')
            user_email = current_user.get('email', '')

            if role == 'admin_sistema':
                cursor.execute("""
                    SELECT o.*, c.nome as condominio_nome
                    FROM mobile_operadores o
                    LEFT JOIN condominios c ON o.condominio_id = c.id
                    ORDER BY o.nome
                """)
            elif role == 'sindico':
                condominios_ids = get_condominios_do_sindico(cursor, user_email)
                if not condominios_ids:
                    return []
                placeholders = ','.join(['%s'] * len(condominios_ids))
                cursor.execute(f"""
                    SELECT o.*, c.nome as condominio_nome
                    FROM mobile_operadores o
                    LEFT JOIN condominios c ON o.condominio_id = c.id
                    WHERE o.condominio_id IN ({placeholders})
                    ORDER BY c.nome, o.nome
                """, condominios_ids)
            else:
                raise HTTPException(status_code=403, detail="Você não tem acesso a esta funcionalidade")

            operadores = cursor.fetchall()
            return [
                {
                    "id": op['id'],
                    "email": op['email'],
                    "nome": op['nome'],
                    "telefone": op['telefone'],
                    "condominio_id": op['condominio_id'],
                    "condominio_nome": op.get('condominio_nome', ''),
                    "role": _role_publico(op),
                    "nivel_id": op['nivel_id'],
                    "ativo": bool(op['ativo']),
                    "email_verificado": bool(op.get('email_verificado', False)),
                    "ultimo_login": str(op['ultimo_login']) if op.get('ultimo_login') else None,
                    "criado_em": str(op['criado_em']) if op.get('criado_em') else None,
                }
                for op in operadores
            ]
    finally:
        conn.close()


@router.get("/operadores/bloqueados")
async def listar_operadores_bloqueados(current_user: dict = Depends(get_current_user)):
    """Lista operadores bloqueados por excesso de tentativas de login (só admin_sistema)"""
    if current_user.get('role') != 'admin_sistema':
        raise HTTPException(status_code=403, detail="Apenas admin_sistema pode ver usuários bloqueados")
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT o.id, o.email, o.nome, o.role, o.login_falhos, o.bloqueado_ate,
                       c.nome AS condominio_nome
                FROM mobile_operadores o
                LEFT JOIN condominios c ON o.condominio_id = c.id
                WHERE o.bloqueado_ate IS NOT NULL AND o.bloqueado_ate > NOW()
                ORDER BY o.bloqueado_ate DESC
            """)
            bloqueados = cursor.fetchall()
            return [
                {
                    "id": b['id'],
                    "email": b['email'],
                    "nome": b['nome'],
                    "role": b['role'],
                    "condominio_nome": b.get('condominio_nome', ''),
                    "login_falhos": b['login_falhos'],
                    "bloqueado_ate": str(b['bloqueado_ate']) if b.get('bloqueado_ate') else None,
                }
                for b in bloqueados
            ]
    finally:
        conn.close()


@router.post("/operadores/{operador_id}/desbloquear")
async def desbloquear_operador(operador_id: int, current_user: dict = Depends(get_current_user)):
    """Desbloqueia operador (zera login_falhos/bloqueado_ate e limpa tentativas). Só admin_sistema."""
    if current_user.get('role') != 'admin_sistema':
        raise HTTPException(status_code=403, detail="Apenas admin_sistema pode desbloquear usuários")
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT email FROM mobile_operadores WHERE id = %s", (operador_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Usuário não encontrado")
            email = row['email']

            cursor.execute(
                "UPDATE mobile_operadores SET bloqueado_ate = NULL, login_falhos = 0 WHERE id = %s",
                (operador_id,)
            )

            cursor.execute(
                "SELECT DISTINCT ip FROM mobile_login_attempts WHERE LOWER(identifier) = LOWER(%s) AND sucesso = 0",
                (email,)
            )
            ips = [r['ip'] for r in cursor.fetchall()]

            cursor.execute(
                "DELETE FROM mobile_login_attempts WHERE LOWER(identifier) = LOWER(%s)",
                (email,)
            )
            if ips:
                placeholders = ','.join(['%s'] * len(ips))
                cursor.execute(
                    f"DELETE FROM mobile_login_attempts WHERE sucesso = 0 AND ip IN ({placeholders})",
                    ips
                )
        conn.commit()
        return {"success": True, "message": f"Usuário {email} desbloqueado com sucesso"}
    finally:
        conn.close()


@router.get("/operadores/{operador_id}")
async def obter_operador(operador_id: int, current_user: dict = Depends(get_current_user)):
    """Obtém um operador por ID"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT o.*, c.nome as condominio_nome
                FROM mobile_operadores o
                LEFT JOIN condominios c ON o.condominio_id = c.id
                WHERE o.id = %s
            """, (operador_id,))
            op = cursor.fetchone()
            if not op:
                raise HTTPException(status_code=404, detail="Operador não encontrado")

            verificar_permissao_editar(current_user, op['condominio_id'], cursor)

            return {
                "id": op['id'],
                "email": op['email'],
                "nome": op['nome'],
                "telefone": op['telefone'],
                "condominio_id": op['condominio_id'],
                "condominio_nome": op.get('condominio_nome', ''),
                "role": _role_publico(op),
                "nivel_id": op['nivel_id'],
                "ativo": bool(op['ativo']),
                "email_verificado": bool(op.get('email_verificado', False)),
                "ultimo_login": str(op['ultimo_login']) if op.get('ultimo_login') else None,
                "criado_em": str(op['criado_em']) if op.get('criado_em') else None,
            }
    finally:
        conn.close()


@router.post("/operadores")
async def criar_operador(data: OperadorCreate, current_user: dict = Depends(get_current_user)):
    """Cria novo operador"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            verificar_permissao_criar(current_user, data.condominio_id, cursor)

            cursor.execute("SELECT id FROM mobile_operadores WHERE LOWER(email) = LOWER(%s)", (data.email,))
            if cursor.fetchone():
                raise HTTPException(status_code=400, detail="Email já cadastrado")

            if not data.senha:
                raise HTTPException(status_code=400, detail="Senha é obrigatória")

            senha_hash = auth_service.hash_senha(data.senha)

            role_db, nivel_sistema = data.role, None
            if data.role in NIVEIS_SISTEMA:  # 2026-10-04
                _exigir_master_para(current_user, "cadastrar Admin do Sistema ou Colaborador")
                role_db, nivel_sistema = 'admin_sistema', ('colaborador' if data.role == 'colaborador' else 'master')

            cursor.execute("""
                INSERT INTO mobile_operadores
                (email, nome, telefone, senha_hash, condominio_id, role, nivel_sistema, nivel_id, ativo, criado_por)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                data.email.lower(), data.nome, data.telefone, senha_hash,
                data.condominio_id, role_db, nivel_sistema, 1 if nivel_sistema else data.nivel_id,
                1 if data.ativo else 0, current_user.get('id')
            ))
            conn.commit()
            operador_id = cursor.lastrowid
            logger.info(f"Operador criado: {data.email} (role={data.role})")
            return {"message": "Operador criado com sucesso!", "id": operador_id}

    finally:
        conn.close()


@router.put("/operadores/{operador_id}")
async def atualizar_operador(operador_id: int, data: OperadorUpdate, current_user: dict = Depends(get_current_user)):
    """Atualiza operador"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM mobile_operadores WHERE id = %s", (operador_id,))
            operador = cursor.fetchone()
            if not operador:
                raise HTTPException(status_code=404, detail="Operador não encontrado")

            verificar_permissao_editar(current_user, operador['condominio_id'], cursor)

            if data.role is not None and data.role == _role_publico(operador):
                data.role = None  # 2026-10-04: a tela sempre manda o nível; igual ao atual = sem mudança
            # 2026-10-04: usuário da equipe do sistema (master/colaborador) ou nível de sistema → só o master
            if operador['role'] == 'admin_sistema' or (data.role in NIVEIS_SISTEMA):
                if operador['id'] != current_user.get('id') or data.role is not None:
                    _exigir_master_para(current_user, "alterar Admin do Sistema ou Colaborador")
                if operador['id'] == current_user.get('id') and data.role not in (None, 'admin_sistema'):
                    raise HTTPException(status_code=400, detail="Você não pode rebaixar o seu próprio usuário")
                if operador['id'] == current_user.get('id') and data.ativo is False:
                    raise HTTPException(status_code=400, detail="Você não pode desativar o seu próprio usuário")

            if data.condominio_id and data.condominio_id != operador['condominio_id']:
                verificar_permissao_criar(current_user, data.condominio_id, cursor)

            if data.email and data.email.lower() != operador['email'].lower():
                cursor.execute(
                    "SELECT id FROM mobile_operadores WHERE LOWER(email) = LOWER(%s) AND id != %s",
                    (data.email, operador_id)
                )
                if cursor.fetchone():
                    raise HTTPException(status_code=400, detail="Email já cadastrado")

            updates = []
            values = []

            if data.email is not None:
                updates.append("email = %s"); values.append(data.email.lower())
            if data.nome is not None:
                updates.append("nome = %s"); values.append(data.nome)
            if data.telefone is not None:
                updates.append("telefone = %s"); values.append(data.telefone or None)
            if data.senha:
                updates.append("senha_hash = %s"); values.append(auth_service.hash_senha(data.senha))
            if data.condominio_id is not None:
                updates.append("condominio_id = %s"); values.append(data.condominio_id)
            if data.role is not None:
                if data.role in NIVEIS_SISTEMA:  # 2026-10-04
                    updates.append("role = %s"); values.append('admin_sistema')
                    updates.append("nivel_sistema = %s"); values.append('colaborador' if data.role == 'colaborador' else 'master')
                else:
                    updates.append("role = %s"); values.append(data.role)
                    updates.append("nivel_sistema = NULL")
            if data.nivel_id is not None:
                updates.append("nivel_id = %s"); values.append(data.nivel_id)
            if data.ativo is not None:
                updates.append("ativo = %s"); values.append(1 if data.ativo else 0)

            if updates:
                values.append(operador_id)
                cursor.execute(
                    f"UPDATE mobile_operadores SET {', '.join(updates)} WHERE id = %s",
                    values
                )
                conn.commit()

            logger.info(f"Operador atualizado: id={operador_id}")
            return {"message": "Operador atualizado com sucesso!"}

    finally:
        conn.close()


@router.delete("/operadores/{operador_id}")
async def excluir_operador(operador_id: int, current_user: dict = Depends(get_current_user)):
    """Exclui operador"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM mobile_operadores WHERE id = %s", (operador_id,))
            operador = cursor.fetchone()
            if not operador:
                raise HTTPException(status_code=404, detail="Operador não encontrado")

            verificar_permissao_editar(current_user, operador['condominio_id'], cursor)

            if operador_id == current_user.get('id'):
                raise HTTPException(status_code=400, detail="Você não pode excluir a si mesmo")
            if operador['role'] == 'admin_sistema':
                if operador.get('nivel_sistema') != 'colaborador':
                    raise HTTPException(status_code=400, detail="Não é possível excluir administrador do sistema")
                _exigir_master_para(current_user, "excluir colaborador")  # 2026-10-04

            cursor.execute("DELETE FROM mobile_operadores WHERE id = %s", (operador_id,))
            conn.commit()
            logger.info(f"Operador excluído: id={operador_id}")
            return {"message": "Operador excluído com sucesso!"}

    finally:
        conn.close()


@router.get("/roles")
async def listar_roles(current_user: dict = Depends(get_current_user)):
    """Lista roles disponíveis baseado na permissão"""
    role = current_user.get('role', '')
    todas_roles = [
        {"value": "admin_sistema", "label": "Administrador Sistema", "nivel": 0},
        {"value": "colaborador",   "label": "Colaborador",            "nivel": 1},  # 2026-10-04
        {"value": "sindico",       "label": "Síndico",               "nivel": 2},
        {"value": "operador",      "label": "Operador",               "nivel": 3},
        {"value": "porteiro",      "label": "Porteiro",               "nivel": 4},
    ]
    if role == 'admin_sistema':
        from app.services.nivel_sistema import eh_master_sistema
        if eh_master_sistema(current_user):
            return todas_roles
        return [r for r in todas_roles if r['value'] not in NIVEIS_SISTEMA]  # 2026-10-04: colaborador
    if role in ['sindico', 'admin_condominio']:
        return [r for r in todas_roles if r['nivel'] >= 3]
    return []
