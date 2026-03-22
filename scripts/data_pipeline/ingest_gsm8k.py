#!/usr/bin/env python3
"""
Phase 2, Step 2.3b — Dataset Ingestion: openai/gsm8k
Dataset : openai/gsm8k (8,792 grade-school math problems, MIT license)
Target  : AlloyDB problems table

GSM8K = Grade School Math 8K. Each problem is an open-ended word problem
with a multi-step solution ending in #### <answer>.

Prerequisites:
  1. AlloyDB Auth Proxy running on 127.0.0.1:5432
       Windows: run scripts/infra/start_proxy.ps1 in a separate PowerShell window
  2. pip install -r requirements.txt
  3. DB_PASSWORD env var set (or gcloud auth for Secret Manager)

Usage:
  # Explore dataset structure (no DB/embed)
  python ingest_gsm8k.py --explore

  # Dry run — embed 10 rows, skip DB write
  python ingest_gsm8k.py --dry-run --limit 10

  # Ingest first 100 rows to verify end-to-end
  python ingest_gsm8k.py --limit 100

  # Full ingestion (train=7473 + test=1319 = 8792 total)
  python ingest_gsm8k.py
"""

import os
import sys
import json
import re
import argparse
import logging
from collections import Counter

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

DATASET_ID    = "openai/gsm8k"
DATASET_CONFIG = "main"
SOURCE_TAG    = f"huggingface:{DATASET_ID}"
EMBED_MODEL   = "text-embedding-005"
EMBED_DIM     = 768
EMBED_BATCH   = 100
DEFAULT_BATCH = 50

# GSM8K is grade-school math — difficulty 2 out of 5
GSM8K_DIFFICULTY = 2

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_db_password() -> str:
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
        log.error("Fix: set DB_PASSWORD env var or run: gcloud auth application-default login")
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
    log.info(f"Vertex AI embedding model: {EMBED_MODEL}")
    return TextEmbeddingModel.from_pretrained(EMBED_MODEL)


def generate_embeddings(model: TextEmbeddingModel, texts: list[str]) -> list[list[float]]:
    results = []
    for start in range(0, len(texts), EMBED_BATCH):
        chunk = texts[start : start + EMBED_BATCH]
        embeddings = model.get_embeddings(chunk)
        results.extend([e.values for e in embeddings])
    return results


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


# ── GSM8K field mapping ───────────────────────────────────────────────────────

def parse_gsm8k_answer(raw_answer: str) -> tuple[str, str | None]:
    """
    GSM8K answer format:
      'Step 1: ...\nStep 2: ...\n#### 42'

    Returns (full_solution_text, numeric_answer_string)
    """
    parts = raw_answer.split("####")
    solution_text = raw_answer.strip()
    numeric = None
    if len(parts) == 2:
        numeric = parts[1].strip().replace(",", "")  # remove thousands separators
    return solution_text, numeric


def map_row(row: dict, idx: int, split: str) -> dict | None:
    question = (row.get("question") or "").strip()
    if not question:
        return None

    raw_answer = row.get("answer") or ""
    solution_text, numeric_answer = parse_gsm8k_answer(raw_answer)

    metadata = {
        "row_index":  idx,
        "split":      split,
        "answer_type": "numeric",
        "numeric_answer": numeric_answer,
        "source_exam": "GSM8K",
        "topic_tags": ["word_problem", "arithmetic"],
    }

    return {
        "source":         SOURCE_TAG,
        "subject":        "math",
        "difficulty":     GSM8K_DIFFICULTY,
        "problem_text":   question,
        "solution_text":  solution_text,
        "solution_steps": None,   # could parse steps later
        "options":        None,   # open-ended, no MCQ
        "correct_option": None,
        "metadata":       json.dumps(metadata),
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
    print("  GSM8K DATASET EXPLORATION")
    print("=" * 60)
    splits = list(dataset.keys())
    print(f"Splits: {splits}")
    for s in splits:
        print(f"  {s}: {len(dataset[s]):,} rows — columns: {dataset[s].column_names}")

    print("\n── Sample row (train[0]) ──────────────────────────────────")
    row = dataset["train"][0]
    print(f"  question : {row['question'][:200]}")
    print(f"  answer   : {row['answer'][:300]}")
    sol, num = parse_gsm8k_answer(row["answer"])
    print(f"\n  → parsed numeric answer: {num!r}")
    print(f"  → solution_text length : {len(sol)} chars")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest openai/gsm8k into AlloyDB")
    parser.add_argument("--explore",     action="store_true", help="Print dataset structure and exit")
    parser.add_argument("--dry-run",     action="store_true", help="Map + embed rows but skip DB writes")
    parser.add_argument("--limit",       type=int, default=0,             help="Max rows per split (0 = all)")
    parser.add_argument("--batch-size",  type=int, default=DEFAULT_BATCH, help=f"Rows per batch (default {DEFAULT_BATCH})")
    parser.add_argument("--skip-embed",  action="store_true",             help="Insert NULL embedding (schema test only)")
    parser.add_argument("--splits",      default="train,test",            help="Comma-separated splits to ingest (default: train,test)")
    args = parser.parse_args()

    log.info(f"Loading dataset: {DATASET_ID} (config={DATASET_CONFIG})")
    try:
        dataset = load_dataset(DATASET_ID, DATASET_CONFIG)
    except Exception:
        log.info("Config load failed, retrying without config name...")
        dataset = load_dataset(DATASET_ID)

    if args.explore:
        explore(dataset)
        return

    # Init connections
    conn = cur = None
    if not args.dry_run:
        password = get_db_password()
        conn = connect_db(password)
        cur = conn.cursor()

    embed_model = None
    if not args.skip_embed:
        embed_model = get_embed_model()

    # Counters across all splits
    total_inserted = total_skipped = total_errors = 0

    splits_to_run = [s.strip() for s in args.splits.split(",")]

    for split_name in splits_to_run:
        if split_name not in dataset:
            log.warning(f"Split '{split_name}' not found — skipping.")
            continue

        data  = dataset[split_name]
        total = min(args.limit, len(data)) if args.limit else len(data)
        log.info(f"\nSplit '{split_name}': processing {total:,} rows")

        inserted = skipped = errors = 0
        buf_rows:  list[dict] = []
        buf_texts: list[str]  = []

        def flush():
            nonlocal inserted
            if not buf_rows:
                return

            if embed_model:
                log.info(f"  Embedding {len(buf_texts)} texts...")
                embeddings = generate_embeddings(embed_model, buf_texts)
            else:
                embeddings = [None] * len(buf_rows)

            for row, emb in zip(buf_rows, embeddings):
                row["embedding"] = vector_literal(emb) if emb else None

            if not args.dry_run:
                insert_batch(cur, buf_rows)
                conn.commit()

            inserted += len(buf_rows)
            log.info(f"  ✓ {len(buf_rows)} rows committed (split total: {inserted:,})")
            buf_rows.clear()
            buf_texts.clear()

        with tqdm(total=total, unit="row", desc=f"  {split_name}") as pbar:
            for i in range(total):
                try:
                    raw    = data[i]
                    mapped = map_row(raw, i, split_name)
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

        flush()

        log.info(f"  Split '{split_name}' done — inserted={inserted:,} skipped={skipped} errors={errors}")
        total_inserted += inserted
        total_skipped  += skipped
        total_errors   += errors

    if cur:   cur.close()
    if conn:  conn.close()

    print()
    log.info("=" * 50)
    log.info("  INGESTION COMPLETE")
    log.info("=" * 50)
    log.info(f"  Inserted : {total_inserted:,}")
    log.info(f"  Skipped  : {total_skipped:,}")
    log.info(f"  Errors   : {total_errors:,}")
    if args.dry_run:
        log.info("\n  [DRY RUN] No data was written.")
    if total_inserted > 0:
        log.info("\n  Verify in AlloyDB Studio:")
        log.info("    SELECT count(*), difficulty FROM problems WHERE subject='math' GROUP BY difficulty;")
        log.info("    SELECT problem_text FROM problems ORDER BY created_at DESC LIMIT 3;")


if __name__ == "__main__":
    main()
