from google.adk.agents.llm_agent import Agent

from ..prompts.response_formatter_prompt import RESPONSE_FORMATTER_INSTRUCTION

# Reusable response formatter subagent.
# Takes the raw solution stored in session state and reformats it into a
# clean, textbook-style response. Designed to be subject-agnostic so it
# can serve any subject-tutor agent (Math, Physics, Geography, etc.)
# as they are added to the platform.
response_formatter_agent = Agent(
    model='gemini-2.5-flash',
    name='response_formatter',
    description=(
        "Presentation formatter that takes a raw subject-tutor solution from session state "
        "and reformats it into a clean, concise, textbook-style response using Unicode "
        "notation and a structured layout. Does not alter any content — only improves "
        "readability. Always call this agent after a subject-tutor agent produces a solution."
    ),
    instruction=RESPONSE_FORMATTER_INSTRUCTION,
    output_key='formatted_response',
)
