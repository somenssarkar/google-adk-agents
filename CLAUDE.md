# CLAUDE.md — AI Tutoring Platform (Google ADK)

This file defines the project conventions, architecture decisions, and guidelines for Claude Code
when working in this repository. Read this before making any changes.

---

## 1. Project Overview

An AI-powered school tutoring platform built on **Google ADK** (Agent Development Kit) with **Gemini 2.5 Flash**.
Students interact with a root orchestrator agent that routes their questions to specialized subject-tutor agents
and returns clean, textbook-style formatted responses.

**Planned deployment:** Google Cloud Run
**Planned data layer:** AlloyDB (student data, quiz datasets)
**Planned tool integration:** MCP (Model Context Protocol)
**Dataset source:** Hugging Face Hub (open educational datasets)

### Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent Framework | Google ADK (`google-adk`) |
| LLM | `gemini-2.5-flash` (default for all agents) |
| Code Execution | `BuiltInCodeExecutor` (sandboxed Python) |
| Web Search | `GoogleSearchTool` |
| Runtime | Python 3.12+ |
| Dev runner | `adk web` (local), Cloud Run (production) |

---

## 2. Repository Structure

```
google-adk-agents/
├── tutor_platform/                     # ADK agent package (entrypoint for `adk web`)
│   ├── __init__.py                 # Imports agent module (required by ADK)
│   ├── agent.py                    # Defines root_agent (orchestrator)
│   ├── .env                        # API keys — never commit
│   ├── tools/                      # Shared tool instances (import from here, never re-instantiate)
│   │   └── __init__.py             # Exports: google_search, url_context, code_executor
│   ├── subagents/                  # Self-contained subject-tutor and utility agents
│   │   ├── __init__.py
│   │   ├── math_tutor.py           # Math Tutor subagent
│   │   └── response_formatter.py   # Response Formatter subagent (shared)
│   └── prompts/                    # All agent instruction strings
│       ├── __init__.py
│       ├── root_agent_prompt.py
│       ├── math_tutor_prompt.py
│       └── response_formatter_prompt.py
├── CLAUDE.md                       # This file
└── README.md                       # Human-facing project overview
```

> **Note:** `tutor_platform/` is the ADK package root. `adk web` is run from the repo root,
> and ADK discovers the `root_agent` exported by `tutor_platform/agent.py`.

---

## 3. Agent Architecture

### 3.1 Hierarchy

```
root_tutor_agent  (LlmAgent — Orchestrator)
├── math_tutor_agent    (LlmAgent — Subject Tutor)
└── response_formatter  (LlmAgent — Shared Formatter)
```

### 3.2 Agent Roles

| Agent | Type | Role |
|-------|------|------|
| `root_tutor_agent` | `LlmAgent` | Understands query → routes to subject agent → triggers formatter |
| `math_tutor_agent` | `LlmAgent` | Solves math with code execution and search; zero hallucination |
| `response_formatter` | `LlmAgent` | Reformats raw solution into clean textbook-style output |

### 3.3 Orchestration Pattern

`root_tutor_agent` is an **LlmAgent with sub_agents**. In ADK, this means:
- Subagents are exposed to the root LLM as callable tools.
- The root LLM decides which subagent(s) to call based on the query and its instructions.
- Subagents execute and return results back to the root LLM context.
- The root LLM then calls the next subagent (formatter) or responds directly.

This is the **call-and-return** pattern — distinct from `transfer_to_agent`, which
is one-way and does not return control to the parent.

### 3.4 Session State Flow

```
Student query
    │
    ▼
root_tutor_agent
    │  calls
    ▼
math_tutor_agent ──── output_key='math_solution' ──► session state['math_solution']
    │                                                         │
    │  root agent calls formatter                             │ injected as {math_solution}
    ▼                                                         │
response_formatter ◄──────────────────────────────────────────┘
    │  output_key='formatted_response'
    ▼
session state['formatted_response'] → displayed to student
```

**Key mechanics:**
- `output_key='math_solution'` on `math_tutor_agent` writes its response to session state.
- ADK injects `{math_solution}` into `response_formatter`'s instruction template at call time.
- `output_key='formatted_response'` on `response_formatter` writes the final clean output.

### 3.5 Out-of-Scope Query Handling

If the student asks about a subject not yet supported, `root_tutor_agent` responds directly
(without calling any subagent) and lists the currently supported subjects. This logic lives
entirely in `prompts/root_agent_prompt.py`.

---

## 4. Coding Conventions

### 4.1 File Naming

| What | Convention | Example |
|------|-----------|---------|
| Subagent module | `<subject>_tutor.py` or `<utility>.py` | `math_tutor.py`, `response_formatter.py` |
| Prompt module | `<agent_name>_prompt.py` | `math_tutor_prompt.py` |
| Agent variable | `<name>_agent` | `math_tutor_agent`, `response_formatter_agent` |
| Prompt constant | `<AGENT_NAME>_INSTRUCTION` | `MATH_TUTOR_INSTRUCTION` |

### 4.2 Agent Naming (ADK `name=` field)

The `name=` field is used by ADK for routing and logs. Use `snake_case`.

| Agent | `name=` |
|-------|---------|
| Root orchestrator | `root_tutor_agent` |
| Math tutor | `math_tutor_agent` |
| Response formatter | `response_formatter` |
| Future: Physics | `physics_tutor_agent` |

### 4.3 output_key Conventions

- Subject-tutor agents: `output_key='<subject>_solution'` (e.g., `math_solution`, `physics_solution`)
- Response formatter: `output_key='formatted_response'` (always generic)

> **Future note:** When multiple subject agents exist, standardize to a single
> `subject_solution` key so the formatter instruction template doesn't need per-subject variants.
> Track this as a refactor task before adding the second subject agent.

### 4.4 Imports

Always use relative imports within the `tutor_platform` package:
```python
from ..prompts.math_tutor_prompt import MATH_TUTOR_INSTRUCTION  # correct
from tutor_platform.prompts.math_tutor_prompt import ...         # wrong
```

### 4.5 Model

Default model for all agents: `gemini-2.5-flash`. Do not hardcode model strings in
multiple places — if changing the model, update each agent's `model=` argument explicitly
and document the reason in a comment.

---

## 5. Agent Design Principles

1. **Single responsibility.** Each subagent does exactly one thing. The math tutor solves; the
   formatter formats. Neither crosses into the other's domain.

2. **Decoupled subagents.** Subagents must not import from each other. All coupling happens
   in `agent.py` (root) through `sub_agents=[...]`.

3. **Prompts live in `prompts/`.** Never inline long instruction strings directly in agent
   definitions. Always define the string as a constant in a dedicated prompt module and import it.

4. **Descriptions are routing signals.** The `description=` field of each subagent is read by
   the root LLM to decide which agent to call. Write descriptions from the perspective of
   *when to call this agent*, not just what it does.

5. **No callbacks for now.** The `_trim_history_for_formatter` callback was removed to baseline
   token behavior during development. Callbacks will be reintroduced after measuring token
   consumption across conversation lengths. Do not add `before_model_callback` or
   `after_model_callback` without a documented reason and measurement plan.

6. **No context/memory yet.** Cross-session student context (e.g., remembering a student's
   weak areas) is a planned future feature. Do not add memory tooling until the architecture
   for it is designed. Track in TODO below.

7. **Thinking budget (deferred optimization).** Gemini 2.5 Flash supports a `thinking_budget`
   parameter that controls how many tokens are used for internal chain-of-thought reasoning.
   When token efficiency work begins (TODO #1), configure per-agent:
   - `math_tutor_agent`: enable thinking (e.g., `thinking_budget=8192`) — complex reasoning needed
   - `response_formatter`: disable thinking (`thinking_budget=0`) — formatting only, no reasoning needed
   - Root orchestrator: low budget (e.g., `thinking_budget=1024`) — routing decision only

   ```python
   # Future pattern — do not add yet
   from google.genai import types
   Agent(
       model='gemini-2.5-flash',
       generate_content_config=types.GenerateContentConfig(
           thinking_config=types.ThinkingConfig(thinking_budget=0)
       ),
       ...
   )
   ```

8. **`include_contents` for token efficiency (deferred).** ADK's `include_contents` parameter
   on subagents controls whether full conversation history is forwarded. The formatter only
   needs the current solution, not prior turns. Set `include_contents="none"` on
   `response_formatter` when token efficiency work begins (TODO #1). Do not add yet.

---

## 6. Tools — What Each Agent Gets and Why

### 6.1 Tool Assignment by Agent

| Agent | `google_search` | `url_context` | `code_executor` | Why |
|-------|:-----------:|:----------:|:-----------:|-----|
| `math_tutor_agent` | ✓ | — | ✓ | Needs search + verified computation; url_context deferred (see note below) |
| `response_formatter` | — | — | — | Pure text transformation — no lookups needed, ever |
| `root_tutor_agent` | — | — | — | Routes only — no subject reasoning performed |
| Future STEM agents (Physics) | ✓ | — | ✓ | Same pattern as math |
| Future non-STEM agents (Geography, English) | ✓ | ✓ | — | No code_executor; url_context adds full-page reading |

> **Gemini API constraint and fix:** `code_execution` (built-in) and function-calling tools cannot be combined in the same request. ADK's `canonical_tools()` assembles the final tool list including the code_executor's `code_execution` entry. When it sees `len(tools) > 1`, it wraps `google_search` in a `GoogleSearchAgentTool` (a function call), causing a 400 conflict. **Fix:** instantiate `GoogleSearchTool(bypass_multi_tools_limit=True)` — this prevents ADK from ever wrapping `google_search` into a function-call agent tool, keeping it as a native Gemini built-in. Two native built-ins (`google_search` + `code_execution`) are fully compatible. This is already applied in `tutor_platform/tools/__init__.py`.

### 6.2 Shared Tools Module

All tool instances live in `tutor_platform/tools/__init__.py`. **Always import from there — never instantiate tools inside a subagent file.**

```python
# In any subagent that needs tools:
from ..tools import google_search, url_context, code_executor
```

This ensures:
- One instance shared across all agents (tools are stateless — this is safe and efficient)
- One place to update tool configuration (e.g., swap `google_search` → `enterprise_web_search`)
- No risk of agents drifting to different tool versions or configs

### 6.3 How GoogleSearchTool Works

`google_search` is **not a traditional function call**. It is Gemini-native grounding:
- ADK injects `types.Tool(google_search=types.GoogleSearch())` into the LLM request
- Gemini's infrastructure runs the search and inserts results into the model's context
- Python code never receives raw search results — the model synthesizes them directly
- **Billing:** Each prompt that triggers a search counts as one grounded prompt

When `google_search` is used alongside other tools, ADK automatically wraps it in an internal `GoogleSearchAgentTool` workaround. This is transparent — you do not need to manage it.

### 6.4 How url_context Works

`url_context` is also Gemini-native (Gemini 2+ only):
- Agent passes a URL; Gemini fetches and reads the full page content
- Ideal for: following a search result to its source, reading a full definition, fetching content from an official curriculum reference

**Current status:** Available in `tutor_platform/tools/__init__.py` but **not assigned to `math_tutor_agent`** due to the Gemini API constraint documented in Section 6.1. It will be used by future non-STEM subject agents (Geography, English, History) that do not require `code_executor`.

### 6.5 Forward-Looking Tools (Not Yet Implemented)

| Tool | When to Add | Purpose |
|------|-------------|---------|
| `enterprise_web_search` | Before Cloud Run prod | Replace `google_search` on Vertex AI for FERPA/COPPA data governance |
| `VertexAiRagRetrieval` | When curriculum corpus is built | Query curated textbook/curriculum content instead of raw web |
| `load_memory` / `preload_memory` | When student memory is designed | Cross-session progress tracking, personalisation |
| `load_artifacts` | When student uploads are needed | Students upload worksheets, photos of handwritten problems |

---

## 7. Extending the Platform — Adding a New Subject Agent

Follow these steps exactly when adding a new subject (e.g., Physics):

**Step 1 — Create the prompt file**
```
tutor_platform/prompts/physics_tutor_prompt.py
```
Define `PHYSICS_TUTOR_INSTRUCTION`. Model it after `math_tutor_prompt.py`.
- Core principles: accuracy, no hallucination, use code/search to verify
- Teaching methodology (internal only)
- Output: clean, natural explanation for the formatter to structure

**Step 2 — Create the subagent module**
```
tutor_platform/subagents/physics_tutor.py
```
```python
from google.adk.agents.llm_agent import Agent
from ..prompts.physics_tutor_prompt import PHYSICS_TUTOR_INSTRUCTION

physics_tutor_agent = Agent(
    model='gemini-2.5-flash',
    name='physics_tutor_agent',
    description="Expert physics tutor. Call for any physics question. ...",
    instruction=PHYSICS_TUTOR_INSTRUCTION,
    output_key='physics_solution',   # NOTE: see Step 4
)
```

**Step 3 — Register with the root orchestrator**
In `tutor_platform/agent.py`, add the new agent to `sub_agents`:
```python
from .subagents.physics_tutor import physics_tutor_agent

root_agent = Agent(
    ...
    sub_agents=[math_tutor_agent, physics_tutor_agent, response_formatter_agent],
)
```

**Step 4 — Address the formatter's input key (before adding second subject)**
Before shipping a second subject agent, refactor the formatter to use a generic
`subject_solution` key instead of `math_solution`. Update:
- `math_tutor_agent`: `output_key='subject_solution'`
- `physics_tutor_agent`: `output_key='subject_solution'`
- `response_formatter_prompt.py`: replace `{math_solution}` with `{subject_solution}`

**Step 5 — Update root agent prompt**
In `root_agent_prompt.py`, add Physics to the supported subjects list and routing rules.

**Step 6 — Test**
- Math questions still route correctly to math tutor
- Physics questions route correctly to physics tutor
- Out-of-scope now lists both Math and Physics
- Response formatter works for both

---

## 8. MCP Integration (Forward-Looking)

> **Status:** Planned. Not yet implemented.

MCP (Model Context Protocol) will be used to connect external tools and data sources to agents.
Planned use cases:
- AlloyDB queries for student performance data
- Quiz dataset lookups from curated educational content
- External curriculum APIs

**Integration pattern (when implemented):**
- Use `MCPToolset` from `google.adk.tools.mcp_tool.mcp_toolset`
- Each MCP server is a separate process/container, registered as a tool in the relevant agent
- Prefer connecting MCP tools to specific subject-tutor agents (not the root orchestrator) so
  tool scope stays narrow and routing descriptions remain accurate
- Store MCP server URLs and credentials in environment variables, never in code

```python
# Future pattern — do not add yet
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseServerParams

quiz_tool = MCPToolset(
    connection_params=SseServerParams(url="https://your-mcp-server/sse")
)
math_tutor_agent = Agent(
    name='math_tutor_agent',
    tools=[google_search, url_context, quiz_tool],   # MCP tool added alongside shared tools
    ...
)
```

**Authentication:** For Cloud Run service-to-service MCP calls, use service account identity
tokens (Cloud Run invoker role) — no API keys needed between internal services.

---

## 9. Dataset Pipeline (Forward-Looking)

> **Status:** Planned. Not yet implemented.

**Goal:** Ingest open educational datasets from Hugging Face Hub, store in AlloyDB, and use
them for student quizzes and concept-check questions.

**Planned pipeline:**
```
Hugging Face Hub
    │  (datasets library — streaming or batch download)
    ▼
Preprocessing script (Python)
    │  - Normalize schema: {question, answer, subject, difficulty, source}
    │  - Deduplicate and validate
    ▼
AlloyDB (Cloud SQL PostgreSQL-compatible)
    │  - Table: quiz_questions
    │  - Indexed by: subject, difficulty, topic_tag
    ▼
MCP Tool → Subject-Tutor Agent
    │  (agent queries AlloyDB for relevant quiz questions)
    ▼
Student interaction (quiz mode)
```

**Recommended Hugging Face datasets for math:**
| Dataset | Content | Size |
|---------|---------|------|
| `lighteval/MATH` | Competition math, 5 difficulty levels | 12,500 problems |
| `gsm8k` | Grade-school word problems with chain-of-thought | 8,500 problems |
| `meta-math/MetaMathQA` | Augmented math QA | Large |
| `TIGER-Lab/MathInstruct` | Diverse math instruction | Large |

**Schema (when implemented):**
```sql
CREATE TABLE problems (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(100),           -- 'huggingface:lighteval/MATH'
    subject VARCHAR(50),           -- 'algebra', 'geometry', etc.
    difficulty INT CHECK (difficulty BETWEEN 1 AND 5),
    problem_text TEXT NOT NULL,
    solution_text TEXT,
    solution_steps JSONB,          -- structured steps for hints
    metadata JSONB,                -- grade_level, topic_tags
    embedding VECTOR(768),         -- pgvector for semantic search
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- Semantic search index
CREATE INDEX ON problems USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
-- Filtering index
CREATE INDEX ON problems (subject, difficulty);
```

AlloyDB has native `pgvector` support — use it to find problems semantically similar to
what a student is struggling with, enabling adaptive difficulty.

- Difficulty levels: `1` (beginner) → `5` (advanced)
- All dataset ingestion scripts go in `scripts/data_pipeline/`

---

## 10. Cloud Run Deployment (Forward-Looking)

> **Status:** Planned. Not yet implemented.

**Planned setup:**
- Each ADK agent package runs as a Cloud Run service
- Environment variables for all secrets (never baked into image)
- `GOOGLE_GENAI_USE_VERTEXAI=1` in production (switch from API key to Vertex AI auth)
- Health check endpoint: ADK's built-in `/health` (confirm when implementing)
- Min instances: 1 (avoid cold start for students), Max: scale per load
- Timeout: `--timeout 300` — agentic chains with code execution can take 30–60s; default 60s is too short
- Concurrency: `--concurrency 1` initially (ADK in-memory session service is not thread-safe by default)

**Environment variables required:**
```
GOOGLE_GENAI_USE_VERTEXAI=0    # 0 for dev (API key), 1 for prod (Vertex AI)
GOOGLE_API_KEY=...             # Dev only — never in production
GOOGLE_CLOUD_PROJECT=...       # Production
GOOGLE_CLOUD_LOCATION=...      # Production (e.g., us-central1)
```

**`.env` file:** Present in `tutor_platform/.env` for local dev. This file is gitignored.
Never commit API keys.

---

## 11. Quality Standards

### Testing

- **No dedicated test suite yet.** Manual testing via `adk web` is the current approach.
- When adding tests: use `pytest` + `pytest-asyncio`. Place test files in `tests/` at repo root.
- Test each subagent independently using ADK's `Runner` + `InMemorySessionService`.
- Validate: correct routing, correct `output_key` values in state, formatter output structure.
- Use `adk eval` CLI with `.test.json` eval datasets for automated regression testing:
  ```bash
  adk eval tutor_platform/agent.py tutor_platform/evals/math_basic.test.json
  ```

**Test pattern (when implemented):**
```python
import pytest
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

async def run_agent(agent, message: str):
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="test", user_id="student1")
    runner = Runner(agent=agent, session_service=session_service, app_name="test")
    events = []
    async for event in runner.run_async(
        user_id="student1", session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)])
    ):
        events.append(event)
    return events

@pytest.mark.asyncio
async def test_math_routing():
    from tutor_platform.agent import root_agent
    events = await run_agent(root_agent, "What is 15 + 27?")
    final = next(e for e in events if e.is_final_response())
    assert "42" in final.content.parts[0].text
```

### Observability

- ADK emits structured traces in `adk web`. Use this for development debugging.
- In production (Cloud Run): integrate with Google Cloud Trace and Logging.
- Track token consumption per agent per turn — critical for cost management at scale.

### Error Handling

- Subject-tutor agents: if code execution fails, the agent must debug and retry before reporting.
  Never surface a raw Python traceback to the student.
- Root orchestrator: if a subagent fails to respond, the root agent should catch the failure
  and return a graceful message. (Currently not explicitly handled — add when moving to production.)
- Formatter: if `{math_solution}` is empty or missing, the formatter should return a safe fallback.
  (Currently not explicitly handled — add when moving to production.)

---

## 12. TODO — Tracked Future Work

These items are deferred by design, not forgotten:

| # | Item | Priority | Notes |
|---|------|----------|-------|
| 1 | **Token efficiency measurement** | High | Measure tokens per agent per turn across short, medium, and long sessions. Reintroduce `before_model_callback` history trimming once baseline is established. |
| 2 | **Cross-session student context/memory** | Medium | Design memory layer (AlloyDB or ADK memory tool) for tracking student progress, weak areas, and past sessions. Do not implement ad-hoc. |
| 3 | **Generic `subject_solution` state key** | High (before 2nd subject) | Rename `math_solution` → `subject_solution` across math tutor and formatter before adding any second subject agent. |
| 4 | **Production error handling** | High (before Cloud Run) | Add subagent failure handling in root orchestrator and empty-state handling in formatter. |
| 5 | **MCP tool integration** | Medium | Connect AlloyDB via MCP for quiz datasets and student data. |
| 6 | **Hugging Face dataset pipeline** | Medium | Build ingestion scripts and AlloyDB schema for educational datasets. |
| 7 | **Cloud Run deployment** | Medium | Containerize, set up Vertex AI auth, configure scaling. |
| 8 | **Test suite** | Medium | Add pytest-based agent unit tests using ADK's `Runner` + `InMemorySessionService`. Add `adk eval` eval datasets. |
| 9 | **thinking_budget + include_contents** | Low (after token measurement) | Disable thinking on formatter (`thinking_budget=0`), set `include_contents="none"` on formatter. Tune math tutor budget based on measured cost vs. quality. Do after TODO #1. |

---

## 13. Cost & Platform Considerations

> **Research date:** March 2025. Verify pricing at cloud.google.com/vertex-ai/pricing before
> making production cost decisions — Gemini pricing changes frequently.

### 13.1 Token Pricing — Gemini 2.5 Flash

Token rates are **identical** between Google AI Studio (API key) and Vertex AI:

| Tier | Input | Output (incl. thinking tokens) | Cached Input |
|------|-------|-------------------------------|--------------|
| Standard | $0.30 / 1M tokens | $2.50 / 1M tokens | $0.03 / 1M |
| Batch | $0.15 / 1M tokens | $1.25 / 1M tokens | — |

There is no cost penalty for switching to Vertex AI at the token level.

### 13.2 Google Search Grounding — Dominant Cost Driver

This is the most important cost line for this platform. The math tutor triggers grounded
searches frequently (theorem lookups, formula verification).

| Platform | Free quota | Paid rate |
|----------|-----------|-----------|
| API key (paid tier) | 1,500 grounded prompts/day | **$35 / 1,000 grounded prompts** |
| Vertex AI (standard) | 1,500 grounded prompts/day | **$35 / 1,000 grounded prompts** |
| Vertex AI (Enterprise Grounding) | — | $45 / 1,000 prompts + enterprise data guarantees |

**Scale example:** 1,000 student sessions/day × 2 grounded searches each = 2,000 grounded
prompts/day → 500 above free tier → ~$17.50/day ($525/month) in grounding alone.

**Implication for TODO #1 (token efficiency):** Reducing grounding calls on the math tutor
(e.g., cache common formula lookups, instruct the agent to batch searches) has a direct
and significant cost impact. This should be the first optimization target at scale.

### 13.3 API Key vs Vertex AI — When to Switch

The switch requires **zero agent code changes** — only environment variable changes:

```
# Dev (now):          GOOGLE_GENAI_USE_VERTEXAI=0, GOOGLE_API_KEY=...
# Production:         GOOGLE_GENAI_USE_VERTEXAI=1, GOOGLE_CLOUD_PROJECT=..., GOOGLE_CLOUD_LOCATION=...
```

**Switch trigger: before any real student data touches the system.** Reasons:

| Requirement | API Key | Vertex AI |
|-------------|---------|-----------|
| FERPA (student education records) | ❌ Not covered | ✅ Google Cloud FERPA agreement available |
| COPPA (users under 13) | ❌ No contractual support | ✅ Controls available |
| Data residency guarantee | ❌ Data can go anywhere | ✅ Region-locked (e.g., `us-central1`) |
| Audit logs (all API calls) | ❌ None | ✅ Cloud Logging with caller identity |
| SLA with financial credits | ❌ None | ✅ 99.9% monthly uptime |
| Secret management on Cloud Run | Manual (Secret Manager wiring) | Automatic (service account identity) |
| AlloyDB AI integration | Awkward split (needs Vertex IAM anyway) | Native unified IAM |

**Bottom line:** API key is correct for local development (free tier, zero setup friction).
Vertex AI is required for production — the compliance gap is non-negotiable for a platform
serving students.

---

## 14. Local Development

```bash
# Create and activate virtual environment (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install dependencies
pip install google-adk

# Set up environment
cp tutor_platform/.env.example tutor_platform/.env   # then fill in GOOGLE_API_KEY

# Run the agent locally
adk web   # from repo root — opens browser UI
```

**Required environment variables (local):**
```
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=<your-gemini-api-key>
```
