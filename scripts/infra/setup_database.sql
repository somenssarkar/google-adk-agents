-- =============================================================================
-- AlloyDB Database Setup for AI Tutoring Platform
-- =============================================================================
-- Purpose: Creates the tutor_db database, enables extensions, creates the
--          problems table with pgvector + ScaNN indexes, and configures
--          AlloyDB AI for in-database embedding generation.
--
-- Usage (run from Google Cloud Shell after setup_alloydb.sh and setup_iam.sh):
--
--   Step 1 — Create the database (connect as postgres to the default db):
--     psql -h <INSTANCE_IP> -U postgres -d postgres -c "CREATE DATABASE tutor_db;"
--
--   Step 2 — Run this script against tutor_db:
--     psql -h <INSTANCE_IP> -U postgres -d tutor_db -f setup_database.sql
--
-- =============================================================================

-- ---- Enable required extensions ----

-- pgvector: vector data type and similarity operators (<=> cosine, <-> L2)
CREATE EXTENSION IF NOT EXISTS vector;

-- AlloyDB ScaNN: Google's ScaNN index for fast approximate nearest neighbor
-- (AlloyDB-exclusive — significantly faster than IVFFlat)
CREATE EXTENSION IF NOT EXISTS alloydb_scann;

-- AlloyDB AI: enables google_ml.embedding() for in-database embedding generation
-- CASCADE also creates the google_ml_integration dependency
CREATE EXTENSION IF NOT EXISTS google_ml_integration CASCADE;

-- ---- Create the problems table ----

CREATE TABLE IF NOT EXISTS problems (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          VARCHAR(100),           -- 'huggingface:datavorous/entrance-exam-dataset'
    subject         VARCHAR(50) NOT NULL,   -- 'math', 'physics', 'biology', 'chemistry', 'environmental_science'
    difficulty      INT CHECK (difficulty BETWEEN 1 AND 5),
    problem_text    TEXT NOT NULL,
    solution_text   TEXT,
    solution_steps  JSONB,                  -- structured steps for LoopAgent hint delivery
    options         JSONB,                  -- MCQ answer choices: ["A. ...", "B. ...", "C. ...", "D. ..."]
    correct_option  VARCHAR(5),             -- 'A', 'B', 'C', 'D' for MCQ
    metadata        JSONB,                  -- {topic_tags, source_exam, grade_level, answer_type}
    embedding       VECTOR(768),            -- pgvector for semantic search (text-embedding-005)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ---- Create indexes ----

-- Filtering index: fast lookup by subject and difficulty
CREATE INDEX IF NOT EXISTS idx_problems_subject_difficulty
    ON problems (subject, difficulty);

-- Full-text search on problem_text (optional, useful for keyword search)
CREATE INDEX IF NOT EXISTS idx_problems_problem_text_gin
    ON problems USING gin (to_tsvector('english', problem_text));

-- ScaNN index for approximate nearest neighbor search on embeddings
-- NOTE: ScaNN index can only be created after the table has data (rows > 0).
--       Run this command AFTER ingesting the first dataset batch:
--
--   CREATE INDEX idx_problems_embedding_scann
--       ON problems USING scann (embedding cosine)
--       WITH (num_leaves = 100);
--
-- For now, exact KNN search works without an index (fine for < 100K rows during dev).

-- ---- Verify setup ----

-- Check extensions
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('vector', 'alloydb_scann', 'google_ml_integration');

-- Check table structure
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'problems'
ORDER BY ordinal_position;

-- Test AlloyDB AI embedding generation (requires setup_iam.sh to be run first)
-- Uncomment the line below to verify Vertex AI integration works:
-- SELECT google_ml.embedding('text-embedding-005', 'test query')::vector(768);

-- ---- Summary ----
DO $$
BEGIN
    RAISE NOTICE '=============================================';
    RAISE NOTICE 'Database setup complete!';
    RAISE NOTICE '=============================================';
    RAISE NOTICE 'Database:   tutor_db';
    RAISE NOTICE 'Extensions: vector, alloydb_scann, google_ml_integration';
    RAISE NOTICE 'Table:      problems (with pgvector VECTOR(768))';
    RAISE NOTICE 'Indexes:    subject+difficulty (B-tree), problem_text (GIN)';
    RAISE NOTICE '';
    RAISE NOTICE 'Next steps:';
    RAISE NOTICE '  1. Verify AlloyDB AI: uncomment the embedding test query above';
    RAISE NOTICE '  2. Run the dataset ingestion pipeline (Phase 2, Step 2.3a)';
    RAISE NOTICE '  3. After ingesting data, create the ScaNN index (see comment above)';
    RAISE NOTICE '=============================================';
END $$;
