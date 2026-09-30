# ================================================================================
# ARQUIVO: afiliado_scheduler.py
# PASTA:   ~/backend/afiliado/
# CAMINHO: visionlpr@vps60688:~/backend/afiliado/afiliado_scheduler.py
# ================================================================================
# DESCRIÇÃO: Tarefas agendadas para o sistema de afiliados
# ================================================================================

import schedule
import time
import logging
from datetime import datetime
from sqlalchemy.orm import Session

from app.database import SessionLocal
from .afiliado_service import liberar_comissoes_pendentes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def job_liberar_comissoes():
    """
    Job que libera comissões pendentes que já passaram da carência de 7 dias
    Executa diariamente
    """
    logger.info("=== Iniciando job de liberação de comissões ===")
    db = SessionLocal()
    
    try:
        quantidade = liberar_comissoes_pendentes(db)
        logger.info(f"Job concluído: {quantidade} comissões liberadas")
        
    except Exception as e:
        logger.error(f"Erro no job de liberação: {e}")
        
    finally:
        db.close()


def iniciar_scheduler():
    """Inicia o scheduler de tarefas do sistema de afiliados"""
    logger.info("Iniciando scheduler do sistema de afiliados")
    
    # Agendar job de liberação de comissões para rodar diariamente às 2h da manhã
    schedule.every().day.at("02:00").do(job_liberar_comissoes)
    
    # Também roda uma vez ao iniciar
    job_liberar_comissoes()
    
    logger.info("Scheduler configurado. Jobs agendados:")
    logger.info("- Liberar comissões: diariamente às 02:00")
    
    # Loop principal
    while True:
        schedule.run_pending()
        time.sleep(60)  # Verificar a cada 1 minuto


if __name__ == "__main__":
    """
    Para rodar o scheduler:
    
    1. Como processo separado:
       python -m afiliado.afiliado_scheduler
    
    2. Como serviço systemd (criar arquivo /etc/systemd/system/afiliado-scheduler.service):
       
       [Unit]
       Description=e-Condomínio Afiliados Scheduler
       After=network.target mysql.service
       
       [Service]
       Type=simple
       User=visionlpr
       WorkingDirectory=/home/visionlpr/backend
       Environment="PYTHONPATH=/home/visionlpr/backend"
       ExecStart=/usr/bin/python3 -m afiliado.afiliado_scheduler
       Restart=on-failure
       RestartSec=10
       
       [Install]
       WantedBy=multi-user.target
       
    3. Ativar o serviço:
       sudo systemctl daemon-reload
       sudo systemctl enable afiliado-scheduler
       sudo systemctl start afiliado-scheduler
       sudo systemctl status afiliado-scheduler
    
    4. Ver logs:
       sudo journalctl -u afiliado-scheduler -f
    """
    try:
        iniciar_scheduler()
    except KeyboardInterrupt:
        logger.info("Scheduler interrompido pelo usuário")
    except Exception as e:
        logger.error(f"Erro fatal no scheduler: {e}")
