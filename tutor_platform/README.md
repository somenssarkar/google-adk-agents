# Tutor Platform

An AI-powered school tutoring platform built with Google ADK and Gemini 2.5 Flash.
A root orchestrator routes student questions to specialized subject-tutor agents and
quiz pipelines, returning clean textbook-style responses.

**Live demo:** https://tutor-frontend-319376906222.asia-southeast1.run.app

## Usage

### ADK web UI (local dev — tutoring only)

```powershell
# From the repository root
adk web
```

Open `http://127.0.0.1:8000/dev-ui/` and **select `tutor_platform` from the dropdown**.

> **Important:** `adk web` lists all repo subdirectories in the dropdown. `mcp_toolbox` will
> also appear — do **not** select it (it has no `root_agent` and will error). The UI may
> default to `mcp_toolbox` alphabetically, so always verify the dropdown before sending a message.


### Quiz Mode — Additional Requirement

The quiz pipeline connects to the MCP Toolbox server. Start it in a **separate terminal**
before sending quiz requests:

```bash
# Terminal 1 — MCP Toolbox (keep open)
bash scripts/infra/start_toolbox.sh        # Cloud Shell / Linux / macOS
.\scripts\infra\start_toolbox.ps1          # Windows

# Terminal 2 — ADK web UI
adk web tutor_platform
```

Tutoring (math, physics, science) works without the toolbox. Only quiz requests need it.

---

## Architecture

```
Student Question
      │
      ▼
root_tutor_agent  (LlmAgent — orchestrator)
      │
      ├── AgentTool(math_pipeline)      ←── "solve this algebra problem"
      │       math_tutor_agent              google_search + code_executor
      │       response_formatter_math       include_contents='none'
      │
      ├── AgentTool(physics_pipeline)   ←── "explain Newton's second law"
      │       physics_tutor_agent           google_search + code_executor
      │       response_formatter_physics    include_contents='none'
      │
      ├── AgentTool(science_pipeline)   ←── "how does photosynthesis work?"
      │       science_tutor_agent           google_search only
      │       response_formatter_science    include_contents='none'
      │
      └── AgentTool(quiz_pipeline)      ←── "quiz me on algebra difficulty 3"
              quiz_agent                    MCPToolset only → MCP Toolbox → AlloyDB
                                            (no formatter — quiz output is student-ready)
```

Each pipeline is a `SequentialAgent`: tutor/quiz agent runs first and writes its output to
`session state['subject_solution']`, then the formatter reads `{subject_solution}` and
produces the final presentation. The root agent relays the formatted response verbatim.

---

## File Structure

```
tutor_platform/
├── agent.py                              # root_agent + all pipeline definitions
├── __init__.py                           # Required by ADK — imports agent module
├── .env                                  # API keys — never commit
├── tools/
│   └── __init__.py                       # Shared tool instances (google_search, code_executor)
├── subagents/
│   ├── math_tutor.py                     # math_tutor_agent
│   ├── physics_tutor.py                  # physics_tutor_agent
│   ├── science_tutor.py                  # science_tutor_agent
│   ├── quiz_agent.py                     # quiz_agent (MCPToolset → AlloyDB)
│   └── response_formatter.py            # make_response_formatter(pipeline_name) factory
└── prompts/
    ├── root_agent_prompt.py              # Orchestrator routing rules
    ├── math_tutor_prompt.py              # Math tutor instructions
    ├── physics_tutor_prompt.py           # Physics tutor instructions
    ├── science_tutor_prompt.py           # Science tutor instructions
    ├── quiz_agent_prompt.py              # Quiz master instructions
    └── response_formatter_prompt.py     # Formatter instructions (shared by all pipelines)
```

---

## Agents

### `root_tutor_agent`
| Property | Value |
|---|---|
| Type | `LlmAgent` (orchestrator) |
| Tools | `AgentTool` wrappers for each pipeline |
| Role | Detects tutoring vs quiz intent; routes to the correct pipeline |

### `math_tutor_agent`
| Property | Value |
|---|---|
| Tools | `google_search` + `BuiltInCodeExecutor` |
| `output_key` | `subject_solution` |
| Scope | Arithmetic, algebra, geometry, trigonometry, calculus, linear algebra, probability, statistics, number theory |

### `physics_tutor_agent`
| Property | Value |
|---|---|
| Tools | `google_search` + `BuiltInCodeExecutor` |
| `output_key` | `subject_solution` |
| Scope | Mechanics, thermodynamics, waves, optics, electromagnetism, modern physics |

### `science_tutor_agent`
| Property | Value |
|---|---|
| Tools | `google_search` only |
| `output_key` | `subject_solution` |
| Scope | Biology, Chemistry, Environmental Science |

### `quiz_agent`
| Property | Value |
|---|---|
| Tools | `MCPToolset` only (no code_executor — Gemini API constraint) |
| `output_key` | `formatted_response` (no formatter in quiz pipeline — see below) |
| MCP tools | `get-quiz-question`, `get-quiz-answer`, `find-similar-easier-problems` |
| Requires | MCP Toolbox server running at `http://127.0.0.1:5000/mcp` |

> **Why no formatter in `quiz_pipeline`?** The `response_formatter` is designed to reformat
> tutoring *solutions* (step-by-step math, LaTeX → Unicode, TYPE A/B/C classification).
> Quiz output is conversational — questions with MCQ options, feedback, hints. When the
> formatter receives a quiz question it classifies the content as a malformed solution and
> returns a fallback error message. The quiz_agent produces clear, student-ready output
> with no transformation needed.

### `response_formatter_*`
| Property | Value |
|---|---|
| Tools | None |
| `output_key` | `formatted_response` |
| `include_contents` | `'none'` — sees only `{subject_solution}`, no conversation history |
| Role | Converts raw solution to structured textbook-style response |

One formatter instance per pipeline (ADK one-parent rule). Created by `make_response_formatter(pipeline_name)`.

---

## Example Questions

### Tutoring
- `Solve 6x² + 11x - 35 = 0`
- `Explain Newton's second law with an example`
- `How does cellular respiration work?`
- `What is the difference between mitosis and meiosis?`
- `Balance the equation: Fe + O₂ → Fe₂O₃`
- `What's the capital of France?` *(out-of-scope — handled gracefully)*

### Quiz Mode (requires MCP Toolbox running)
- `Quiz me on math`
- `Give me a physics question at difficulty 3`
- `Test my knowledge of biology`
- `Quiz me on exponents, easy level`
- *(After a question appears)* `A` or `My answer is B` *(evaluates answer)*
- `Give me a hint` *(requests a clue without revealing the answer)*
- `Next question, harder` *(adaptive difficulty)*

---

## Tool Assignment & Constraints

| Agent | `google_search` | `code_executor` | `MCPToolset` | Reason |
|-------|:-:|:-:|:-:|---|
| `math_tutor_agent` | ✓ | ✓ | — | Both are Gemini native built-ins — compatible |
| `physics_tutor_agent` | ✓ | ✓ | — | Same pattern as math |
| `science_tutor_agent` | ✓ | — | — | Search grounding sufficient; no computation needed |
| `quiz_agent` | — | — | ✓ | MCP tools are function-calls; cannot combine with code_execution |
| `response_formatter_*` | — | — | — | Pure text transformation — no tool lookups |
| `root_tutor_agent` | — | — | — | Routes only |

**Key Gemini API constraint:** `code_execution` (built-in) and function-calling tools cannot
be combined in the same agent request. MCP tools are function calls. This is why `quiz_agent`
is a dedicated agent with `MCPToolset` only. See CLAUDE.md §6.1 for full details.
