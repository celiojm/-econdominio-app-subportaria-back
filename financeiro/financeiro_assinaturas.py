"""
================================================================================
ARQUIVO: financeiro_assinaturas.py
PASTA:   ~/backend/financeiro/
FINALIDADE: Gestão de Cadastros de Condomínios e Assinaturas
DATA CRIAÇÃO: 11/02/2026
VERSÃO: 2.2.0 - Correção do INSERT em condominios (bug campos duplicados/errados)
================================================================================
"""
from app.services.protecao_financeiro import nome_usuario_atual  # 2026-10-04: colaborador logado
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional, List
from datetime import date, datetime, timedelta
from pydantic import BaseModel
import secrets
import re
import unicodedata
import string
import smtplib
import httpx
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.database import get_db

router = APIRouter()

PAINEL_URL = "https://admin.econdominio.com.br"
MOBILE_URL = "https://portaria.econdominio.com.br"

# ========================== SCHEMAS ==========================

class ContatoCondominioResponse(BaseModel):
    id: int
    cnpj: str
    razao_social: str
    nome_fantasia: Optional[str]
    endereco: Optional[str]
    numero: Optional[str]
    complemento: Optional[str]
    bairro: Optional[str]
    cidade: Optional[str]
    estado: Optional[str]
    cep: Optional[str]
    sindico_nome: str
    sindico_email: str
    sindico_whatsapp: str
    sindico_cpf: Optional[str]
    total_apartamentos: int
    email_condominio: Optional[str]
    telefone_condominio: Optional[str]
    whatsapp_condominio: Optional[str]
    financeiro_responsavel: Optional[str]
    financeiro_email: Optional[str]
    financeiro_whatsapp: Optional[str]
    confirmado: bool
    data_confirmacao: Optional[str]
    ip_confirmacao: Optional[str]
    status: str
    data_cadastro: str
    data_atualizacao: Optional[str]
    ip_cadastro: Optional[str]
    origem: str
    observacoes: Optional[str]
    plano_selecionado: str
    forma_pagamento: str
    valor_mensal_base: float
    valor_plano_final: float
    plano_desconto: int

    class Config:
        from_attributes = True


class ContatoCondominioListResponse(BaseModel):
    id: int
    razao_social: str
    cidade: Optional[str]
    sindico_nome: str
    sindico_whatsapp: str
    confirmado: bool
    forma_pagamento: str
    valor_mensal_base: float
    status: str
    data_cadastro: str
    total_apartamentos: int

    class Config:
        from_attributes = True


class PaginatedResponse(BaseModel):
    data: List[ContatoCondominioListResponse]
    total: int
    page: int
    limit: int
    total_pages: int


class AtualizarStatusRequest(BaseModel):
    status: str


class AtualizarObservacoesRequest(BaseModel):
    observacoes: str


class ContatoRequest(BaseModel):
    pessoa_contactada: str
    tipo_contato: str
    assunto: str
    descricao: str
    resultado: str
    observacoes: Optional[str] = None
    data_agendamento: Optional[str] = None


class ContatoResponse(BaseModel):
    id: int
    id_condominio: int
    operador_nome: str
    operador_id: Optional[int]
    pessoa_contactada: str
    tipo_contato: str
    assunto: str
    descricao: str
    resultado: str
    observacoes: Optional[str]
    data_contato: str
    data_agendamento: Optional[str]
    status_seguimento: str

    class Config:
        from_attributes = True


class ContatoUpdateRequest(BaseModel):
    resultado: Optional[str] = None
    observacoes: Optional[str] = None
    status_seguimento: Optional[str] = None
    data_agendamento: Optional[str] = None


# ========================== HELPERS DE ATIVAÇÃO ==========================

def _gerar_senha(nome_condominio: str = "", tamanho: int = 10) -> str:
    """
    Gera senha baseada no nome do condomínio + 2 dígitos numéricos.
    Ex: 'CONDOMINIO BELLA VITA' → 'BellaVita42'
    Fallback: senha aleatória se nome vazio/muito curto.
    """
    import unicodedata
    # Remove prefixos comuns
    prefixos = ["condominio", "condomínio", "residencial", "edificio",
                "edifício", "conjunto", "village", "parque"]
    nome = (nome_condominio or "").strip()
    nome_lower = nome.lower()
    for pref in prefixos:
        if nome_lower.startswith(pref):
            nome = nome[len(pref):].strip()
            nome_lower = nome.lower()
            break
    # Remove acentos
    nome = unicodedata.normalize("NFD", nome)
    nome = "".join(c for c in nome if unicodedata.category(c) != "Mn")
    # Mantém apenas letras e espaços, depois CamelCase
    palavras = re.findall(r"[A-Za-z]+", nome)
    if len(palavras) >= 1 and len("".join(palavras)) >= 3:
        base = "".join(p.capitalize() for p in palavras)
        digitos = "".join(secrets.choice(string.digits) for _ in range(2))
        return f"{base}{digitos}"
    # Fallback: senha aleatória
    alfabeto = (
        string.ascii_letters.replace("l", "").replace("O", "").replace("I", "")
        + string.digits.replace("0", "")
    )
    return "".join(secrets.choice(alfabeto) for _ in range(tamanho))

def _enviar_email_ativacao(dest: str, nome_cond: str, sindico: str, email: str, senha: str) -> bool:
    try:
        smtp_server = os.getenv("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
        smtp_port   = int(os.getenv("BREVO_SMTP_PORT", "587"))
        smtp_user   = os.getenv("BREVO_SMTP_USER", "")
        smtp_pass   = os.getenv("BREVO_SMTP_KEY", "")
        from_addr   = os.getenv("BREVO_FROM_ADDRESS", "contato@econdominio.com.br")
        from_name   = os.getenv("BREVO_FROM_NAME", "eCondominio")

        corpo = f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden">
          <div style="background:linear-gradient(135deg,#16a34a,#059669);padding:28px 24px;text-align:center">
            <h1 style="color:#fff;margin:0;font-size:22px">🎉 Conta Ativada!</h1>
            <p style="color:#bbf7d0;margin:6px 0 0;font-size:14px">{nome_cond}</p>
          </div>
          <div style="padding:28px 24px;background:#fff">
            <p style="color:#374151;margin:0 0 16px">Olá, <strong>{sindico}</strong>!</p>
            <p style="color:#374151;margin:0 0 20px">
              Sua conta no <strong>e-Condomínio</strong> foi ativada pela nossa equipe. Confira suas credenciais:
            </p>
            <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:16px;margin-bottom:20px">
              <table style="width:100%;border-collapse:collapse">
                <tr>
                  <td style="padding:6px 0;color:#6b7280;font-size:13px">Email / Login</td>
                  <td style="padding:6px 0;font-weight:600;font-family:monospace;color:#1f2937;text-align:right">{email}</td>
                </tr>
                <tr>
                  <td style="padding:6px 0;color:#6b7280;font-size:13px">Senha temporária</td>
                  <td style="padding:6px 0;text-align:right">
                    <span style="background:#fef3c7;color:#92400e;font-family:monospace;font-weight:700;font-size:16px;padding:2px 10px;border-radius:6px">{senha}</span>
                  </td>
                </tr>
              </table>
            </div>
            <div style="display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap">
              <a href="{PAINEL_URL}" style="flex:1;min-width:140px;display:inline-block;background:#2563eb;color:#fff;text-align:center;padding:12px 16px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
                🖥️ Painel Administrativo
              </a>
              <a href="{MOBILE_URL}" style="flex:1;min-width:140px;display:inline-block;background:#7c3aed;color:#fff;text-align:center;padding:12px 16px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
                📱 App Operacional
              </a>
            </div>
            <div style="background:linear-gradient(135deg,#16a34a,#059669);border-radius:10px;padding:16px;text-align:center;color:#fff;margin-bottom:20px">
              <p style="margin:0;font-weight:700;font-size:16px">🎁 7 DIAS GRÁTIS ATIVADOS!</p>
              <p style="margin:4px 0 0;font-size:13px;color:#bbf7d0">Você não será cobrado agora. Só pague se gostar!</p>
            </div>
            <p style="color:#6b7280;font-size:12px;text-align:center;margin:0">
              ⚠️ Por segurança, altere sua senha no primeiro acesso.<br>
              Dúvidas? <a href="mailto:suporte@econdominio.com.br" style="color:#2563eb">suporte@econdominio.com.br</a>
            </p>
          </div>
          <div style="background:#f9fafb;padding:12px;text-align:center;border-top:1px solid #e5e7eb">
            <p style="color:#9ca3af;font-size:11px;margin:0">© 2026 e-Condomínio • Todos os direitos reservados</p>
          </div>
        </div>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🎉 Sua conta no e-Condomínio foi ativada — {nome_cond}"
        msg["From"]    = f"{from_name} <{from_addr}>"
        msg["To"]      = dest
        msg.attach(MIMEText(corpo, "html", "utf-8"))

        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(smtp_user, smtp_pass)
            s.sendmail(from_addr, [dest], msg.as_string())
        print(f"[EMAIL] ✅ Enviado via Brevo para {dest}")
        return True
    except Exception as e:
        print(f"[ERRO] email ativacao: {e}")
        return False


async def _enviar_whatsapp_ativacao(whatsapp: str, nome_cond: str, email: str, senha: str) -> bool:
    try:
        zapi_instance = os.getenv("ZAPI_INSTANCE_ID", "")
        zapi_token    = os.getenv("ZAPI_TOKEN", "")
        zapi_url      = os.getenv("ZAPI_API_URL", "http://191.252.221.192:8080")
        client_token  = os.getenv("ZAPI_CLIENT_TOKEN", "")

        tel = whatsapp.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if not tel.startswith("55"):
            tel = f"55{tel}"

        msg = (
            f"🎉 *{nome_cond}* — sua conta foi *ativada* pela nossa equipe!\n\n"
            f"📧 *Login:* {email}\n"
            f"🔑 *Senha:* `{senha}`\n\n"
            f"🖥️ *Painel Administrativo:*\n{PAINEL_URL}\n\n"
            f"📱 *App Operacional (portaria):*\n{MOBILE_URL}\n\n"
            f"⚠️ Altere sua senha no primeiro acesso.\n"
            f"🎁 Você tem *7 dias grátis* — aproveite!\n\n"
            f"Dúvidas? wa.me/5548984046118"
        )

        url = f"{zapi_url}/instances/{zapi_instance}/token/{zapi_token}/send-text"
        headers = {"Client-Token": client_token, "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json={"phone": tel, "message": msg}, headers=headers)
            return r.status_code == 200
    except Exception as e:
        print(f"[ERRO] whatsapp ativacao: {e}")
        return False


# ========================== ENDPOINTS ==========================

@router.get("/assinaturas/cadastros", response_model=PaginatedResponse)
async def listar_cadastros_condominios(
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=1, le=50),
    status: Optional[str] = Query(None),
    confirmado: Optional[bool] = Query(None),
    busca: Optional[str] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:
        base_query  = """
            SELECT id, razao_social, cidade, sindico_nome, sindico_whatsapp,
                   confirmado, forma_pagamento, valor_mensal_base, status,
                   data_cadastro, total_apartamentos
            FROM contato_condominios WHERE 1=1
        """
        count_query = "SELECT COUNT(*) FROM contato_condominios WHERE 1=1"

        params = {}
        conditions = []

        if status:
            conditions.append("status = :status")
            params["status"] = status

        if confirmado is not None:
            conditions.append("confirmado = :confirmado")
            params["confirmado"] = 1 if confirmado else 0

        if busca:
            conditions.append(
                "(razao_social LIKE :busca OR cidade LIKE :busca OR "
                "sindico_nome LIKE :busca OR sindico_whatsapp LIKE :busca OR "
                "sindico_email LIKE :busca)"
            )
            params["busca"] = f"%{busca}%"

        if data_inicio:
            conditions.append("DATE(data_cadastro) >= :data_inicio")
            params["data_inicio"] = data_inicio

        if data_fim:
            conditions.append("DATE(data_cadastro) <= :data_fim")
            params["data_fim"] = data_fim

        if conditions:
            where_clause = " AND " + " AND ".join(conditions)
            base_query  += where_clause
            count_query += where_clause

        total      = db.execute(text(count_query), params).scalar() or 0
        offset     = (page - 1) * limit
        total_pages = (total + limit - 1) // limit

        base_query += " ORDER BY data_cadastro DESC LIMIT :limit OFFSET :offset"
        params["limit"]  = limit
        params["offset"] = offset

        rows = db.execute(text(base_query), params).fetchall()

        cadastros = [{
            "id": r[0], "razao_social": r[1], "cidade": r[2],
            "sindico_nome": r[3], "sindico_whatsapp": r[4],
            "confirmado": bool(r[5]),
            "forma_pagamento": r[6] or "mensal",
            "valor_mensal_base": float(r[7]) if r[7] else 0.0,
            "status": r[8],
            "data_cadastro": r[9].strftime('%Y-%m-%d %H:%M:%S') if r[9] else None,
            "total_apartamentos": r[10],
        } for r in rows]

        return {"data": cadastros, "total": total, "page": page,
                "limit": limit, "total_pages": total_pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar cadastros: {str(e)}")


@router.get("/assinaturas/cadastros/{id_cadastro}")
async def obter_cadastro_completo(id_cadastro: int, db: Session = Depends(get_db)):
    try:
        row = db.execute(text("""
            SELECT id, cnpj, razao_social, nome_fantasia,
                   endereco, numero, complemento, bairro, cidade, estado, cep,
                   sindico_nome, sindico_email, sindico_whatsapp, sindico_cpf,
                   total_apartamentos, email_condominio, telefone_condominio, whatsapp_condominio,
                   financeiro_responsavel, financeiro_email, financeiro_whatsapp,
                   confirmado, data_confirmacao, ip_confirmacao,
                   status, data_cadastro, data_atualizacao, ip_cadastro, origem, observacoes,
                   plano_selecionado, forma_pagamento, valor_mensal_base, valor_plano_final, plano_desconto
            FROM contato_condominios WHERE id = :id
        """), {"id": id_cadastro}).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Cadastro não encontrado")

        return {"success": True, "data": {
            "id": row[0], "cnpj": row[1], "razao_social": row[2], "nome_fantasia": row[3],
            "endereco": row[4], "numero": row[5], "complemento": row[6], "bairro": row[7],
            "cidade": row[8], "estado": row[9], "cep": row[10],
            "sindico_nome": row[11], "sindico_email": row[12],
            "sindico_whatsapp": row[13], "sindico_cpf": row[14],
            "total_apartamentos": row[15],
            "email_condominio": row[16], "telefone_condominio": row[17],
            "whatsapp_condominio": row[18],
            "financeiro_responsavel": row[19], "financeiro_email": row[20],
            "financeiro_whatsapp": row[21],
            "confirmado": bool(row[22]),
            "data_confirmacao": row[23].strftime('%Y-%m-%d %H:%M:%S') if row[23] else None,
            "ip_confirmacao": row[24], "status": row[25],
            "data_cadastro": row[26].strftime('%Y-%m-%d %H:%M:%S') if row[26] else None,
            "data_atualizacao": row[27].strftime('%Y-%m-%d %H:%M:%S') if row[27] else None,
            "ip_cadastro": row[28], "origem": row[29], "observacoes": row[30],
            "plano_selecionado": row[31] or "mensal",
            "forma_pagamento": row[32] or "mensal",
            "valor_mensal_base": float(row[33]) if row[33] else 0.0,
            "valor_plano_final": float(row[34]) if row[34] else 0.0,
            "plano_desconto": row[35] or 0,
        }}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter cadastro: {str(e)}")


@router.put("/assinaturas/cadastros/{id_cadastro}/status")
async def atualizar_status(id_cadastro: int, dados: AtualizarStatusRequest, db: Session = Depends(get_db)):
    try:
        if not db.execute(text("SELECT id FROM contato_condominios WHERE id = :id"), {"id": id_cadastro}).fetchone():
            raise HTTPException(status_code=404, detail="Cadastro não encontrado")
        db.execute(text("UPDATE contato_condominios SET status=:status, data_atualizacao=NOW() WHERE id=:id"),
                   {"status": dados.status, "id": id_cadastro})
        db.commit()
        return {"success": True, "message": f"Status atualizado para '{dados.status}'"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar status: {str(e)}")


@router.put("/assinaturas/cadastros/{id_cadastro}/observacoes")
async def atualizar_observacoes(id_cadastro: int, dados: AtualizarObservacoesRequest, db: Session = Depends(get_db)):
    try:
        if not db.execute(text("SELECT id FROM contato_condominios WHERE id = :id"), {"id": id_cadastro}).fetchone():
            raise HTTPException(status_code=404, detail="Cadastro não encontrado")
        db.execute(text("UPDATE contato_condominios SET observacoes=:obs, data_atualizacao=NOW() WHERE id=:id"),
                   {"obs": dados.observacoes, "id": id_cadastro})
        db.commit()
        return {"success": True, "message": "Observações atualizadas com sucesso"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar observações: {str(e)}")


@router.delete("/assinaturas/cadastros/{id_cadastro}")
async def deletar_cadastro(id_cadastro: int, db: Session = Depends(get_db)):
    try:
        row = db.execute(text("SELECT id, status FROM contato_condominios WHERE id = :id"), {"id": id_cadastro}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Cadastro não encontrado")
        if row[1] == 'convertido':
            raise HTTPException(status_code=400, detail="Não é possível deletar cadastros já convertidos")
        db.execute(text("DELETE FROM contato_condominios WHERE id = :id"), {"id": id_cadastro})
        db.commit()
        return {"success": True, "message": "Cadastro deletado com sucesso"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao deletar cadastro: {str(e)}")


@router.get("/assinaturas/estatisticas")
async def obter_estatisticas(db: Session = Depends(get_db)):
    try:
        row = db.execute(text("""
            SELECT COUNT(*),
                   SUM(CASE WHEN status='pendente'   THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status='confirmado' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status='convertido' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status='cancelado'  THEN 1 ELSE 0 END),
                   SUM(CASE WHEN confirmado=1        THEN 1 ELSE 0 END),
                   AVG(valor_mensal_base),
                   SUM(total_apartamentos)
            FROM contato_condominios
        """)).fetchone()
        return {"success": True, "data": {
            "total": row[0] or 0, "pendentes": row[1] or 0,
            "confirmados": row[2] or 0, "convertidos": row[3] or 0,
            "cancelados": row[4] or 0, "total_confirmados": row[5] or 0,
            "valor_medio": float(row[6]) if row[6] else 0.0,
            "total_apartamentos": row[7] or 0,
        }}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter estatísticas: {str(e)}")


# ========================== CONTATOS ==========================

@router.get("/assinaturas/cadastros/{id_condominio}/contatos")
async def listar_contatos_condominio(
    id_condominio: int,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    resultado: Optional[str] = Query(None),
    tipo_contato: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:
        cond = db.execute(text("SELECT id, razao_social FROM contato_condominios WHERE id = :id"),
                          {"id": id_condominio}).fetchone()
        if not cond:
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")

        base_query  = "SELECT id, id_condominio, operador_nome, operador_id, pessoa_contactada, tipo_contato, assunto, descricao, resultado, observacoes, data_contato, data_agendamento, status_seguimento FROM contatos_condominios WHERE id_condominio = :id_condominio"
        count_query = "SELECT COUNT(*) FROM contatos_condominios WHERE id_condominio = :id_condominio"
        params = {"id_condominio": id_condominio}
        conditions = []

        if resultado:
            conditions.append("resultado = :resultado")
            params["resultado"] = resultado
        if tipo_contato:
            conditions.append("tipo_contato = :tipo_contato")
            params["tipo_contato"] = tipo_contato

        if conditions:
            where_clause = " AND " + " AND ".join(conditions)
            base_query  += where_clause
            count_query += where_clause

        total       = db.execute(text(count_query), params).scalar() or 0
        offset      = (page - 1) * limit
        total_pages = (total + limit - 1) // limit

        base_query += " ORDER BY data_contato DESC LIMIT :limit OFFSET :offset"
        params["limit"]  = limit
        params["offset"] = offset

        rows = db.execute(text(base_query), params).fetchall()
        contatos = [{
            "id": r[0], "id_condominio": r[1], "operador_nome": r[2],
            "operador_id": r[3], "pessoa_contactada": r[4], "tipo_contato": r[5],
            "assunto": r[6], "descricao": r[7], "resultado": r[8],
            "observacoes": r[9],
            "data_contato": r[10].strftime('%Y-%m-%d %H:%M:%S') if r[10] else None,
            "data_agendamento": r[11].strftime('%Y-%m-%d %H:%M:%S') if r[11] else None,
            "status_seguimento": r[12],
        } for r in rows]

        return {"success": True, "data": {
            "contatos": contatos,
            "condominio": {"id": cond[0], "nome": cond[1]},
            "total": total, "page": page, "limit": limit, "total_pages": total_pages,
        }}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar contatos: {str(e)}")


@router.post("/assinaturas/cadastros/{id_condominio}/contatos")
async def criar_contato(
    id_condominio: int,
    dados: ContatoRequest,
    operador_nome: str = Query("Sistema"),
    db: Session = Depends(get_db)
):
    try:
        if not db.execute(text("SELECT id FROM contato_condominios WHERE id = :id"), {"id": id_condominio}).fetchone():
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")

        data_agendamento = None
        if dados.data_agendamento:
            try:
                data_agendamento = datetime.fromisoformat(dados.data_agendamento.replace('Z', '+00:00'))
            except Exception:
                pass

        db.execute(text("""
            INSERT INTO contatos_condominios
                (id_condominio, operador_nome, pessoa_contactada, tipo_contato,
                 assunto, descricao, resultado, observacoes, data_agendamento)
            VALUES
                (:id_condominio, :operador_nome, :pessoa_contactada, :tipo_contato,
                 :assunto, :descricao, :resultado, :observacoes, :data_agendamento)
        """), {
            "id_condominio": id_condominio, "operador_nome": nome_usuario_atual(operador_nome),
            "pessoa_contactada": dados.pessoa_contactada, "tipo_contato": dados.tipo_contato,
            "assunto": dados.assunto, "descricao": dados.descricao,
            "resultado": dados.resultado, "observacoes": dados.observacoes,
            "data_agendamento": data_agendamento,
        })
        db.commit()
        return {"success": True, "message": "Contato registrado com sucesso"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar contato: {str(e)}")


@router.put("/assinaturas/contatos/{id_contato}")
async def atualizar_contato(id_contato: int, dados: ContatoUpdateRequest, db: Session = Depends(get_db)):
    try:
        if not db.execute(text("SELECT id FROM contatos_condominios WHERE id = :id"), {"id": id_contato}).fetchone():
            raise HTTPException(status_code=404, detail="Contato não encontrado")

        updates, params = [], {"id": id_contato}

        if dados.resultado is not None:
            updates.append("resultado = :resultado"); params["resultado"] = dados.resultado
        if dados.observacoes is not None:
            updates.append("observacoes = :observacoes"); params["observacoes"] = dados.observacoes
        if dados.status_seguimento is not None:
            updates.append("status_seguimento = :status_seguimento"); params["status_seguimento"] = dados.status_seguimento
        if dados.data_agendamento is not None:
            updates.append("data_agendamento = :data_agendamento")
            try:
                params["data_agendamento"] = datetime.fromisoformat(dados.data_agendamento.replace('Z', '+00:00'))
            except Exception:
                params["data_agendamento"] = None

        if not updates:
            raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")

        db.execute(text(f"UPDATE contatos_condominios SET {', '.join(updates)} WHERE id = :id"), params)
        db.commit()
        return {"success": True, "message": "Contato atualizado com sucesso"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar contato: {str(e)}")


@router.delete("/assinaturas/contatos/{id_contato}")
async def deletar_contato(id_contato: int, db: Session = Depends(get_db)):
    try:
        if not db.execute(text("SELECT id FROM contatos_condominios WHERE id = :id"), {"id": id_contato}).fetchone():
            raise HTTPException(status_code=404, detail="Contato não encontrado")
        db.execute(text("DELETE FROM contatos_condominios WHERE id = :id"), {"id": id_contato})
        db.commit()
        return {"success": True, "message": "Contato deletado com sucesso"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao deletar contato: {str(e)}")


@router.get("/assinaturas/agenda")
async def listar_agenda_contatos(
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=1, le=50),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:
        base_query = """
            SELECT cc.id, cc.id_condominio, cc.pessoa_contactada, cc.tipo_contato,
                   cc.assunto, cc.descricao, cc.resultado, cc.data_agendamento,
                   cc.status_seguimento, cond.razao_social, cond.cidade,
                   cond.sindico_nome, cond.status
            FROM contatos_condominios cc
            INNER JOIN contato_condominios cond ON cc.id_condominio = cond.id
            WHERE cc.data_agendamento IS NOT NULL
        """
        count_query = """
            SELECT COUNT(*) FROM contatos_condominios cc
            INNER JOIN contato_condominios cond ON cc.id_condominio = cond.id
            WHERE cc.data_agendamento IS NOT NULL
        """
        params, conditions = {}, []

        if data_inicio:
            conditions.append("DATE(cc.data_agendamento) >= :data_inicio")
            params["data_inicio"] = data_inicio
        if data_fim:
            conditions.append("DATE(cc.data_agendamento) <= :data_fim")
            params["data_fim"] = data_fim

        if conditions:
            where_clause = " AND " + " AND ".join(conditions)
            base_query  += where_clause
            count_query += where_clause

        total       = db.execute(text(count_query), params).scalar() or 0
        offset      = (page - 1) * limit
        total_pages = (total + limit - 1) // limit

        base_query += " ORDER BY cc.data_agendamento ASC LIMIT :limit OFFSET :offset"
        params["limit"]  = limit
        params["offset"] = offset

        rows = db.execute(text(base_query), params).fetchall()
        agendamentos = [{
            "id_contato": r[0], "id_condominio": r[1], "pessoa_contactada": r[2],
            "tipo_contato": r[3], "assunto": r[4], "descricao": r[5],
            "resultado": r[6],
            "data_agendamento": r[7].strftime('%Y-%m-%d %H:%M:%S') if r[7] else None,
            "status_seguimento": r[8], "razao_social": r[9],
            "cidade": r[10], "sindico_nome": r[11], "status_condominio": r[12],
        } for r in rows]

        return {"success": True, "data": agendamentos, "total": total,
                "page": page, "limit": limit, "total_pages": total_pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar agenda: {str(e)}")


# ========================== ATIVAR CONTA ==========================

@router.post("/assinaturas/cadastros/{cadastro_id}/ativar")
async def ativar_conta_condominio(cadastro_id: int, db: Session = Depends(get_db)):
    """
    Ativa a conta de um cadastro pendente/convertido:
    1. Busca todos os dados de contato_condominios
    2. Atualiza confirmado=1 / status='confirmado'
    3. Insere na tabela condominios (se CNPJ ainda não existir)
    4. Envia credenciais via WhatsApp e/ou Email
    """
    try:
        # ── 1. Busca dados completos do pré-cadastro ──────────────────────────
        cadastro = db.execute(text("""
            SELECT
                id, cnpj, razao_social, nome_fantasia,
                sindico_nome, sindico_email, sindico_whatsapp,
                endereco, numero, complemento, bairro,
                cidade, estado, cep,
                telefone_condominio, whatsapp_condominio,
                total_apartamentos,
                forma_pagamento, valor_mensal_base, valor_plano_final, plano_desconto,
                confirmado, status,
                financeiro_responsavel, financeiro_email, financeiro_whatsapp
            FROM contato_condominios
            WHERE id = :id
        """), {"id": cadastro_id}).fetchone()

        if not cadastro:
            raise HTTPException(status_code=404, detail="Cadastro não encontrado")

        (
            _id, cnpj, razao_social, nome_fantasia,
            sindico_nome, sindico_email, sindico_whatsapp,
            endereco, numero, complemento, bairro,
            cidade, estado, cep,
            telefone_condominio, whatsapp_condominio,
            total_apartamentos,
            forma_pagamento, valor_mensal_base, valor_plano_final, plano_desconto,
            confirmado, status_atual,
            financeiro_responsavel, financeiro_email, financeiro_whatsapp
        ) = cadastro

        if confirmado:
            raise HTTPException(
                status_code=409,
                detail="ALREADY_CONFIRMED: Conta já foi ativada anteriormente"
            )

        nome_cond = (nome_fantasia or razao_social or "Condomínio").strip()
        sindico   = (sindico_nome or "Síndico").strip()
        email     = (sindico_email or "").strip()
        whatsapp  = (sindico_whatsapp or "").strip()

        if not email:
            raise HTTPException(status_code=422, detail="Email do síndico não cadastrado")

        # ── 2. Gera senha ─────────────────────────────────────────────────────

#        senha = _gerar_senha(10)
        senha = _gerar_senha(nome_cond)
        # ── 3. Atualiza pré-cadastro ──────────────────────────────────────────
        db.execute(text("""
            UPDATE contato_condominios
            SET confirmado=1, status='confirmado',
                data_confirmacao=NOW(), data_atualizacao=NOW()
            WHERE id = :id
        """), {"id": cadastro_id})
        db.commit()
        print(f"[INFO] contato_condominios id={cadastro_id} marcado como confirmado")

        # ── 4. Insere/verifica na tabela condominios ──────────────────────────
        condominio_id = None
        try:
            existe = db.execute(text("""
                SELECT id FROM condominios WHERE cnpj = :cnpj LIMIT 1
            """), {"cnpj": cnpj}).fetchone()

            if existe:
                condominio_id = existe[0]
                print(f"[INFO] Condomínio já existe em condominios id={condominio_id}, pulando INSERT")
            else:
                telefone_final  = (telefone_condominio or whatsapp_condominio or whatsapp or "").strip()
                endereco_final  = " ".join(filter(None, [endereco, numero, complemento])).strip() or None
                validade        = (date.today() + timedelta(days=7)).isoformat()
                aptos           = int(total_apartamentos) if total_apartamentos else 0
                valor_base      = float(valor_mensal_base)  if valor_mensal_base  else 0.0
                valor_final     = float(valor_plano_final)  if valor_plano_final  else valor_base
                desconto        = int(plano_desconto)        if plano_desconto     else 0
                plano           = (forma_pagamento or "mensal").strip()

                # email_financeiro: usa financeiro_email se existir, senão sindico_email
                email_fin = (financeiro_email or email).strip()
                # cobranca_whats: usa financeiro_whatsapp se existir, senão sindico_whatsapp
                cobr_wpp  = (financeiro_whatsapp or whatsapp).strip()
                cobr_resp = (financeiro_responsavel or sindico).strip()

                result = db.execute(text("""
                    INSERT INTO condominios (
                        nome, cnpj,
                        endereco, bairro, cidade, estado, cep,
                        telefone, email, email_financeiro,
                        sindico,
                        total_apartamentos,
                        plano_selecionado, forma_pagamento,
                        valor_mensal_base, valor_plano_final, plano_desconto,
                        validade_ate, assinatura_status,
                        cobranca_responsavel, cobranca_email, cobranca_whats,
                        ativo, periodo_bonificado,
                        data_inicio_bonificado, data_fim_bonificado, bonificado
                    ) VALUES (
                        :nome, :cnpj,
                        :endereco, :bairro, :cidade, :estado, :cep,
                        :telefone, :email, :email_fin,
                        :sindico,
                        :aptos,
                        :plano, :plano,
                        :valor_base, :valor_final, :desconto,
                        :validade, 'trial',
                        :cobr_resp, :cobr_email, :cobr_wpp,
                        1, 1,
                        CURDATE(), DATE_ADD(CURDATE(), INTERVAL 7 DAY), 1
                    )
                """), {
                    "nome":       nome_cond,
                    "cnpj":       cnpj,
                    "endereco":   endereco_final,
                    "bairro":     bairro,
                    "cidade":     cidade,
                    "estado":     estado,
                    "cep":        cep,
                    "telefone":   telefone_final,
                    "email":      email,
                    "email_fin":  email_fin,
                    "sindico":    sindico,
                    "aptos":      aptos,
                    "plano":      plano,
                    "valor_base": valor_base,
                    "valor_final":valor_final,
                    "desconto":   desconto,
                    "validade":   validade,
                    "cobr_resp":  cobr_resp,
                    "cobr_email": email_fin,
                    "cobr_wpp":   cobr_wpp,
                })
                db.commit()
                condominio_id = result.lastrowid
                print(f"[INFO] ✅ Condomínio inserido em condominios id={condominio_id} — {nome_cond}")

        except Exception as e_cond:
            # Não deixar falha de INSERT em condominios derrubar o fluxo todo,
            # mas logar com detalhes para diagnóstico
            db.rollback()
            print(f"[ERRO] ❌ Falha ao inserir em condominios: {e_cond}")
            # Re-raise como warning no response mas sem 500
            condominio_id = None


          # ── 5. Cria operador em mobile_operadores ────────────────────────────
        if condominio_id:
            try:
                from mobile.auth_service import hash_password as _hash_password
                senha_hash = _hash_password(senha)
                db.execute(text(
                    "INSERT INTO mobile_operadores "
                    "(condominio_id, nome, email, telefone, senha_hash, role, ativo) "
                    "VALUES (:cid, :nome, :email, :tel, :hash, 'sindico', 1)"
                ), {"cid": condominio_id, "nome": sindico, "email": email,
                    "tel": whatsapp, "hash": senha_hash})
                db.commit()
                print(f"[INFO] ✅ Operador criado — {email}")
            except Exception as e_op:
                db.rollback()
                print(f"[ERRO] ❌ Falha ao criar operador: {e_op}")




        # ── 5. Envia credenciais ──────────────────────────────────────────────
        canais = []

        if whatsapp and len(whatsapp.replace(" ", "")) >= 10:
            ok_wpp = await _enviar_whatsapp_ativacao(whatsapp, nome_cond, email, senha)
            if ok_wpp:
                canais.append("WhatsApp")

        if email:
            ok_email = _enviar_email_ativacao(email, nome_cond, sindico, email, senha)
            if ok_email:
                canais.append("Email")

        return {
            "success": True,
            "message": f"Conta ativada. Enviado via: {' e '.join(canais) if canais else 'nenhum canal'}",
            "data": {
                "email":         email,
                "senha":         senha,
                "painel_url":    PAINEL_URL,
                "mobile_url":    MOBILE_URL,
                "canais":        canais,
                "sindico":       sindico,
                "condominio":    nome_cond,
                "condominio_id": condominio_id,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[ERRO] ativar_conta id={cadastro_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao ativar conta: {str(e)}")


# ========================== RESGATAR ==========================

@router.post("/assinaturas/cadastros/{cadastro_id}/resgatar")
async def resgatar_cliente(cadastro_id: int, payload: dict, db: Session = Depends(get_db)):
    """Envia mensagem de resgate para cadastro pendente."""
    try:
        row = db.execute(text("""
            SELECT id, razao_social, sindico_whatsapp, sindico_email
            FROM contato_condominios WHERE id = :id
        """), {"id": cadastro_id}).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Cadastro não encontrado")

        canal    = payload.get("canal", "ambos")
        whatsapp = (row[2] or "").strip()
        email    = (row[3] or "").strip()
        nome     = row[1] or "Condomínio"

        MENSAGEM = (
            f"Olá! 👋 Percebi que você iniciou o cadastro do *{nome}* no eCondomínio, mas não finalizou.\n\n"
            f"✅ Você pode concluir agora (leva menos de 2 minutos):\nhttps://econdominio.com.br/cadastro\n\n"
            f"🎁 7 dias grátis para testar, sem cobrança agora.\n\n"
            f"Equipe eCondomínio\nhttps://econdominio.com.br"
        )

        canais, erros = [], []

        if canal in ("whatsapp", "ambos") and whatsapp:
            try:
                zapi_instance = os.getenv("ZAPI_INSTANCE_ID", "")
                zapi_token    = os.getenv("ZAPI_TOKEN", "")
                zapi_url      = os.getenv("ZAPI_API_URL", "http://191.252.221.192:8080")
                client_token  = os.getenv("ZAPI_CLIENT_TOKEN", "")
                tel = whatsapp.replace(" ","").replace("-","").replace("(","").replace(")","")
                if not tel.startswith("55"): tel = "55" + tel
                url = f"{zapi_url}/instances/{zapi_instance}/token/{zapi_token}/send-text"
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.post(url, json={"phone": tel, "message": MENSAGEM},
                                          headers={"Client-Token": client_token, "Content-Type": "application/json"})
                if r.status_code == 200: canais.append("whatsapp")
                else: erros.append(f"WhatsApp: {r.text[:100]}")
            except Exception as e:
                erros.append(f"WhatsApp: {str(e)}")

        if canal in ("email", "ambos") and email:
            try:
                import smtplib
                from email.mime.multipart import MIMEMultipart as MP
                from email.mime.text import MIMEText as MT
                smtp_server = os.getenv("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
                smtp_port   = int(os.getenv("BREVO_SMTP_PORT", "587"))
                smtp_user   = os.getenv("BREVO_SMTP_USER", "")
                smtp_pass   = os.getenv("BREVO_SMTP_KEY", "")
                from_addr   = os.getenv("BREVO_FROM_ADDRESS", "contato@econdominio.com.br")
                from_name   = os.getenv("BREVO_FROM_NAME", "eCondominio")
                msg = MP("alternative")
                msg["Subject"] = "Finalize seu cadastro no eCondomínio 🎁"
                msg["From"]    = f"{from_name} <{from_addr}>"
                msg["To"]      = email
                msg.attach(MT(MENSAGEM.replace("\n", "<br>"), "html", "utf-8"))
                with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as s:
                    s.ehlo(); s.starttls(); s.ehlo()
                    s.login(smtp_user, smtp_pass)
                    s.sendmail(from_addr, [email], msg.as_string())
                canais.append("email")
            except Exception as e:
                erros.append(f"Email: {str(e)}")

        if not canais:
            raise HTTPException(status_code=500, detail=" | ".join(erros) or "Nenhum canal disponível")

        return {"success": True, "canais": canais,
                "message": f"Enviado via {' e '.join(canais)}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
