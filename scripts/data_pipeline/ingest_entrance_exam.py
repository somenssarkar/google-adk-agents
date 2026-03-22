#!/usr/bin/env python3
"""
Phase 2, Step 2.3a — Dataset Ingestion
Dataset : datavorous/entrance-exam-dataset (97.4K rows, CC-BY-4.0)
Target  : AlloyDB problems table

Prerequisites:
  1. AlloyDB Auth Proxy running on 127.0.0.1:5432
       Windows : run scripts/infra/start_proxy.ps1 in a separate PowerShell window
       Linux   : run scripts/infra/start_proxy.sh in a separate terminal
  2. pip install -r requirements.txt
  3. gcloud auth application-default login (for Vertex AI + Secret Manager)
  4. DB_PASSWORD env var set, OR gcloud authenticated for Secret Manager retrieval

Usage:
  # Step 1: explore dataset structure (no DB writes, no embeddings)
  python ingest_entrance_exam.py --explore

  # Step 2: dry run — map + embed 10 rows, skip DB write
  python ingest_entrance_exam.py --dry-run --limit 10

  # Step 3: ingest first 500 rows to verify end-to-end
  python ingest_entrance_exam.py --limit 500

  # Step 4: full ingestion
  python ingest_entrance_exam.py

Environment variables (all optional — defaults shown):
  DB_HOST     = 127.0.0.1       (Auth Proxy local address)
  DB_PORT     = 5432
  DB_NAME     = tutor_db
  DB_USER     = postgres
  DB_PASSWORD = <pulled from Secret Manager if unset>
  SECRET_NAME = alloydb-tutor-password
  GOOGLE_CLOUD_PROJECT  = genai-apac-demo-project
  GOOGLE_CLOUD_LOCATION = asia-southeast1
"""

import os
import sys
import json
import argparse
import logging
from collections import Counter
from typing import Optional

import psycopg2
import psycopg2.extras
from datasets import load_dataset
from tqdm import tqdm
import vertexai
from vertexai.language_models import TextEmbeddingModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

GCP_PROJECT  = os.environ.get("GOOGLE_CLOUD_PROJECT",  "genai-apac-demo-project")
GCP_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "asia-southeast1")
SECRET_NAME  = os.environ.get("SECRET_NAME",            "alloydb-tutor-password")

DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "tutor_db")
DB_USER = os.environ.get("DB_USER", "postgres")

DATASET_ID   = "datavorous/entrance-exam-dataset"
SOURCE_TAG   = f"huggingface:{DATASET_ID}"
EMBED_MODEL  = "text-embedding-005"
EMBED_DIM    = 768
EMBED_BATCH  = 100   # Vertex AI max batch for text-embedding-005
DEFAULT_BATCH = 50   # Rows per DB INSERT

# Normalise subject strings to our canonical values
SUBJECT_MAP = {
    "mathematics": "math",
    "math":        "math",
    "maths":       "math",
    "physics":     "physics",
    "chemistry":   "chemistry",
    "biology":     "biology",
    "zoology":     "biology",
    "botany":      "biology",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_db_password() -> str:
    """Return DB_PASSWORD env var, or pull from Secret Manager."""
    pw = os.environ.get("DB_PASSWORD", "")
    if pw:
        return pw
    log.info("DB_PASSWORD not set — retrieving from Secret Manager...")
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        resource = f"projects/{GCP_PROJECT}/secrets/{SECRET_NAME}/versions/latest"
        resp = client.access_secret_version(request={"name": resource})
        log.info("Password retrieved from Secret Manager.")
        return resp.payload.data.decode("UTF-8")
    except Exception as exc:
        log.error(f"Secret Manager retrieval failed: {exc}")
        log.error("Fix: set DB_PASSWORD=<password> env var, or run: gcloud auth application-default login")
        sys.exit(1)


def connect_db(password: str) -> psycopg2.extensions.connection:
    log.info(f"Connecting → {DB_HOST}:{DB_PORT}/{DB_NAME} (user={DB_USER})")
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=password,
        connect_timeout=10,
    )
    conn.autocommit = False
    log.info("Connected to AlloyDB.")
    return conn


def get_embed_model() -> TextEmbeddingModel:
    vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
    log.info(f"Initialised Vertex AI embedding model: {EMBED_MODEL}")
    return TextEmbeddingModel.from_pretrained(EMBED_MODEL)


def generate_embeddings(model: TextEmbeddingModel, texts: list[str]) -> list[list[float]]:
    """Embed texts in batches of EMBED_BATCH. Returns list of float lists."""
    results = []
    for start in range(0, len(texts), EMBED_BATCH):
        chunk = texts[start : start + EMBED_BATCH]
        embeddings = model.get_embeddings(chunk)
        results.extend([e.values for e in embeddings])
    return results


def vector_literal(values: list[float]) -> str:
    """Convert float list to PostgreSQL vector literal string."""
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


# ── Field mapping ─────────────────────────────────────────────────────────────

def _first(row: dict, *keys) -> str:
    """Return the first non-empty string found across candidate column names."""
    for k in keys:
        v = row.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def map_row(row: dict, idx: int) -> Optional[dict]:
    """
    Map one raw dataset row → problems table schema dict.

    Column names are based on the datavorous/entrance-exam-dataset structure.
    If --explore shows different column names, update the _first() calls below.
    """
    # ── Problem text ──
    problem_text = _first(row, "question", "Question", "problem", "Problem")
    if not problem_text:
        return None  # skip rows with no question

    # ── Subject ──
    raw_subj = _first(row, "subject", "Subject", "domain", "Domain").lower()
    subject = SUBJECT_MAP.get(raw_subj, raw_subj) or "unknown"

    # ── MCQ options ──
    # Try multiple common column name patterns
    opt_a = _first(row, "A", "a", "option_a", "Option A", "OptionA", "opa")
    opt_b = _first(row, "B", "b", "option_b", "Option B", "OptionB", "opb")
    opt_c = _first(row, "C", "c", "option_c", "Option C", "OptionC", "opc")
    opt_d = _first(row, "D", "d", "option_d", "Option D", "OptionD", "opd")
    options = None
    if opt_a or opt_b:
        options = {}
        for letter, val in [("A", opt_a), ("B", opt_b), ("C", opt_c), ("D", opt_d)]:
            if val:
                options[letter] = val

    # ── Correct answer ──
    raw_ans = _first(row, "answer", "Answer", "correct", "correct_answer", "ans")
    correct_option = None
    if raw_ans and raw_ans[0].upper() in "ABCD":
        correct_option = raw_ans[0].upper()

    # ── Solution / explanation ──
    solution_text = _first(
        row, "explanation", "Explanation", "solution", "Solution",
        "rationale", "Rationale", "hint", "Hint",
    ) or None

    # ── Difficulty (JEE/NEET = competitive entrance → default 4) ──
    raw_diff = row.get("difficulty") or row.get("Difficulty")
    try:
        difficulty = max(1, min(5, int(raw_diff)))
    except (TypeError, ValueError):
        difficulty = 4

    # ── Metadata ──
    metadata: dict = {"row_index": idx}
    topic = _first(row, "topic", "Topic", "chapter", "Chapter", "subject_area")
    if topic:
        metadata["topic_tags"] = [topic]
    exam = _first(row, "exam_type", "exam", "Exam", "source_exam")
    if exam:
        metadata["source_exam"] = exam

    return {
        "source":         SOURCE_TAG,
        "subject":        subject,
        "difficulty":     difficulty,
        "problem_text":   problem_text,
        "solution_text":  solution_text,
        "solution_steps": None,
        "options":        json.dumps(options) if options else None,
        "correct_option": correct_option,
        "metadata":       json.dumps(metadata),
        # embedding added by caller after Vertex AI call
    }


# ── DB insertion ──────────────────────────────────────────────────────────────

_INSERT_SQL = """
INSERT INTO problems (
    source, subject, difficulty,
    problem_text, solution_text, solution_steps,
    options, correct_option, metadata, embedding
)
VALUES %s
ON CONFLICT DO NOTHING
"""

_ROW_TEMPLATE = """(
    %(source)s,
    %(subject)s,
    %(difficulty)s,
    %(problem_text)s,
    %(solution_text)s,
    %(solution_steps)s,
    %(options)s::jsonb,
    %(correct_option)s,
    %(metadata)s::jsonb,
    %(embedding)s::vector
)"""


def insert_batch(cur, rows: list[dict]) -> None:
    psycopg2.extras.execute_values(
        cur, _INSERT_SQL, rows,
        template=_ROW_TEMPLATE,
        page_size=len(rows),
    )


# ── Explore mode ──────────────────────────────────────────────────────────────

def explore(dataset) -> None:
    print("\n" + "=" * 60)
    print("  DATASET EXPLORATION")
    print("=" * 60)
    splits = list(dataset.keys())
    print(f"Splits     : {splits}")
    split = dataset[splits[0]]
    print(f"Total rows : {len(split):,}")
    print(f"Columns    : {split.column_names}\n")

    for label, idx in [("Row 0", 0), ("Row 1", 1), ("Row 100", 100)]:
        print(f"── {label} " + "─" * 40)
        row = split[idx]
        for k, v in row.items():
            print(f"  {k!r:30s}: {str(v)[:100]!r}")
        print()

    # Subject distribution
    print("── Subject distribution (first 2000 rows) " + "─" * 18)
    subjects = []
    for i in range(min(2000, len(split))):
        row = split[i]
        s = (row.get("subject") or row.get("Subject") or "?")
        subjects.append(str(s).strip())
    for subj, cnt in Counter(subjects).most_common():
        print(f"  {subj:30s}: {cnt}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest datavorous/entrance-exam-dataset into AlloyDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--explore",    action="store_true", help="Print dataset structure and exit (no DB/embed)")
    parser.add_argument("--dry-run",    action="store_true", help="Map + embed rows but skip DB writes")
    parser.add_argument("--limit",      type=int, default=0,             help="Max rows to process (0 = all)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH, help=f"Rows per batch (default {DEFAULT_BATCH})")
    parser.add_argument("--skip-embed", action="store_true", help="Skip embedding generation (insert NULL embedding — for schema testing only)")
    args = parser.parse_args()

    log.info(f"Loading dataset: {DATASET_ID}")
    dataset = load_dataset(DATASET_ID, trust_remote_code=True)

    if args.explore:
        explore(dataset)
        return

    split_name = "train" if "train" in dataset else list(dataset.keys())[0]
    data = dataset[split_name]
    total = min(args.limit, len(data)) if args.limit else len(data)
    log.info(f"Split '{split_name}' — processing {total:,} of {len(data):,} rows")

    # Initialise connections
    conn = cur = None
    if not args.dry_run:
        password = get_db_password()
        conn = connect_db(password)
        cur = conn.cursor()

    embed_model = None
    if not args.skip_embed:
        embed_model = get_embed_model()

    # Counters
    inserted = skipped = errors = 0

    # Rolling buffers for batching
    buf_rows:  list[dict] = []
    buf_texts: list[str]  = []

    def flush():
        nonlocal inserted
        if not buf_rows:
            return

        # Generate embeddings
        if embed_model:
            log.info(f"  Embedding {len(buf_texts)} texts via Vertex AI...")
            embeddings = generate_embeddings(embed_model, buf_texts)
        else:
            embeddings = [None] * len(buf_rows)

        for row, emb in zip(buf_rows, embeddings):
            row["embedding"] = vector_literal(emb) if emb else None

        if not args.dry_run:
            insert_batch(cur, buf_rows)
            conn.commit()

        inserted += len(buf_rows)
        log.info(f"  ✓ Batch committed — total inserted: {inserted:,}")
        buf_rows.clear()
        buf_texts.clear()

    # Main loop
    with tqdm(total=total, unit="row", desc="Ingesting") as pbar:
        for i in range(total):
            try:
                raw = data[i]
                mapped = map_row(raw, i)
                if mapped is None:
                    skipped += 1
                else:
                    buf_rows.append(mapped)
                    buf_texts.append(mapped["problem_text"])
                    if len(buf_rows) >= args.batch_size:
                        flush()
            except Exception as exc:
                log.warning(f"Row {i}: {exc}")
                errors += 1
            pbar.update(1)

    flush()  # final partial batch

    if cur:
        cur.close()
    if conn:
        conn.close()

    print()
    log.info("=" * 50)
    log.info("  INGESTION COMPLETE")
    log.info("=" * 50)
    log.info(f"  Inserted : {inserted:,}")
    log.info(f"  Skipped  : {skipped:,}  (rows with no question text)")
    log.info(f"  Errors   : {errors:,}")
    log.info(f"  Total    : {total:,}")

    if args.dry_run:
        log.info("\n  [DRY RUN] No data was written to the database.")

    if inserted > 0:
        log.info("\n  Verify in AlloyDB Studio:")
        log.info("    SELECT subject, count(*) FROM problems GROUP BY subject ORDER BY count DESC;")
        log.info("    SELECT subject, difficulty, count(*) FROM problems GROUP BY subject, difficulty ORDER BY 1, 2;")


if __name__ == "__main__":
    main()
