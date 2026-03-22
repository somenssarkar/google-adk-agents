from google.adk.agents.llm_agent import Agent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from ..prompts.response_formatter_prompt import RESPONSE_FORMATTER_INSTRUCTION

# ADK enforces a one-parent rule: an Agent instance can only belong to one
# SequentialAgent. Since every subject pipeline needs its own formatter step,
# we use a factory function to create a fresh instance per pipeline.
# Each instance gets a unique name (required by ADK) but shares the same
# instruction and configuration.

_FORMATTER_DESCRIPTION = (
    "Presentation formatter that takes a raw subject-tutor solution from session state "
    "and reformats it into a clean, concise, textbook-style response using Unicode "
    "notation and a structured layout. Does not alter any content — only improves "
    "readability. Always call this agent after a subject-tutor agent produces a solution."
)


def _validate_solution(callback_context: CallbackContext) -> types.Content | None:
    """Guard: short-circuit the LLM call if subject_solution is missing or empty.

    After many conversation turns the formatter's LLM would otherwise receive
    a growing history from prior subjects and risk formatting stale content.
    include_contents='none' eliminates that history, but this callback adds a
    second layer of safety: if subject_solution is empty (tutor failed silently),
    return a graceful fallback immediately without calling the LLM at all.

    Returns:
        Content with a fallback message if solution is missing/empty.
        None to proceed normally with the LLM call.
    """
    solution = callback_context.state.get('subject_solution', '')
    if not solution or not solution.strip():
        return types.Content(
            role='model',
            parts=[types.Part(text=(
                "I wasn't able to retrieve a solution for your question. "
                "Please try asking again."
            ))],
        )
    return None  # solution present — proceed with LLM formatting call


def make_response_formatter(pipeline_name: str) -> Agent:
    """Return a new response formatter Agent instance for the given pipeline.

    Key design decisions:
    - include_contents='none': The formatter only needs {subject_solution} from
      session state, not the full conversation history. Without this, after many
      turns the formatter's LLM receives a growing history of prior subject answers
      and risks formatting stale content from an earlier turn instead of the current
      solution. include_contents='none' also saves significant tokens per call.
    - before_agent_callback: validates that subject_solution is non-empty before
      the LLM call. If the tutor failed silently, returns a graceful fallback
      immediately instead of letting the LLM hallucinate a response.

    Args:
        pipeline_name: Short identifier for the parent pipeline (e.g. 'math',
                       'physics', 'science'). Used to give each instance a
                       unique ADK name so the one-parent constraint is satisfied.
    """
    return Agent(
        model='gemini-2.5-flash',
        name=f'response_formatter_{pipeline_name}',
        description=_FORMATTER_DESCRIPTION,
        instruction=RESPONSE_FORMATTER_INSTRUCTION,
        output_key='formatted_response',
        include_contents='none',
        before_agent_callback=_validate_solution,
    )
