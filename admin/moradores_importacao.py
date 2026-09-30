# ============================================================================
# ARQUIVO: moradores_importacao.py
# PASTA: /home/visionlpr/app_subportaria_back/admin/
# DESCRIÇÃO: Endpoints para importação de moradores via staging (moradores_tmp)
#            com revisão antes de confirmar na tabela operacional moradores.
#            Respeita o condomínio do usuário logado.
# VERSÃO: 1.0.1 - Port de ~/backend/admin/moradores_importacao.py (só existia
#         no backend clássico; admin_s/admin.econdominio.com.br usa
#         app_subportaria_back, porta 5002, onde a rota nunca tinha sido
#         registrada — causa raiz do "Erro ao enviar para staging" na tela
#         de Importação de Moradores). Nenhuma mudança de lógica, só o
#         cabeçalho. Tabela moradores_tmp já existe em AdmGeral (produção),
#         confirmado por DESCRIBE antes do port — não precisou de ALTER.
# data criação: 2026-03-09   data alteração: 2026-09-11
# ============================================================================

import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from .database import get_db_connection
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# ========================================
# Schemas
# ========================================

class MoradorTmpItem(BaseModel):
    nome: str
    apartamento: str
    bloco: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None
    condominio_id: int
    condominio_nome: Optional[str] = None
    importado_por: Optional[str] = None


class ImportarTmpRequest(BaseModel):
    moradores: List[MoradorTmpItem]


class AtualizarStatusRequest(BaseModel):
    status_revisao: str  # 'pendente' | 'rejeitado'
    obs_revisao: Optional[str] = ''


class ConfirmarImportacaoRequest(BaseModel):
    condominio_id: int
    atualizar_existentes: bool = False


# ========================================
# Helpers
# ========================================

def verificar_acesso_condominio(current_user: dict, condominio_id: int):
    """
    Garante que o usuário só acessa o condomínio do seu próprio login.
    Admin sistema pode acessar qualquer um.
    """
    role = current_user.get('role', '')
    if role == 'admin_sistema':
        return True
    if current_user.get('condominio_id') != condominio_id:
        raise HTTPException(
            status_code=403,
            detail="Você não tem permissão para este condomínio"
        )
    return True


def _resolver_duplicados_pendentes(cursor, condominio_id: int) -> list:
    """
    Resolve, de forma segura, quais registros 'pendente' de moradores_tmp
    correspondem a um morador JÁ CADASTRADO (mesmo nome, ativo) no condomínio.

    Cuidado: o mesmo nome pode aparecer em MAIS DE UM morador (ex: uma pessoa
    dona de 2 unidades) — casar só por nome é ambíguo nesse caso. Regra:
      - 1 morador com esse nome  → é o duplicado (apto/bloco podem ter mudado
        ou não; se não mudou, só o telefone pode estar diferente).
      - Vários moradores com esse nome → só considera duplicado se algum
        deles já tiver EXATAMENTE o mesmo apartamento+bloco do registro
        importado (aí sim é a mesma unidade, resto é telefone). Se nenhum
        bater exatamente, NÃO é duplicado — é uma unidade nova pra essa
        pessoa (ex: comprou um 2º apartamento), deixa virar INSERT normal.
    """
    cursor.execute("""
        SELECT mt.id AS tmp_id, mt.nome, mt.apartamento AS apto_novo,
               mt.bloco AS bloco_novo, mt.telefone AS telefone_novo,
               m.id AS morador_id, m.apartamento AS apto_atual, m.bloco AS bloco_atual
        FROM moradores_tmp mt
        JOIN moradores m
          ON m.condominio_id = mt.condominio_id
         AND LOWER(TRIM(m.nome)) = LOWER(TRIM(mt.nome))
         AND m.ativo = 1
        WHERE mt.condominio_id = %s AND mt.status_revisao = 'pendente'
    """, (condominio_id,))
    candidatos = cursor.fetchall()

    por_tmp_id: dict = {}
    for c in candidatos:
        por_tmp_id.setdefault(c['tmp_id'], []).append(c)

    def _norm(v):
        return (v or '').strip().lower()

    resolvidos = []
    for tmp_id, lista in por_tmp_id.items():
        if len(lista) == 1:
            resolvidos.append(lista[0])
            continue
        # Nome ambíguo (mais de 1 morador) — só conta como duplicado se
        # achar exatamente a mesma unidade (apto+bloco) entre os candidatos.
        exato = next(
            (c for c in lista
             if _norm(c['apto_atual']) == _norm(c['apto_novo'])
             and _norm(c['bloco_atual']) == _norm(c['bloco_novo'])),
            None
        )
        if exato:
            resolvidos.append(exato)
        # Senão: nome bate com várias unidades diferentes, mas nenhuma é
        # exatamente esta — trata como pessoa nova naquela unidade, não
        # duplicado. Não adiciona a `resolvidos` de propósito.

    return resolvidos


# ========================================
# Rotas
# ========================================

# ── POST /painel/moradores/importar-tmp ─────────────────────────────────────
@router.post("/moradores/importar-tmp")
async def importar_para_tmp(
    data: ImportarTmpRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Recebe lote de moradores do frontend e insere em moradores_tmp.
    Limpa registros pendentes anteriores do mesmo condomínio antes de inserir.
    O condominio_id é validado contra o login do usuário.
    """
    if not data.moradores:
        raise HTTPException(status_code=400, detail="Nenhum morador enviado")

    # Todos do lote devem ser do mesmo condomínio do usuário logado
    condominio_id = data.moradores[0].condominio_id
    verificar_acesso_condominio(current_user, condominio_id)

    # Nome do operador vem do token, nunca do frontend
    operador_nome = current_user.get('nome') or current_user.get('email', '')

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:

            # Limpar pendentes anteriores do condomínio para evitar duplicação
            cursor.execute("""
                DELETE FROM moradores_tmp
                WHERE condominio_id = %s AND status_revisao = 'pendente'
            """, (condominio_id,))

            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"🧹 Limpou {deleted} registros pendentes anteriores do condomínio {condominio_id}")

            # Inserir novo lote
            total_inseridos = 0
            for m in data.moradores:
                # Segurança: forçar condominio_id do token, ignorar o que veio no body
                cursor.execute("""
                    INSERT INTO moradores_tmp
                        (nome, apartamento, bloco, telefone, email,
                         condominio_id, condominio_nome, importado_por, status_revisao)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pendente')
                """, (
                    m.nome,
                    m.apartamento,
                    m.bloco or None,
                    m.telefone or None,
                    m.email or None,
                    condominio_id,          # sempre do token
                    m.condominio_nome,
                    operador_nome,          # sempre do token
                ))
                total_inseridos += 1

            conn.commit()

        logger.info(f"✅ {total_inseridos} moradores inseridos em tmp pelo usuário {operador_nome} (cond={condominio_id})")

        return {
            "ok": True,
            "total_inseridos": total_inseridos,
            "message": f"{total_inseridos} registros enviados para revisão"
        }

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Erro ao inserir em moradores_tmp: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao salvar staging: {str(e)}")
    finally:
        conn.close()


# ── GET /painel/moradores/tmp ────────────────────────────────────────────────
@router.get("/moradores/tmp")
async def listar_moradores_tmp(
    condominio_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Retorna registros pendentes e rejeitados de moradores_tmp
    para o condomínio do usuário logado.
    """
    verificar_acesso_condominio(current_user, condominio_id)

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, nome, apartamento, bloco, telefone, email,
                       status_revisao, obs_revisao, importado_por,
                       data_importacao
                FROM moradores_tmp
                WHERE condominio_id = %s
                  AND status_revisao IN ('pendente', 'rejeitado')
                ORDER BY apartamento + 0, apartamento, nome
            """, (condominio_id,))

            rows = cursor.fetchall()

        return [
            {
                "id":             r['id'],
                "nome":           r['nome'],
                "apartamento":    r['apartamento'],
                "bloco":          r['bloco'] or '',
                "telefone":       r['telefone'] or '',
                "email":          r['email'] or '',
                "status_revisao": r['status_revisao'],
                "obs_revisao":    r['obs_revisao'] or '',
                "importado_por":  r['importado_por'] or '',
                "data_importacao": str(r['data_importacao']) if r.get('data_importacao') else None,
            }
            for r in rows
        ]

    except Exception as e:
        logger.error(f"❌ Erro ao listar moradores_tmp: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao buscar staging: {str(e)}")
    finally:
        conn.close()


# ── PATCH /painel/moradores/tmp/{id} ────────────────────────────────────────
@router.patch("/moradores/tmp/{registro_id}")
async def atualizar_status_tmp(
    registro_id: int,
    data: AtualizarStatusRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Atualiza status de um registro em moradores_tmp.
    Permite: 'pendente' (restaurar) ou 'rejeitado'.
    Valida que o registro pertence ao condomínio do usuário.
    """
    if data.status_revisao not in ('pendente', 'rejeitado'):
        raise HTTPException(status_code=400, detail="Status inválido. Use 'pendente' ou 'rejeitado'")

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:

            # Buscar o registro para validar condomínio
            cursor.execute(
                "SELECT id, condominio_id, nome FROM moradores_tmp WHERE id = %s",
                (registro_id,)
            )
            registro = cursor.fetchone()

            if not registro:
                raise HTTPException(status_code=404, detail="Registro não encontrado")

            verificar_acesso_condominio(current_user, registro['condominio_id'])

            cursor.execute("""
                UPDATE moradores_tmp
                SET status_revisao = %s, obs_revisao = %s
                WHERE id = %s
            """, (data.status_revisao, data.obs_revisao or '', registro_id))

            conn.commit()

        logger.info(
            f"✅ moradores_tmp id={registro_id} ({registro['nome']}) "
            f"→ {data.status_revisao} por {current_user.get('email')}"
        )

        return {"ok": True, "id": registro_id, "status_revisao": data.status_revisao}

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Erro ao atualizar moradores_tmp id={registro_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar registro: {str(e)}")
    finally:
        conn.close()


# ── GET /painel/moradores/tmp/duplicados ─────────────────────────────────────
@router.get("/moradores/tmp/duplicados")
async def verificar_duplicados(
    condominio_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Lista os registros 'pendente' de moradores_tmp cujo NOME já existe em
    moradores (ativo=1) no mesmo condomínio — usado pelo front pra perguntar
    se deve atualizar apartamento/bloco/telefone de quem já está cadastrado
    antes de confirmar a importação.
    """
    verificar_acesso_condominio(current_user, condominio_id)

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            duplicados = _resolver_duplicados_pendentes(cursor, condominio_id)

        return {
            "total": len(duplicados),
            "duplicados": [
                {
                    "nome": d["nome"],
                    "apartamento_novo": d["apto_novo"],
                    "bloco_novo": d["bloco_novo"] or "",
                    "telefone_novo": d["telefone_novo"] or "",
                    "apartamento_atual": d["apto_atual"],
                    "bloco_atual": d["bloco_atual"] or "",
                }
                for d in duplicados
            ],
        }
    except Exception as e:
        logger.error(f"❌ Erro ao verificar duplicados: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao verificar duplicados: {str(e)}")
    finally:
        conn.close()


# ── POST /painel/moradores/confirmar-importacao ──────────────────────────────
@router.post("/moradores/confirmar-importacao")
async def confirmar_importacao(
    data: ConfirmarImportacaoRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Move os registros 'pendente' de moradores_tmp para a tabela operacional moradores.
    Marca-os como 'aprovado' na tmp após a cópia.
    Só roles sindico e admin_sistema podem confirmar.
    """
    role = current_user.get('role', '')
    if role not in ('admin_sistema', 'sindico', 'operador'):
        raise HTTPException(status_code=403, detail="Sem permissão para confirmar importação")

    verificar_acesso_condominio(current_user, data.condominio_id)

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:

            # Contar pendentes antes de mover
            cursor.execute("""
                SELECT COUNT(*) as total
                FROM moradores_tmp
                WHERE condominio_id = %s AND status_revisao = 'pendente'
            """, (data.condominio_id,))
            total_pendentes = cursor.fetchone()['total']

            if total_pendentes == 0:
                raise HTTPException(status_code=400, detail="Nenhum registro pendente para confirmar")

            # Detectar duplicados: mesmo nome (e, se o nome for ambíguo — mais
            # de 1 morador com esse nome —, também mesma unidade) já
            # cadastrado e ativo no condomínio. Ver _resolver_duplicados_pendentes.
            duplicados = _resolver_duplicados_pendentes(cursor, data.condominio_id)
            ids_duplicados_tmp = [d['tmp_id'] for d in duplicados]

            total_atualizados = 0
            total_ignorados = 0

            if duplicados:
                if data.atualizar_existentes:
                    for d in duplicados:
                        cursor.execute("""
                            UPDATE moradores
                            SET apartamento = %s, bloco = %s, telefone = %s
                            WHERE id = %s
                        """, (d['apto_novo'], d['bloco_novo'], d['telefone_novo'], d['morador_id']))
                    total_atualizados = len(duplicados)
                else:
                    total_ignorados = len(duplicados)

            # 2026-09-30: bloco padronizado antes da cópia (app/services/blocos.py)
            try:
                from app.services.blocos import normalizar_tmp_importacao
                normalizar_tmp_importacao(data.condominio_id)
            except Exception as _e:
                logger.warning(f"[BLOCO] padronização da importação falhou: {_e}")

            # Copiar só os NÃO-duplicados → moradores
            if ids_duplicados_tmp:
                placeholders = ','.join(['%s'] * len(ids_duplicados_tmp))
                cursor.execute(f"""
                    INSERT INTO moradores
                        (nome, apartamento, bloco, telefone, email,
                         condominio_id, condominio_nome, ativo)
                    SELECT
                        nome, apartamento, bloco, telefone, email,
                        condominio_id, condominio_nome, 1
                    FROM moradores_tmp
                    WHERE condominio_id = %s AND status_revisao = 'pendente'
                      AND id NOT IN ({placeholders})
                """, [data.condominio_id] + ids_duplicados_tmp)
            else:
                cursor.execute("""
                    INSERT INTO moradores
                        (nome, apartamento, bloco, telefone, email,
                         condominio_id, condominio_nome, ativo)
                    SELECT
                        nome, apartamento, bloco, telefone, email,
                        condominio_id, condominio_nome, 1
                    FROM moradores_tmp
                    WHERE condominio_id = %s AND status_revisao = 'pendente'
                """, (data.condominio_id,))

            total_importados = cursor.rowcount

            # Marcar TODOS os pendentes como aprovados na tmp (novos, atualizados
            # e ignorados — a decisão já foi tomada, não ficam mais "pendente")
            cursor.execute("""
                UPDATE moradores_tmp
                SET status_revisao = 'aprovado'
                WHERE condominio_id = %s AND status_revisao = 'pendente'
            """, (data.condominio_id,))

            conn.commit()

        logger.info(
            f"✅ Importação confirmada: {total_importados} novo(s), {total_atualizados} atualizado(s), "
            f"{total_ignorados} ignorado(s) (cond={data.condominio_id}) por {current_user.get('email')}"
        )

        return {
            "ok": True,
            "total_importados": total_importados,
            "total_atualizados": total_atualizados,
            "total_ignorados": total_ignorados,
            "message": f"{total_importados} novo(s), {total_atualizados} atualizado(s), {total_ignorados} ignorado(s)"
        }

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Erro ao confirmar importação cond={data.condominio_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao confirmar importação: {str(e)}")
    finally:
        conn.close()


# ── GET /painel/moradores/tmp/historico ─────────────────────────────────────
@router.get("/moradores/tmp/historico")
async def historico_importacoes(
    condominio_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Retorna registros já aprovados em moradores_tmp (histórico de importações).
    """
    verificar_acesso_condominio(current_user, condominio_id)

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT
                    importado_por,
                    DATE(data_importacao) as data,
                    COUNT(*) as total,
                    SUM(status_revisao = 'aprovado')  as aprovados,
                    SUM(status_revisao = 'rejeitado') as rejeitados
                FROM moradores_tmp
                WHERE condominio_id = %s
                GROUP BY importado_por, DATE(data_importacao)
                ORDER BY data_importacao DESC
                LIMIT 20
            """, (condominio_id,))

            rows = cursor.fetchall()

        return [
            {
                "importado_por": r['importado_por'] or '—',
                "data":          str(r['data']),
                "total":         r['total'],
                "aprovados":     r['aprovados'],
                "rejeitados":    r['rejeitados'],
            }
            for r in rows
        ]

    except Exception as e:
        logger.error(f"❌ Erro ao buscar histórico tmp: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao buscar histórico: {str(e)}")
    finally:
        conn.close()
