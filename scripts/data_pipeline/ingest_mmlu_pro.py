#!/usr/bin/env python3
"""
Phase 2, Step 2.3d — Dataset Ingestion: TIGER-Lab/MMLU-Pro (biology + chemistry)
Dataset : TIGER-Lab/MMLU-Pro (MIT license)
Target  : AlloyDB problems table — biology, chemistry, and physics subsets

MMLU-Pro is a rigorous MCQ benchmark with 10 answer options (A–J) and
chain-of-thought explanations. We ingest biology, chemistry, and physics subsets.
Approximate row counts: biology ~717, chemistry ~1,132, physics ~1,299.

Field mapping:
  question     → problem_text
  options      → JSONB array ["A. ...", "B. ...", ..., "J. ..."]
  answer       → correct_option  (single letter, e.g. 'A')
  cot_content  → solution_text   (chain-of-thought explanation)
  category     → subject         (biology → biology, chemistry → chemistry)
  difficulty   → difficulty      (mapped: easy=1, medium=3, hard=5)

Prerequisites:
  1. AlloyDB reachable (public IP or Auth Proxy on 127.0.0.1:5432)
  2. pip install -r requirements.txt
  3. DB_PASSWORD env var set (or ADC for Secret Manager)

Usage:
  # Explore dataset structure first
  python ingest_mmlu_pro.py --explore

  # Dry run
  python ingest_mmlu_pro.py --dry-run --limit 20

  # Physics only
  python ingest_mmlu_pro.py --subjects physics

  # Biology + chemistry + physics (default)
  python ingest_mmlu_pro.py

  # Full ingestion (biology ~717, chemistry ~1132, physics ~1299)
  python ingest_mmlu_pro.py
"""

import os
import sys
import json
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

DATASET_ID = "TIGER-Lab/MMLU-Pro"
SOURCE_TAG  = f"huggingface:{DATASET_ID}"
EMBED_MODEL = "text-embedding-005"
EMBED_BATCH = 100
DEFAULT_BATCH = 50

# Category strings in the dataset that map to our subjects
CATEGORY_TO_SUBJECT = {
    "biology":   "biology",
    "chemistry": "chemistry",
    "physics":   "physics",
}

# MMLU-Pro difficulty → our 1–5 scale.
# The dataset uses "easy"/"medium"/"hard" strings, or sometimes integers.
DIFFICULTY_MAP_STR = {"easy": 1, "medium": 3, "hard": 5}
DIFFICULTY_MAP_INT = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}

# Default difficulty if field is missing (MMLU-Pro is HS→undergrad level)
DEFAULT_DIFFICULTY = 3

# Letters for MMLU-Pro's 10 options (A–J)
OPTION_LETTERS = list("ABCDEFGHIJ")


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
        sys.exit(1)


def connect_db(password: str) -> psycopg2.extensions.connection:
    log.info(f"Connecting → {DB_HOST}:{DB_PORT}/{DB_NAME} (user={DB_USER})")
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=password, connect_timeout=10,
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


# ── Field mapping ─────────────────────────────────────────────────────────────

def _parse_difficulty(raw) -> int:
    if raw is None:
        return DEFAULT_DIFFICULTY
    if isinstance(raw, str):
        return DIFFICULTY_MAP_STR.get(raw.lower().strip(), DEFAULT_DIFFICULTY)
    if isinstance(raw, int):
        return DIFFICULTY_MAP_INT.get(raw, DEFAULT_DIFFICULTY)
    return DEFAULT_DIFFICULTY


def _build_options_json(options_list: list) -> str | None:
    """Convert ['option text', ...] to JSON array with letter prefixes.

    MMLU-Pro has 10 options (A–J). We label them A. B. C. ... J.
    The quiz_agent and check_answer tool handle multi-letter options correctly.
    """
    if not options_list:
        return None
    labelled = [
        f"{OPTION_LETTERS[i]}. {str(opt).strip()}"
        for i, opt in enumerate(options_list)
        if i < len(OPTION_LETTERS)
    ]
    return json.dumps(labelled)


def map_row(row: dict, idx: int, split: str, target_subjects: set[str]) -> dict | None:
    # Filter by subject
    category = str(row.get("category") or "").lower().strip()
    subject = CATEGORY_TO_SUBJECT.get(category)
    if subject not in target_subjects:
        return None

    problem_text = str(row.get("question") or "").strip()
    if not problem_text:
        return None

    # Options: list of strings → labelled JSON array
    raw_options = row.get("options") or []
    options_json = _build_options_json(raw_options) if raw_options else None

    # Correct answer: already a letter (A–J)
    correct_option = str(row.get("answer") or "").strip().upper() or None

    # Chain-of-thought explanation → solution_text
    solution_text = str(row.get("cot_content") or "").strip() or None

    difficulty = _parse_difficulty(row.get("difficulty"))

    # Topic tags from src field (original exam source)
    src = str(row.get("src") or "").strip()
    topic_tags = [category, src] if src else [category]

    metadata = {
        "row_index":    idx,
        "split":        split,
        "answer_type":  "mcq",
        "original_category": category,
        "src":          src,
        "topic_tags":   topic_tags,
        "source_exam":  "MMLU-Pro",
        "num_options":  len(raw_options),
    }

    return {
        "source":         SOURCE_TAG,
        "subject":        subject,
        "difficulty":     difficulty,
        "problem_text":   problem_text,
        "solution_text":  solution_text,
        "solution_steps": None,
        "options":        options_json,
        "correct_option": correct_option,
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
    print("  MMLU-Pro DATASET EXPLORATION")
    print("=" * 60)
    splits = list(dataset.keys())
    print(f"Splits: {splits}")
    for s in splits:
        print(f"  {s}: {len(dataset[s]):,} rows — columns: {dataset[s].column_names}")

    print("\n── Category distribution (all splits) ─────────────────────")
    for s in splits:
        cats = Counter(str(r.get("category", "?")).lower() for r in dataset[s])
        bio  = cats.get("biology", 0)
        chem = cats.get("chemistry", 0)
        phys = cats.get("physics", 0)
        print(f"  {s}: biology={bio:,}  chemistry={chem:,}  physics={phys:,}  (total categories: {len(cats)})")

    print("\n── Sample biology row ──────────────────────────────────────")
    first_split = splits[0]
    for row in dataset[first_split]:
        if str(row.get("category", "")).lower() == "biology":
            print(f"  question    : {str(row.get('question',''))[:200]}")
            opts = row.get("options", [])
            print(f"  options     : {opts[:3]}... ({len(opts)} total)")
            print(f"  answer      : {row.get('answer')}")
            print(f"  difficulty  : {row.get('difficulty')}")
            cot = str(row.get("cot_content", ""))[:300].replace("\n", " ↵ ")
            print(f"  cot_content : {cot}")
            break

    print("\n── Sample chemistry row ────────────────────────────────────")
    for row in dataset[first_split]:
        if str(row.get("category", "")).lower() == "chemistry":
            print(f"  question    : {str(row.get('question',''))[:200]}")
            print(f"  answer      : {row.get('answer')}")
            print(f"  difficulty  : {row.get('difficulty')}")
            break


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=f"Ingest {DATASET_ID} (bio+chem) into AlloyDB")
    parser.add_argument("--explore",    action="store_true", help="Print dataset structure and exit")
    parser.add_argument("--dry-run",    action="store_true", help="Map + embed rows but skip DB writes")
    parser.add_argument("--limit",      type=int, default=0,             help="Max rows per subject (0 = all)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH, help=f"Rows per batch (default {DEFAULT_BATCH})")
    parser.add_argument("--skip-embed", action="store_true",             help="Insert NULL embedding (schema test only)")
    parser.add_argument("--splits",     default="test,validation",       help="Comma-separated splits (default: test,validation)")
    parser.add_argument("--subjects",   default="biology,chemistry,physics", help="Subjects to ingest (default: biology,chemistry,physics)")
    args = parser.parse_args()

    log.info(f"Loading dataset: {DATASET_ID}")
    try:
        dataset = load_dataset(DATASET_ID)
    except Exception as exc:
        log.error(f"Failed to load dataset: {exc}")
        sys.exit(1)

    if args.explore:
        explore(dataset)
        return

    target_subjects = {s.strip() for s in args.subjects.split(",")}
    log.info(f"Ingesting subjects: {target_subjects}")

    conn = cur = None
    if not args.dry_run:
        password = get_db_password()
        conn = connect_db(password)
        cur = conn.cursor()

    embed_model = None
    if not args.skip_embed:
        embed_model = get_embed_model()

    total_inserted = total_skipped = total_errors = 0
    subject_counts: Counter = Counter()
    splits_to_run = [s.strip() for s in args.splits.split(",")]

    for split_name in splits_to_run:
        if split_name not in dataset:
            log.warning(f"Split '{split_name}' not found — available: {list(dataset.keys())}")
            continue

        data  = dataset[split_name]
        total = min(args.limit, len(data)) if args.limit else len(data)
        log.info(f"\nSplit '{split_name}': scanning {total:,} rows for {target_subjects}")

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
                    mapped = map_row(raw, i, split_name, target_subjects)
                    if mapped is None:
                        skipped += 1
                    else:
                        subject_counts[mapped["subject"]] += 1
                        buf_rows.append(mapped)
                        buf_texts.append(mapped["problem_text"])
                        if len(buf_rows) >= args.batch_size:
                            flush()
                except Exception as exc:
                    log.warning(f"Row {i}: {exc}")
                    errors += 1
                pbar.update(1)

        flush()
        log.info(f"Split '{split_name}' done — inserted={inserted:,} skipped={skipped} errors={errors}")
        total_inserted += inserted
        total_skipped  += skipped
        total_errors   += errors

    if cur:  cur.close()
    if conn: conn.close()

    log.info("=" * 50)
    log.info("  INGESTION COMPLETE")
    log.info("=" * 50)
    log.info(f"  Inserted : {total_inserted:,}")
    log.info(f"  Skipped  : {total_skipped:,}  (other subjects filtered out)")
    log.info(f"  Errors   : {total_errors:,}")
    for subj, count in subject_counts.items():
        log.info(f"  {subj:12s}: {count:,} rows")
    if args.dry_run:
        log.info("\n  [DRY RUN] No data was written.")
    if total_inserted > 0:
        log.info("\n  Verify in AlloyDB:")
        log.info("    SELECT subject, count(*) FROM problems WHERE subject IN ('biology','chemistry','physics') GROUP BY subject;")


if __name__ == "__main__":
    main()
