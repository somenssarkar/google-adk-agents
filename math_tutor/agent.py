from google.adk.agents.llm_agent import Agent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.code_executors.built_in_code_executor import BuiltInCodeExecutor
from google.adk.tools.google_search_tool import GoogleSearchTool

from .prompts.math_tutor_prompt import MATH_TUTOR_INSTRUCTION
from .prompts.response_formatter_prompt import RESPONSE_FORMATTER_INSTRUCTION

# Agent 1: Solves the math problem correctly and stores the result in session state
math_tutor_agent = Agent(
    model='gemini-2.5-flash',
    name='math_tutor_agent',
    description=(
        "Expert mathematics solver. Handles all math domains — arithmetic, algebra, "
        "calculus, linear algebra, statistics, number theory. Uses code execution for "
        "verified computation and Google Search for theorems and formulas. "
        "Produces a fully correct, step-by-step solution with zero hallucination."
    ),
    instruction=MATH_TUTOR_INSTRUCTION,
    tools=[GoogleSearchTool()],
    code_executor=BuiltInCodeExecutor(),
    output_key='math_solution',
)

# Agent 2: Receives the raw solution from math_tutor_agent and reformats it
# into clean textbook-style presentation (no LaTeX delimiters, concise, structured)
response_formatter_agent = Agent(
    model='gemini-2.5-flash',
    name='response_formatter',
    description=(
        "Presentation formatter that takes a raw mathematical solution from the "
        "math_tutor_agent and reformats it into a clean, concise, textbook-style "
        "response using Unicode math notation and a structured layout. "
        "Does not alter any mathematics — only improves readability."
    ),
    instruction=RESPONSE_FORMATTER_INSTRUCTION,
)

# Pipeline: math_tutor_agent solves first, response_formatter presents second
root_agent = SequentialAgent(
    name='math_tutor_pipeline',
    description=(
        "A two-stage mathematics tutoring pipeline. Stage 1 (math_tutor_agent) solves "
        "the problem with full verification using code and search. Stage 2 "
        "(response_formatter) presents the solution in clean textbook-style format."
    ),
    sub_agents=[math_tutor_agent, response_formatter_agent],
)

