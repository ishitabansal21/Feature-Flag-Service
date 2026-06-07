CREATE TABLE IF NOT EXISTS flags (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(100) UNIQUE NOT NULL,
    description         TEXT DEFAULT '',
    enabled             BOOLEAN DEFAULT FALSE,
    rollout_percentage  INTEGER DEFAULT 0 CHECK (rollout_percentage BETWEEN 0 AND 100),
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS flag_audit (
    id          SERIAL PRIMARY KEY,
    flag_name   VARCHAR(100) NOT NULL,
    action      VARCHAR(50)  NOT NULL,
    changed_by  VARCHAR(100),
    changed_at  TIMESTAMP DEFAULT NOW()
);
