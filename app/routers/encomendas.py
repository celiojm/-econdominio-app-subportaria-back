@router.get("/stats")
def get_stats(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    total = db.query(Encomenda).filter(Encomenda.condominio_id == current_user["condominio_id"]).count()
    pendentes = db.query(Encomenda).filter(
        Encomenda.condominio_id == current_user["condominio_id"],
        Encomenda.status == "pendente"
    ).count()
    entregues = db.query(Encomenda).filter(
        Encomenda.condominio_id == current_user["condominio_id"],
        Encomenda.status == "entregue"
    ).count()
    hoje = db.query(Encomenda).filter(
        Encomenda.condominio_id == current_user["condominio_id"],
        func.date(Encomenda.data_recebimento) == func.date(func.now())
    ).count()
    
    return {
        "total": total,
        "pendentes": pendentes,
        "entregues": entregues,
        "hoje": hoje
    }
