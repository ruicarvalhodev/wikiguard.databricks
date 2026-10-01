-- WikiGuard Lakebase DDL
-- Idempotent: safe to re-run at any time.
-- Run by wikiguard_app (native Postgres role); all objects are owned by that role.
-- Database: databricks_postgres   Schema: public

-- -----------------------------------------------------------------------
-- Enum: case_status
-- Postgres has no CREATE TYPE IF NOT EXISTS; use a DO block instead.
-- -----------------------------------------------------------------------
DO $$
BEGIN
    CREATE TYPE case_status AS ENUM (
        'open', 'in_review', 'escalated', 'resolved', 'dismissed'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- -----------------------------------------------------------------------
-- Table: reviewers
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reviewers (
    reviewer_id  SERIAL      PRIMARY KEY,
    display_name TEXT        NOT NULL,
    email        TEXT UNIQUE NOT NULL,
    wiki_focus   TEXT[],
    role         TEXT        NOT NULL DEFAULT 'reviewer',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------
-- Table: cases
-- Columns mirror gold.triage_candidates.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cases (
    case_id         BIGSERIAL   PRIMARY KEY,
    wiki            TEXT        NOT NULL,
    rev_id          BIGINT      NOT NULL,
    rev_old         BIGINT,
    lang            TEXT,
    page_title      TEXT        NOT NULL,
    editor          TEXT,
    is_temp_account BOOLEAN     NOT NULL DEFAULT false,
    byte_delta      INTEGER,
    patrol_state    TEXT,
    revert_risk     NUMERIC(5,4)
        CHECK (revert_risk IS NULL OR revert_risk BETWEEN 0 AND 1),
    model_version   TEXT,
    tier            CHAR(1)     NOT NULL CHECK (tier IN ('A','B','C','D','N')),
    priority        SMALLINT    NOT NULL,
    diff_url        TEXT,
    event_ts        TIMESTAMPTZ,
    status          case_status NOT NULL DEFAULT 'open',
    assigned_to     INTEGER     REFERENCES reviewers,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    UNIQUE (wiki, rev_id),
    -- resolved_at must be set iff status is resolved or dismissed.
    -- The cases_before_update trigger sets/clears it automatically on
    -- status transitions, so agent write tools only need to change status.
    CONSTRAINT chk_resolved_at CHECK (
        (status IN ('resolved', 'dismissed') AND resolved_at IS NOT NULL)
        OR (status NOT IN ('resolved', 'dismissed') AND resolved_at IS NULL)
    )
);

-- -----------------------------------------------------------------------
-- Table: case_notes
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS case_notes (
    note_id     BIGSERIAL   PRIMARY KEY,
    case_id     BIGINT      NOT NULL REFERENCES cases ON DELETE CASCADE,
    author_type TEXT        NOT NULL CHECK (author_type IN ('human', 'agent')),
    author_id   INTEGER     REFERENCES reviewers,
    body        TEXT        NOT NULL CHECK (trim(body) <> ''),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------
-- Table: watchlists
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watchlists (
    watchlist_id SERIAL      PRIMARY KEY,
    reviewer_id  INTEGER     NOT NULL REFERENCES reviewers,
    name         TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------
-- Table: watchlist_pages
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watchlist_pages (
    watchlist_id INTEGER NOT NULL REFERENCES watchlists ON DELETE CASCADE,
    wiki         TEXT    NOT NULL,
    page_title   TEXT    NOT NULL,
    PRIMARY KEY (watchlist_id, wiki, page_title)
);

-- -----------------------------------------------------------------------
-- Table: agent_actions
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_actions (
    action_id   BIGSERIAL   PRIMARY KEY,
    session_id  UUID        NOT NULL,
    tool_name   TEXT        NOT NULL,
    tool_input  JSONB,
    tool_output JSONB,
    is_write    BOOLEAN     NOT NULL,
    latency_ms  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------
-- Indexes (IF NOT EXISTS)
-- -----------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_cases_status_priority ON cases (status, priority DESC);
CREATE INDEX IF NOT EXISTS idx_cases_assigned_status ON cases (assigned_to, status);
CREATE INDEX IF NOT EXISTS idx_case_notes_case_id    ON case_notes (case_id);

-- -----------------------------------------------------------------------
-- Trigger function: maintain updated_at and resolved_at on cases.
--
-- Runs BEFORE UPDATE so it can set resolved_at = now() and satisfy
-- chk_resolved_at in the same statement — no two-step update needed.
-- Agent write tools therefore only need to change status.
-- -----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cases_before_update()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    -- Always stamp updated_at on every update.
    NEW.updated_at := now();

    -- Entering resolved or dismissed: stamp resolved_at.
    IF NEW.status IN ('resolved', 'dismissed')
       AND OLD.status NOT IN ('resolved', 'dismissed')
    THEN
        NEW.resolved_at := now();

    -- Leaving resolved or dismissed: clear resolved_at.
    ELSIF OLD.status IN ('resolved', 'dismissed')
          AND NEW.status NOT IN ('resolved', 'dismissed')
    THEN
        NEW.resolved_at := NULL;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_cases_before_update
BEFORE UPDATE ON cases
FOR EACH ROW EXECUTE FUNCTION cases_before_update();

-- -----------------------------------------------------------------------
-- REPLICA IDENTITY FULL
-- Required so Change Data Feed carries full before/after row images on
-- UPDATE and DELETE operations.
-- These ALTER TABLE statements are idempotent.
-- -----------------------------------------------------------------------
ALTER TABLE cases         REPLICA IDENTITY FULL;
ALTER TABLE case_notes    REPLICA IDENTITY FULL;
ALTER TABLE agent_actions REPLICA IDENTITY FULL;
