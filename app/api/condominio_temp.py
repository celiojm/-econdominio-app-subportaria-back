from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional, Dict, Any
from datetime import datetime

from ..database import get_db
from .auth import get_current_user
from ..models import Condominio, Usuario

router = APIRouter()

# Função auxiliar para converter objeto para dict
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
        "observacoes": cond.observacoes or "",
        "data_cadastro": cond.data_cadastro.isoformat() if cond.data_cadastro else None,
        "ativo": cond.ativo
    }

@router.get("/")
async def list_condominios(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """Lista todos os condomínios"""
    try:
        query = db.query(Condominio).filter(Condominio.ativo == True)
        
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Condominio.nome.ilike(search_pattern),
                    Condominio.cnpj.ilike(search_pattern),
                    Condominio.sindico.ilike(search_pattern),
                    Condominio.cidade.ilike(search_pattern)
                )
            )
        
        condominios = query.order_by(Condominio.nome).offset(skip).limit(limit).all()
        
        # Converter para dicionários
        return [condominio_to_dict(cond) for cond in condominios]
        
    except Exception as e:
        print(f"Erro ao listar condomínios: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{condominio_id}")
async def get_condominio(
    condominio_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """Busca um condomínio específico"""
    condominio = db.query(Condominio).filter(
        Condominio.id == condominio_id,
        Condominio.ativo == True
    ).first()
    
    if not condominio:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")
    
    return condominio_to_dict(condominio)

@router.post("/")
async def create_condominio(
    data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """Cria um novo condomínio"""
    try:
        # Verificar CNPJ
        existing = db.query(Condominio).filter(
            Condominio.cnpj == data["cnpj"]
        ).first()
        
        if existing:
            raise HTTPException(status_code=400, detail="CNPJ já cadastrado")
        
        # Criar condomínio
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
            observacoes=data.get("observacoes", "")
        )
        
        db.add(db_condominio)
        db.commit()
        db.refresh(db_condominio)
        
        return condominio_to_dict(db_condominio)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"Erro ao criar condomínio: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{condominio_id}")
async def update_condominio(
    condominio_id: int,
    data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """Atualiza um condomínio"""
    db_condominio = db.query(Condominio).filter(
        Condominio.id == condominio_id
    ).first()
    
    if not db_condominio:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")
    
    # Verificar CNPJ se estiver sendo atualizado
    if "cnpj" in data and data["cnpj"] != db_condominio.cnpj:
        existing = db.query(Condominio).filter(
            Condominio.cnpj == data["cnpj"],
            Condominio.id != condominio_id
        ).first()
        
        if existing:
            raise HTTPException(status_code=400, detail="CNPJ já cadastrado em outro condomínio")
    
    # Atualizar campos
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
    current_user: Usuario = Depends(get_current_user)
):
    """Desativa um condomínio"""
    db_condominio = db.query(Condominio).filter(
        Condominio.id == condominio_id
    ).first()
    
    if not db_condominio:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")
    
    db_condominio.ativo = False
    db.commit()
    
    return {"message": "Condomínio desativado com sucesso", "id": condominio_id}
