# Trecho correto para meu-perfil
@router.get("/meu-perfil/{afiliado_id}", response_model=AfiliadoResponse)
def obter_perfil_afiliado(afiliado_id: int, db: Session = Depends(get_db)):
    """Obtém perfil do afiliado pelo ID do afiliado"""
    try:
        afiliado = db.execute(text("""
            SELECT
                id, afiliado_usuario_id, nome_completo, whatsapp, email, cpf,
                conta_pix, tipo_pix, codigo_afiliado, tipo_comissao,
                percentual, ativo, is_admin, data_cadastro
            FROM afiliados
            WHERE id = :afiliado_id AND ativo = 1
        """), {"afiliado_id": afiliado_id}).fetchone()
