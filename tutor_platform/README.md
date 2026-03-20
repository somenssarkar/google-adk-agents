# Tutor Platform

An AI-powered school tutoring platform built with Google ADK and Gemini 2.5 Flash. A root orchestrator agent routes student questions to specialized subject-tutor agents and returns clean, textbook-style responses. Currently supports Mathematics.

## Architecture

```
Student Question
      │
      ▼
┌──────────────────────┐
│   root_tutor_agent   │  Orchestrator — understands query, routes to subject agent
│   (LlmAgent)         │  • Routes math questions → math_tutor_agent
│                      │  • Handles out-of-scope queries gracefully
│                      │  • Always calls response_formatter after subject agent
└──────────────────────┘
      │               │
      ▼               ▼
┌─────────────────┐   ┌──────────────────────┐
│ math_tutor_     │   │   response_formatter  │  Shared formatter (all subjects)
│ agent           │   │                       │  • Reads {math_solution} from state
│                 │   │                       │  • Strips LaTeX → Unicode notation
│ • google_search │   │                       │  • Structured textbook-style layout
│ • url_context   │   │                       │  • output_key='formatted_response'
│ • code_executor │   └──────────────────────┘
│ output_key=     │
│ 'math_solution' │
└─────────────────┘
```

**Orchestration pattern:** `root_tutor_agent` is an `LlmAgent` with `sub_agents`. Subagents are called as tools — the LLM routes to the subject agent, then calls the formatter. New subject agents (Physics, Geography, etc.) plug in by adding to `sub_agents` and updating the root prompt.

## File Structure

```
tutor_platform/
├── agent.py                            # root_tutor_agent (orchestrator)
├── __init__.py
├── .env                                # API key configuration
├── tools/
│   └── __init__.py                     # Shared tool instances (google_search, url_context, code_executor)
├── subagents/
│   ├── math_tutor.py                   # math_tutor_agent
│   └── response_formatter.py          # response_formatter (shared across all subjects)
└── prompts/
    ├── root_agent_prompt.py            # Orchestrator instructions + routing rules
    ├── math_tutor_prompt.py            # Math tutor instructions
    └── response_formatter_prompt.py   # Formatter instructions
```

## Agents

### `root_tutor_agent`
| Property | Value |
|---|---|
| Model | `gemini-2.5-flash` |
| Type | `LlmAgent` (orchestrator) |
| Sub-agents | `math_tutor_agent`, `response_formatter` |
| Scope | Routes to all subject agents; handles out-of-scope gracefully |

### `math_tutor_agent`
| Property | Value |
|---|---|
| Model | `gemini-2.5-flash` |
| Tools | `google_search`, `url_context`, `BuiltInCodeExecutor` |
| `output_key` | `math_solution` |
| Scope | All math domains — arithmetic, algebra, geometry, trigonometry, calculus, linear algebra, discrete math, probability, statistics, number theory |

Solves problems step-by-step with verified computation. Uses `google_search` to look up theorems and formulas, `url_context` to read authoritative reference pages in full, and `code_executor` to verify every numerical result. Internal reasoning stays hidden — only the clean solution is output.

### `response_formatter`
| Property | Value |
|---|---|
| Model | `gemini-2.5-flash` |
| Tools | None (pure text transformation) |
| `output_key` | `formatted_response` |
| Input | `{math_solution}` injected from session state |

Reformats the raw subject-tutor solution into a structured, textbook-style response: converts LaTeX to Unicode, removes code execution noise, and applies a fixed layout (Problem → Concept → Method → Solution → Answer → Verification → Key Takeaway).

## Supported Subjects

| Subject | Agent | Status |
|---------|-------|--------|
| Mathematics | `math_tutor_agent` | Active |
| Physics | — | Planned |
| Geography | — | Planned |
| English | — | Planned |

## Usage

```powershell
# From the repository root
adk web
```

Open `http://127.0.0.1:8000/dev-ui/` → select **tutor_platform** from the dropdown.

### Example Questions
- `Solve 6x² + 11x - 35 = 0`
- `What is Heron's formula?`
- `Find the derivative of x³ - 3x² + 2x`
- `What does the central limit theorem state?`
- `Calculate the determinant of [[1,2],[3,4]]`
- `What's the capital of France?` *(out-of-scope — handled gracefully)*
