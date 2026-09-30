from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from typing import List, Dict, Any
import pandas as pd
import io
import re
from datetime import datetime

from app.database import get_db
from app.api.auth import get_current_user
from app.models.morador import Morador
from app.models.condominio import CondominioOperador
from app.models.operador import Operador

router = APIRouter(
    prefix="/api/configuracoes",
    tags=["configuracoes"]
)

def formatar_telefone(telefone: str) -> str:
    """Formata telefone removendo caracteres especiais"""
    if pd.isna(telefone) or not telefone:
        return None
    # Remove todos os caracteres não numéricos
    telefone_limpo = re.sub(r'[^0-9]', '', str(telefone))
    return telefone_limpo if telefone_limpo else None

def normalizar_nome(nome: str) -> str:
    """Normaliza nome para comparação"""
    if not nome:
        return ""
    return nome.strip().lower()

@router.post("/importar-moradores")
async def importar_moradores(
    file: UploadFile = File(...),
    condominio_id: int = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Importa lista de moradores via CSV ou Excel
    Admin geral pode especificar condominio_id
    Outros usuários importam apenas para seu condomínio
    """
    
    # Verifica permissões
    is_admin = current_user.get("nivel") == 1 and current_user.get("user_id") == 12
    
    # Define o condomínio de destino
    if is_admin and condominio_id:
        # Admin pode escolher o condomínio
        target_condominio_id = condominio_id
    else:
        # Outros usuários só podem importar para seu próprio condomínio
        operador = db.query(Operador).filter(
            Operador.id == current_user.get("user_id")
        ).first()
        
        if not operador or not operador.Condomino_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Usuário não está associado a nenhum condomínio"
            )
        
        target_condominio_id = operador.Condomino_id
    
    # Verifica se o arquivo é CSV ou Excel
    if file.filename.endswith('.csv'):
        # Lê arquivo CSV
        contents = await file.read()
        df = pd.read_csv(io.StringIO(contents.decode('utf-8')))
    elif file.filename.endswith(('.xlsx', '.xls')):
        # Lê arquivo Excel
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato de arquivo não suportado. Use CSV ou Excel."
        )
    
    # Normaliza nomes das colunas
    df.columns = df.columns.str.strip().str.lower()
    
    # Trata variações comuns de nomes de colunas
    df.rename(columns={'e-mail': 'email', 'e_mail': 'email'}, inplace=True)
    
    # Verifica colunas obrigatórias
    colunas_obrigatorias = ['nome', 'apto']
    colunas_existentes = df.columns.tolist()
    colunas_faltantes = [col for col in colunas_obrigatorias if col not in colunas_existentes]
    
    # Tenta mapear "apartamento" para "apto" se necessário
    if 'apartamento' in colunas_existentes and 'apto' not in colunas_existentes:
        df['apto'] = df['apartamento']
        colunas_faltantes = [col for col in colunas_faltantes if col != 'apto']
    
    if colunas_faltantes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Colunas obrigatórias faltando: {', '.join(colunas_faltantes)}"
        )
    
    # Resultados da importação
    resultados = {
        "total": len(df),
        "sucesso": 0,
        "erros": [],
        "duplicados": [],
        "atualizados": [],
        "detalhes": []
    }
    
    # Busca todos os moradores existentes do condomínio para verificação de duplicados
    moradores_existentes = db.query(Morador).filter(
        Morador.condominio_id == target_condominio_id
    ).all()
    
    # Cria dicionário para busca rápida por nome normalizado
    moradores_dict = {}
    for morador in moradores_existentes:
        nome_norm = normalizar_nome(morador.nome)
        if nome_norm not in moradores_dict:
            moradores_dict[nome_norm] = []
        moradores_dict[nome_norm].append(morador)
    
    # Processa cada linha
    for index, row in df.iterrows():
        try:
            linha_num = index + 2  # +2 porque índice começa em 0 e tem cabeçalho
            
            # Extrai dados
            nome = str(row.get('nome', '')).strip() if pd.notna(row.get('nome')) else ''
            apto = str(row.get('apto', '')).strip() if pd.notna(row.get('apto')) else ''
            bloco = str(row.get('bloco', '')).strip() if pd.notna(row.get('bloco')) else ''
            telefone = formatar_telefone(row.get('telefone', ''))
            email = str(row.get('email', '')).strip() if pd.notna(row.get('email')) else ''
            
            # Validações
            erros_linha = []
            
            if not nome:
                erros_linha.append("Nome é obrigatório")
            
            if not bloco:
                erros_linha.append("Bloco é obrigatório")
            
            if not apto:
                erros_linha.append("Apartamento é obrigatório")
            
            if erros_linha:
                resultados["erros"].append({
                    "linha": linha_num,
                    "nome": nome,
                    "erros": erros_linha
                })
                continue
            
            # Verifica se já existe morador com mesmo nome no condomínio
            nome_normalizado = normalizar_nome(nome)
            moradores_mesmo_nome = moradores_dict.get(nome_normalizado, [])
            
            if moradores_mesmo_nome:
                # Verifica se é o mesmo apartamento/bloco
                morador_mesmo_local = None
                for morador in moradores_mesmo_nome:
                    # Se não tiver bloco, considera apenas o apartamento
                    if bloco and morador.bloco == bloco and morador.apartamento == apto:
                        morador_mesmo_local = morador
                        break
                    elif not bloco and morador.apartamento == apto:
                        morador_mesmo_local = morador
                        break
                
                if morador_mesmo_local:
                    # Atualiza telefone se houver mudança
                    atualizado = False
                    if telefone and telefone != morador_mesmo_local.telefone:
                        morador_mesmo_local.telefone = telefone
                        atualizado = True
                    
                    if atualizado:
                        db.commit()
                        resultados["atualizados"].append({
                            "linha": linha_num,
                            "nome": nome,
                            "bloco": bloco,
                            "apto": apto,
                            "campo_atualizado": "telefone"
                        })
                    else:
                        resultados["duplicados"].append({
                            "linha": linha_num,
                            "nome": nome,
                            "bloco": bloco,
                            "apto": apto,
                            "motivo": "Morador já cadastrado neste apartamento"
                        })
                    continue
                else:
                    # Mesmo nome mas apartamento diferente - permitir cadastro
                    pass
            
            # Cria novo morador
            novo_morador = Morador(
                nome=nome,
                telefone=telefone,
                email=email if email else None,
                condominio_id=target_condominio_id,
                bloco=bloco if bloco else None,
                apartamento=apto,
                ativo=True,
                data_cadastro=datetime.now()
            )
            
            db.add(novo_morador)
            db.commit()
            
            # Adiciona ao dicionário para verificações futuras
            if nome_normalizado not in moradores_dict:
                moradores_dict[nome_normalizado] = []
            moradores_dict[nome_normalizado].append(novo_morador)
            
            resultados["sucesso"] += 1
            resultados["detalhes"].append({
                "linha": linha_num,
                "nome": nome,
                "bloco": bloco,
                "apto": apto,
                "status": "Importado com sucesso"
            })
            
        except Exception as e:
            db.rollback()
            resultados["erros"].append({
                "linha": linha_num,
                "nome": nome if 'nome' in locals() else "Desconhecido",
                "erros": [f"Erro ao processar: {str(e)}"]
            })
    
    return resultados

@router.get("/template-importacao")
async def download_template():
    """
    Retorna um template de exemplo para importação
    """
    template_data = {
        "nome": ["João da Silva", "Maria Santos", "Pedro Oliveira"],
        "apto": [101, 102, 201],
        "bloco": ["A", "A", "B"],
        "telefone": ["(48) 99999-9999", "(48) 98888-8888", "(48) 97777-7777"]
    }
    
    df = pd.DataFrame(template_data)
    
    # Cria arquivo Excel em memória
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Moradores')
    
    output.seek(0)
    
    return {
        "filename": "template_moradores.xlsx",
        "content": output.getvalue().hex(),
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }

@router.get("/condominios")
async def listar_condominios(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Lista condomínios disponíveis para importação
    Admin vê todos, outros usuários não veem nada (importam automaticamente para seu condomínio)
    """
    is_admin = current_user.get("nivel") == 1 and current_user.get("user_id") == 12
    
    if not is_admin:
        return []
    
    # Admin pode ver todos os condomínios
    condominios = db.query(CondominioOperador).filter(
        CondominioOperador.ativo == 1
    ).all()
    
    return [
        {
            "id": c.Id,
            "nome": c.Nomedocondomino
        }
        for c in condominios
    ]
