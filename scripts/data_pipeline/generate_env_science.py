#!/usr/bin/env python3
"""
Phase 2, Step 2.3e — AI-Generated Environmental Science Questions
Generator : Gemini 2.5 Flash via Vertex AI (or Google AI Studio API key)
Validator : Second Gemini call to verify each question before ingestion
Target    : AlloyDB problems table, subject='environmental_science'

No suitable open dataset exists for environmental science. This script uses
Gemini to generate 600–800 MCQ questions across a defined topic taxonomy,
validates each via a second LLM pass, then ingests validated questions.

Topic taxonomy (15 topics × ~3 difficulties × ~15 questions ≈ 675 questions):
  ecosystems          climate_change       pollution
  natural_resources   conservation         renewable_energy
  biodiversity        soil_and_water       atmosphere
  food_chains         carbon_cycle         waste_management
  environmental_law   sustainability       oceans_and_marine

Difficulty mapping:
  1 = beginner (grade 6-8 level)
  3 = intermediate (grade 9-12 level)
  5 = advanced (undergraduate level)

Prerequisites:
  1. AlloyDB reachable (public IP or Auth Proxy on 127.0.0.1:5432)
  2. pip install -r requirements.txt
  3. Either:
       a. GOOGLE_API_KEY env var (Google AI Studio — local dev)
       b. GOOGLE_GENAI_USE_VERTEXAI=1 + gcloud auth (Vertex AI)
  4. DB_PASSWORD env var set

Usage:
  # Dry run — generate 5 questions, print them, skip DB
  python generate_env_science.py --dry-run --questions-per-slot 1 --topics ecosystems,climate_change

  # Generate and ingest all questions
  python generate_env_science.py

  # Control volume
  python generate_env_science.py --questions-per-slot 15
"""

import os
import sys
import json
import time
import argparse
import logging
import re
from collections import Counter

import psycopg2
import psycopg2.extras
from tqdm import tqdm

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

EMBED_MODEL   = "text-embedding-005"
EMBED_BATCH   = 100
DEFAULT_BATCH = 50
SOURCE_TAG    = "ai-generated:gemini-2.5-flash"
SUBJECT       = "environmental_science"

GEMINI_MODEL       = "gemini-2.5-flash"
GENERATION_DELAY_S = 1.0   # pause between generation calls to avoid rate limits
VALIDATION_DELAY_S = 0.5

# ── Topic taxonomy ─────────────────────────────────────────────────────────────

TOPICS = [
    ("ecosystems",        ["food_web", "biomes", "energy_flow", "nutrient_cycles"]),
    ("climate_change",    ["greenhouse_effect", "global_warming", "climate_impacts", "IPCC"]),
    ("pollution",         ["air_pollution", "water_pollution", "soil_contamination", "plastic_waste"]),
    ("natural_resources", ["fossil_fuels", "minerals", "freshwater", "deforestation"]),
    ("conservation",      ["protected_areas", "endangered_species", "habitat_restoration"]),
    ("renewable_energy",  ["solar", "wind", "hydroelectric", "geothermal", "biomass"]),
    ("biodiversity",      ["species_richness", "genetic_diversity", "ecosystem_services"]),
    ("soil_and_water",    ["soil_formation", "erosion", "water_cycle", "aquifers"]),
    ("atmosphere",        ["ozone_layer", "atmospheric_layers", "acid_rain", "smog"]),
    ("carbon_cycle",      ["carbon_sequestration", "photosynthesis", "respiration", "emissions"]),
    ("waste_management",  ["recycling", "landfill", "composting", "e-waste", "reduce_reuse"]),
    ("sustainability",    ["sustainable_development", "circular_economy", "SDGs", "ESG"]),
    ("oceans_and_marine", ["ocean_acidification", "coral_reefs", "marine_biodiversity", "overfishing"]),
    ("food_chains",       ["trophic_levels", "producers_consumers", "decomposers", "biomagnification"]),
    ("environmental_law", ["Paris_Agreement", "Kyoto_Protocol", "EPA", "CITES", "environmental_impact"]),
]

DIFFICULTIES = [
    (1, "beginner",     "Grade 6–8. Simple vocabulary, no calculations. Focus on definitions and basic concepts."),
    (3, "intermediate", "Grade 9–12. Cause-and-effect relationships, data interpretation, real-world examples."),
    (5, "advanced",     "Undergraduate level. Analysis, multi-factor reasoning, quantitative thinking where appropriate."),
]


# ── Gemini client ─────────────────────────────────────────────────────────────

def get_gemini_client():
    """Return a configured google-genai Client (unified SDK, already installed via google-adk)."""
    from google import genai

    use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "0") == "1"
    if use_vertex:
        log.info("Using Vertex AI for Gemini generation")
        return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    else:
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            log.error("GOOGLE_API_KEY not set and GOOGLE_GENAI_USE_VERTEXAI!=1")
            sys.exit(1)
        log.info("Using Google AI Studio for Gemini generation")
        return genai.Client(api_key=api_key)


def call_gemini(client, prompt: str) -> str:
    """Call Gemini and return the text response."""
    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text.strip()
    except Exception as exc:
        log.warning(f"Gemini call failed: {exc}")
        return ""


# ── Prompt templates ──────────────────────────────────────────────────────────

GENERATION_PROMPT = """You are an expert environmental science educator creating quiz questions
for an AI tutoring platform. Generate exactly {n} multiple-choice questions about "{topic}".

Sub-topics to draw from: {subtopics}
Difficulty: {difficulty_label}
Level guidance: {level_guidance}

STRICT OUTPUT FORMAT — output ONLY a JSON array, no markdown, no extra text:
[
  {{
    "question": "Full question text here?",
    "options": ["A. First option", "B. Second option", "C. Third option", "D. Fourth option"],
    "correct": "A",
    "explanation": "2-3 sentence explanation of why the correct answer is right and the key concept."
  }},
  ...
]

Rules:
- Each question must have exactly 4 options labeled A, B, C, D
- Options must be plausible but only one correct
- Questions must be factually accurate and curriculum-aligned
- Vary question types: definition, cause-effect, example identification, data reading
- Do NOT repeat questions from each other
- correct field must be exactly one of: A, B, C, D
"""

VALIDATION_PROMPT = """You are a curriculum quality reviewer for an environmental science quiz platform.
Review this MCQ question and respond with VALID or INVALID.

Question: {question}
Options: {options}
Stated correct answer: {correct}
Explanation: {explanation}

Respond VALID if:
- The question is factually correct
- The stated answer is actually correct
- All 4 options are present and plausible
- The explanation is accurate

Respond INVALID if:
- The question contains a factual error
- The stated answer is wrong
- Options are missing or nonsensical
- The question is ambiguous or has multiple correct answers

Reply with ONLY one word: VALID or INVALID"""


# ── Generation + validation ────────────────────────────────────────────────────

def generate_questions(model, topic: str, subtopics: list[str],
                       difficulty: int, difficulty_label: str,
                       level_guidance: str, n: int) -> list[dict]:
    prompt = GENERATION_PROMPT.format(
        n=n,
        topic=topic.replace("_", " "),
        subtopics=", ".join(subtopics),
        difficulty_label=f"{difficulty_label} (difficulty {difficulty}/5)",
        level_guidance=level_guidance,
    )
    raw = call_gemini(model, prompt)
    if not raw:
        return []

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        items = json.loads(raw)
        if isinstance(items, list):
            return items
    except json.JSONDecodeError as exc:
        log.warning(f"JSON parse error for topic={topic} diff={difficulty}: {exc}")
        log.debug(f"Raw output: {raw[:500]}")
    return []


def validate_question(model, q: dict) -> bool:
    """Returns True if Gemini validation says VALID."""
    options_str = " | ".join(q.get("options", []))
    prompt = VALIDATION_PROMPT.format(
        question=q.get("question", ""),
        options=options_str,
        correct=q.get("correct", ""),
        explanation=q.get("explanation", ""),
    )
    response = call_gemini(model, prompt).upper().strip()
    return response.startswith("VALID")


def build_options_json(options: list[str]) -> str | None:
    """Options are already labelled 'A. ...', 'B. ...' — just serialise."""
    if not options:
        return None
    return json.dumps(options)


def questions_to_rows(questions: list[dict], topic: str, subtopics: list[str],
                      difficulty: int) -> list[dict]:
    rows = []
    for q in questions:
        problem_text = str(q.get("question") or "").strip()
        if not problem_text:
            continue
        options = q.get("options") or []
        correct = str(q.get("correct") or "").strip().upper()
        explanation = str(q.get("explanation") or "").strip()

        # Validate structure minimally
        if not correct or correct not in "ABCD" or len(options) < 4:
            continue

        metadata = {
            "answer_type":  "mcq",
            "topic":        topic,
            "subtopics":    subtopics,
            "source_exam":  "AI-generated",
            "grade_level":  {1: "grade_6_8", 3: "grade_9_12", 5: "undergraduate"}.get(difficulty, ""),
            "topic_tags":   [topic] + subtopics[:2],
        }

        rows.append({
            "source":         SOURCE_TAG,
            "subject":        SUBJECT,
            "difficulty":     difficulty,
            "problem_text":   problem_text,
            "solution_text":  explanation or None,
            "solution_steps": None,
            "options":        build_options_json(options),
            "correct_option": correct,
            "metadata":       json.dumps(metadata),
        })
    return rows


# ── Embedding + DB ─────────────────────────────────────────────────────────────

def get_embed_model():
    """Return a google-genai Client configured for embeddings via Vertex AI."""
    from google import genai
    return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)


def generate_embeddings(embed_client, texts: list[str]) -> list[list[float]]:
    results = []
    for start in range(0, len(texts), EMBED_BATCH):
        chunk = texts[start : start + EMBED_BATCH]
        response = embed_client.models.embed_content(model=EMBED_MODEL, contents=chunk)
        results.extend([e.values for e in response.embeddings])
    return results


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


def get_db_password() -> str:
    pw = os.environ.get("DB_PASSWORD", "")
    if pw:
        return pw
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        resource = f"projects/{GCP_PROJECT}/secrets/{SECRET_NAME}/versions/latest"
        resp = client.access_secret_version(request={"name": resource})
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
    return conn


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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and ingest environmental science questions")
    parser.add_argument("--dry-run",            action="store_true", help="Generate and validate but skip DB writes")
    parser.add_argument("--skip-validation",    action="store_true", help="Skip second-pass LLM validation (faster)")
    parser.add_argument("--skip-embed",         action="store_true", help="Insert NULL embedding (schema test only)")
    parser.add_argument("--questions-per-slot", type=int, default=15, help="Questions per topic+difficulty slot (default 15)")
    parser.add_argument("--topics",             default="",           help="Comma-separated topic subset (default: all)")
    parser.add_argument("--difficulties",       default="1,3,5",      help="Comma-separated difficulties (default: 1,3,5)")
    parser.add_argument("--batch-size",         type=int, default=DEFAULT_BATCH)
    args = parser.parse_args()

    # Filter topics
    if args.topics:
        topic_filter = {t.strip() for t in args.topics.split(",")}
        active_topics = [(t, s) for t, s in TOPICS if t in topic_filter]
    else:
        active_topics = TOPICS

    # Filter difficulties
    diff_filter = {int(d.strip()) for d in args.difficulties.split(",")}
    active_diffs = [(d, lbl, guidance) for d, lbl, guidance in DIFFICULTIES if d in diff_filter]

    total_slots = len(active_topics) * len(active_diffs)
    estimated   = total_slots * args.questions_per_slot
    log.info(f"Topics: {len(active_topics)}  Difficulties: {[d for d,_,_ in active_diffs]}")
    log.info(f"Slots: {total_slots}  Target questions: ~{estimated}")

    # Init Gemini
    gemini = get_gemini_client()

    # Init DB
    conn = cur = None
    if not args.dry_run:
        password = get_db_password()
        conn = connect_db(password)
        cur = conn.cursor()

    # Init embeddings
    embed_model = None
    if not args.skip_embed:
        embed_model = get_embed_model()

    # Generation loop — embed and commit each slot immediately so partial
    # runs are not lost if the process is interrupted.
    stats = Counter()
    total_inserted = 0
    dry_run_samples: list[dict] = []

    with tqdm(total=total_slots, unit="slot", desc="Generating") as pbar:
        for topic, subtopics in active_topics:
            for difficulty, diff_label, level_guidance in active_diffs:
                pbar.set_description(f"{topic} d={difficulty}")

                # Generate
                raw_questions = generate_questions(
                    gemini, topic, subtopics,
                    difficulty, diff_label, level_guidance,
                    args.questions_per_slot,
                )
                time.sleep(GENERATION_DELAY_S)

                # Validate
                valid_questions = []
                for q in raw_questions:
                    if args.skip_validation:
                        valid_questions.append(q)
                    else:
                        is_valid = validate_question(gemini, q)
                        time.sleep(VALIDATION_DELAY_S)
                        stats["validated"] += 1
                        if is_valid:
                            valid_questions.append(q)
                        else:
                            stats["rejected"] += 1

                # Map to DB rows
                rows = questions_to_rows(valid_questions, topic, subtopics, difficulty)
                stats["generated"] += len(raw_questions)
                stats["accepted"]  += len(rows)

                if not rows:
                    pbar.update(1)
                    continue

                # Embed this slot's rows immediately
                texts = [r["problem_text"] for r in rows]
                if embed_model:
                    embeddings = generate_embeddings(embed_model, texts)
                else:
                    embeddings = [None] * len(rows)

                for row, emb in zip(rows, embeddings):
                    row["embedding"] = vector_literal(emb) if emb else None

                # Commit immediately — progress is safe even if interrupted
                if not args.dry_run:
                    insert_batch(cur, rows)
                    conn.commit()
                    total_inserted += len(rows)
                    log.info(f"  ✓ slot {topic} d={difficulty}: {len(rows)} rows committed (total: {total_inserted:,})")
                else:
                    dry_run_samples.extend(rows)

                pbar.update(1)

    if cur:  cur.close()
    if conn: conn.close()

    log.info("=" * 50)
    log.info("  GENERATION COMPLETE")
    log.info("=" * 50)
    log.info(f"  Generated  : {stats['generated']:,}")
    log.info(f"  Accepted   : {stats['accepted']:,}")
    log.info(f"  Rejected   : {stats['rejected']:,}")
    log.info(f"  Inserted   : {total_inserted:,}")
    if args.dry_run:
        log.info("\n  [DRY RUN] No data was written.")
        log.info("\n  Sample accepted questions:")
        for row in dry_run_samples[:3]:
            print(f"\n  Q: {row['problem_text'][:150]}")
            print(f"  Options: {row['options'][:100]}...")
            print(f"  Answer: {row['correct_option']}")
            print(f"  Difficulty: {row['difficulty']}")
    if total_inserted > 0:
        log.info("\n  Verify in AlloyDB:")
        log.info("    SELECT count(*), difficulty FROM problems WHERE subject='environmental_science' GROUP BY difficulty;")


if __name__ == "__main__":
    main()
