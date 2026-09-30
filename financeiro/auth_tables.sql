-- ================================================================================
-- ARQUIVO: auth_tables.sql
-- DESCRIÇÃO: Script SQL para criar tabelas de autenticação do módulo financeiro
-- BANCO: AdmGeral (MySQL)
-- ================================================================================

-- Tabela de usuários do financeiro
CREATE TABLE IF NOT EXISTS financeiro_usuarios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    senha_hash VARCHAR(255) NOT NULL,
    nome VARCHAR(100) NOT NULL,
    tipo VARCHAR(20) DEFAULT 'operador',  -- admin, operador, visualizador
    ativo BOOLEAN DEFAULT TRUE,
    
    -- Controle de acesso
    ultimo_login DATETIME NULL,
    login_falhos INT DEFAULT 0,
    bloqueado_ate DATETIME NULL,
    
    -- Auditoria
    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    criado_por INT NULL,
    
    INDEX idx_email (email),
    INDEX idx_ativo (ativo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Tabela de refresh tokens
CREATE TABLE IF NOT EXISTS financeiro_refresh_tokens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    usuario_id INT NOT NULL,
    token_hash VARCHAR(255) NOT NULL UNIQUE,
    dispositivo VARCHAR(255) NULL,
    ip VARCHAR(45) NULL,
    
    -- Validade
    expira_em DATETIME NOT NULL,
    revogado BOOLEAN DEFAULT FALSE,
    revogado_em DATETIME NULL,
    
    -- Auditoria
    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
    ultimo_uso DATETIME NULL,
    
    INDEX idx_token_hash (token_hash),
    INDEX idx_usuario (usuario_id),
    INDEX idx_expira (expira_em),
    INDEX idx_revogado (revogado),
    
    FOREIGN KEY (usuario_id) REFERENCES financeiro_usuarios(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Tabela de tentativas de login (rate limiting)
CREATE TABLE IF NOT EXISTS financeiro_login_attempts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ip VARCHAR(45) NOT NULL,
    email VARCHAR(255) NULL,
    sucesso BOOLEAN DEFAULT FALSE,
    tentativa_em DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_ip_tentativa (ip, tentativa_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ================================================================================
-- CRIAR USUÁRIO ADMIN INICIAL
-- Senha: Admin@123 (hash Argon2)
-- IMPORTANTE: Altere a senha após o primeiro login!
-- ================================================================================

-- Inserir admin padrão se não existir
INSERT IGNORE INTO financeiro_usuarios (email, senha_hash, nome, tipo, ativo)
VALUES (
    'admin@econdominio.com',
    '$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$EUkRDgHvpIHmRwI8dWJzjg',  -- Admin@123
    'Administrador',
    'admin',
    TRUE
);

-- ================================================================================
-- EVENTOS AGENDADOS PARA LIMPEZA AUTOMÁTICA
-- ================================================================================

-- Criar evento para limpar tokens expirados (executa diariamente)
DELIMITER //
CREATE EVENT IF NOT EXISTS cleanup_expired_tokens
ON SCHEDULE EVERY 1 DAY
STARTS CURRENT_TIMESTAMP
DO
BEGIN
    -- Remover tokens expirados há mais de 30 dias
    DELETE FROM financeiro_refresh_tokens 
    WHERE expira_em < DATE_SUB(NOW(), INTERVAL 30 DAY)
       OR (revogado = TRUE AND revogado_em < DATE_SUB(NOW(), INTERVAL 30 DAY));
    
    -- Remover tentativas de login antigas
    DELETE FROM financeiro_login_attempts 
    WHERE tentativa_em < DATE_SUB(NOW(), INTERVAL 24 HOUR);
END //
DELIMITER ;

-- Habilitar event scheduler (necessário para eventos funcionarem)
SET GLOBAL event_scheduler = ON;


-- ================================================================================
-- QUERIES ÚTEIS
-- ================================================================================

-- Listar usuários:
-- SELECT id, email, nome, tipo, ativo, ultimo_login FROM financeiro_usuarios;

-- Listar sessões ativas de um usuário:
-- SELECT * FROM financeiro_refresh_tokens WHERE usuario_id = ? AND revogado = FALSE AND expira_em > NOW();

-- Verificar tentativas de login de um IP:
-- SELECT * FROM financeiro_login_attempts WHERE ip = ? AND tentativa_em > DATE_SUB(NOW(), INTERVAL 5 MINUTE);

-- Revogar todas as sessões de um usuário:
-- UPDATE financeiro_refresh_tokens SET revogado = TRUE, revogado_em = NOW() WHERE usuario_id = ?;
