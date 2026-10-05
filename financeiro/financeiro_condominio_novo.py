# ============================================================================
# ARQUIVO: financeiro_condominio_novo.py
# PASTA: /home/visionlpr/app_subportaria_back/financeiro/
# DESCRIÇÃO: "Novo condomínio" na tela Condomínios do financeiro (master e colaborador). Cadastra o
#            condomínio em TESTE (assinatura_status='trial', validade = hoje + TRIAL_DIAS, padrão 14) e o
#            SÍNDICO (mobile_operadores, role sindico), gera a senha e envia as credenciais por e-mail e/ou
#            WhatsApp (mesmas mensagens do "Ativar" dos cadastros do site). CNPJ e e-mail do síndico não
#            podem repetir (409). A senha volta UMA vez na resposta para o operador conferir/repassar.
#            Rotas: POST /api/financeiro/condominios/novo
#                   POST /api/financeiro/condominios/{id}/sindico/{operador_id}/nova-senha (gera, grava e envia)
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-05 data alteração: 2026-10-05
# ============================================================================
import logging
import re
import secrets
import string
import unicodedata
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.assinatura_situacao import TRIAL_DIAS
from app.services.protecao_financeiro import usuario_interno

logger = logging.getLogger(__name__)
router = APIRouter(tags=["condominio-novo"])

PREFIXOS = ("condominio", "residencial", "edificio", "conjunto", "village", "parque", "cond", "res", "ed")


def _digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def gerar_senha(nome_condominio: str = "") -> str:
    """Senha fácil de ditar e difícil de adivinhar: palavra do nome + 4 dígitos + 2 letras aleatórias.
    Ex.: 'CONDOMINIO BELLA VITA' → 'BellaVita4827kq'. (A do "Ativar" antigo tinha só 2 dígitos = 100 opções.)"""
    s = unicodedata.normalize("NFKD", nome_condominio or "").encode("ascii", "ignore").decode()
    palavras = [p for p in re.findall(r"[A-Za-z]+", s) if p.lower() not in PREFIXOS]
    base = "".join(p.capitalize() for p in palavras)[:10] or "Econdominio"
    if len(base) < 4:
        base = (base + "Econd")[:6]
    digitos = "".join(secrets.choice(string.digits) for _ in range(4))
    letras = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz") for _ in range(2))
    return f"{base}{digitos}{letras}"


async def _enviar(canais: dict, whatsapp: str, email: str, nome_cond: str, sindico: str, senha: str) -> dict:
    """Mesmas mensagens do 'Ativar' (financeiro_assinaturas). Nunca derruba o cadastro."""
    from financeiro.financeiro_assinaturas import _enviar_whatsapp_ativacao, _enviar_email_ativacao
    res = {"whatsapp": None, "email": None}
    if canais.get("whatsapp"):
        if len(_digitos(whatsapp)) >= 10:
            try:
                res["whatsapp"] = bool(await _enviar_whatsapp_ativacao(whatsapp, nome_cond, email, senha))
            except Exception as e:
                logger.warning("NOVO_CONDOMINIO: WhatsApp falhou: %s", e)
                res["whatsapp"] = False
        else:
            res["whatsapp"] = False
    if canais.get("email"):
        try:
            res["email"] = bool(_enviar_email_ativacao(email, nome_cond, sindico, email, senha))
        except Exception as e:
            logger.warning("NOVO_CONDOMINIO: e-mail falhou: %s", e)
            res["email"] = False
    return res


class NovoCondominio(BaseModel):
    model_config = ConfigDict(extra="ignore")
    nome: str = Field(..., min_length=3, max_length=200)
    cnpj: Optional[str] = Field(None, max_length=20)
    endereco: Optional[str] = Field(None, max_length=255)
    numero: Optional[str] = Field(None, max_length=20)
    complemento: Optional[str] = Field(None, max_length=100)
    bairro: Optional[str] = Field(None, max_length=100)
    cidade: Optional[str] = Field(None, max_length=100)
    estado: Optional[str] = Field(None, max_length=2)
    cep: Optional[str] = Field(None, max_length=10)
    telefone: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=150)
    total_apartamentos: int = Field(..., ge=1, le=100000)
    sindico_nome: str = Field(..., min_length=2, max_length=150)
    sindico_email: str = Field(..., min_length=5, max_length=150)
    sindico_whatsapp: Optional[str] = Field(None, max_length=20)
    dias_teste: int = Field(TRIAL_DIAS, ge=1, le=90)
    enviar_email: bool = True
    enviar_whatsapp: bool = True


@router.post("/condominios/novo")
async def cadastrar_condominio(dados: NovoCondominio, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    email = dados.sindico_email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="E-mail do síndico inválido")
    whats = _digitos(dados.sindico_whatsapp)
    cnpj = _digitos(dados.cnpj)
    if cnpj and len(cnpj) != 14:
        raise HTTPException(status_code=400, detail="CNPJ inválido (14 dígitos)")
    if cnpj:
        ja = db.execute(text("""SELECT id, nome FROM condominios
                                 WHERE REPLACE(REPLACE(REPLACE(cnpj,'.',''),'/',''),'-','') = :c LIMIT 1"""), {"c": cnpj}).fetchone()
        if ja:
            raise HTTPException(status_code=409, detail=f"Este CNPJ já está cadastrado: #{ja.id} {ja.nome}")
    ja = db.execute(text("SELECT id, nome, role FROM mobile_operadores WHERE LOWER(email) = :e"), {"e": email}).fetchone()
    if ja:
        raise HTTPException(status_code=409, detail=f"O e-mail do síndico já é usado por {ja.nome} ({ja.role}, usuário #{ja.id}). Use outro e-mail.")

    nome_cond = dados.nome.strip()
    validade = date.today() + timedelta(days=dados.dias_teste)
    cnpj_fmt = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}" if cnpj else None
    senha = gerar_senha(nome_cond)
    from mobile.auth_service import hash_password
    try:
        r = db.execute(text("""
            INSERT INTO condominios (nome, cnpj, endereco, numero, complemento, bairro, cidade, estado, cep,
                telefone, email, email_financeiro, sindico, total_apartamentos, validade_ate, assinatura_status,
                cobranca_responsavel, cobranca_email, cobranca_whats, ativo, situacao_assinatura, situacao_atualizada_em)
            VALUES (:nome, :cnpj, :end, :num, :comp, :bairro, :cidade, :uf, :cep, :tel, :email, :email_fin, :sindico,
                :aptos, :validade, 'trial', :sindico, :email_fin, :whats, 1, 'em_teste', NOW())"""),
            {"nome": nome_cond, "cnpj": cnpj_fmt, "end": dados.endereco, "num": dados.numero, "comp": dados.complemento,
             "bairro": dados.bairro, "cidade": dados.cidade, "uf": (dados.estado or "").upper() or None,
             "cep": _digitos(dados.cep) or None, "tel": _digitos(dados.telefone) or whats or None,
             "email": (dados.email or email).strip(), "email_fin": email, "sindico": dados.sindico_nome.strip(),
             "aptos": dados.total_apartamentos, "validade": validade, "whats": whats or None})
        cid = r.lastrowid
        r2 = db.execute(text("""
            INSERT INTO mobile_operadores (condominio_id, nome, email, telefone, senha_hash, role, nivel_id, ativo)
            VALUES (:cid, :nome, :email, :tel, :hash, 'sindico', 2, 1)"""),
            {"cid": cid, "nome": dados.sindico_nome.strip(), "email": email, "tel": whats or None, "hash": hash_password(senha)})
        sid = r2.lastrowid
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("NOVO_CONDOMINIO: falha ao gravar: %s", e)
        raise HTTPException(status_code=400, detail="Não foi possível cadastrar (confira os dados).")

    envio = await _enviar({"email": dados.enviar_email, "whatsapp": dados.enviar_whatsapp}, whats, email,
                          nome_cond, dados.sindico_nome.strip(), senha)
    logger.info("NOVO_CONDOMINIO: #%s %s cadastrado por %s (síndico #%s, envio=%s)", cid, nome_cond, quem.get("nome"), sid, envio)
    return {"success": True, "condominio_id": cid, "sindico_id": sid, "login": email, "senha": senha,
            "validade_ate": validade.isoformat(), "envio": envio}


class NovaSenha(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enviar_email: bool = True
    enviar_whatsapp: bool = True


@router.post("/condominios/{condominio_id}/sindico/{operador_id}/nova-senha")
async def nova_senha_sindico(condominio_id: int, operador_id: int, dados: NovaSenha, db: Session = Depends(get_db),
                             quem: dict = Depends(usuario_interno)):
    """Gera senha nova para um usuário do condomínio (síndico/admin/operador/porteiro), grava e envia."""
    r = db.execute(text("""SELECT o.id, o.nome, o.email, o.telefone, o.role, c.nome AS cond
                             FROM mobile_operadores o JOIN condominios c ON c.id = o.condominio_id
                            WHERE o.id = :o AND o.condominio_id = :c"""), {"o": operador_id, "c": condominio_id}).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Usuário não encontrado neste condomínio")
    if r.role == "admin_sistema":
        raise HTTPException(status_code=403, detail="Usuários da equipe do sistema não são alterados por aqui")
    senha = gerar_senha(r.cond)
    from mobile.auth_service import hash_password
    db.execute(text("UPDATE mobile_operadores SET senha_hash = :h, login_falhos = 0, bloqueado_ate = NULL WHERE id = :o"),
               {"h": hash_password(senha), "o": operador_id})
    db.commit()
    envio = await _enviar({"email": dados.enviar_email, "whatsapp": dados.enviar_whatsapp}, r.telefone or "", r.email,
                          r.cond, r.nome, senha)
    logger.info("NOVA_SENHA: usuário #%s (%s) do condomínio #%s por %s, envio=%s", operador_id, r.role, condominio_id, quem.get("nome"), envio)
    return {"success": True, "login": r.email, "senha": senha, "envio": envio}
