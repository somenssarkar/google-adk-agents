from google.adk.agents.llm_agent import Agent

from ..tools import code_executor
from ..prompts.physics_tutor_prompt import PHYSICS_TUTOR_INSTRUCTION

# Self-contained physics tutor subagent.
#
# Tools:
#   - code_executor:   runs numerical physics calculations in Gemini's sandbox
#
# NOTE: google_search is intentionally excluded. Vertex AI rejects combining
# code_execution (built-in) with google_search when both are active in the same
# agent. Between the two, code_executor is more valuable for physics — verified
# unit-consistent numerical computation is the priority. Gemini 2.5 Flash covers
# physical constants, laws, and named theorems from training data sufficiently
# well for tutoring purposes.
#
# Writes the verified solution to session state under 'subject_solution'.
# The Response Formatter reads this via {subject_solution} template injection.
# The formatter uses include_contents='none' so it ONLY sees its instruction
# (with the substituted solution) — no conversation history that could cause confusion.
physics_tutor_agent = Agent(
    model='gemini-2.5-flash',
    name='physics_tutor_agent',
    description=(
        "Expert physics tutor and problem solver. Handles all school and introductory "
        "undergraduate physics — mechanics (kinematics, dynamics, energy, momentum, "
        "rotational motion), thermodynamics, waves and optics, electromagnetism "
        "(circuits, electric fields, magnetism), and modern physics (atomic structure, "
        "radioactivity, quantum basics). Uses code execution for verified numerical "
        "calculations and Google Search for physical constants and laws. Produces correct, "
        "unit-consistent solutions with free body diagrams and graphs where helpful. "
        "Call this agent for any physics question."
    ),
    instruction=PHYSICS_TUTOR_INSTRUCTION,
    code_executor=code_executor,
    output_key='subject_solution',
)
