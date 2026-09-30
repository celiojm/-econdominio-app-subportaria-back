import requests
import re
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional, Dict, Any

from ..database import get_db
from .auth import get_current_user
from ..models import Condominio

router = APIRouter()

# -----------------------
# Helpers
# -----------------------

def condominio_to_dict(cond: Condominio) -> Dict[str, Any]:
    return {
        "id": cond.id,
        "nome": cond.nome,
        "cnpj": cond.cnpj,
        "endereco": cond.endereco,
        "numero": cond.numero or "",
        "complemento": cond.complemento or "",
        "bairro": cond.bairro or "",
        "cidade": cond.cidade or "",
        "estado": cond.estado or "",
        "cep": cond.cep or "",
        "telefone": cond.telefone or "",
        "email": cond.email or "",
        "sindico": cond.sindico or "",
        "total_apartamentos": cond.total_apartamentos or 0,
        "usa_subportaria": bool(cond.usa_subportaria),
        "envio_whatsapp": cond.envio_whatsapp or "S",
        "permite_auto_cadastro": bool(cond.permite_auto_cadastro),
        "observacoes": cond.observacoes or "",
        "data_cadastro": cond.data_cadastro.isoformat() if cond.data_cadastro else None,
        "ativo": cond.ativo,
    }

def _claim(user: dict, *keys, default=None):
    """Helper para extrair claims do token com fallback"""
    for k in keys:
        if k in user and user[k] is not None:
            return user[k]
    return default
def is_admin_master(user: dict) -> bool:
    """
    Admin master = usuário 'admin' (case-insensitive) com nível 1 (padrão legado
    tabela `operadores`), OU role == 'admin_sistema' (padrão novo tabela
    `mobile_operadores`).
    """
    if (user.get("role") or "").lower() == "admin_sistema":
        return True

    username = (_claim(user, "sub", "username", "nome", default="") or "").lower()
    nivel = _claim(user, "nivel", "nivel_id", "Nivel_id", default=None)
    try:
        nivel = int(nivel) if nivel is not None else None
    except Exception:
        pass

    return username == "admin" and nivel == 1


def get_user_condominio_id(user: dict) -> Optional[int]:
    """
    Extrai o condominio_id do usuário do token.
    Com a nova estrutura, operadores.Condomino_id aponta diretamente para condominios.id
    """
    return _claim(user, "condominio_id", "Condomino_id", default=None)

# -----------------------
# Rotas
# -----------------------

@router.get("/")
async def list_condominios(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Lista condomínios:
      - admin master vê TODOS
      - demais usuários veem apenas o próprio condomínio
    """
    try:
        query = db.query(Condominio).filter(Condominio.ativo == True)

        if not is_admin_master(current_user):
            user_cond_id = get_user_condominio_id(current_user)
            if user_cond_id:
                query = query.filter(Condominio.id == user_cond_id)
            else:
                return []

        if search:
            sp = f"%{search}%"
            query = query.filter(
                or_(
                    Condominio.nome.ilike(sp),
                    Condominio.cnpj.ilike(sp),
                    Condominio.sindico.ilike(sp),
                    Condominio.cidade.ilike(sp),
                )
            )

        condominios = query.order_by(Condominio.nome).offset(skip).limit(limit).all()
        return [condominio_to_dict(c) for c in condominios]

    except Exception as e:
        print(f"[condominios] erro listagem: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{condominio_id}")
async def get_condominio(
    condominio_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    condominio = (
        db.query(Condominio)
        .filter(Condominio.id == condominio_id, Condominio.ativo == True)
        .first()
    )
    if not condominio:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")

    if not is_admin_master(current_user):
        user_cond_id = get_user_condominio_id(current_user)
        if user_cond_id != condominio_id:
            raise HTTPException(
                status_code=403, detail="Sem permissão para visualizar este condomínio"
            )

    return condominio_to_dict(condominio)


@router.get("/{condominio_id}/nome-publico")
async def get_nome_publico(
    condominio_id: int,
    db: Session = Depends(get_db),
):
    """
    Endpoint público (sem autenticação) - usado pela página /auto-cadastro
    para exibir o nome do condomínio e se o auto-cadastro está habilitado.
    Retorna SOMENTE nome + permite_auto_cadastro, nenhum outro campo de
    condominio_to_dict.
    """
    condominio = (
        db.query(Condominio)
        .filter(Condominio.id == condominio_id, Condominio.ativo == True)
        .first()
    )
    if not condominio:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")

    return {
        "nome": condominio.nome,
        "permite_auto_cadastro": bool(condominio.permite_auto_cadastro),
    }


@router.post("/")
async def create_condominio(
    data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Cria um novo condomínio - apenas admin master"""
    try:
        if not is_admin_master(current_user):
            raise HTTPException(
                status_code=403,
                detail="Apenas o administrador geral pode criar condomínios",
            )

        # validações simples
        if not data.get("nome") or not data.get("cnpj") or not data.get("endereco"):
            raise HTTPException(status_code=422, detail="Dados obrigatórios ausentes")

        # telefone longo não deve quebrar
        if "telefone" in data and data["telefone"]:
            data["telefone"] = str(data["telefone"])[:200]  # casa com VARCHAR(200)

        existing = db.query(Condominio).filter(Condominio.cnpj == data["cnpj"]).first()
        if existing:
            raise HTTPException(status_code=400, detail="CNPJ já cadastrado")

        db_condominio = Condominio(
            nome=data["nome"],
            cnpj=data["cnpj"],
            endereco=data["endereco"],
            numero=data.get("numero", ""),
            complemento=data.get("complemento", ""),
            bairro=data.get("bairro", ""),
            cidade=data.get("cidade", ""),
            estado=data.get("estado", ""),
            cep=data.get("cep", ""),
            telefone=data.get("telefone", ""),
            email=data.get("email", ""),
            sindico=data.get("sindico", ""),
            total_apartamentos=data.get("total_apartamentos", 0),
            usa_subportaria=bool(data.get("usa_subportaria", False)),
            envio_whatsapp=(data.get("envio_whatsapp") or "S"),
            observacoes=data.get("observacoes", ""),
        )

        db.add(db_condominio)
        db.commit()
        db.refresh(db_condominio)

        return condominio_to_dict(db_condominio)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[condominios] erro criação: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{condominio_id}")
async def update_condominio(
    condominio_id: int,
    data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    db_condominio = db.query(Condominio).filter(Condominio.id == condominio_id).first()
    if not db_condominio:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")

    if not is_admin_master(current_user):
        user_cond_id = get_user_condominio_id(current_user)
        if user_cond_id != condominio_id:
            raise HTTPException(
                status_code=403, detail="Sem permissão para editar este condomínio"
            )

    # não deixar telefone estourar coluna
    if "telefone" in data and data["telefone"]:
        data["telefone"] = str(data["telefone"])[:200]

    if "cnpj" in data and data["cnpj"] != db_condominio.cnpj:
        exists = (
            db.query(Condominio)
            .filter(Condominio.cnpj == data["cnpj"], Condominio.id != condominio_id)
            .first()
        )
        if exists:
            raise HTTPException(status_code=400, detail="CNPJ já cadastrado em outro condomínio")

    for field, value in data.items():
        if hasattr(db_condominio, field):
            setattr(db_condominio, field, value)

    db.commit()
    db.refresh(db_condominio)

    return condominio_to_dict(db_condominio)


@router.delete("/{condominio_id}")
async def delete_condominio(
    condominio_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not is_admin_master(current_user):
        raise HTTPException(
            status_code=403, detail="Apenas o administrador geral pode desativar condomínios"
        )

    db_condominio = db.query(Condominio).filter(Condominio.id == condominio_id).first()
    if not db_condominio:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")

    db_condominio.ativo = False
    db.commit()

    return {"message": "Condomínio desativado com sucesso", "id": condominio_id}

# -------- CEP / CNPJ --------

def consulta_cep(cep: str):
    cep = "".join(filter(str.isdigit, cep))
    if len(cep) != 8:
        raise ValueError("CEP inválido")

    try:
        resp = requests.get(f"https://viacep.com.br/ws/{cep}/json/")
        resp.raise_for_status()
        data = resp.json()
        if data.get("erro"):
            raise ValueError("CEP não encontrado")
        return data
    except requests.RequestException:
        raise ValueError("Erro ao consultar CEP")

@router.get("/cep/{cep}")
async def buscar_cep(cep: str, current_user: dict = Depends(get_current_user)):
    try:
        d = consulta_cep(cep)
        return {
            "cep": d.get("cep", "").replace("-", ""),
            "endereco": d.get("logradouro", ""),
            "complemento": d.get("complemento", ""),
            "bairro": d.get("bairro", ""),
            "cidade": d.get("localidade", ""),
            "estado": d.get("uf", ""),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Erro ao buscar CEP")

def limpar_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj)

@router.get("/consultar-cnpj/{cnpj}")
async def consultar_cnpj(cnpj: str):
    cnpj_limpo = limpar_cnpj(cnpj)
    if len(cnpj_limpo) != 14:
        raise HTTPException(status_code=400, detail="CNPJ inválido. Deve conter 14 dígitos")

    url = f"https://www.receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            dados = response.json()
            if dados.get("status") == "OK":
                return {
                    "success": True,
                    "data": {
                        "nome": dados.get("nome", ""),
                        "fantasia": dados.get("fantasia", ""),
                        "cnpj": dados.get("cnpj", ""),
                        "logradouro": dados.get("logradouro", ""),
                        "numero": dados.get("numero", ""),
                        "complemento": dados.get("complemento", ""),
                        "bairro": dados.get("bairro", ""),
                        "municipio": dados.get("municipio", ""),
                        "uf": dados.get("uf", ""),
                        "cep": dados.get("cep", "").replace(".", "").replace("-", ""),
                        "telefone": dados.get("telefone", ""),
                        "email": dados.get("email", ""),
                        "situacao": dados.get("situacao", ""),
                        "abertura": dados.get("abertura", ""),
                    },
                }
            else:
                raise HTTPException(status_code=404, detail=dados.get("message", "CNPJ não encontrado"))
        else:
            raise HTTPException(status_code=response.status_code, detail="Erro ao consultar CNPJ")

    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Timeout ao consultar Receita Federal. Tente novamente.")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Erro ao conectar com a Receita Federal: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")
