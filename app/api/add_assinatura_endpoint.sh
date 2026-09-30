#!/bin/bash
# ==============================================================================
# PATCH: ADICIONAR ENDPOINT /storage/upload/assinatura
# ==============================================================================
# Este script adiciona APENAS o endpoint de assinatura sem mexer no resto

ARQUIVO="$HOME/backend/app/api/encomendas.py"
BACKUP="$HOME/backend/app/api/encomendas.py.backup-antes-patch-$(date +%Y%m%d-%H%M%S)"

echo "🔧 APLICANDO PATCH - ENDPOINT DE ASSINATURA"
echo "==========================================="
echo ""

# 1. Fazer backup
echo "📦 Fazendo backup..."
cp "$ARQUIVO" "$BACKUP"
echo "   Backup: $BACKUP"
echo ""

# 2. Verificar se endpoint já existe
if grep -q "@router.post.*storage/upload/assinatura" "$ARQUIVO"; then
    echo "⚠️  Endpoint já existe! Nada a fazer."
    exit 0
fi

# 3. Criar arquivo temporário com o novo endpoint
cat > /tmp/patch_assinatura.py << 'PATCH_END'

# ==============================================================================
# 🔥 NOVO ENDPOINT - UPLOAD DE ASSINATURA (ADICIONADO VIA PATCH)
# ==============================================================================

from pydantic import BaseModel

class UploadAssinaturaRequest(BaseModel):
    image: str
    encomenda_id: int

@router.post("/storage/upload/assinatura")
async def upload_assinatura(
    dados: UploadAssinaturaRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload de assinatura digital para o storage
    Endpoint específico para upload separado da assinatura
    """
    try:
        logger.info(f"📸 === UPLOAD DE ASSINATURA - Encomenda {dados.encomenda_id} ===")

        # Verificar se encomenda existe e pertence ao condomínio do usuário
        query = text("""
            SELECT id, condominio_id, status 
            FROM encomendas 
            WHERE id = :encomenda_id
        """)
        
        result = db.execute(query, {"encomenda_id": dados.encomenda_id})
        encomenda = result.fetchone()

        if not encomenda:
            logger.error(f"❌ Encomenda {dados.encomenda_id} não encontrada")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Encomenda não encontrada"
            )

        if encomenda.condominio_id != current_user.get("condominio_id"):
            logger.error(f"❌ Encomenda não pertence ao condomínio do usuário")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Sem permissão para esta encomenda"
            )

        # Processar e fazer upload da assinatura
        logger.info(f"📤 Enviando assinatura para storage...")
        
        assinatura_filename = await image_storage_service.process_and_upload_assinatura(
            dados.image,
            dados.encomenda_id
        )

        if not assinatura_filename:
            logger.error(f"❌ Falha ao enviar assinatura para storage")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Erro ao salvar assinatura no storage"
            )

        logger.info(f"✅ Assinatura salva: {assinatura_filename}")

        # Registrar no mapeamento (usar INSERT ... ON DUPLICATE KEY UPDATE)
        try:
            insert_query = text("""
                INSERT INTO imagens_mapeamento 
                (encomenda_id, tipo, nome_personalizado, nome_servidor)
                VALUES (:encomenda_id, 'assinatura', :nome_personalizado, :nome_servidor)
                ON DUPLICATE KEY UPDATE
                nome_servidor = VALUES(nome_servidor),
                data_upload = CURRENT_TIMESTAMP
            """)

            db.execute(insert_query, {
                "encomenda_id": dados.encomenda_id,
                "nome_personalizado": assinatura_filename,
                "nome_servidor": assinatura_filename
            })
            
            db.commit()
            logger.info(f"✅ Assinatura registrada no mapeamento")

        except Exception as e:
            logger.error(f"❌ Erro ao registrar no mapeamento: {str(e)}")
            # Não falhar se apenas o mapeamento falhou

        logger.info(f"✅ === UPLOAD DE ASSINATURA CONCLUÍDO ===")

        return {
            "success": True,
            "message": "Assinatura salva com sucesso",
            "filename": assinatura_filename,
            "encomenda_id": dados.encomenda_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao processar upload de assinatura: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao processar upload: {str(e)}"
        )

# ==============================================================================
# FIM DO PATCH
# ==============================================================================
PATCH_END

# 4. Adicionar o patch ao final do arquivo (antes do último comentário ou linha em branco)
echo "📝 Adicionando endpoint ao arquivo..."

# Remover últimas linhas em branco
sed -i -e :a -e '/^\s*$/d;N;ba' "$ARQUIVO"

# Adicionar patch
cat /tmp/patch_assinatura.py >> "$ARQUIVO"

# 5. Verificar se foi adicionado
if grep -q "upload_assinatura" "$ARQUIVO"; then
    echo "✅ Endpoint adicionado com sucesso!"
    echo ""
    echo "📊 Estatísticas:"
    echo "   Linhas do arquivo original: $(wc -l < "$BACKUP")"
    echo "   Linhas do arquivo atualizado: $(wc -l < "$ARQUIVO")"
    echo "   Linhas adicionadas: $(($(wc -l < "$ARQUIVO") - $(wc -l < "$BACKUP")))"
    echo ""
    echo "🔧 PRÓXIMO PASSO:"
    echo "   sudo systemctl restart backend"
else
    echo "❌ Erro ao adicionar endpoint!"
    echo "   Restaurando backup..."
    cp "$BACKUP" "$ARQUIVO"
    exit 1
fi

# Limpar temporário
rm -f /tmp/patch_assinatura.py
