-- ================================================================================
-- ARQUIVO: 01_criar_tabelas_afiliados.sql
-- PASTA:   ~/backend/afiliado/
-- CAMINHO: visionlpr@vps60688:~/backend/afiliado/01_criar_tabelas_afiliados.sql
-- ================================================================================
-- DESCRIÇÃO: Cria as tabelas necessárias para o sistema de afiliados
-- ================================================================================

USE AdmGeral;

-- Tabela de afiliados
CREATE TABLE IF NOT EXISTS afiliados (
    id INT AUTO_INCREMENT PRIMARY KEY,
    usuario_id INT UNSIGNED NOT NULL,
    codigo_afiliado VARCHAR(50) UNIQUE NOT NULL,
    tipo_comissao ENUM('primeiro_mes', 'recorrente') NOT NULL DEFAULT 'primeiro_mes',
    percentual DECIMAL(5,2) NOT NULL DEFAULT 100.00,
    ativo TINYINT(1) DEFAULT 1,
    data_cadastro DATETIME DEFAULT CURRENT_TIMESTAMP,
    data_atualizacao DATETIME ON UPDATE CURRENT_TIMESTAMP,
    observacoes TEXT,
    
    -- Constraints
    CONSTRAINT fk_afiliado_usuario FOREIGN KEY (usuario_id) 
        REFERENCES financeiro_usuarios(id) ON DELETE RESTRICT,
    
    INDEX idx_codigo_afiliado (codigo_afiliado),
    INDEX idx_usuario_id (usuario_id),
    INDEX idx_ativo (ativo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de vinculação afiliado-condomínio
CREATE TABLE IF NOT EXISTS afiliado_condominios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    afiliado_id INT NOT NULL,
    condominio_id INT NOT NULL,
    data_vinculo DATETIME DEFAULT CURRENT_TIMESTAMP,
    ip_origem VARCHAR(50),
    user_agent TEXT,
    ativo TINYINT(1) DEFAULT 1,
    
    -- Constraints
    CONSTRAINT fk_afiliado_vinculo FOREIGN KEY (afiliado_id) 
        REFERENCES afiliados(id) ON DELETE RESTRICT,
    CONSTRAINT fk_condominio_vinculo FOREIGN KEY (condominio_id) 
        REFERENCES condominios(id) ON DELETE RESTRICT,
    
    -- Garantir que um condomínio só pode ter um afiliado
    UNIQUE KEY uk_condominio_afiliado (condominio_id),
    
    INDEX idx_afiliado_id (afiliado_id),
    INDEX idx_condominio_id (condominio_id),
    INDEX idx_data_vinculo (data_vinculo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de comissões
CREATE TABLE IF NOT EXISTS afiliado_comissoes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    afiliado_id INT NOT NULL,
    condominio_id INT NOT NULL,
    cobranca_id INT NOT NULL,
    valor_comissao DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    status ENUM('pendente', 'liberada', 'paga', 'cancelada') DEFAULT 'pendente',
    tipo ENUM('primeira', 'recorrente') NOT NULL,
    motivo_bloqueio VARCHAR(100),
    data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP,
    data_liberacao DATETIME,
    data_pagamento DATETIME,
    observacoes TEXT,
    
    -- Constraints
    CONSTRAINT fk_comissao_afiliado FOREIGN KEY (afiliado_id) 
        REFERENCES afiliados(id) ON DELETE RESTRICT,
    CONSTRAINT fk_comissao_condominio FOREIGN KEY (condominio_id) 
        REFERENCES condominios(id) ON DELETE RESTRICT,
    CONSTRAINT fk_comissao_cobranca FOREIGN KEY (cobranca_id) 
        REFERENCES cobrancas(id_cobranca) ON DELETE RESTRICT,
    
    -- Garantir que não há duplicidade de comissão por cobrança
    UNIQUE KEY uk_comissao_cobranca (cobranca_id),
    
    INDEX idx_afiliado_id (afiliado_id),
    INDEX idx_condominio_id (condominio_id),
    INDEX idx_status (status),
    INDEX idx_tipo (tipo),
    INDEX idx_data_criacao (data_criacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de saques
CREATE TABLE IF NOT EXISTS afiliado_saques (
    id INT AUTO_INCREMENT PRIMARY KEY,
    afiliado_id INT NOT NULL,
    valor_solicitado DECIMAL(10,2) NOT NULL,
    valor_pago DECIMAL(10,2),
    status ENUM('solicitado', 'em_analise', 'aprovado', 'pago', 'rejeitado') DEFAULT 'solicitado',
    metodo_pagamento VARCHAR(50) DEFAULT 'pix',
    dados_pagamento TEXT,
    data_solicitacao DATETIME DEFAULT CURRENT_TIMESTAMP,
    data_aprovacao DATETIME,
    data_pagamento DATETIME,
    aprovado_por INT UNSIGNED,
    motivo_rejeicao TEXT,
    comprovante TEXT,
    observacoes TEXT,
    
    -- Constraints
    CONSTRAINT fk_saque_afiliado FOREIGN KEY (afiliado_id) 
        REFERENCES afiliados(id) ON DELETE RESTRICT,
    CONSTRAINT fk_saque_aprovador FOREIGN KEY (aprovado_por) 
        REFERENCES financeiro_usuarios(id) ON DELETE SET NULL,
    
    INDEX idx_afiliado_id (afiliado_id),
    INDEX idx_status (status),
    INDEX idx_data_solicitacao (data_solicitacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de logs de atividades do afiliado
CREATE TABLE IF NOT EXISTS afiliado_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    afiliado_id INT NOT NULL,
    acao VARCHAR(100) NOT NULL,
    descricao TEXT,
    ip_origem VARCHAR(50),
    user_agent TEXT,
    dados_extra TEXT,
    data_log DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    CONSTRAINT fk_log_afiliado FOREIGN KEY (afiliado_id) 
        REFERENCES afiliados(id) ON DELETE CASCADE,
    
    INDEX idx_afiliado_id (afiliado_id),
    INDEX idx_acao (acao),
    INDEX idx_data_log (data_log)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Inserir novo nível de permissão para afiliados
INSERT INTO permissoes (nivel, permissao, descricao) 
VALUES (6, 'Afiliado', 'Acesso ao painel de afiliados')
ON DUPLICATE KEY UPDATE 
    permissao = 'Afiliado',
    descricao = 'Acesso ao painel de afiliados';

-- Comentários descritivos
ALTER TABLE afiliados 
    COMMENT = 'Tabela de cadastro de afiliados do sistema';

ALTER TABLE afiliado_condominios 
    COMMENT = 'Vinculação entre afiliados e condomínios indicados';

ALTER TABLE afiliado_comissoes 
    COMMENT = 'Registro de comissões geradas, liberadas e pagas aos afiliados';

ALTER TABLE afiliado_saques 
    COMMENT = 'Solicitações de saque de comissões pelos afiliados';

ALTER TABLE afiliado_logs 
    COMMENT = 'Log de atividades e ações dos afiliados no sistema';
