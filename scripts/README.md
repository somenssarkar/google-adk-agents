# Scripts — Infrastructure Setup & Data Pipeline

This directory contains all scripts for provisioning the AlloyDB database and ingesting quiz
datasets. They were written as part of **Phase 2** of the AI Tutoring Platform hackathon roadmap
(MCP + Database layer).

---

## Directory Structure

```
scripts/
├── README.md                        ← You are here
├── infra/                           ← Cloud infrastructure setup (run once)
│   ├── env.sh.example               ← Bash env template (copy → env.sh, fill values)
│   ├── env.ps1.example              ← PowerShell env template (copy → env.ps1, fill values)
│   ├── env.sh                       ← Bash env config (gitignored — do not commit)
│   ├── env.ps1                      ← PowerShell env config (gitignored — do not commit)
│   ├── setup_alloydb.sh             ← Step 1: Create AlloyDB cluster + instance
│   ├── setup_iam.sh                 ← Step 2: Grant AlloyDB service agent Vertex AI access
│   ├── setup_database.sh            ← Step 3: Create database, extensions, schema
│   ├── setup_database.sql           ← SQL schema (run by setup_database.sh)
│   ├── start_proxy.sh               ← Start AlloyDB Auth Proxy (Linux/macOS)
│   └── start_proxy.ps1              ← Start AlloyDB Auth Proxy (Windows)
└── data_pipeline/                   ← Dataset ingestion (run after infra is ready)
    ├── requirements.txt             ← Python dependencies for ingestion scripts
    ├── ingest_gsm8k.py              ← Ingest openai/gsm8k (math, grade school)
    └── ingest_entrance_exam.py      ← Ingest datavorous/entrance-exam-dataset (multi-subject)
```

---

## Background — Why These Scripts Exist

The AI Tutoring Platform uses **AlloyDB** (Google Cloud's PostgreSQL-compatible database) to
store quiz questions from open educational datasets. AlloyDB was chosen specifically because it
supports:

- **pgvector** — stores 768-dimensional embeddings alongside each quiz question
- **ScaNN index** — AlloyDB-exclusive approximate nearest neighbour index, faster than IVFFlat,
  enables semantic similarity search (find easier problems on the same topic)
- **AlloyDB AI (`google_ml_integration`)** — generates embeddings directly in SQL via
  `google_ml.embedding()`, calling Vertex AI without extra application code

This database layer powers the `quiz_agent` in Phase 2 via MCP Toolbox for Databases.

---

## Prerequisites

### GCP Prerequisites
- Google Cloud project with billing enabled (`genai-apac-demo-project`)
- `gcloud` CLI authenticated: `gcloud auth login` and `gcloud auth application-default login`
- Owner or Editor role on the project (to enable APIs and create resources)

### Local Prerequisites
- `psql` client (for running `setup_database.sh`)
  - **Windows:** Install PostgreSQL tools from postgresql.org — only the CLI tools are needed
  - **macOS:** `brew install libpq && brew link --force libpq`
  - **Linux:** `sudo apt install postgresql-client`
- Python 3.12+ with a virtual environment (for data pipeline scripts)
- Access to Google Cloud Shell is an alternative to local `gcloud` setup

### Data Pipeline Prerequisites (additional)
```bash
cd scripts/data_pipeline
pip install -r requirements.txt
```

---

## Step-by-Step: Infrastructure Setup

> Run these once to provision AlloyDB and create the schema.
> All steps are idempotent — safe to re-run if interrupted.

### Step 0 — Configure environment

```bash
# Bash (Linux/macOS/Cloud Shell)
cp scripts/infra/env.sh.example scripts/infra/env.sh
# Edit env.sh — the defaults match the project; no changes needed unless you renamed resources
source scripts/infra/env.sh
```

```powershell
# PowerShell (Windows)
copy scripts\infra\env.ps1.example scripts\infra\env.ps1
# Edit env.ps1 if needed — defaults match the project
. scripts\infra\env.ps1
```

> **Note:** `DB_PASSWORD` is intentionally absent from both templates.
> `setup_alloydb.sh` will prompt you interactively and store the password in **Secret Manager**.
> It is never written to disk.

---

### Step 1 — Create AlloyDB cluster + instance

```bash
bash scripts/infra/setup_alloydb.sh
```

**What it does:**
1. Sets active GCP project
2. Enables required APIs: `alloydb`, `compute`, `servicenetworking`, `secretmanager`, `aiplatform`
3. Prompts for a DB password → stores it in Secret Manager (`alloydb-tutor-password`)
4. Sets up VPC Private Services Access (required for AlloyDB)
5. Creates AlloyDB cluster `tutor-cluster` in `asia-southeast1`
6. Creates primary instance `tutor-instance` (2 vCPU)
7. Retrieves and saves the instance private IP to `env.sh`

**Runtime:** 15–20 minutes (cluster + instance creation is slow — this is normal).

**Outcome:** AlloyDB instance running at a private IP. Public IP also enabled for local
development (see `setup_alloydb.sh` for the `--enable-public-ip` flag details).

---

### Step 2 — Grant AlloyDB service agent Vertex AI access

```bash
bash scripts/infra/setup_iam.sh
```

**What it does:**
- Identifies the AlloyDB service agent for your project:
  `service-{PROJECT_NUMBER}@gcp-sa-alloydb.iam.gserviceaccount.com`
- Grants it `roles/aiplatform.user`

**Why this is required:** The `google_ml_integration` extension uses this service account
to call the Vertex AI embedding API from inside SQL (`google_ml.embedding()`). Without this
grant, the in-database embedding function returns a permission error.

> Must run **after** Step 1 (the AlloyDB service agent is created when the cluster is created).

---

### Step 3 — Create database, extensions, and schema

```bash
bash scripts/infra/setup_database.sh
```

**What it does:**
1. Retrieves the DB password from Secret Manager
2. Creates the `tutor_db` database (skips if already exists)
3. Runs `setup_database.sql` which:
   - Enables extensions: `vector`, `alloydb_scann`, `google_ml_integration`
   - Creates the `problems` table (12 columns — see schema below)
   - Creates indexes: B-tree on `(subject, difficulty)`, GIN on `problem_text`

**Schema overview:**

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `source` | VARCHAR | Dataset origin, e.g. `huggingface:openai/gsm8k` |
| `subject` | VARCHAR | `math`, `physics`, `biology`, `chemistry`, `environmental_science` |
| `difficulty` | INT (1–5) | 1 = beginner, 5 = advanced |
| `problem_text` | TEXT | The question |
| `solution_text` | TEXT | Full solution |
| `solution_steps` | JSONB | Step-by-step breakdown (for hint delivery) |
| `options` | JSONB | MCQ choices: `{"A": "...", "B": "..."}` |
| `correct_option` | VARCHAR | `A`, `B`, `C`, or `D` |
| `metadata` | JSONB | `{topic_tags, source_exam, grade_level, answer_type}` |
| `embedding` | VECTOR(768) | text-embedding-005 vector for semantic search |
| `created_at` | TIMESTAMPTZ | Ingestion timestamp |

> **ScaNN index:** `setup_database.sql` includes the ScaNN index creation command but it is
> **commented out**. Create it only after ingesting at least a few hundred rows — ScaNN
> requires data to build its index structures. Uncomment and run manually after ingestion:
> ```sql
> CREATE INDEX ON problems USING scann (embedding cosine);
> ```

---

## Step-by-Step: Data Pipeline

> Run after infrastructure is ready. Keep the Auth Proxy running in a separate terminal
> if connecting via private IP (not needed if using the public IP directly).

### Optional: Start AlloyDB Auth Proxy (private IP connection)

```bash
# Linux/macOS
bash scripts/infra/start_proxy.sh
```

```powershell
# Windows
.\scripts\infra\start_proxy.ps1
```

The proxy auto-downloads its binary on first run. Keep the terminal open — the proxy must
stay running while ingestion scripts execute. If using the public IP (`34.124.206.1`), the
proxy is not required.

---

### Dataset 1 — GSM8K (math, grade school) ✅ Ingested

> **Status:** Already ingested — 8,792 problems in AlloyDB as of 2026-03-22.
> Re-running is safe (script uses `ON CONFLICT DO NOTHING`).

```bash
cd scripts/data_pipeline
python ingest_gsm8k.py --explore          # Preview dataset structure first
python ingest_gsm8k.py --limit 100        # Test batch of 100
python ingest_gsm8k.py                    # Full ingestion (train + test splits)
```

**What this ingests:**
- Source: `openai/gsm8k` (MIT license, Hugging Face Hub)
- 7,473 train + 1,319 test = 8,792 grade-school math word problems
- Each problem includes step-by-step solution; answers follow `#### <number>` format
- Difficulty: `2` (grade school), tags: `word_problem`, `arithmetic`
- Embeddings generated via Vertex AI `text-embedding-005` (768 dims)

**CLI flags:**

| Flag | Description |
|------|-------------|
| `--explore` | Print sample rows and stats, do not insert |
| `--dry-run` | Process and embed rows but do not insert |
| `--limit N` | Only process first N rows (for testing) |
| `--skip-embed` | Skip embedding generation (insert NULL for embedding) |
| `--splits train test` | Which dataset splits to ingest (default: both) |

---

### Dataset 2 — entrance-exam-dataset (multi-subject) ⚠️ Broken

> **Status:** Skip for now. The `datavorous/entrance-exam-dataset` Hugging Face loading script
> is deprecated and fails to load. The ingestion script (`ingest_entrance_exam.py`) is complete
> and ready but cannot be run until the dataset loader is fixed upstream or we find a mirror.
>
> Resume with **Dataset 3** (physics) next — see Phase 2.3c in CLAUDE.md §13.

```bash
# When the dataset is available again:
python ingest_entrance_exam.py --explore
python ingest_entrance_exam.py --subject math --limit 500
python ingest_entrance_exam.py --subject physics --limit 500
```

---

### Upcoming Datasets (not yet ingested)

| Phase | Dataset | Subject | Notes |
|-------|---------|---------|-------|
| 2.3c | `Zhengsh123/PHYSICS` (~8.3K, CC-BY-4.0) | Physics | 4 difficulty levels, 5 domains |
| 2.3c | `zhibei1204/PhysReason` (1.2K, MIT) | Physics | Challenge-level problems |
| 2.3d | `TIGER-Lab/MMLU-Pro` bio subset (717, MIT) | Biology | Has chain-of-thought explanations |
| 2.3d | `TIGER-Lab/MMLU-Pro` chem subset (1.1K, MIT) | Chemistry | 10-option MCQ → trim to 4 |
| 2.3e | AI-generated (Gemini + validation) | Env. Science | 600–800 questions, no dataset exists |

New ingestion scripts for these datasets will follow the same structure as `ingest_gsm8k.py`.

---

## Security Notes

- **Passwords are never stored in files.** `env.sh` and `env.ps1` do not contain `DB_PASSWORD`.
  The password is entered interactively during `setup_alloydb.sh` and stored in **Secret Manager**
  under the key `alloydb-tutor-password`. All scripts retrieve it at runtime via `gcloud`.
- **`PGPASSWORD` env var** is used transiently in shell scripts and unset immediately after use.
- **Application Default Credentials (ADC):** Ingestion scripts use ADC (`gcloud auth application-default login`),
  not API keys. No credentials are written to code or config files.
- **Vertex AI calls:** Made by the ingestion scripts (embedding generation) and by AlloyDB
  itself (in-database `google_ml.embedding()`). Both paths use IAM, not API keys.

---

## Common Issues

**`setup_alloydb.sh` fails at VPC peering:**
Private Services Access requires the `compute.networks.addPeering` permission. Ensure your
account has Owner or Editor role. Re-running the script after fixing permissions is safe.

**`setup_database.sh`: `psql: command not found`:**
Install the PostgreSQL client tools (see Prerequisites above). Only the client tools are
needed — not a full PostgreSQL server installation.

**`google_ml.embedding()` returns permission error:**
Run `setup_iam.sh` and wait 1–2 minutes for IAM propagation, then retry.

**Ingestion script: embedding generation is slow:**
Vertex AI embedding API is called in batches of 5 (configurable). For large datasets,
expect ~1–2 seconds per batch. Use `--limit N` to test connectivity before running the
full ingestion.

**Auth Proxy exits immediately:**
Ensure `gcloud auth application-default login` has been run in the same session. The proxy
uses ADC. Also verify `INSTANCE_URI` in `env.sh` matches your AlloyDB instance URI.

---

## Next Step: MCP Toolbox for Databases

With AlloyDB provisioned and data ingested, the next phase is **Phase 2.4 — MCP Toolbox for
Databases**. This will expose the `problems` table to the `quiz_agent` via three parameterized
SQL tools:

- `get-quiz-question(subject, difficulty)`
- `get-quiz-answer(problem_id)`
- `find-similar-easier-problems(topic_description, max_difficulty, subject)`

See CLAUDE.md §8.1 and §13 (Phase 2.4) for the implementation plan.
