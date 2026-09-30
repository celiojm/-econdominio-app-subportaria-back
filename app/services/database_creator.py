def create_condominio_database(nome: str, condominio_id: int):
    """Placeholder para criação de banco de condomínio"""
    print(f"Criando banco para condomínio: {nome} (ID: {condominio_id})")
    return f"DB_{nome[:5].upper()}{condominio_id}"
