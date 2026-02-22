"""
SQL-схема базы данных. 6 таблиц.
"""

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ─────────────────────────────────────────────────────────────
-- jobs: основная таблица задач
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    url             TEXT UNIQUE NOT NULL,
    chat_id         INTEGER NOT NULL,
    msg_id          INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- pending | authenticating | downloading | transcribing
    -- summarizing | exporting | done | error | cancelled
    retry_count     INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_url    ON jobs(url);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

-- ─────────────────────────────────────────────────────────────
-- assets: скачанные медиафайлы
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    asset_type          TEXT NOT NULL,   -- video | audio | voice | document
    original_filename   TEXT,
    mime_type           TEXT,
    temp_path           TEXT,            -- NULL после удаления
    file_size_bytes     INTEGER,
    duration_sec        REAL,
    downloaded_at       TEXT DEFAULT (datetime('now')),
    deleted_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_assets_job ON assets(job_id);

-- ─────────────────────────────────────────────────────────────
-- transcripts: результат транскрибации
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transcripts (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    full_text           TEXT NOT NULL,
    segments_json       TEXT NOT NULL,   -- JSON: [{start, end, text, avg_logprob}]
    language            TEXT,            -- 'ru', 'de', etc.
    model_used          TEXT,
    duration_sec        REAL,
    word_count          INTEGER,
    unrecognized_count  INTEGER DEFAULT 0,
    created_at          TEXT DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────
-- summaries: результат конспектирования
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS summaries (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    content             TEXT NOT NULL,
    model_used          TEXT,
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    chunks_count        INTEGER DEFAULT 1,
    summary_language    TEXT DEFAULT 'ru',
    created_at          TEXT DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────
-- exports: сгенерированные PDF
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS exports (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    export_type     TEXT NOT NULL,   -- 'transcript' | 'summary'
    file_path       TEXT NOT NULL,
    file_size_bytes INTEGER,
    page_count      INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_exports_job ON exports(job_id);

-- ─────────────────────────────────────────────────────────────
-- errors: аудит лог ошибок
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS errors (
    id              TEXT PRIMARY KEY,
    job_id          TEXT REFERENCES jobs(id),
    step            TEXT,
    error_type      TEXT NOT NULL,
    error_message   TEXT NOT NULL,
    stack_trace     TEXT,
    occurred_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_errors_job ON errors(job_id);
"""
