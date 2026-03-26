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
| Web Search | `GoogleSearchTool` (Gemini-native grounding) |
| Database | AlloyDB (pgvector + ScaNN + AlloyDB AI) |
| Database Protocol | MCP via MCP Toolbox for Databases |
| Frontend Protocol | AG-UI via CopilotKit + `ag-ui-adk` |
| Frontend | React/Next.js + CopilotKit (primary) or Streamlit (fallback) |
| Embeddings | Vertex AI `text-embedding-005` (768 dims) |
| Backend API | FastAPI (`get_fast_api_app()` + `DatabaseSessionService`) |
| Runtime | Python 3.12+ |
| Dev runner | `adk web` (local dev), Cloud Run (production) |
| Deployment | Google Cloud Run (3 services) + AlloyDB |

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

> **Note:** `tutor_platform/` is the ADK package root. Run `adk web` from the repo root.
> ADK lists all subdirectories in the UI dropdown — **always select `tutor_platform`**.
> Do not select `mcp_toolbox/` — it is a service config directory with no `root_agent`
> and will 500 if selected. `mcp_toolbox` may be the default/first selection alphabetically,
> so always verify the dropdown shows `tutor_platform` before sending any message.

---

## 3. Agent Architecture

### 3.1 Hierarchy

```
root_tutor_agent  (LlmAgent — Orchestrator)
├── math_pipeline       (SequentialAgent)
│   ├── math_tutor_agent        (LlmAgent — google_search + code_executor)
│   └── response_formatter_math (LlmAgent — include_contents='none')
├── physics_pipeline    (SequentialAgent)
│   ├── physics_tutor_agent     (LlmAgent — google_search + code_executor)
│   └── response_formatter_physics
├── science_pipeline    (SequentialAgent)
│   ├── science_tutor_agent     (LlmAgent — google_search)
│   └── response_formatter_science
└── quiz_pipeline       (SequentialAgent — Phase 2)
    ├── quiz_agent              (LlmAgent — MCPToolset only)
    └── response_formatter_quiz
                                    │
                                    ▼ MCP (HTTP)
                               Toolbox Server (tools.yaml)
                                    │
                                    ▼ AlloyDB connector
                               AlloyDB (pgvector + ScaNN + AlloyDB AI)
```

### 3.2 Agent Roles

| Agent | Type | Role |
|-------|------|------|
| `root_tutor_agent` | `LlmAgent` | Understands query → routes to subject/quiz pipeline → relays formatted response |
| `math_tutor_agent` | `LlmAgent` | Solves math with code execution and search; zero hallucination |
| `physics_tutor_agent` | `LlmAgent` | Solves physics with code execution and search; unit-consistent |
| `science_tutor_agent` | `LlmAgent` | Explains biology, chemistry, environmental science with search grounding |
| `quiz_agent` (Phase 2) | `LlmAgent` | Fetches quiz questions via MCP, evaluates answers, provides adaptive difficulty |
| `response_formatter_*` | `LlmAgent` | Reformats raw solution into clean textbook-style output (one instance per pipeline) |

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
    │  calls (AgentTool)
    ▼
[subject]_pipeline (SequentialAgent)
    │
    ├─► [subject]_tutor_agent ── output_key='subject_solution' ──► session state['subject_solution']
    │                                                                          │
    │   (SequentialAgent runs next)                                            │ injected as
    │                                                                          │ {subject_solution}
    ├─► before_agent_callback (_validate_solution) ◄───────────────────────────┤
    │       checks session state['subject_solution'] is non-empty             │
    │       returns graceful fallback if missing; proceeds if present          │
    │                                                                          │
    ▼                                                                          │
    response_formatter_[subject]  ◄────────────────────────────────────────────┘
    │   include_contents='none'  ← sees ONLY its instruction + {subject_solution}
    │   output_key='formatted_response'
    ▼
session state['formatted_response'] → AgentTool returns output → root agent relays verbatim
```

**Key mechanics:**
- `output_key='subject_solution'` on each tutor writes the verified solution to session state.
- ADK injects `{subject_solution}` into the formatter's instruction at the time the formatter
  makes its LLM call (after the tutor has run and written to session state).
- `include_contents='none'` on the formatter is the critical fix: the formatter receives
  ONLY its instruction (with `{subject_solution}` substituted). No conversation history.
  This prevents a long multi-turn history from confusing the formatter into formatting a
  response from an earlier turn instead of the current solution.
- `before_agent_callback` validates that `subject_solution` is non-empty before the LLM
  call. If the tutor failed silently, a graceful fallback is returned immediately.
- This design is also token-efficient: the formatter never pays for prior conversation turns.

**Root cause of the cross-subject bug (fixed by `include_contents='none'`):**
After 5-6 turns, the formatter's LLM call accumulated a long conversation history of prior
subject answers. Even though `{subject_solution}` in the instruction correctly contained the
new solution, the LLM's attention drifted to the dominant prior-turn content in the history
(e.g., physics answers) and formatted that instead of the current solution. Removing the
history entirely via `include_contents='none'` eliminates this confusion.

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
| Physics tutor | `physics_tutor_agent` |
| Science tutor | `science_tutor_agent` |
| Response formatter | `response_formatter_{pipeline_name}` (one instance per pipeline — see Section 6) |
| Quiz agent (Phase 2) | `quiz_agent` |
| Future: Geography | `geography_tutor_agent` |

### 4.3 output_key Conventions

- All subject-tutor agents: `output_key='subject_solution'` (single generic key shared across all subjects)
- Response formatter: `output_key='formatted_response'` (always generic)

> **Why a single `subject_solution` key:** Only one pipeline runs per student turn.
> Using the same key for all subjects means the formatter instruction template never
> needs per-subject variants. The formatter's `include_contents='none'` ensures it
> always reads the current turn's solution, not a stale value from a prior turn.

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

5. **Callbacks require a documented reason.** Do not add `before_agent_callback`,
   `after_agent_callback`, `before_model_callback`, or `after_model_callback` without
   a clear, documented purpose. Current callbacks in use:
   - `_validate_solution` on `response_formatter_*`: short-circuits the LLM call if
     `subject_solution` is empty, returning a graceful fallback without wasting tokens.
   Any future callbacks must follow the same pattern — document the reason in both the
   function docstring and a comment at the call site.

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

| Agent | `google_search` | `url_context` | `code_executor` | `MCPToolset` | Why |
|-------|:-----------:|:----------:|:-----------:|:-----------:|-----|
| `math_tutor_agent` | ✓ | — | ✓ | — | Search + verified computation. `bypass_multi_tools_limit=True` keeps both as native built-ins |
| `physics_tutor_agent` | ✓ | — | ✓ | — | Same pattern as math — verified numerical physics calculations |
| `science_tutor_agent` | ✓ | — | — | — | Google Search grounding is sufficient; url_context cannot be combined (see constraint below) |
| `quiz_agent` (Phase 2) | — | — | — | ✓ | MCP tools only — `code_executor` cannot combine with function-calling tools (Constraint 1). Dedicated agent avoids conflict |
| `response_formatter_*` | — | — | — | — | Pure text transformation — no lookups ever |
| `root_tutor_agent` | — | — | — | — | Routes only — no subject reasoning |
| Future: Geography, English | ✓ | — | — | — | url_context unusable alongside google_search; google_search alone is sufficient |

> **Gemini API constraints — confirmed through testing:**
>
> **Constraint 1 — `code_execution` + function calling:** `code_execution` (built-in) and function-calling tools cannot be combined. Fix: `GoogleSearchTool(bypass_multi_tools_limit=True)` keeps `google_search` as a native Gemini built-in rather than a function-call wrapper. Two native built-ins (`google_search` + `code_execution`) are fully compatible.
>
> **Constraint 2 — `url_context` + any other tool:** `url_context` (native built-in) cannot be combined with `google_search` or any other tool in the same request. Error: `"Built-in tools ({url_context}) and Function Calling cannot be combined."` This means `url_context` is only usable as the sole tool on an agent. Currently no active agent uses it — `google_search` grounding is sufficient for all subject tutors.
>
> **Constraint 3 — `code_execution` + MCP tools (Phase 2):** MCP tools are exposed to Gemini as function-calling tools. Per Constraint 1, they cannot coexist with `code_execution` on the same agent. This is why `quiz_agent` is a dedicated agent with MCPToolset only — it cannot be merged into `math_tutor_agent` or `physics_tutor_agent`. The root orchestrator routes quiz requests to `quiz_pipeline` separately from subject tutoring requests.

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
    output_key='subject_solution',
)
```

**Step 3 — Register with the root orchestrator**
In `tutor_platform/agent.py`, create a new pipeline and register it.

> **Important — one-parent rule:** ADK enforces that each Agent instance can only belong
> to one parent SequentialAgent. Always use `make_response_formatter('<subject>')` to
> get a fresh formatter instance per pipeline — never share the same instance.

```python
from .subagents.physics_tutor import physics_tutor_agent
from .subagents.response_formatter import make_response_formatter

physics_pipeline = SequentialAgent(
    name='physics_pipeline',
    description="...",
    sub_agents=[physics_tutor_agent, make_response_formatter('physics')],
)

root_agent = Agent(
    ...
    tools=[AgentTool(agent=math_pipeline), AgentTool(agent=physics_pipeline)],
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

## 8. Protocol Integration (MCP + AG-UI)

> **Status:** Planned for Phase 2 (MCP) and Phase 3 (AG-UI). Not yet implemented.

This platform uses two open protocols alongside ADK's native orchestration:

### 8.1 MCP — Agent ↔ Database (Phase 2)

MCP (Model Context Protocol) connects agents to external tools and data sources.
Our use: MCP Toolbox for Databases exposes AlloyDB quiz data as parameterized SQL tools.

**Architecture:**
- MCP Toolbox for Databases (Go binary) runs as a separate service
- Configured via `tools.yaml` with AlloyDB `alloydb-postgres` source
- Exposes tools: `get-quiz-question`, `get-quiz-answer`, `find-similar-easier-problems`
- ADK connects via `MCPToolset` with `StreamableHTTPConnectionParams`

**Key constraint:** MCP tools are function-calling tools. They **cannot coexist** with
`code_execution` on the same agent (Gemini API Constraint 1). This is why `quiz_agent`
is a dedicated agent — see Section 6.1, Constraint 3.

```python
# Phase 2 pattern — quiz_agent with MCPToolset
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

quiz_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="http://127.0.0.1:5000/mcp",  # local dev
    ),
    tool_filter=['get-quiz-question', 'get-quiz-answer', 'find-similar-easier-problems'],
)

quiz_agent = Agent(
    model='gemini-2.5-flash',
    name='quiz_agent',
    tools=[quiz_toolset],  # MCPToolset only — no code_executor
    ...
)
```

### 8.2 AG-UI — Agent ↔ Frontend (Phase 3)

AG-UI (Agent-User Interaction Protocol) standardizes real-time streaming between agent
backends and user-facing frontends. It is officially supported by Google ADK.

**Key resources:**
- ADK docs: `google.github.io/adk-docs/integrations/ag-ui/`
- Google blog: "Delight users by combining ADK Agents with Fancy Frontends using AG-UI"
- PyPI: `ag-ui-adk` (bridge package)
- Scaffold: `npx copilotkit@latest create -f adk`
- Production middleware: `adk-agui-middleware` (Trend Micro, pip installable)

**How it works:** Client POSTs to the agent endpoint; agent streams back JSON events via
SSE (text chunks, tool calls, state deltas, lifecycle signals). CopilotKit React components
consume the event stream and render in real time.

### 8.3 Protocol Stack Summary

| Protocol | Layer | Direction | Purpose |
|----------|-------|-----------|---------|
| AG-UI | Frontend ↔ Backend | Bidirectional (SSE) | Real-time streaming chat UI |
| MCP | Backend ↔ Database | Request/Response | Quiz data access via parameterized SQL |
| ADK | Agent ↔ Agent | Internal | Multi-agent orchestration, SequentialAgent, AgentTool |
| A2A | Agent ↔ Agent (remote) | HTTP | Cross-framework agent interop (not needed — all agents co-located) |
| A2UI | Agent → UI schema | Declarative JSON | Agent-generated dynamic UI components (future — v0.8) |

**Authentication (Cloud Run):** For service-to-service calls (ADK backend → MCP Toolbox →
AlloyDB), use service account identity tokens (Cloud Run invoker role) — no API keys needed
between internal services.

---

## 9. Dataset Pipeline (Forward-Looking)

> **Status:** Planned. Not yet implemented.

**Goal:** Ingest open educational datasets from Hugging Face Hub, store in AlloyDB, and use
them for student quizzes and concept-check questions.

**Planned pipeline:**
```
Hugging Face Hub (primary: datavorous/entrance-exam-dataset + per-subject supplements)
    │  (datasets library — streaming or batch download)
    ▼
Preprocessing scripts (Python, in scripts/data_pipeline/)
    │  - Filter by subject tags, normalize to common schema
    │  - Parse LaTeX/HTML, extract MCQ options + correct answer
    │  - Classify difficulty (1-5) and topic tags via LLM where missing
    │  - Deduplicate and validate
    │  - Generate embeddings via Vertex AI text-embedding-005
    ▼
AlloyDB (free trial, asia-southeast1)
    │  - Table: problems (pgvector + ScaNN index + AlloyDB AI)
    │  - Indexed by: (subject, difficulty), ScaNN on embedding
    │  - In-DB embedding generation: google_ml.embedding()
    ▼
MCP Toolbox for Databases (tools.yaml → parameterized SQL)
    │  - get-quiz-question(subject, difficulty)
    │  - get-quiz-answer(problem_id)
    │  - find-similar-easier-problems(topic, max_difficulty, subject)
    ▼
quiz_agent (MCPToolset, dedicated — no code_executor)
    │  (routes: "quiz me on X", adaptive difficulty, hint delivery)
    ▼
Student interaction (quiz mode)
```

**Recommended datasets by subject (researched March 2025):**

| Subject | Primary Dataset | Supplement(s) | Est. Total | Difficulty Range |
|---------|----------------|---------------|-----------|-----------------|
| Math | `datavorous/entrance-exam-dataset` (JEE math) | `openai/gsm8k` (8.8K, MIT) + `deepmind/aqua_rat` (98K, Apache 2.0, MCQ) | ~50-60K | Grade school → competition |
| Physics | `datavorous/entrance-exam-dataset` (JEE physics) | PHYSICS NeurIPS (`Zhengsh123/PHYSICS`, 8.3K EN, CC-BY-4.0) + `zhibei1204/PhysReason` (1.2K, MIT) | ~35-45K | Grade 2 → graduate |
| Biology | `datavorous/entrance-exam-dataset` (NEET bio) | `TIGER-Lab/MMLU-Pro` bio subset (717, MIT) + `derek-thomas/ScienceQA` | ~8-12K | HS → undergrad |
| Chemistry | `datavorous/entrance-exam-dataset` (JEE chem) | `TIGER-Lab/MMLU-Pro` chem subset (1,132, MIT) | ~8-12K | HS → undergrad |
| Env. Science | AI-generated (Gemini + validation) | — | 600-800 | Beginner → advanced |

**Cross-subject backbone:** `datavorous/entrance-exam-dataset` (97.4K total, CC-BY-4.0) covers math,
physics, biology, and chemistry from JEE/NEET Indian competitive exams. Strong APAC narrative for judges.
Ingest this first, then supplement per subject.

**Ingestion order (incremental — verify after each step):**
1. `datavorous/entrance-exam-dataset` — filter by subject tags, normalize, ingest all subjects
2. `openai/gsm8k` — grade school math supplement
3. `deepmind/aqua_rat` — HS/undergrad math MCQ supplement
4. PHYSICS NeurIPS — textbook physics supplement
5. `TIGER-Lab/MMLU-Pro` bio+chem — science supplement with CoT explanations
6. AI-generate environmental science questions (600-800)

**Schema:**
```sql
-- Enable AlloyDB extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS alloydb_scann;          -- AlloyDB-exclusive, faster than IVFFlat
CREATE EXTENSION IF NOT EXISTS google_ml_integration CASCADE;  -- AlloyDB AI for in-DB embeddings

CREATE TABLE problems (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(100),           -- 'huggingface:datavorous/entrance-exam-dataset'
    subject VARCHAR(50) NOT NULL,  -- 'math', 'physics', 'biology', 'chemistry', 'environmental_science'
    difficulty INT CHECK (difficulty BETWEEN 1 AND 5),
    problem_text TEXT NOT NULL,
    solution_text TEXT,
    solution_steps JSONB,          -- structured steps for hints (LoopAgent hint delivery)
    options JSONB,                 -- MCQ answer choices: ["A. ...", "B. ...", ...]
    correct_option VARCHAR(5),     -- 'A', 'B', 'C', 'D' for MCQ
    metadata JSONB,                -- {topic_tags, source_exam, grade_level, answer_type}
    embedding VECTOR(768),         -- pgvector for semantic search (text-embedding-005)
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- Semantic search index (AlloyDB ScaNN — faster than IVFFlat)
CREATE INDEX ON problems USING scann (embedding cosine);
-- Filtering index
CREATE INDEX ON problems (subject, difficulty);
```

AlloyDB has native `pgvector` + ScaNN support — use it to find problems semantically similar to
what a student is struggling with, enabling adaptive difficulty. AlloyDB AI's `google_ml.embedding()`
function generates query-time embeddings directly in SQL without application code.

**Embedding strategy:**
- **Ingestion time:** Vertex AI Embeddings API (`text-embedding-005`) via Python batch script
- **Query time:** AlloyDB AI `google_ml.embedding('text-embedding-005', $1)` in MCP Toolbox SQL
- **Must use same model** for both — mixing models produces incompatible vector spaces

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

## 12. Hackathon Submission Context

### 12.1 Target Event

**Google Cloud Gen AI Academy — APAC Edition** (hosted on Hack2Skill Vision platform)
- **Submission track:** Track 2 — AI Agent Integration & External Systems
- **Also showcasing competencies from:** Track 1 (Agentic AI with ADK) and Track 3 (AI-Ready Databases)
- **Platform URL:** https://vision.hack2skill.com/event/apac-genaiacademy

### 12.2 Academy Track Alignment

The Gen AI Academy APAC teaches three tracks. This project demonstrates mastery of all three:

| Track | Academy Focus | How This Project Demonstrates It |
|-------|--------------|----------------------------------|
| **Track 1: Agentic AI Applications** | Build AI agents from design to deployment using Gemini + ADK | Multi-agent orchestrator with subject-tutor agents, SequentialAgent pipelines, AgentTool wrappers, routing logic |
| **Track 2: AI Agent Integration & External Systems** (PRIMARY) | Connect AI agents to real-world data and tools using MCP | MCP Toolbox connecting agents to quiz database (AlloyDB/Cloud SQL), external curriculum data |
| **Track 3: AI-Ready Databases** | Power AI applications with AlloyDB / Cloud SQL | AlloyDB or Cloud SQL for PostgreSQL storing multi-subject quiz datasets with pgvector for semantic similarity search |

### 12.3 Judging Criteria (Weighted)

| Criteria | Weight | What Judges Look For | Our Strategy |
|----------|--------|---------------------|--------------|
| **Impactful Vision** | 30% | Problem alignment with APAC communities, real-world applicability | Democratizing STEM education for underserved APAC students who lack access to quality tutors |
| **Technical Merit** | 30% | GenAI tool integration depth, code quality, scalability | Deep Google Cloud stack: ADK multi-agent + MCP Toolbox + AlloyDB/Cloud SQL + pgvector + Vertex AI |
| **User Experience** | 20% | Intuitive interface, seamless GenAI integration | Student-facing web UI with chat, quiz mode, multilingual APAC language support |
| **Innovation & Creativity** | 20% | Uniqueness, disruptive potential, positive social impact | Adaptive difficulty via semantic vector search, multi-subject agent scaling, APAC language support |

### 12.4 Submission Requirements

- Functional prototype built with Google Cloud GenAI tools
- 3-minute demo video (YouTube/Vimeo, public)
- Text description: goals, functionalities, technical implementation
- Instructions for judges to access and test (test account, live URL, sandbox)
- All materials in English

---

## 13. TODO — Hackathon Roadmap

Organized into phases. Each phase builds on the previous one. Items within a phase
can be parallelized. Phase 1 is prerequisite for all others.

### Phase 1: Core Platform Expansion (Track 1 — Agentic AI) — ✅ COMPLETE
> **Goal:** Transform from single-subject proof-of-concept into a multi-subject platform.
> **Demonstrates:** ADK multi-agent orchestration, SequentialAgent, AgentTool, routing patterns.
> **Status:** All critical items done. Two items partially complete — remaining work deferred to Phase 2 where it fits naturally.

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1.1 | **Generic `subject_solution` state key** | ✅ Done | Renamed `math_solution` → `subject_solution` across math tutor, formatter, and prompt template. All three subject tutors write to `subject_solution`; formatter reads via `{subject_solution}`. |
| 1.2 | **Add Physics tutor agent** | ✅ Done | `physics_tutor.py` + `physics_tutor_prompt.py` added. Uses `google_search` + `code_executor` (same tool pattern as math). Covers mechanics, thermodynamics, optics, electromagnetism. Output key: `subject_solution`. |
| 1.3 | **Add Science tutor agent** | ✅ Done | `science_tutor.py` + `science_tutor_prompt.py` added. Covers biology, chemistry, environmental science. Uses `google_search` only — `url_context` removed due to Gemini API 400 conflict (url_context cannot be combined with any other tool). |
| 1.4 | **Update root agent prompt for multi-subject routing** | ✅ Done | `root_agent_prompt.py` updated with all three subjects, routing disambiguation rules for cross-subject questions, Step 3 error recovery instructions, and constraint to never expose pipeline names. |
| 1.5 | **Production error handling** | ⚠️ Partial | Formatter: `before_agent_callback` short-circuits LLM if `subject_solution` is empty — returns graceful fallback immediately. `include_contents='none'` eliminates history drift. Root orchestrator: prompt-level error recovery instructions added; code-level subagent failure catching deferred to Phase 4 (pre-Cloud Run). Sufficient for demo. |
| 1.6 | **ADK workflow patterns showcase** | ⚠️ Partial | SequentialAgent deployed across all three subject pipelines (math, physics, science). ParallelAgent and LoopAgent deferred — they fit more naturally in Phase 2 (parallel quiz fetch + solve; iterative hint loop). See Phase 2 items 2.8 and 2.9. |

### Phase 2: MCP Integration & Quiz Database (Track 2 + Track 3) — ✅ COMPLETE
> **Goal:** Connect agents to a real database via MCP — the core of Track 2, powered by Track 3.
> **Demonstrates:** MCPToolset, MCP Toolbox for Databases, AlloyDB with pgvector + ScaNN + AlloyDB AI.

| # | Item | Status | Notes |
|---|------|--------|-------|
| 2.1 | **AlloyDB setup (free trial)** | ✅ Done | Cluster: tutor-cluster, Instance: tutor-instance, Region: asia-southeast1. Extensions: vector, alloydb_scann, google_ml_integration. Public IP: 34.124.206.1. |
| 2.2 | **Quiz database schema** | ✅ Done | `problems` table with 12 columns. ScaNN index on embedding, B-tree on (subject, difficulty), GIN on problem_text. |
| 2.3a | **Dataset ingestion — entrance-exam-dataset** | ⚠️ Skipped | `datavorous/entrance-exam-dataset` loading script deprecated and broken on HuggingFace. Replaced by MMLU-Pro. |
| 2.3b | **Dataset ingestion — Math (GSM8K)** | ✅ Done | 8,792 math problems ingested. Script: `scripts/data_pipeline/ingest_gsm8k.py`. |
| 2.3c | **Dataset ingestion — Physics** | ✅ Done | 1,304 physics problems via TIGER-Lab/MMLU-Pro (physics category). `Zhengsh123/PHYSICS` was private/inaccessible. Script: `ingest_mmlu_pro.py --subjects physics`. |
| 2.3d | **Dataset ingestion — Biology + Chemistry** | ✅ Done | Biology: 722, Chemistry: 1,137 via TIGER-Lab/MMLU-Pro. Script: `ingest_mmlu_pro.py`. |
| 2.3e | **Dataset ingestion — Environmental Science (AI-generated)** | ✅ Done | 570 MCQ questions generated via Gemini 2.5 Flash across 15 topics × 3 difficulty levels. Script: `generate_env_science.py`. |
| 2.4 | **MCP Toolbox for Databases setup** | ✅ Done | `mcp_toolbox/tools.yaml` with 3 SQL tools. Start scripts: `start_toolbox.sh` + `start_toolbox.ps1`. Tested at `http://127.0.0.1:5000/mcp`. |
| 2.5 | **Quiz agent + pipeline** | ✅ Done | `quiz_agent.py` + `quiz_agent_prompt.py`. MCPToolset only. `check_answer` deterministic comparison tool. `quiz_hint_given` state tracking for 1st vs 2nd wrong attempt. No response_formatter in quiz_pipeline. |
| 2.6 | **"Quiz Me" mode in root orchestrator** | ✅ Done | Root agent routes quiz signals to quiz_pipeline. `root_agent_prompt.py` updated with quiz routing rules. |
| 2.7 | **Semantic similarity for adaptive difficulty** | ✅ Done | `find-similar-easier-problems` tool in tools.yaml uses pgvector cosine distance + AlloyDB AI `google_ml.embedding()`. |
| 2.8 | **ParallelAgent — fetch quiz + context** | ⏳ Deferred | Deferred to post-Phase 3. Lower priority than UI for judging. |
| 2.9 | **LoopAgent — iterative hint delivery** | ⏳ Deferred | Deferred to post-Phase 3. Hint delivery handled via session state (`quiz_hint_given`) instead. |

**Total quiz database: 12,525 questions across 5 subjects (math, physics, biology, chemistry, environmental_science).**

### Phase 3: Student-Facing UI & Experience (UX — 20% of score)
> **Goal:** Replace `adk web` with a polished, student-facing interface with real-time streaming.
> **Demonstrates:** Seamless GenAI integration, intuitive design, real-world usability.
>
> **UI decision: Streamlit chosen** (over AG-UI + React). Rationale: no React experience needed,
> Python-only, demo-ready in hours vs days, ADK technical depth is the judging differentiator.
> AG-UI/React remains a post-hackathon upgrade path.
>
> **Session persistence architecture (researched 2026-03-22):**
> - ADK `DatabaseSessionService` persists state to PostgreSQL/AlloyDB via SQLAlchemy async.
> - `user:` prefix keys go to `StorageUserState` table — persist across ALL sessions for a user_id.
> - `{user:preferred_language}`, `{user:grade_level}`, `{user:name}` inject directly into agent prompts.
> - Migration: `session_service_uri="sqlite+aiosqlite:///./sessions.db"` → `"postgresql+asyncpg://..."` — 1 line change.
> - `auto_create_session=True` in `get_fast_api_app()` — Streamlit needs no session pre-creation call.
> - ADK auto-creates schema tables on startup. No manual migrations needed.
>
> **Key implementation files:**
> - `main.py` — FastAPI backend (`get_fast_api_app()` + SQLite dev / AlloyDB prod)
> - `streamlit_app.py` — Streamlit chat UI with streaming, profile, quiz shortcuts
> - `requirements-ui.txt` — UI-specific dependencies

| # | Item | Status | Notes |
|---|------|--------|-------|
| 3.1 | **ADK backend API** | ✅ Done | `main.py` at repo root. `get_fast_api_app()` with `session_service_uri` + `auto_create_session=True`. Dev: SQLite. Prod (Phase 4): swap to `postgresql+asyncpg://...` pointing at AlloyDB. |
| 3.2 | **Student web UI** | ✅ Done | `streamlit_app.py` at repo root. Streaming chat via `/run_sse`, sidebar profile form, subject shortcuts (tutoring + quiz), session info panel, New Session button. |
| 3.3 | **Quiz mode UI** | ✅ Done | Quiz mode works through chat — quiz shortcuts in sidebar trigger quiz_pipeline. MCQ options, evaluation, hints all rendered as chat messages. No separate UI needed. |
| 3.4 | **Student profile & onboarding** | ✅ Done | Profile form in sidebar: name, grade (1–12 + undergraduate), preferred language. Stored in ADK session state with `user:` prefix on session creation. Root agent prompt injects `{user:name}`, `{user:grade_level}`, `{user:preferred_language}`. Phase 3.4 upgrade: swap SQLite → AlloyDB for cross-device persistence. |
| 3.5 | **Multilingual APAC language support** | ✅ Done | Language selector in sidebar (12 APAC languages + English). Stored as `user:preferred_language`. Root agent prompt instructs Gemini to respond in that language. Zero translation API cost. |
| 3.6 | **Inline image rendering** | ✅ Done | Streamlit `st.write_stream()` renders markdown including base64 images from BuiltInCodeExecutor output. |
| 3.7 | **Session continuity across refreshes** | ✅ Done | `user_id` and `session_id` persisted in `st.query_params` (URL). Page refresh restores same ADK session. "New Session" button in sidebar starts fresh. |
| 3.8 | **Mobile responsiveness** | ⚠️ Partial | Streamlit is responsive by default. Not mobile-first. Sufficient for demo. PWA noted as post-hackathon roadmap item. |

### Phase 4: Cloud Deployment & Polish (Technical Merit) — ✅ COMPLETE
> **Goal:** Deploy on Google Cloud to provide judges a live URL.
> **Demonstrates:** Production readiness, Cloud Run scalability, MCP + AlloyDB integration end-to-end.
>
> **Deployed architecture:** Three Cloud Run services in `asia-southeast1` + AlloyDB.
> - Service 1: tutor-frontend (Streamlit) — `tutor-frontend-319376906222.asia-southeast1.run.app`
> - Service 2: tutor-backend (ADK FastAPI) — `tutor-backend-319376906222.asia-southeast1.run.app`
> - Service 3: tutor-toolbox (MCP Toolbox) — `tutor-toolbox-cg7k42k3uq-as.a.run.app`
> - AlloyDB: `tutor-cluster` / `tutor-instance`, `asia-southeast1`, public IP `34.124.206.1`

| # | Item | Status | Notes |
|---|------|--------|-------|
| 4.1 | **Switch to Vertex AI** | ⚠️ Reverted | Vertex AI `us-central1` default quota (~5 RPM) too low for multi-agent quiz flows (4–6 LLM calls/turn). Reverted to API key (`GOOGLE_GENAI_USE_VERTEXAI=0`) for reliable demo. Switch back to Vertex AI only after requesting quota increase (60+ RPM). See §16. |
| 4.2 | **Cloud Run deployment — ADK backend** | ✅ Done | `Dockerfile.backend` + `uvicorn`. `SESSION_DB_URI` from Secret Manager pointing to AlloyDB. `MCP_TOOLBOX_URL` env var pointing to tutor-toolbox service URL. |
| 4.3 | **Cloud Run deployment — Frontend** | ✅ Done | `Dockerfile.frontend` + Streamlit. `BACKEND_URL` env var. Images persist across chat history via `st.session_state.messages["images"]`. |
| 4.4 | **Cloud Run deployment — MCP Toolbox** | ✅ Done | `Dockerfile.toolbox` (Alpine + gcompat for glibc). IAM: `allUsers` invoker on tutor-toolbox (required — backend calls toolbox without identity token). |
| 4.5 | **Observability** | ⏳ Deferred | Cloud Logging works (errors visible via `gcloud logging read`). Cloud Trace not configured. |

---

## 16. Cloud Run Deployment — Lessons Learned

> Documented from actual deployment debugging (2026-03-25). Read before re-deploying.

### 16.1 Service URLs (live)

| Service | URL | Auth |
|---------|-----|------|
| tutor-frontend | `https://tutor-frontend-319376906222.asia-southeast1.run.app` | Public (allUsers) |
| tutor-backend | `https://tutor-backend-319376906222.asia-southeast1.run.app` | Public (allUsers) |
| tutor-toolbox | `https://tutor-toolbox-cg7k42k3uq-as.a.run.app` | Public (allUsers) |

### 16.2 Environment Variables (tutor-backend)

| Variable | Value | Source |
|----------|-------|--------|
| `GOOGLE_GENAI_USE_VERTEXAI` | `0` (dev) / `1` (prod with quota) | env var |
| `GOOGLE_API_KEY` | Gemini API key | env var (dev only) |
| `GOOGLE_CLOUD_PROJECT` | `genai-apac-demo-project` | env var |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | env var — **must be `us-central1`**, not `asia-southeast1` (Gemini endpoint DNS fails in asia-southeast1 even when services are deployed there) |
| `MCP_TOOLBOX_URL` | `https://tutor-toolbox-cg7k42k3uq-as.a.run.app/mcp` | env var |
| `SESSION_DB_URI` | `postgresql+asyncpg://postgres:...@34.124.206.1/postgres` | Secret Manager: `session-db-uri` |

### 16.3 Known Gotchas

**Env vars with newlines (PowerShell):** Never set env vars by copy-pasting multi-line text in PowerShell — trailing `\n` corrupts the value. Always use `gcloud run services update --update-env-vars="KEY=VALUE"` directly, or `Set-Content -NoNewline` for secrets. Symptom: `httpx.InvalidURL: Invalid non-printable ASCII character in URL, '\n' at position N`.

**MCP Toolbox IAM:** The `tutor-toolbox` Cloud Run service must have `allUsers` with `roles/run.invoker`. The ADK `MCPToolset` uses a plain `httpx` client with no auth header — it cannot call authenticated Cloud Run services. Symptom: `403 Forbidden` on `/mcp`.

**Alpine + Go binary:** `Dockerfile.toolbox` uses Alpine. The `genai-toolbox` binary is glibc-linked. Requires `apk add gcompat` to provide glibc shim. Without it: `sh: toolbox: not found` (exit 127).

**`PORT` is reserved on Cloud Run:** Never set `--set-env-vars="PORT=..."` when deploying — Cloud Run injects `$PORT` automatically. Symptom: `Error: PORT is a reserved name`.

**AlloyDB `@` in password:** If the AlloyDB password contains `@`, URL-encode it as `%40` in the connection URI. Symptom: `[Errno -2] Name or service not known` (URI parser treats text after `@` as hostname).

**AlloyDB authorized networks:** Cloud Run egress IPs must be allowed. For dev, set `--authorized-external-networks=0.0.0.0/0` on the AlloyDB instance. For production, use Private Service Connect instead.

**`GOOGLE_CLOUD_LOCATION=asia-southeast1` DNS failure:** Gemini API endpoint resolution fails when location is `asia-southeast1`, even though Cloud Run services are deployed there. Always use `us-central1` as the Gemini location. This does NOT affect where Cloud Run services run — only where Gemini API calls are routed.

### 16.4 Vertex AI Rate Limits

Gemini 2.5 Flash on Vertex AI has a very low default quota (~5 RPM in `us-central1`). Multi-agent quiz flows make 4–6 LLM calls per student turn (root_agent routing + quiz_agent tool calls + quiz_agent response generation). This exhausts the quota immediately.

**Current state:** `GOOGLE_GENAI_USE_VERTEXAI=0` (API key). Google AI Studio API key has 15 RPM / 1,500 RPD free tier — sufficient for demo testing.

**To switch to Vertex AI for production:**
1. Request quota increase: Cloud Console → IAM & Admin → Quotas → `aiplatform.googleapis.com` → `generate_content_requests` → request 60+ RPM
2. Then: `gcloud run services update tutor-backend --region=asia-southeast1 --update-env-vars="GOOGLE_GENAI_USE_VERTEXAI=1" --remove-env-vars="GOOGLE_API_KEY"`

### 16.5 Streamlit SSE Streaming Architecture

With `streaming: False` on the ADK `/run_sse` endpoint:
- ADK sends one final text event per agent (not incremental chunks)
- Events have an `author` field identifying which agent produced them
- `turnComplete: true` marks the end of the turn

**Response display strategy:** Prefer the last pipeline agent's text (response_formatter or quiz_agent) over root_tutor_agent relay. The root_tutor_agent is instructed to relay verbatim but sometimes summarizes quiz evaluations. The pipeline output is always authoritative.

```
fallback_text = text from last non-root agent (quiz_agent / response_formatter)
root_text     = text from root_tutor_agent (relay — may be truncated)
→ On turnComplete: yield fallback_text if present, else root_text
→ root_text only used when root_agent responds directly (out-of-scope queries)
```

### 16.6 Saving Costs During Development

| Resource | Idle cost | Action |
|----------|-----------|--------|
| Cloud Run (min-instances=0) | $0 | Leave running |
| AlloyDB instance | ~$0.18–0.20/vCPU-hr | **Stop via Cloud Console** when not testing |
| Artifact Registry | ~$0.001/hr | Leave as-is |

To stop AlloyDB: Cloud Console → AlloyDB → tutor-cluster → tutor-instance → Stop.
To start: same path → Start (takes ~3-4 min to become READY before quiz works).

### Future Enhancements
> These items are valuable for evolving the platform beyond the current state.

| # | Item | Priority | Notes |
|---|------|----------|-------|
| D.1 | **Cross-session student progress persistence** | High | Design persistent student progress tracking in AlloyDB (topics, scores, weak areas). Requires auth system and student identity. Session state is sufficient for current use. |
| D.2 | **Token efficiency & thinking_budget** | Medium | `include_contents='none'` is already set on the formatter. Remaining: disable thinking on formatter (`thinking_budget=0`), tune per-agent budgets based on measured cost vs. quality. |
| D.3 | **Token measurement baseline** | Medium | Measure tokens per agent per turn across short, medium, and long sessions. Reintroduce `before_model_callback` history trimming on subject tutors if needed after measurement. |
| D.4 | **Test suite** | Medium | Add pytest-based agent unit tests using ADK's `Runner` + `InMemorySessionService`. Add `adk eval` eval datasets. |
| D.5 | **Enterprise web search** | Medium | Replace `google_search` with `enterprise_web_search` on Vertex AI for FERPA/COPPA compliance in production. |
| D.6 | **A2UI + Flutter GenUI SDK** | Low | Migrate frontend to Flutter using Google's A2UI protocol (v0.8) and GenUI SDK (alpha). Agents generate declarative UI components rendered natively on mobile/web. |
| D.7 | **PWA for offline access** | Low | Convert frontend to Progressive Web App for offline-capable mobile access in low-connectivity regions. Service worker caches UI shell + previously loaded content. |

### Phase Summary — Execution Order

```
Phase 1 (Core Expansion)        ←── ✅ COMPLETE
    ↓
Phase 2 (MCP + Database)        ←── ✅ COMPLETE
    │
Phase 3 (Student UI)             ←── ✅ COMPLETE
    │
Phase 4 (Cloud Deployment)       ←── ✅ COMPLETE
    │
Future Enhancements              ←── See table above
```

### Full Stack Architecture (Target)

```
Student Browser (mobile / desktop)
    │
    ▼  AG-UI protocol (SSE)
Frontend ─── Cloud Run Service 1 (React/Next.js + CopilotKit)
    │
    ▼  POST /run_sse (streaming: true)
ADK Backend ─── Cloud Run Service 2 (FastAPI + get_fast_api_app)
    │  DatabaseSessionService → AlloyDB (sessions)
    │
    ├──► root_tutor_agent (orchestrator)
    │    ├── math_pipeline      [code_executor → formatter]
    │    ├── physics_pipeline   [code_executor → formatter]
    │    ├── science_pipeline   [google_search → formatter]
    │    └── quiz_pipeline      [MCPToolset (no formatter)]
    │                               │
    │                               ▼  MCP (HTTP)
    │                          MCP Toolbox ─── Cloud Run Service 3
    │                               │
    ▼                               ▼  AlloyDB connector
AlloyDB (asia-southeast1, free trial)
    ├── problems (quiz data + pgvector + ScaNN + AlloyDB AI)
    └── adk_sessions (DatabaseSessionService)

Protocols: AG-UI (frontend↔backend) │ MCP (backend↔database) │ ADK (agent orchestration)
Google Cloud: Vertex AI │ Cloud Run │ AlloyDB │ pgvector │ AlloyDB AI
```

---

## 14. Cost & Platform Considerations

> **Research date:** March 2025. Verify pricing at cloud.google.com/vertex-ai/pricing before
> making production cost decisions — Gemini pricing changes frequently.

### 14.1 Token Pricing — Gemini 2.5 Flash

Token rates are **identical** between Google AI Studio (API key) and Vertex AI:

| Tier | Input | Output (incl. thinking tokens) | Cached Input |
|------|-------|-------------------------------|--------------|
| Standard | $0.30 / 1M tokens | $2.50 / 1M tokens | $0.03 / 1M |
| Batch | $0.15 / 1M tokens | $1.25 / 1M tokens | — |

There is no cost penalty for switching to Vertex AI at the token level.

### 14.2 Google Search Grounding — Dominant Cost Driver

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

### 14.3 API Key vs Vertex AI — When to Switch

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

## 15. Local Development

```bash
# Create and activate virtual environment (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install dependencies
pip install google-adk

# Set up environment
cp tutor_platform/.env.example tutor_platform/.env   # then fill in GOOGLE_API_KEY

# Run the agent locally
adk web   # from repo root — then select 'tutor_platform' from the dropdown (not mcp_toolbox)
```

**Required environment variables (local):**
```
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=<your-gemini-api-key>
```
