# ================================================================================
# ALTERAÇÃO 2026-09-27: situacao_assinatura em GET /condominios (filtro por status no financeiro)
# ALTERAÇÃO 2026-09-27: padronização 7 status — situacao na lista do financeiro; avisos = não convertido/cancelado
# ALTERAÇÃO 2026-09-27: régua de cobrança — GET /regua/avisos (popup do financeiro)
#  PATH: backend/financeiro/financeiro_condominios.py
#  DESCRIPTION: Endpoints auxiliares para o módulo financeiro
#  VERSÃO: 2.1.0 - Com correção de campos inteiros
# ================================================================================

from app.services.protecao_financeiro import nome_usuario_atual  # 2026-10-04: colaborador logado
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.database import get_db
from app.models.condominio import Condominio

import httpx
import logging
import os

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/condominios")
async def listar_condominios(
    ativo: Optional[bool] = None,
    busca: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db)
):
    query = db.query(Condominio)

    if ativo is not None:
        try:
            query = query.filter(Condominio.ativo == ativo)
        except:
            pass

    if busca:
        query = query.filter(Condominio.nome.ilike(f"%{busca}%"))

    try:
        rows = db.execute(text("""
            SELECT c.id, c.nome, c.cnpj, c.email, c.email_financeiro, c.telefone,
                   c.endereco, c.cidade, c.estado, c.sindico, c.ativo, c.total_apartamentos,
                   c.plano_selecionado, c.valor_mensal_base, c.valor_plano_final,
                   c.validade_ate, c.cobranca_responsavel, c.cobranca_email, c.cobranca_whats,
                   cc.status AS status_contato, c.situacao_assinatura
            FROM condominios c
            LEFT JOIN contato_condominios cc ON cc.cnpj = c.cnpj
            ORDER BY c.nome LIMIT :lim
        """), {"lim": limit}).fetchall()
        items = []
        for r in rows:
            items.append({
                "id": r[0], "nome": r[1], "cnpj": r[2],
                "email": r[3], "email_financeiro": r[4], "telefone": r[5],
                "endereco": r[6], "cidade": r[7], "estado": r[8], "sindico": r[9],
                "ativo": bool(r[10]), "total_apartamentos": r[11],
                "plano_selecionado": r[12],
                "valor_mensal_base": float(r[13]) if r[13] else None,
                "valor_plano_final": float(r[14]) if r[14] else None,
                "validade_ate": r[15].isoformat() if r[15] else None,
                "cobranca_responsavel": r[16], "cobranca_email": r[17], "cobranca_whats": r[18],
                "status_contato": r[19],
                "situacao_assinatura": r[20],
            })
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error(f"Erro: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/condominios-com-validade")
async def listar_condominios_com_validade(db: Session = Depends(get_db)):
    try:
        result = db.execute(text("""

            SELECT c.id, c.nome, c.email, c.email_financeiro, c.validade_ate,
                   DATEDIFF(c.validade_ate, CURDATE()) as dias_restantes,
                   c.total_apartamentos, c.plano_selecionado,
                   c.assinatura_status, c.situacao_assinatura, c.ativo
            FROM condominios c WHERE c.ativo = 1 ORDER BY c.nome


        """))
        items = []
        for row in result.fetchall():
            items.append({
                "id": row.id, "nome": row.nome, "email": row.email,
                "email_financeiro": row.email_financeiro,
                "validade_ate": row.validade_ate.isoformat() if row.validade_ate else None,
                "dias_restantes": row.dias_restantes,
                "total_apartamentos": row.total_apartamentos,
                "plano_selecionado": row.plano_selecionado,
                "assinatura_status": row.assinatura_status,
                "situacao_assinatura": row.situacao_assinatura,
                "ativo": bool(row.ativo), 
            })
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error(f"Erro ao listar condomínios com validade: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/condominios/{id_condominio}")
async def obter_condominio(id_condominio: int, db: Session = Depends(get_db)):
    cond = db.query(Condominio).filter(Condominio.id == id_condominio).first()
    if not cond:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")
    return {
        "id": cond.id, "nome": cond.nome,
        "email": getattr(cond, 'email', None),
        "email_financeiro": getattr(cond, 'email_financeiro', None),
        "telefone": getattr(cond, 'telefone', None),
        "endereco": getattr(cond, 'endereco', None),
        "cnpj": getattr(cond, 'cnpj', None),
    }


@router.get("/condominios/{id_condominio}/completo")
async def obter_condominio_completo(id_condominio: int, db: Session = Depends(get_db)):
    try:
        result = db.execute(text("""
            SELECT id, nome, cnpj, email, email_financeiro, telefone,
                   endereco, numero, complemento, bairro, cidade, estado, cep,
                   sindico, total_apartamentos, observacoes, ativo, validade_ate,
                   cobranca_responsavel, cobranca_email, cobranca_whats
            FROM condominios WHERE id = :id_cond
        """), {"id_cond": id_condominio})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")
        return {
            "condominio": {
                "id": row.id, "nome": row.nome, "cnpj": row.cnpj,
                "email": row.email, "email_financeiro": row.email_financeiro,
                "telefone": row.telefone, "endereco": row.endereco,
                "numero": row.numero, "complemento": row.complemento,
                "bairro": row.bairro, "cidade": row.cidade,
                "estado": row.estado, "cep": row.cep, "sindico": row.sindico,
                "total_apartamentos": row.total_apartamentos,
                "observacoes": row.observacoes,
                "ativo": row.ativo if row.ativo is not None else True,
                "validade_ate": row.validade_ate.isoformat() if row.validade_ate else None,
                "cobranca_responsavel": row.cobranca_responsavel,
                "cobranca_email": row.cobranca_email,
                "cobranca_whats": row.cobranca_whats,
            },
            "assinatura": None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter condomínio completo: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/condominios/{id_condominio}")
async def atualizar_condominio(id_condominio: int, dados: dict, db: Session = Depends(get_db)):
    cond = db.query(Condominio).filter(Condominio.id == id_condominio).first()
    if not cond:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")

    try:
        # Converter strings vazias para None em campos inteiros
        if 'total_apartamentos' in dados:
            val = dados['total_apartamentos']
            if val == '' or val is None:
                dados.pop('total_apartamentos')
            else:
                try:
                    dados['total_apartamentos'] = int(val)
                except (ValueError, TypeError):
                    dados.pop('total_apartamentos')

        # Campos ORM
        campos_orm = ['nome', 'email', 'telefone', 'sindico', 'total_apartamentos', 'observacoes', 'ativo']
        for campo in campos_orm:
            if campo in dados and hasattr(cond, campo):
                valor = dados[campo]
                if valor == '' and campo in ['sindico', 'observacoes', 'telefone']:
                    continue  # Pula campos vazios que não aceitam NULL
                    valor = None
                setattr(cond, campo, valor)
        db.commit()

        # Campos SQL direto

        campos_sql = {
            'email_financeiro': dados.get('email_financeiro'),
            'cobranca_responsavel': dados.get('cobranca_responsavel'),
            'cobranca_email': dados.get('cobranca_email'),
            'cobranca_whats': dados.get('cobranca_whats'),
            'validade_ate': dados.get('validade_ate'),
        }
        campos_para_atualizar = {}
        for k, v in campos_sql.items():
            if k in dados:
                campos_para_atualizar[k] = v if v else None

        if campos_para_atualizar:
            set_parts = []
            params = {"id_cond": id_condominio}
            for campo, valor in campos_para_atualizar.items():
                set_parts.append(f"{campo} = :{campo}")
                params[campo] = valor
            if set_parts:
                sql = f"UPDATE condominios SET {', '.join(set_parts)} WHERE id = :id_cond"
                db.execute(text(sql), params)
                db.commit()

        logger.info(f"Condomínio {id_condominio} atualizado: {dados}")

        result = db.execute(text("SELECT nome, email, email_financeiro, telefone FROM condominios WHERE id = :id"), {"id": id_condominio})
        row = result.fetchone()

        return {
            "success": True,
            "message": "Condomínio atualizado com sucesso",
            "data": {
                "id": id_condominio,
                "nome": row.nome if row else cond.nome,
                "email": row.email if row else None,
                "email_financeiro": row.email_financeiro if row else None,
                "telefone": row.telefone if row else None
            }
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao atualizar condomínio: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar: {str(e)}")


# WHATSAPP
ZAPI_INSTANCE_ID = os.getenv("ZAPI_INSTANCE_ID", "")
ZAPI_TOKEN = os.getenv("ZAPI_TOKEN", "")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")
ZAPI_BASE_URL = os.getenv("ZAPI_BASE_URL", "https://api.z-api.io")


@router.post("/whatsapp/enviar")
async def enviar_whatsapp(dados: dict, db: Session = Depends(get_db)):
    telefone = dados.get("telefone", "")
    mensagem = dados.get("mensagem", "")
    if not telefone:
        raise HTTPException(status_code=400, detail="Telefone é obrigatório")
    if not mensagem:
        raise HTTPException(status_code=400, detail="Mensagem é obrigatória")
    telefone_limpo = ''.join(filter(str.isdigit, telefone))
    if len(telefone_limpo) <= 11:
        telefone_limpo = "55" + telefone_limpo
    if not ZAPI_INSTANCE_ID or not ZAPI_TOKEN:
        return {"success": False, "message": "Z-API não configurado", "whatsapp_web_url": f"https://wa.me/{telefone_limpo}?text={mensagem}"}
    try:
        url = f"{ZAPI_BASE_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
        payload = {"phone": telefone_limpo, "message": mensagem}
        headers = {"Content-Type": "application/json", "Client-Token": ZAPI_CLIENT_TOKEN}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                return {"success": True, "message": "Mensagem enviada", "telefone": telefone_limpo}
            return {"success": False, "message": response.text, "whatsapp_web_url": f"https://wa.me/{telefone_limpo}?text={mensagem}"}
    except Exception as e:
        return {"success": False, "message": str(e), "whatsapp_web_url": f"https://wa.me/{telefone_limpo}?text={mensagem}"}


@router.get("/whatsapp/status")
async def status_whatsapp():
    if not ZAPI_INSTANCE_ID or not ZAPI_TOKEN:
        return {"connected": False, "message": "Z-API não configurado"}
    try:
        url = f"{ZAPI_BASE_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/status"
        headers = {"Client-Token": ZAPI_CLIENT_TOKEN}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                result = response.json()
                return {"connected": result.get("connected", False), "message": "Conectado" if result.get("connected") else "Desconectado"}
            return {"connected": False, "message": f"Erro: {response.status_code}"}
    except Exception as e:
        return {"connected": False, "message": str(e)}


@router.get("/condominios/{id_condominio}/dados-cobranca")
async def obter_dados_cobranca(id_condominio: int, db: Session = Depends(get_db)):
    """Retorna dados para cobrança do condomínio"""
    try:
        result = db.execute(text("""
            SELECT email, email_financeiro, cobranca_email, 
                   cobranca_whats, cobranca_responsavel, telefone
            FROM condominios WHERE id = :id_cond
        """), {"id_cond": id_condominio})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")
        return {
            "email": row.email,
            "email_financeiro": row.email_financeiro,
            "cobranca_email": row.cobranca_email,
            "cobranca_whats": row.cobranca_whats,
            "cobranca_responsavel": row.cobranca_responsavel,
            "telefone": row.telefone
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter dados cobrança: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/condominios/{id_condominio}/dados-cobranca")
async def obter_dados_cobranca(id_condominio: int, db: Session = Depends(get_db)):
    """Retorna dados para cobrança do condomínio"""
    try:
        result = db.execute(text("""
            SELECT email, email_financeiro, cobranca_email, 
                   cobranca_whats, cobranca_responsavel, telefone
            FROM condominios WHERE id = :id_cond
        """), {"id_cond": id_condominio})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")
        return {
            "email": row.email,
            "email_financeiro": row.email_financeiro,
            "cobranca_email": row.cobranca_email,
            "cobranca_whats": row.cobranca_whats,
            "cobranca_responsavel": row.cobranca_responsavel,
            "telefone": row.telefone
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter dados cobrança: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Mensagens padronizadas ──────────────────────────────────────────────────

@router.get("/mensagens-condominios")
async def listar_mensagens(
    categoria: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Lista mensagens padronizadas para envio WhatsApp."""
    try:
        query = "SELECT id, titulo, mensagem, categoria FROM mensagens_condominios WHERE ativo=1"
        params = {}
        if categoria:
            query += " AND categoria = :cat"
            params["cat"] = categoria
        query += " ORDER BY categoria, titulo"
        rows = db.execute(text(query), params).fetchall()
        return {"items": [{"id": r[0], "titulo": r[1], "mensagem": r[2], "categoria": r[3]} for r in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mensagens-condominios/enviar")
async def enviar_mensagem_whatsapp(
    payload: dict,
    db: Session = Depends(get_db)
):
    """
    Envia mensagem WhatsApp para um condomínio.
    payload: { condominio_id, whatsapp, mensagem }
    """
    import httpx, os
    try:
        whatsapp  = (payload.get("whatsapp") or "").replace(" ","").replace("-","").replace("(","").replace(")","")
        if not whatsapp.startswith("55"):
            whatsapp = "55" + whatsapp
        mensagem  = payload.get("mensagem", "")
        if not whatsapp or not mensagem:
            raise HTTPException(status_code=422, detail="whatsapp e mensagem são obrigatórios")

        zapi_instance = os.getenv("ZAPI_INSTANCE_ID", "")
        zapi_token    = os.getenv("ZAPI_TOKEN", "")
        zapi_url      = os.getenv("ZAPI_API_URL", "http://191.252.221.192:8080")
        client_token  = os.getenv("ZAPI_CLIENT_TOKEN", "")

        url = f"{zapi_url}/instances/{zapi_instance}/token/{zapi_token}/send-text"
        headers = {"Client-Token": client_token, "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json={"phone": whatsapp, "message": mensagem}, headers=headers)

        if r.status_code == 200:
            return {"success": True, "message": "Mensagem enviada!"}
        else:
            return {"success": False, "message": f"Erro Z-API: {r.text[:200]}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Operadores (usuários) do condomínio ──────────────────────────────────────

@router.get("/condominios/{id_condominio}/operadores")
async def listar_operadores_condominio(
    id_condominio: int,
    db: Session = Depends(get_db)
):
    """Lista operadores da tabela mobile_operadores para um condomínio."""
    try:
        rows = db.execute(text("""
            SELECT id, nome, email, telefone, role, ativo, ultimo_login, criado_em
            FROM mobile_operadores
            WHERE condominio_id = :cid
            ORDER BY nome
        """), {"cid": id_condominio}).fetchall()

        return {"items": [
            {
                "id": r[0], "nome": r[1], "email": r[2], "telefone": r[3],
                "role": r[4], "ativo": bool(r[5]),
                "ultimo_login": str(r[6]) if r[6] else None,
                "criado_em": str(r[7]) if r[7] else None,
            }
            for r in rows
        ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/condominios/{id_condominio}/operadores")
async def criar_operador_condominio(
    id_condominio: int,
    payload: dict,
    db: Session = Depends(get_db)
):
    """Cria um operador na tabela mobile_operadores."""
    import bcrypt
    try:
        nome     = payload.get("nome", "").strip()
        email    = payload.get("email", "").strip()
        senha    = payload.get("password", "").strip()
        role     = payload.get("role", "operador")

        if not nome or not email or not senha:
            raise HTTPException(status_code=422, detail="nome, email e senha são obrigatórios")

        senha_hash = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()

        db.execute(text("""
            INSERT INTO mobile_operadores (nome, email, senha_hash, condominio_id, role, ativo)
            VALUES (:nome, :email, :senha_hash, :cid, :role, 1)
        """), {"nome": nome, "email": email, "senha_hash": senha_hash, "cid": id_condominio, "role": role})
        db.commit()
        return {"success": True, "message": "Operador criado com sucesso"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/condominios/{id_condominio}/operadores/{operador_id}")
async def atualizar_operador(
    id_condominio: int,
    operador_id: int,
    payload: dict,
    db: Session = Depends(get_db)
):
    """Atualiza senha ou role de um operador."""
    import bcrypt
    try:
        updates = []
        params  = {"id": operador_id, "cid": id_condominio}

        if "password" in payload and payload["password"]:
            senha_hash = bcrypt.hashpw(payload["password"].encode(), bcrypt.gensalt()).decode()
            updates.append("senha_hash = :senha_hash")
            params["senha_hash"] = senha_hash

        if "role" in payload:
            updates.append("role = :role")
            params["role"] = payload["role"]

        if not updates:
            raise HTTPException(status_code=422, detail="Nada para atualizar")

        db.execute(text(f"""
            UPDATE mobile_operadores SET {', '.join(updates)}
            WHERE id = :id AND condominio_id = :cid
        """), params)
        db.commit()
        return {"success": True, "message": "Operador atualizado"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/condominios/{id_condominio}/operadores/{operador_id}")
async def deletar_operador(
    id_condominio: int,
    operador_id: int,
    db: Session = Depends(get_db)
):
    """Remove um operador."""
    try:
        db.execute(text("""
            DELETE FROM mobile_operadores
            WHERE id = :id AND condominio_id = :cid
        """), {"id": operador_id, "cid": id_condominio})
        db.commit()
        return {"success": True, "message": "Operador removido"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ── Stats de encomendas por condomínio ───────────────────────────────────────

@router.get("/condominios/{id_condominio}/encomendas/stats")
async def stats_encomendas_condominio(
    id_condominio: int,
    db: Session = Depends(get_db)
):
    """Stats de encomendas para um condomínio específico."""
    try:
        def q(sql, p={}): return db.execute(text(sql), {"c": id_condominio, **p}).scalar() or 0
        def ult(col):  # último uso do app (2026-09-28)
            v = db.execute(text(f"SELECT MAX({col}) FROM encomendas WHERE condominio_id=:c"), {"c": id_condominio}).scalar()
            return v.isoformat() if v else None
        return {
            "total_encomendas":      q("SELECT COUNT(*) FROM encomendas WHERE condominio_id=:c AND data_recebimento >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)"),
            "pendentes":             q("SELECT COUNT(*) FROM encomendas WHERE condominio_id=:c AND status='pendente'"),
            "entregues":             q("SELECT COUNT(*) FROM encomendas WHERE condominio_id=:c AND status='entregue'"),
            "recebidas_hoje":        q("SELECT COUNT(*) FROM encomendas WHERE condominio_id=:c AND DATE(data_recebimento)=CURDATE()"),
            "recebidas_ontem":       q("SELECT COUNT(*) FROM encomendas WHERE condominio_id=:c AND DATE(data_recebimento)=DATE_SUB(CURDATE(),INTERVAL 1 DAY)"),
            "recebidas_mes":         q("SELECT COUNT(*) FROM encomendas WHERE condominio_id=:c AND YEAR(data_recebimento)=YEAR(CURDATE()) AND MONTH(data_recebimento)=MONTH(CURDATE())"),
            "encomendas_atrasadas":  q("SELECT COUNT(*) FROM encomendas WHERE condominio_id=:c AND status='pendente' AND DATEDIFF(CURDATE(),DATE(data_recebimento))>3"),
            "ultimo_recebimento":    ult("data_recebimento"),
            "ultima_entrega":        ult("data_entrega"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/condominios/{id_condominio}/encomendas/recentes")
async def encomendas_recentes_condominio(
    id_condominio: int,
    limit: int = 8,
    db: Session = Depends(get_db)
):
    """Últimas encomendas de um condomínio."""
    try:
        rows = db.execute(text("""
            SELECT id, nome_destinatario, apartamento, bloco,
                   status, data_recebimento, data_entrega, remetente
            FROM encomendas
            WHERE condominio_id = :c
            ORDER BY data_recebimento DESC
            LIMIT :lim
        """), {"c": id_condominio, "lim": limit}).fetchall()

        return {"items": [
            {
                "id": r[0], "nome_destinatario": r[1], "apartamento": r[2],
                "bloco": r[3], "status": r[4],
                "data_recebimento": r[5].isoformat() if r[5] else None,
                "data_entrega": r[6].isoformat() if r[6] else None,
                "remetente": r[7],
            }
            for r in rows
        ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Enviar credenciais para operador ─────────────────────────────────────────

@router.post("/condominios/{id_condominio}/operadores/{operador_id}/enviar-credenciais")
async def enviar_credenciais_operador(
    id_condominio: int,
    operador_id: int,
    payload: dict,
    db: Session = Depends(get_db)
):
    """Envia nova senha para operador via WhatsApp e/ou Email."""
    import httpx, os
    from app.database import get_db

    canal  = payload.get("canal", "whatsapp")  # whatsapp | email | ambos
    senha  = payload.get("senha", "")

    # buscar dados do operador
    row = db.execute(text("""
        SELECT o.nome, o.email, o.telefone, c.nome as cond_nome
        FROM mobile_operadores o
        JOIN condominios c ON c.id = o.condominio_id
        WHERE o.id = :oid AND o.condominio_id = :cid
    """), {"oid": operador_id, "cid": id_condominio}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Operador não encontrado")

    nome, email, telefone, cond_nome = row[0], row[1], row[2], row[3]
    canais = []
    erros  = []

    PAINEL_URL = "https://admin.econdominio.com.br"
    MOBILE_URL = "https://portaria.econdominio.com.br"

    msg_wpp = (
        f"Olá, *{nome}*! 👋\n\n"
        f"Suas credenciais de acesso ao *eCondomínio* foram atualizadas.\n\n"
        f"🔑 *Nova senha:* `{senha}`\n"
        f"📧 *Login:* {email}\n\n"
        f"🖥️ *Painel Admin:* {PAINEL_URL}\n"
        f"📱 *App Portaria:* {MOBILE_URL}\n\n"
        f"Altere sua senha no primeiro acesso. 😊"
    )

    # WhatsApp
    if canal in ("whatsapp", "ambos") and telefone:
        try:
            tel = ''.join(c for c in telefone if c.isdigit())
            if not tel.startswith("55"): tel = "55" + tel
            zapi_url   = os.getenv("ZAPI_API_URL", "http://191.252.221.192:8080")
            zapi_inst  = os.getenv("ZAPI_INSTANCE_ID", "")
            zapi_token = os.getenv("ZAPI_TOKEN", "")
            cli_token  = os.getenv("ZAPI_CLIENT_TOKEN", "")
            url = f"{zapi_url}/instances/{zapi_inst}/token/{zapi_token}/send-text"
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(url, json={"phone": tel, "message": msg_wpp},
                    headers={"Client-Token": cli_token, "Content-Type": "application/json"})
            if r.status_code == 200: canais.append("WhatsApp")
            else: erros.append(f"WhatsApp erro: {r.text[:100]}")
        except Exception as e:
            erros.append(f"WhatsApp: {str(e)}")

    # Email
    if canal in ("email", "ambos") and email:
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            smtp_server = os.getenv("EMAIL_SMTP_SERVER", "smtp-relay.brevo.com")
            smtp_port   = int(os.getenv("EMAIL_SMTP_PORT", "587"))
            smtp_user   = os.getenv("EMAIL_SMTP_USERNAME", "")
            smtp_pass   = os.getenv("EMAIL_SMTP_PASSWORD", "")
            from_addr   = os.getenv("EMAIL_FROM_ADDRESS", "contato@econdominio.com.br")
            from_name   = os.getenv("EMAIL_FROM_NAME", "eCondominio")

            corpo = f"""
            <div style="font-family:Arial,sans-serif;max-width:500px;margin:auto;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden">
              <div style="background:linear-gradient(135deg,#1d4ed8,#7c3aed);padding:24px;text-align:center">
                <h1 style="color:white;margin:0;font-size:20px">🔑 Credenciais Atualizadas</h1>
                <p style="color:#bfdbfe;margin:4px 0 0;font-size:13px">{cond_nome}</p>
              </div>
              <div style="padding:24px;background:white">
                <p style="color:#374151">Olá, <strong>{nome}</strong>!</p>
                <p style="color:#475569">Suas credenciais de acesso ao <strong>eCondomínio</strong> foram atualizadas:</p>
                <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin:16px 0">
                  <div style="margin-bottom:8px"><span style="color:#6b7280;font-size:13px">Login:</span><br><strong style="font-family:monospace">{email}</strong></div>
                  <div><span style="color:#6b7280;font-size:13px">Nova Senha:</span><br><strong style="font-family:monospace;background:#fef3c7;padding:2px 8px;border-radius:4px;color:#92400e">{senha}</strong></div>
                </div>
                <div style="display:flex;gap:10px;margin-top:16px;flex-wrap:wrap">
                  <a href="{PAINEL_URL}" style="flex:1;min-width:120px;display:inline-block;background:#2563eb;color:white;text-align:center;padding:10px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:13px">🖥️ Painel Admin</a>
                  <a href="{MOBILE_URL}" style="flex:1;min-width:120px;display:inline-block;background:#7c3aed;color:white;text-align:center;padding:10px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:13px">📱 App Portaria</a>
                </div>
              </div>
            </div>"""

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"🔑 Suas credenciais eCondomínio — {cond_nome}"
            msg["From"]    = f"{from_name} <{from_addr}>"
            msg["To"]      = email
            msg.attach(MIMEText(corpo, "html", "utf-8"))

            with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as s:
                s.ehlo(); s.starttls(); s.ehlo()
                s.login(smtp_user, smtp_pass)
                s.sendmail(from_addr, [email], msg.as_string())
            canais.append("Email")
        except Exception as e:
            erros.append(f"Email: {str(e)}")

    if not canais and erros:
        raise HTTPException(status_code=500, detail=" | ".join(erros))

    return {"success": True, "canais": canais, "message": f"Enviado via {' e '.join(canais)}"}


# ── Enviar mensagem por Email ─────────────────────────────────────────────────

@router.post("/mensagens-condominios/enviar-email")
async def enviar_mensagem_email(
    payload: dict,
    db: Session = Depends(get_db)
):
    """Envia mensagem por email para um condomínio."""
    import smtplib, os
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    email   = payload.get("email", "").strip()
    assunto = payload.get("assunto", "Mensagem eCondomínio")
    mensagem = payload.get("mensagem", "")
    cond_id  = payload.get("condominio_id")

    if not email or not mensagem:
        raise HTTPException(status_code=422, detail="email e mensagem são obrigatórios")

    # buscar nome do condomínio
    nome_cond = ""
    if cond_id:
        try:
            r = db.execute(text("SELECT nome FROM condominios WHERE id=:id"), {"id": cond_id}).fetchone()
            if r: nome_cond = r[0]
        except: pass

    try:
        smtp_server = os.getenv("EMAIL_SMTP_SERVER", "smtp-relay.brevo.com")
        smtp_port   = int(os.getenv("EMAIL_SMTP_PORT", "587"))
        smtp_user   = os.getenv("EMAIL_SMTP_USERNAME", "")
        smtp_pass   = os.getenv("EMAIL_SMTP_PASSWORD", "")
        from_addr   = os.getenv("EMAIL_FROM_ADDRESS", "contato@econdominio.com.br")
        from_name   = os.getenv("EMAIL_FROM_NAME", "eCondominio")

        # converter \n em <br> para HTML
        msg_html = mensagem.replace("\n", "<br>").replace("*", "<strong>").replace("*", "</strong>")

        corpo = f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden">
          <div style="background:linear-gradient(135deg,#1d4ed8,#7c3aed);padding:24px;text-align:center">
            <h1 style="color:white;margin:0;font-size:20px">📬 {assunto}</h1>
            {f'<p style="color:#bfdbfe;margin:6px 0 0;font-size:13px">{nome_cond}</p>' if nome_cond else ''}
          </div>
          <div style="padding:24px;background:white;line-height:1.7;color:#374151;font-size:14px">
            {mensagem.replace(chr(10), '<br>')}
          </div>
          <div style="background:#f9fafb;padding:16px;text-align:center;border-top:1px solid #e5e7eb">
            <p style="color:#9ca3af;font-size:12px;margin:0">eCondomínio — Sistema de Gestão de Condomínios</p>
          </div>
        </div>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"]    = f"{from_name} <{from_addr}>"
        msg["To"]      = email
        msg.attach(MIMEText(corpo, "html", "utf-8"))

        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(smtp_user, smtp_pass)
            s.sendmail(from_addr, [email], msg.as_string())

        return {"success": True, "message": f"Email enviado para {email}"}
    except Exception as e:
        logger.error(f"Erro email: {e}")
        return {"success": False, "message": f"Erro ao enviar email: {str(e)[:100]}"}


# ── Ajustar validade manualmente ─────────────────────────────────────────────
@router.post("/condominios/{id_condominio}/ajustar-validade")
async def ajustar_validade(
    id_condominio: int,
    payload: dict,
    db: Session = Depends(get_db)
):
    """Ajusta manualmente a validade da assinatura de um condomínio."""
    from datetime import date, timedelta
    dias     = int(payload.get("dias", 0))
    motivo   = payload.get("motivo", "")
    operador = nome_usuario_atual(payload.get("operador", ""))  # 2026-10-04: usuário logado

    if dias == 0:
        raise HTTPException(status_code=400, detail="Informe valor diferente de zero.")

    cond = db.execute(
        text("SELECT id, nome, validade_ate FROM condominios WHERE id = :id"),
        {"id": id_condominio}
    ).fetchone()

    if not cond:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado.")

    validade_atual = cond.validade_ate or date.today()
    nova_validade  = validade_atual + timedelta(days=dias)

    db.execute(
        text("UPDATE condominios SET validade_ate = :v WHERE id = :id"),
        {"v": nova_validade.isoformat(), "id": id_condominio}
    )
    db.execute(
        text("UPDATE assinaturas SET validade_ate = :v, data_atualizacao = NOW() WHERE id_condominio = :id"),
        {"v": nova_validade.isoformat(), "id": id_condominio}
    )
    db.commit()

    logger.info(
        f"Validade ajustada: cond={id_condominio} ({cond.nome}) "
        f"{validade_atual} → {nova_validade} dias={dias} motivo={motivo} op={operador}"
    )

    return {
        "success":           True,
        "condominio_id":     id_condominio,
        "condominio":        cond.nome,
        "validade_anterior": validade_atual.isoformat(),
        "validade_nova":     nova_validade.isoformat(),
        "dias_ajustados":    dias,
        "motivo":            motivo,
        "operador":          operador,
    }


@router.get("/regua/avisos")
async def avisos_regua(dias: int = 3, db: Session = Depends(get_db)):
    """Condomínios fora do ar pela régua (teste vencido, em débito, desativado), gravados pelo
    job diário em condominios.situacao_assinatura. 'novos' = mudaram nos últimos `dias` dias."""
    from datetime import datetime, timedelta
    try:
        rows = db.execute(text("""
            SELECT id, nome, situacao_assinatura, situacao_atualizada_em, validade_ate,
                   cobranca_whats, telefone, sindico
            FROM condominios
            WHERE ativo = 1 AND situacao_assinatura IN ('nao_convertido', 'cancelado')
            ORDER BY situacao_atualizada_em DESC
        """)).fetchall()
    except Exception as e:
        logger.warning(f"avisos_regua indisponível (coluna situacao_assinatura ausente?): {e}")
        return {"novos": [], "lista": []}
    lista = [dict(r._mapping) for r in rows]
    limite = datetime.now() - timedelta(days=max(1, min(dias, 30)))
    novos = [c for c in lista if c["situacao_atualizada_em"] and c["situacao_atualizada_em"] >= limite]
    return {"novos": novos, "lista": lista}



# ══════════════════════════════════════════════════════════════════════════════
# CONTATOS COM O CONDOMÍNIO + AGENDA DE RETORNO (2026-09-28)
# Tabela condominio_contatos (liga em condominios.id). Usado pelo botão "Contato"
# do dashboard do condomínio e pelo bloco "Agendamentos de condomínios" da Agenda.
# ══════════════════════════════════════════════════════════════════════════════
from datetime import datetime as _dt
from fastapi import Request as _Request
from pydantic import BaseModel as _BaseModel
from app.services.protecao_financeiro import usuario_interno as _usuario_interno


class ContatoCondominioIn(_BaseModel):
    pessoa_contato: str
    resumo: str
    operador_nome: str
    data_contato: Optional[str] = None       # "AAAA-MM-DDTHH:MM" (padrão: agora)
    data_agendamento: Optional[str] = None   # preenchido = ficou agendado


class AgendamentoStatusIn(_BaseModel):
    status: str   # realizado | cancelado | pendente


def _data_hora(v: Optional[str], campo: str):
    if not v:
        return None
    try:
        return _dt.fromisoformat(v.replace("Z", ""))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{campo} inválida")


def _contato_dict(r):
    return {
        "id": r.id, "condominio_id": r.condominio_id, "operador_nome": r.operador_nome,
        "pessoa_contato": r.pessoa_contato, "resumo": r.resumo,
        "data_contato": r.data_contato.isoformat() if r.data_contato else None,
        "data_agendamento": r.data_agendamento.isoformat() if r.data_agendamento else None,
        "agendamento_status": r.agendamento_status,
    }


@router.get("/condominios/{id_condominio}/contatos-registro")
async def listar_contatos_condominio(id_condominio: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, condominio_id, operador_nome, pessoa_contato, resumo, data_contato,
               data_agendamento, agendamento_status
        FROM condominio_contatos WHERE condominio_id = :c
        ORDER BY data_contato DESC, id DESC
    """), {"c": id_condominio}).fetchall()
    return {"items": [_contato_dict(r) for r in rows]}


@router.post("/condominios/{id_condominio}/contatos-registro")
async def registrar_contato_condominio(id_condominio: int, dados: ContatoCondominioIn,
                                       db: Session = Depends(get_db),
                                       quem: dict = Depends(_usuario_interno)):
    if not db.execute(text("SELECT 1 FROM condominios WHERE id = :c"), {"c": id_condominio}).fetchone():
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")
    if not dados.pessoa_contato.strip() or not dados.resumo.strip():
        raise HTTPException(status_code=422, detail="Informe a pessoa contactada e o resumo")
    data_contato = _data_hora(dados.data_contato, "Data do contato") or _dt.now()
    agenda = _data_hora(dados.data_agendamento, "Data do agendamento")
    operador_id = int(quem["usuario"]) if quem.get("origem") == "financeiro" and str(quem.get("usuario", "")).isdigit() else None
    res = db.execute(text("""
        INSERT INTO condominio_contatos
            (condominio_id, operador_id, operador_nome, pessoa_contato, data_contato, resumo,
             data_agendamento, agendamento_status)
        VALUES (:c, :oid, :onome, :pessoa, :dc, :resumo, :ag, :st)
    """), {"c": id_condominio, "oid": operador_id, "onome": (quem.get("nome") or dados.operador_nome).strip()[:150],
           "pessoa": dados.pessoa_contato.strip()[:150], "dc": data_contato,
           "resumo": dados.resumo.strip(), "ag": agenda, "st": "pendente" if agenda else None})
    db.commit()
    return {"success": True, "id": res.lastrowid}


@router.get("/condominios-contatos/agenda")
async def agenda_contatos_condominios(
    status: str = Query("pendente"),            # pendente | realizado | cancelado | todos
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    where, p = ["cc.data_agendamento IS NOT NULL"], {}
    if status != "todos":
        where.append("cc.agendamento_status = :st"); p["st"] = status
    if data_inicio:
        where.append("DATE(cc.data_agendamento) >= :di"); p["di"] = data_inicio
    if data_fim:
        where.append("DATE(cc.data_agendamento) <= :df"); p["df"] = data_fim
    rows = db.execute(text(f"""
        SELECT cc.id, cc.condominio_id, cc.operador_nome, cc.pessoa_contato, cc.resumo,
               cc.data_contato, cc.data_agendamento, cc.agendamento_status,
               c.nome AS condominio_nome, c.cidade, c.situacao_assinatura
        FROM condominio_contatos cc
        JOIN condominios c ON c.id = cc.condominio_id
        WHERE {' AND '.join(where)}
        ORDER BY cc.data_agendamento ASC
        LIMIT 300
    """), p).fetchall()
    return {"items": [{**_contato_dict(r), "condominio_nome": r.condominio_nome, "cidade": r.cidade,
                       "situacao_assinatura": r.situacao_assinatura} for r in rows]}


@router.put("/condominios-contatos/{id_contato}/agendamento")
async def atualizar_agendamento_contato(id_contato: int, dados: AgendamentoStatusIn,
                                        db: Session = Depends(get_db)):
    if dados.status not in ("pendente", "realizado", "cancelado"):
        raise HTTPException(status_code=422, detail="Status inválido")
    n = db.execute(text("""
        UPDATE condominio_contatos SET agendamento_status = :st
        WHERE id = :id AND data_agendamento IS NOT NULL
    """), {"st": dados.status, "id": id_contato}).rowcount
    db.commit()
    if not n:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    return {"success": True}


# ══════════════════════════════════════════════════════════════════════════════
# LEADS DO SITE NO FINANCEIRO (2026-09-30)
# Mesma consulta/edição do admin (/painel/leads em admin/leads.py), com o login do
# financeiro (usuario_interno). Tabela `leads` (site /conheca + WhatsApp do funil).
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/leads-site")
async def leads_site(
    status: Optional[str] = Query(None),
    origem: Optional[str] = Query(None),
    utm_campaign: Optional[str] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    quem: dict = Depends(_usuario_interno),
):
    from admin.leads import listar_leads
    return await listar_leads(status=status, origem=origem, utm_campaign=utm_campaign,
                              data_inicio=data_inicio, data_fim=data_fim, page=page, limit=limit,
                              current_user={"role": "admin_sistema"})


@router.patch("/leads-site/{lead_id}")
async def atualizar_lead_site(lead_id: int, dados: dict, quem: dict = Depends(_usuario_interno)):
    from admin.leads import atualizar_lead, AtualizarLeadRequest
    return await atualizar_lead(lead_id, AtualizarLeadRequest(**dados), current_user={"role": "admin_sistema"})


# ─── 2026-10-02: conversa do lead e agendamentos de lead (Agenda) — usuário interno ─────
def _nome_interno(quem):
    return (quem or {}).get("nome") or (quem or {}).get("username") or (quem or {}).get("email") or "financeiro"


@router.get("/leads-site/{lead_id}/conversa")
async def conversa_lead_site(lead_id: int, quem: dict = Depends(_usuario_interno)):
    from app.services.leads_whatsapp import conversa
    return conversa(lead_id)


@router.get("/leads-site/{lead_id}/midia/{msg_id}")
async def midia_lead_site(lead_id: int, msg_id: str, quem: dict = Depends(_usuario_interno)):
    from app.services.leads_whatsapp import midia
    return midia(lead_id, msg_id)


@router.post("/leads-site/{lead_id}/responder")
async def responder_lead_site(lead_id: int, dados: dict, quem: dict = Depends(_usuario_interno)):
    from app.services.leads_whatsapp import responder
    return responder(lead_id, str(dados.get("mensagem") or ""), _nome_interno(quem))


@router.post("/leads-site/{lead_id}/agendar")
async def agendar_lead_site(lead_id: int, dados: dict, quem: dict = Depends(_usuario_interno)):
    from app.services.leads_whatsapp import agendar
    return agendar(lead_id, str(dados.get("data_agendamento") or ""), dados.get("anotacao"), _nome_interno(quem))


@router.get("/leads-agenda")
async def agenda_leads(status: str = Query("pendente"), quem: dict = Depends(_usuario_interno)):
    from app.services.leads_whatsapp import agenda
    return agenda(status)


@router.put("/leads-agenda/{ag_id}")
async def atualizar_agenda_lead(ag_id: int, dados: dict, quem: dict = Depends(_usuario_interno)):
    from app.services.leads_whatsapp import atualizar_agendamento
    return atualizar_agendamento(ag_id, str(dados.get("status") or ""), _nome_interno(quem))


@router.post("/leads-site")
async def criar_lead_site(dados: dict, quem: dict = Depends(_usuario_interno)):
    """2026-10-02: cadastro manual de lead pelo financeiro."""
    from app.services.leads_whatsapp import criar_manual
    return criar_manual(dados, _nome_interno(quem))


# ─── 2026-10-04: painel do colaborador (venda e suporte — SEM dados financeiros da empresa) ─────
def _qtd(db, sql, p=None):
    try:
        return int(db.execute(text(sql), p or {}).scalar() or 0)
    except Exception as e:
        logger.warning("painel colaborador: %s", e)
        return 0


def _linhas(db, sql, p=None):
    try:
        return [dict(r._mapping) for r in db.execute(text(sql), p or {})]
    except Exception as e:
        logger.warning("painel colaborador: %s", e)
        return []


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


@router.get("/colaborador/painel")
async def painel_colaborador(quem: dict = Depends(_usuario_interno), db: Session = Depends(get_db)):
    eu = quem.get("nome") or ""
    AGUARDANDO = """(l.whatsapp_chat_id IS NOT NULL AND COALESCE(
        (SELECT MAX(m.enviado_em) FROM leads_mensagens m WHERE m.chat_id = l.whatsapp_chat_id AND m.from_me = 0)
        > COALESCE((SELECT MAX(m.enviado_em) FROM leads_mensagens m WHERE m.chat_id = l.whatsapp_chat_id
                    AND m.from_me = 1 AND m.bot = 0), '1970-01-01'), 0))"""
    resumo = {
        "leads_novos": _qtd(db, "SELECT COUNT(*) FROM leads WHERE status = 'novo'"),
        "leads_aguardando_resposta": _qtd(db, f"SELECT COUNT(*) FROM leads l WHERE {AGUARDANDO}"),
        "prospects_interessados": _qtd(db, "SELECT COUNT(*) FROM marketing_leads WHERE status IN ('interessado','em_contato')"),
        "cadastros_site_pendentes": _qtd(db, "SELECT COUNT(*) FROM contato_condominios WHERE status IN ('pendente','a_recuperar')"),
        "teste_vencendo_7d": _qtd(db, """SELECT COUNT(*) FROM condominios WHERE ativo = 1
            AND situacao_assinatura IN ('em_teste','teste_estendido','cadastro_incompleto')
            AND validade_ate BETWEEN CURDATE() AND CURDATE() + INTERVAL 7 DAY"""),
        "cadastro_incompleto": _qtd(db, "SELECT COUNT(*) FROM condominios WHERE ativo = 1 AND situacao_assinatura = 'cadastro_incompleto'"),
    }
    # agenda: atrasados (até 15 dias), hoje e próximos 7 dias — todas as origens
    agenda = _linhas(db, """
        SELECT * FROM (
          SELECT 'lead' AS origem, a.id, a.data_agendamento AS quando, COALESCE(l.nome, 'Lead sem nome') AS quem,
                 a.anotacao AS assunto, a.operador_nome, '/financeiro/leads' AS link
            FROM leads_agendamentos a JOIN leads l ON l.id = a.lead_id WHERE a.status = 'pendente'
          UNION ALL
          SELECT 'condominio', cc.id, cc.data_agendamento, c.nome, cc.resumo, cc.operador_nome,
                 CONCAT('/financeiro/condominios/', cc.condominio_id, '/dashboard')
            FROM condominio_contatos cc JOIN condominios c ON c.id = cc.condominio_id
           WHERE cc.agendamento_status = 'pendente' AND cc.data_agendamento IS NOT NULL
          UNION ALL
          SELECT 'cadastro_site', ct.id, ct.data_agendamento, COALESCE(cs.nome_fantasia, cs.razao_social), ct.assunto,
                 ct.operador_nome, '/financeiro/agenda'
            FROM contatos_condominios ct JOIN contato_condominios cs ON cs.id = ct.id_condominio
           WHERE ct.data_agendamento IS NOT NULL
          UNION ALL
          SELECT 'marketing', mc.id, mc.data_agendamento, COALESCE(ml.nome_fantasia, ml.nome), mc.assunto,
                 mc.operador_nome, '/financeiro/marketing'
            FROM marketing_contatos mc JOIN marketing_leads ml ON ml.id = mc.lead_id
           WHERE mc.data_agendamento IS NOT NULL
        ) x
        WHERE quando BETWEEN NOW() - INTERVAL 15 DAY AND CURDATE() + INTERVAL 8 DAY
        ORDER BY quando ASC LIMIT 60""")
    for a in agenda:
        a["quando"] = _iso(a["quando"])
    leads = _linhas(db, f"""
        SELECT l.id, l.nome, l.whatsapp, l.cidade, l.status, l.ultimo_contato_em,
               (SELECT MAX(m.enviado_em) FROM leads_mensagens m WHERE m.chat_id = l.whatsapp_chat_id AND m.from_me = 0) AS ultima_msg_cliente
          FROM leads l WHERE {AGUARDANDO} ORDER BY ultima_msg_cliente ASC LIMIT 15""")
    for l in leads:
        l["ultimo_contato_em"] = _iso(l["ultimo_contato_em"]); l["ultima_msg_cliente"] = _iso(l["ultima_msg_cliente"])
    teste = _linhas(db, """
        SELECT id, nome, cidade, situacao_assinatura, validade_ate, DATEDIFF(validade_ate, CURDATE()) AS dias
          FROM condominios WHERE ativo = 1 AND situacao_assinatura IN ('em_teste','teste_estendido','cadastro_incompleto')
           AND validade_ate BETWEEN CURDATE() AND CURDATE() + INTERVAL 7 DAY
         ORDER BY validade_ate ASC LIMIT 20""")
    for t in teste:
        t["validade_ate"] = _iso(t["validade_ate"])
    p = {"eu": eu}
    meus = {
        "cadastros_site": _qtd(db, "SELECT COUNT(*) FROM contatos_condominios WHERE operador_nome = :eu AND DATE(data_contato) = CURDATE()", p),
        "condominios": _qtd(db, "SELECT COUNT(*) FROM condominio_contatos WHERE operador_nome = :eu AND DATE(data_contato) = CURDATE()", p),
        "marketing": _qtd(db, "SELECT COUNT(*) FROM marketing_contatos WHERE operador_nome = :eu AND DATE(created_at) = CURDATE()", p),
        "agendamentos_leads": _qtd(db, "SELECT COUNT(*) FROM leads_agendamentos WHERE operador_nome = :eu AND DATE(criado_em) = CURDATE()", p),
    }
    return {"usuario": {"nome": eu, "tipo": quem.get("tipo"), "master": quem.get("master")},
            "resumo": resumo, "agenda": agenda, "leads_aguardando": leads, "teste_vencendo": teste, "meus_contatos_hoje": meus}
