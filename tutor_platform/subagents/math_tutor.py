from google.adk.agents.llm_agent import Agent

from ..tools import code_executor
from ..prompts.math_tutor_prompt import MATH_TUTOR_INSTRUCTION

# Self-contained math tutor subagent.
#
# Tools:
#   - google_search:   looks up theorems, formulas, definitions in real time
#   - code_executor:   verifies every numerical result in Gemini's sandbox
#
# NOTE: url_context is intentionally excluded. The Gemini API does not allow
# combining code_execution (built-in) with function-calling tools in the same
# request. When google_search is the only item in tools[], ADK keeps it as a
# native built-in (no wrapping), which is compatible with code_execution.
# Adding a second tool causes ADK to wrap google_search in a GoogleSearchAgentTool
# (function call), which then conflicts with code_execution.
# url_context is available in tutor_platform/tools/__init__.py for subject agents
# that do not use code_executor (e.g., Geography, English).
#
# Writes the verified solution to session state under 'subject_solution'.
# The Response Formatter reads this via {subject_solution} template injection.
# The formatter uses include_contents='none' so it ONLY sees its instruction
# (with the substituted solution) — no conversation history that could cause confusion.
math_tutor_agent = Agent(
    model='gemini-2.5-flash',
    name='math_tutor_agent',
    description=(
        "Expert mathematics tutor and solver. Handles all math domains — arithmetic, algebra, "
        "geometry, trigonometry, calculus, linear algebra, discrete mathematics, probability, "
        "statistics, and number theory. Uses code execution for verified computation and "
        "Google Search for theorems, formulas, and definitions. Produces a fully correct, "
        "step-by-step solution with zero hallucination. "
        "Call this agent for any mathematics question."
    ),
    instruction=MATH_TUTOR_INSTRUCTION,
    code_executor=code_executor,
    output_key='subject_solution',
)
