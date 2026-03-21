from google.adk.agents.llm_agent import Agent

from ..tools import google_search
from ..prompts.science_tutor_prompt import SCIENCE_TUTOR_INSTRUCTION

# Self-contained science tutor subagent.
#
# Tools:
#   - google_search:  verifies scientific facts, classifications, and processes
#                     using Gemini-native grounding (real-time web content synthesis)
#
# WHY no url_context:
#   The Gemini API rejects combining url_context (native built-in) with any
#   function-calling tool in the same request — including google_search when
#   paired alongside other tools. The error is:
#   "Built-in tools ({url_context}) and Function Calling cannot be combined."
#   google_search alone (with bypass_multi_tools_limit=True) is a safe native
#   built-in and provides sufficient grounding for science explanations.
#   url_context remains available in tools/__init__.py for future agents that
#   need it without google_search.
#
# WHY no code_executor:
#   Science (Biology, Chemistry, Environmental Science) is primarily conceptual.
#   Adding code_executor would re-introduce the code_execution + function-calling
#   conflict. Stoichiometry steps are explained in text; students are directed
#   to the math_pipeline for precise numerical computation.
#
# Writes the verified solution to session state under 'subject_solution'.
# The Response Formatter reads this via {subject_solution} template injection.
# The formatter uses include_contents='none' so it ONLY sees its instruction
# (with the substituted solution) — no conversation history that could cause confusion.
science_tutor_agent = Agent(
    model='gemini-2.5-flash',
    name='science_tutor_agent',
    description=(
        "Expert science tutor covering Biology, Chemistry, and Environmental Science "
        "at school and introductory undergraduate level. Biology topics include: cells, "
        "genetics, evolution, ecology, human body systems, and microbiology. Chemistry "
        "topics include: atomic structure, periodic table, chemical bonding, reactions, "
        "stoichiometry, acids and bases, and organic chemistry basics. Environmental "
        "Science topics include: ecosystems, climate change, pollution, natural resources, "
        "and conservation. Uses Google Search to verify facts from authoritative sources. "
        "Produces clear, accurate concept explanations with diagrams and charts where helpful. "
        "Call this agent for any Biology, Chemistry, or Environmental Science question."
    ),
    instruction=SCIENCE_TUTOR_INSTRUCTION,
    tools=[google_search],
    output_key='subject_solution',
)
