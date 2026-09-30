USE AdmGeral;

ALTER TABLE encomendas
    ADD COLUMN whatsapp TINYINT(1) NOT NULL DEFAULT 0 AFTER morador_id;

CREATE INDEX idx_encomendas_whatsapp ON encomendas (whatsapp);

CREATE TABLE IF NOT EXISTS whatsapp_message_queue (
    id BIGINT NOT NULL AUTO_INCREMENT,
    morador_id BIGINT NULL,
    condominio_id BIGINT NULL,
    encomenda_id BIGINT NULL,
    telefone VARCHAR(20) NOT NULL,
    nome_morador VARCHAR(150) NULL,
    tipo_evento VARCHAR(50) NOT NULL,
    mensagem_original TEXT NULL,
    payload_json JSON NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    tentativas INT NOT NULL DEFAULT 0,
    processar_apos DATETIME NOT NULL,
    enviado_em DATETIME NULL,
    erro TEXT NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_wmq_status (status),
    INDEX idx_wmq_processar_apos (processar_apos),
    INDEX idx_wmq_telefone (telefone),
    INDEX idx_wmq_morador_id (morador_id),
    INDEX idx_wmq_status_processar (status, processar_apos)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
