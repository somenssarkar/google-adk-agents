from google.adk.agents.llm_agent import Agent

from ..tools import code_executor
from ..prompts.math_tutor_prompt import MATH_TUTOR_INSTRUCTION

# Self-contained math tutor subagent.
#
# Tools:
#   - code_executor:   verifies every numerical result in Gemini's sandbox
#
# NOTE: google_search is intentionally excluded. Vertex AI rejects combining
# code_execution (built-in) with google_search when both are active in the same
# agent. Between the two, code_executor is more valuable for math — verified
# computation is the priority. Gemini 2.5 Flash covers theorem/formula knowledge
# from training data sufficiently well for tutoring purposes.
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
