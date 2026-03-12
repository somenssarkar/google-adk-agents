from google.adk.agents.llm_agent import Agent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.agents.context import Context
from google.adk.code_executors.built_in_code_executor import BuiltInCodeExecutor
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.google_search_tool import GoogleSearchTool
from typing import Optional

from .prompts.math_tutor_prompt import MATH_TUTOR_INSTRUCTION
from .prompts.response_formatter_prompt import RESPONSE_FORMATTER_INSTRUCTION

# How many recent conversation turns to keep when formatting.
# 1 turn = 1 user message + 1 model response (2 Content items).
# Keeping 2 turns means the formatter has context for one follow-up question.
_FORMATTER_HISTORY_TURNS = 2


def _trim_history_for_formatter(
    context: Context, llm_request: LlmRequest
) -> Optional[object]:
    """before_model_callback: trims conversation history sent to the formatter.

    The formatter already receives the latest math solution injected directly
    into its instruction via {math_solution}. It only needs recent turns to
    handle follow-up questions (e.g. "explain step 2 more"). Trimming the full
    session history avoids growing token costs across long sessions.

    Keeps the last _FORMATTER_HISTORY_TURNS * 2 Content items (each turn = 1
    user message + 1 model reply). Returns None so the model call proceeds
    normally with the trimmed context.
    """
    if llm_request.contents:
        max_items = _FORMATTER_HISTORY_TURNS * 2
        if len(llm_request.contents) > max_items:
            llm_request.contents = llm_request.contents[-max_items:]
    return None  # None means: proceed with model call (no short-circuit)

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

# Agent 2: Formats the solution for the student.
# - {math_solution} is injected directly into the instruction (always fresh, no extra tokens).
# - before_model_callback trims conversation history to the last 2 turns so
#   follow-up questions still work, but old turns don't accumulate token cost.
response_formatter_agent = Agent(
    model='gemini-2.5-flash',
    name='response_formatter',
    description=(
        "Presentation formatter that takes the raw mathematical solution stored in "
        "session state and reformats it into a clean, concise, textbook-style "
        "response using Unicode math notation and a structured layout. "
        "Does not alter any mathematics — only improves readability."
    ),
    instruction=RESPONSE_FORMATTER_INSTRUCTION,
    before_model_callback=_trim_history_for_formatter,
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

