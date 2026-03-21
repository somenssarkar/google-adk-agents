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

| Agent | `google_search` | `url_context` | `code_executor` | Why |
|-------|:-----------:|:----------:|:-----------:|-----|
| `math_tutor_agent` | ✓ | — | ✓ | Search + verified computation. `bypass_multi_tools_limit=True` keeps both as native built-ins |
| `physics_tutor_agent` | ✓ | — | ✓ | Same pattern as math — verified numerical physics calculations |
| `science_tutor_agent` | ✓ | — | — | Google Search grounding is sufficient; url_context cannot be combined (see constraint below) |
| `response_formatter_*` | — | — | — | Pure text transformation — no lookups ever |
| `root_tutor_agent` | — | — | — | Routes only — no subject reasoning |
| Future: Geography, English | ✓ | — | — | url_context unusable alongside google_search; google_search alone is sufficient |

> **Gemini API constraints — confirmed through testing:**
>
> **Constraint 1 — `code_execution` + function calling:** `code_execution` (built-in) and function-calling tools cannot be combined. Fix: `GoogleSearchTool(bypass_multi_tools_limit=True)` keeps `google_search` as a native Gemini built-in rather than a function-call wrapper. Two native built-ins (`google_search` + `code_execution`) are fully compatible.
>
> **Constraint 2 — `url_context` + any other tool:** `url_context` (native built-in) cannot be combined with `google_search` or any other tool in the same request. Error: `"Built-in tools ({url_context}) and Function Calling cannot be combined."` This means `url_context` is only usable as the sole tool on an agent. Currently no active agent uses it — `google_search` grounding is sufficient for all subject tutors.

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

### Phase 2: MCP Integration & Quiz Database (Track 2 + Track 3)
> **Goal:** Connect agents to a real database via MCP — the core of Track 2, powered by Track 3.
> **Demonstrates:** MCPToolset, MCP Toolbox for Databases, AlloyDB/Cloud SQL with pgvector.

| # | Item | Priority | Notes |
|---|------|----------|-------|
| 2.1 | **AlloyDB / Cloud SQL for PostgreSQL setup** | Critical | Provision AlloyDB (preferred) or Cloud SQL for PostgreSQL (budget fallback) on Google Cloud. Enable pgvector extension. Both are natively supported by MCP Toolbox for Databases. Cloud SQL is significantly cheaper for a demo and has identical pgvector + MCP Toolbox support — use it if AlloyDB cost is prohibitive. |
| 2.2 | **Quiz database schema** | Critical | Implement the `problems` table (see Section 9 schema). Key columns: `subject`, `difficulty`, `topic_tags` (JSONB), `embedding VECTOR(768)` for pgvector. Index by `(subject, difficulty)` and `ivfflat` for vector cosine similarity. Must support Math, Physics, and Science questions. |
| 2.3 | **Quiz dataset ingestion pipeline** | Critical | Ingest `gsm8k` and/or `lighteval/MATH` from Hugging Face for Math. Source or generate Physics and Science question sets. Normalize schema. Generate embeddings using Vertex AI Embeddings API (`text-embedding-005`). Scripts go in `scripts/data_pipeline/`. |
| 2.4 | **MCP Toolbox for Databases setup** | Critical | Use Google's [MCP Toolbox for Databases](https://github.com/googleapis/genai-toolbox) to expose quiz data as MCP tools. Configure `tools.yaml` with query tools: `get_quiz_question(subject, difficulty)`, `get_questions_by_topic(subject, topic)`, `get_similar_question(embedding, difficulty)`. Toolbox connects to AlloyDB/Cloud SQL instance. |
| 2.5 | **Connect quiz MCP tools to subject agents** | Critical | Use `MCPToolset` from ADK to connect each subject-tutor agent to the MCP toolbox. Agents can now pull quiz questions contextually (e.g., "quiz me on algebra" triggers a database lookup via MCP for an appropriate question). |
| 2.6 | **"Quiz Me" mode in root orchestrator** | High | Root agent detects quiz requests (e.g., "test me on calculus") and routes to the appropriate subject agent with quiz context. Agent fetches a question via MCP, presents it, evaluates the student's answer, and provides step-by-step feedback. |
| 2.7 | **Semantic similarity for adaptive difficulty** | High | When a student struggles, use pgvector cosine similarity to find semantically related problems at an easier difficulty level. This is the key innovation differentiator — adaptive learning powered by vector search on AlloyDB/Cloud SQL. |
| 2.8 | **ParallelAgent — fetch quiz + solve simultaneously** | Medium | Use ADK `ParallelAgent` to run a quiz question fetch (via MCP) in parallel with any pre-computation or context loading. Demonstrates Track 1 multi-agent patterns; pairs naturally with quiz mode (2.6). |
| 2.9 | **LoopAgent — iterative hint delivery** | Medium | Use ADK `LoopAgent` to implement a hint loop: student attempts a problem → agent checks answer → if wrong, provides a hint and loops back → exit when correct or max hints reached. Demonstrates Track 1 patterns and improves pedagogical effectiveness. |

### Phase 3: Student-Facing UI & Experience (UX — 20% of score)
> **Goal:** Replace `adk web` with a polished student interface.
> **Demonstrates:** Seamless GenAI integration, intuitive design, real-world usability.
> **Can start in parallel with Phase 2** (mock data initially, connect to real agents later).

| # | Item | Priority | Notes |
|---|------|----------|-------|
| 3.1 | **Student web UI** | Critical | Build with Streamlit or Gradio (fastest for hackathon). Features: chat interface, subject selector, formatted math/science output (Unicode, structured steps). Must render the formatter's output beautifully. |
| 3.2 | **Student profile & onboarding** | High | Simple profile: name, grade level, preferred language, subjects of interest. Stored in session state for the demo. Used by agents to calibrate difficulty and language. |
| 3.3 | **Quiz mode UI** | High | Dedicated quiz interface: question display, answer input, instant feedback, score tracking. Visual progress indicator (e.g., 3/10 questions completed). |
| 3.4 | **Multilingual APAC language support** | High | Gemini natively supports Hindi, Bahasa Indonesia, Thai, Vietnamese, Tagalog, Chinese, Japanese, Korean, etc. Add language detection on student input and instruct agents to respond in the student's preferred language. Low effort, very high APAC relevance for judges. |
| 3.5 | **Session progress display** | Medium | Show topics covered and quiz scores within the current session. In-memory only (no persistent DB needed for demo). Visual summary at end of session. |

### Phase 4: Cloud Deployment & Polish (Technical Merit)
> **Goal:** Deploy on Google Cloud to provide judges a live URL.
> **Demonstrates:** Production readiness, Vertex AI integration, Cloud Run scalability.

| # | Item | Priority | Notes |
|---|------|----------|-------|
| 4.1 | **Switch to Vertex AI** | High | Set `GOOGLE_GENAI_USE_VERTEXAI=1`. Configure project, region. Zero code changes needed — only env vars. Shows production-grade auth and compliance awareness. |
| 4.2 | **Cloud Run deployment** | High | Containerize with Dockerfile. Deploy agent backend to Cloud Run. Configure `--timeout 300`, `--concurrency 1`, min instances 1. Provide judges a live URL. |
| 4.3 | **Observability** | Low | Integrate Cloud Logging and Cloud Trace. Nice-to-have for demo, shows production thinking. |

### Phase 5: Demo & Submission Preparation
> **Goal:** Create compelling submission materials.

| # | Item | Priority | Notes |
|---|------|----------|-------|
| 5.1 | **APAC impact narrative** | Critical | Write project description framing the platform as solving STEM education access inequality across APAC — 250M+ students in the region lack access to quality tutors. Cite specific stats for India, Indonesia, Philippines. |
| 5.2 | **3-minute demo video** | Critical | Structure: Problem (30s) → Solution vision (30s) → Live demo of multi-subject tutoring + quiz mode + multilingual (90s) → Architecture & Google Cloud stack (30s) → Impact & roadmap (30s). Upload to YouTube, public. |
| 5.3 | **Judge access instructions** | Critical | Provide: live Cloud Run URL (or local setup guide), test student account, sample queries per subject, quiz mode walkthrough. |
| 5.4 | **Architecture diagram** | High | Visual showing: Root Agent → Subject Agents → MCP Toolbox → AlloyDB/Cloud SQL (pgvector) → Student UI. Highlight all Google Cloud components. |
| 5.5 | **README overhaul** | High | Rewrite README.md as the public-facing project showcase: problem statement, APAC relevance, architecture diagram, tech stack, setup instructions, screenshots/GIFs. |

### Deferred (Post-Hackathon)
> These items are valuable but not required for a winning demo submission.

| # | Item | Priority | Notes |
|---|------|----------|-------|
| D.1 | **Cross-session student progress persistence** | Post-hackathon | Design persistent student progress tracking in AlloyDB (topics, scores, weak areas). Requires auth system and student identity. Not needed for demo — session state is sufficient. |
| D.2 | **Token efficiency & thinking_budget** | Post-hackathon | `include_contents='none'` is already set on the formatter (done in Phase 1). Remaining: disable thinking on formatter (`thinking_budget=0`), tune per-agent budgets based on measured cost vs. quality. |
| D.3 | **Token measurement baseline** | Post-hackathon | Measure tokens per agent per turn across short, medium, and long sessions. Reintroduce `before_model_callback` history trimming on subject tutors if needed after measurement. |
| D.4 | **Test suite** | Post-hackathon | Add pytest-based agent unit tests using ADK's `Runner` + `InMemorySessionService`. Add `adk eval` eval datasets. |
| D.5 | **Enterprise web search** | Post-hackathon | Replace `google_search` with `enterprise_web_search` on Vertex AI for FERPA/COPPA compliance in production. |

### Phase Summary — Execution Order

```
Phase 1 (Core Expansion)        ←── START HERE, prerequisite for all
    ↓
Phase 2 (MCP + Database)        ←── Primary track (Track 2 + 3), highest differentiation
    ↓                                 ↕ (can overlap)
Phase 3 (Student UI)             ←── Start in parallel with Phase 2
    ↓
Phase 4 (Cloud Deployment)       ←── After core features work locally
    ↓
Phase 5 (Demo & Submission)      ←── Final, after everything works
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
adk web   # from repo root — opens browser UI
```

**Required environment variables (local):**
```
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=<your-gemini-api-key>
```
