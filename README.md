# AI Tutoring Platform — Google ADK

An AI-powered school tutoring platform built with **Google ADK** and **Gemini 2.5 Flash**
for the Google Cloud Gen AI Academy APAC hackathon. Students ask STEM questions in their
own language and receive clear, textbook-style answers — or take adaptive quizzes powered
by a 12,525-question vector-search database.

**Live demo:** https://tutor-frontend-319376906222.asia-southeast1.run.app

---

## What It Does

- **Multi-subject tutoring** — Math, Physics, Science (Biology, Chemistry, Environmental Science)
- **Adaptive quizzes** — 12,525 MCQ questions, semantic similarity for difficulty adjustment
- **Multilingual** — 12 APAC languages (Hindi, Bengali, Tamil, Telugu, Indonesian, Thai, Vietnamese, Filipino, Chinese, Japanese, Korean, English)
- **Student profiles** — Personalized by name, grade level (5–12 + undergraduate), and preferred language
- **Real-time streaming** — SSE-based response streaming via Streamlit chat UI

---

## Architecture

Three Cloud Run services backed by AlloyDB:

```
Student Browser
    │
    ▼  HTTPS
tutor-frontend  (Cloud Run — Streamlit)
    │  POST /run_sse  (SSE streaming)
    ▼
tutor-backend  (Cloud Run — FastAPI + ADK)
    │
    ├── root_tutor_agent  (LlmAgent — orchestrator, Gemini 2.5 Flash)
    │     │
    │     ├── math_pipeline       (SequentialAgent)
    │     │     ├── math_tutor_agent           google_search + code_executor
    │     │     └── response_formatter_math    include_contents='none'
    │     │
    │     ├── physics_pipeline    (SequentialAgent)
    │     │     ├── physics_tutor_agent        google_search + code_executor
    │     │     └── response_formatter_physics
    │     │
    │     ├── science_pipeline    (SequentialAgent)
    │     │     ├── science_tutor_agent        google_search only
    │     │     └── response_formatter_science
    │     │
    │     └── quiz_pipeline       (SequentialAgent)
    │           └── quiz_agent    MCPToolset only → MCP over HTTP
    │                                   │
    │                                   ▼
    │                         tutor-toolbox  (Cloud Run — MCP Toolbox for Databases)
    │                                   │  AlloyDB Go connector
    │                                   ▼
    │                         AlloyDB (asia-southeast1)
    │                           ├── problems  (12,525 quiz questions, pgvector + ScaNN)
    │                           └── adk_sessions  (DatabaseSessionService)
    │
    └── DatabaseSessionService → AlloyDB (session persistence across refreshes)
```

**Orchestration pattern:** `root_tutor_agent` is an `LlmAgent` with sub-agents wrapped in
`AgentTool`. Each pipeline is a `SequentialAgent`: the tutor/quiz agent runs first and writes
its output to `session_state['subject_solution']`, then the formatter reads `{subject_solution}`
and returns the final presentation. `include_contents='none'` on each formatter ensures it
sees only the current solution — not the full conversation history — eliminating cross-subject
formatting drift on long sessions.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent Framework | Google ADK (`google-adk==1.26.0`) |
| LLM | Gemini 2.5 Flash |
| Web Search | `GoogleSearchTool` (Gemini-native grounding) |
| Code Execution | `BuiltInCodeExecutor` (sandboxed Python) |
| Database | AlloyDB (pgvector + ScaNN + AlloyDB AI) |
| Database Protocol | MCP via MCP Toolbox for Databases (`genai-toolbox v0.30.0`) |
| Session Storage | `DatabaseSessionService` → AlloyDB |
| Frontend | Streamlit + httpx (SSE streaming) |
| Backend API | FastAPI (`get_fast_api_app()`) |
| Embeddings | Vertex AI `text-embedding-005` (768 dims) |
| Deployment | Google Cloud Run (3 services) |
| Runtime | Python 3.12 |

---

## Quiz Database

12,525 questions across 5 subjects, sourced from open educational datasets:

| Subject | Count | Source |
|---------|-------|--------|
| Math | 8,792 | `openai/gsm8k` (MIT) — grade-school word problems |
| Physics | 1,304 | `TIGER-Lab/MMLU-Pro` physics subset (MIT) |
| Biology | 722 | `TIGER-Lab/MMLU-Pro` biology subset (MIT) |
| Chemistry | 1,137 | `TIGER-Lab/MMLU-Pro` chemistry subset (MIT) |
| Environmental Science | 570 | AI-generated via Gemini 2.5 Flash + validation |
| **Total** | **12,525** | |

Semantic similarity search (`find-similar-easier-problems`) uses AlloyDB AI's
`google_ml.embedding()` to run Vertex AI embeddings directly inside the database — no
application-side embedding code in the quiz agent.

---

## Repository Structure

```
google-adk-agents/
├── tutor_platform/              # ADK agent package (entrypoint for adk web / uvicorn)
│   ├── agent.py                 # root_agent + all pipeline definitions
│   ├── __init__.py              # Required by ADK
│   ├── tools/                   # Shared tool instances (google_search, code_executor)
│   ├── subagents/               # Math, Physics, Science, Quiz agents + formatter factory
│   └── prompts/                 # All agent instruction strings
├── mcp_toolbox/
│   └── tools.yaml               # MCP Toolbox config (3 SQL tools)
├── scripts/
│   ├── infra/                   # AlloyDB provisioning + MCP Toolbox start scripts
│   └── data_pipeline/           # Dataset ingestion scripts
├── main.py                      # FastAPI backend (get_fast_api_app + DatabaseSessionService)
├── streamlit_app.py             # Student-facing Streamlit UI
├── Dockerfile.backend           # Cloud Run Service 2 — ADK FastAPI backend
├── Dockerfile.frontend          # Cloud Run Service 1 — Streamlit UI
├── Dockerfile.toolbox           # Cloud Run Service 3 — MCP Toolbox
├── requirements-backend.txt     # Backend Python dependencies
├── requirements-ui.txt          # UI + local dev Python dependencies
└── CLAUDE.md                    # Full architecture, ADK patterns, deployment guide
```

---

## Local Development

### Path A — ADK web UI (quickest, no Streamlit)

Runs tutoring agents only (no quiz — MCP Toolbox not needed).

```bash
# 1. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows PowerShell
# source .venv/bin/activate        # Linux / macOS

# 2. Install dependencies
pip install google-adk

# 3. Configure environment — create tutor_platform/.env:
# GOOGLE_GENAI_USE_VERTEXAI=0
# GOOGLE_API_KEY=<your-gemini-api-key>

# 4. Start
adk web
# Open http://127.0.0.1:8000/dev-ui/
# Select 'tutor_platform' from the dropdown (NOT mcp_toolbox)
```

### Path B — Full stack (Streamlit UI + quiz support)

Requires three terminals running in parallel.

**Terminal 1 — MCP Toolbox** (only needed for quiz requests):

```bash
export DB_PASSWORD=<your-alloydb-password>
bash scripts/infra/start_toolbox.sh        # Linux / macOS / Cloud Shell
# .\scripts\infra\start_toolbox.ps1        # Windows
```

**Terminal 2 — ADK FastAPI backend:**

```bash
pip install -r requirements-backend.txt -r requirements-ui.txt
uvicorn main:app --reload --port 8000
```

**Terminal 3 — Streamlit UI:**

```bash
streamlit run streamlit_app.py
# Open http://localhost:8501
```

Tutoring (math, physics, science) works without the MCP Toolbox. Only quiz requests require it.

---

## Cloud Run Deployment (Live)

Deployed on Google Cloud Run in `asia-southeast1`.

| Service | URL |
|---------|-----|
| Frontend (Streamlit) | https://tutor-frontend-319376906222.asia-southeast1.run.app |
| Backend (ADK FastAPI) | https://tutor-backend-319376906222.asia-southeast1.run.app |
| MCP Toolbox | https://tutor-toolbox-cg7k42k3uq-as.a.run.app |

### Key environment variables (tutor-backend)

| Variable | Notes |
|----------|-------|
| `GOOGLE_GENAI_USE_VERTEXAI` | `0` = API key (current); `1` = Vertex AI (after quota increase) |
| `GOOGLE_API_KEY` | Gemini API key — dev / demo only |
| `GOOGLE_CLOUD_PROJECT` | Your GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` — must be `us-central1` even though services run in `asia-southeast1` (Gemini endpoint DNS fails in asia-southeast1) |
| `MCP_TOOLBOX_URL` | Set to the tutor-toolbox Cloud Run service URL (append `/mcp`) |
| `SESSION_DB_URI` | AlloyDB connection string — stored in Secret Manager, injected at deploy time |

See [`CLAUDE.md §16`](CLAUDE.md) for full deployment lessons learned, known gotchas (Alpine + glibc, `PORT` reserved name, AlloyDB `@` in password URL-encoding), and cost-saving notes (stop AlloyDB instance when not testing — ~$0.18–0.20/vCPU-hr idle).

---

## Hackathon

**Google Cloud Gen AI Academy — APAC Edition**
Primary track: Track 2 — AI Agent Integration & External Systems (MCP + AlloyDB)
Also demonstrates: Track 1 (ADK multi-agent orchestration) + Track 3 (AlloyDB pgvector + ScaNN)
