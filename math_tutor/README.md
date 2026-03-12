# Math Tutor Agent

An expert mathematics tutoring pipeline built with Google ADK. Solves problems across all math domains with **zero hallucination** — every answer is computed via code execution or verified via Google Search, never guessed.

## Architecture

```
User Question
     │
     ▼
┌─────────────────────┐
│   math_tutor_agent  │  Stage 1 — Solve
│                     │  • GoogleSearchTool  (theorems, formulas, definitions)
│                     │  • BuiltInCodeExecutor  (sympy, numpy, scipy)
│                     │  • Saves result → state['math_solution']
└─────────────────────┘
     │
     ▼
┌──────────────────────────┐
│   response_formatter     │  Stage 2 — Present
│                          │  • Reads {math_solution} via instruction injection
│                          │  • Strips LaTeX → clean Unicode notation
│                          │  • before_model_callback trims history to last 2 turns
└──────────────────────────┘
     │
     ▼
Clean Textbook-Style Response
```

The two agents are orchestrated by a `SequentialAgent` (`math_tutor_pipeline`) which is exposed as `root_agent`.

## File Structure

```
math_tutor/
├── agent.py                        # Agent definitions and pipeline wiring
├── prompts/
│   ├── __init__.py
│   ├── math_tutor_prompt.py        # Instruction for the solver agent
│   └── response_formatter_prompt.py  # Instruction for the formatter agent
├── __init__.py
└── .env                            # API key configuration
```

## Agents

### `math_tutor_agent`
| Property | Value |
|---|---|
| Model | `gemini-2.5-flash` |
| Tools | `GoogleSearchTool`, `BuiltInCodeExecutor` |
| `output_key` | `math_solution` (writes result to session state) |
| Scope | All math domains — arithmetic, algebra, calculus, linear algebra, statistics, number theory |

Solves the problem step-by-step, runs code to verify every numerical result, and searches for theorem/formula definitions. Internal reasoning (understand → plan → solve → verify) stays hidden from the audience — only the clean solution is output.

### `response_formatter`
| Property | Value |
|---|---|
| Model | `gemini-2.5-flash` |
| `before_model_callback` | `_trim_history_for_formatter` — keeps last 2 turns only |
| Instruction template | `{math_solution}` injected directly, no redundant re-fetching |

Receives the solution via session state injection and reformats it: converts LaTeX delimiters to Unicode, removes code execution metadata (`Outcome:`, `Output:` lines), and presents a clean textbook-style answer. History is trimmed to the last 2 conversation turns to keep token usage bounded while still supporting follow-up questions.

## Token Efficiency Design

| Concern | Solution |
|---|---|
| Formatter re-reading full history | `before_model_callback` trims to last `_FORMATTER_HISTORY_TURNS * 2` content items |
| Formatter re-fetching the solution | `{math_solution}` template injects state directly into the instruction |
| Session state growth | Single `output_key` overwrites the same slot every turn |

## Supported Math Domains

Arithmetic · Algebra · Geometry · Trigonometry · Pre-calculus · Calculus (differential & integral) · Linear Algebra · Discrete Mathematics · Probability · Statistics · Number Theory

## Usage

```powershell
# From the repository root
adk web
```

Open `http://127.0.0.1:8000/dev-ui/` → select **math_tutor** from the dropdown.

### Example Questions
- `Solve 6x² + 11x - 35 = 0`
- `What is Heron's formula?`
- `Find the derivative of x³ - 3x² + 2x`
- `What does the Pythagorean theorem state?`
- `Calculate the determinant of [[1,2],[3,4]]`
