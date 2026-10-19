-- Schéma Postgres du workflow "Document Extraction - Multi-type Inbox"

-- Step 2a : emails filtrés
CREATE TABLE IF NOT EXISTS email_log (
    id               BIGSERIAL PRIMARY KEY,
    email_id         TEXT NOT NULL,
    sender           TEXT,
    subject          TEXT,
    attachment_count INT,
    status           TEXT NOT NULL,            -- skipped
    reason           TEXT,
    test_case_id     TEXT,
    test_run_id      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Step 9 : tables métier
CREATE TABLE IF NOT EXISTS invoices (
    id             BIGSERIAL PRIMARY KEY,
    attachment_id  TEXT NOT NULL,
    email_id       TEXT,
    vendor         TEXT,
    invoice_number TEXT,
    invoice_date   DATE,
    subtotal       NUMERIC(18, 3),
    tax            NUMERIC(18, 3),
    total          NUMERIC(18, 3),
    currency       TEXT,
    line_items     JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_file    TEXT,
    raw            JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS suppliers (
    id               BIGSERIAL PRIMARY KEY,
    attachment_id    TEXT NOT NULL,
    email_id         TEXT,
    company_name     TEXT,
    contact          JSONB,
    address          TEXT,
    products_offered JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_file      TEXT,
    raw              JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id                  BIGSERIAL PRIMARY KEY,
    attachment_id       TEXT NOT NULL,
    email_id            TEXT,
    doc_kind            TEXT NOT NULL DEFAULT 'manual',
    title               TEXT,
    equipment_reference TEXT,
    page_count          INT,
    summary             TEXT,
    content_text        TEXT,                  -- pour la recherche plein texte
    source_file         TEXT,
    raw                 JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Step 10 : une ligne par pièce jointe traitée (toutes branches)
CREATE TABLE IF NOT EXISTS processing_log (
    id                BIGSERIAL PRIMARY KEY,
    attachment_id     TEXT NOT NULL,
    email_id          TEXT,
    test_case_id      TEXT,
    test_run_id       TEXT,
    file_name         TEXT,
    mime_type         TEXT,
    text_source       TEXT,                    -- pdf_text_layer | ocr:easyocr | plain_text
    detected_type     TEXT,                    -- invoice | supplier_sheet | manual | irrelevant | unknown | unsupported
    class_confidence  NUMERIC(5, 3),
    confidence        NUMERIC(5, 3),
    validation_passed BOOLEAN,
    validation_errors JSONB,
    final_status      TEXT NOT NULL,           -- auto_approved | human_approved | human_corrected | human_rejected
                                               -- | review_timeout | discarded_irrelevant | unsupported_format
    review_mode       TEXT,                    -- auto | human | none
    target_table      TEXT,
    record_id         BIGINT,
    extracted         JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Step 11 : erreurs remontées par l'Error Trigger
CREATE TABLE IF NOT EXISTS error_log (
    id             BIGSERIAL PRIMARY KEY,
    workflow_name  TEXT,
    execution_id   TEXT,
    execution_url  TEXT,
    last_node      TEXT,
    error_message  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_processing_log_run ON processing_log (test_run_id, test_case_id);
CREATE INDEX IF NOT EXISTS idx_email_log_run ON email_log (test_run_id, test_case_id);

-- Vue de synthèse (couverture / taux d'auto-approbation par type)
CREATE OR REPLACE VIEW v_processing_summary WITH (security_invoker = true) AS
SELECT detected_type,
       count(*)                                                AS attachments,
       count(*) FILTER (WHERE final_status = 'auto_approved')  AS auto_approved,
       count(*) FILTER (WHERE review_mode = 'human')           AS human_reviewed,
       round(avg(confidence), 3)                               AS avg_confidence
FROM processing_log
GROUP BY detected_type;

-- Supabase : active la Row Level Security pour que ces tables ne soient PAS lisibles via l'API publique
-- (clé anon). n8n se connecte avec le rôle propriétaire "postgres", qui n'est pas soumis à la RLS.
ALTER TABLE email_log      ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices       ENABLE ROW LEVEL SECURITY;
ALTER TABLE suppliers      ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents      ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE error_log      ENABLE ROW LEVEL SECURITY;
