from google.adk.agents.llm_agent import Agent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.tools.agent_tool import AgentTool

from .subagents.math_tutor import math_tutor_agent
from .subagents.response_formatter import response_formatter_agent
from .prompts.root_agent_prompt import ROOT_AGENT_INSTRUCTION

# Math pipeline: SequentialAgent runs math_tutor → response_formatter in order.
#
# WHY SequentialAgent instead of direct sub_agents on root LlmAgent:
# When an LlmAgent orchestrates subagents, ADK injects a transfer_to_agent
# function-call tool into each subagent for routing. The Gemini API rejects
# combining code_execution (built-in) with any function-calling tool in the same
# request — 400 INVALID_ARGUMENT.
# SequentialAgent uses no LLM routing and injects no function-call tools,
# so math_tutor_agent sees only its own defined tools (google_search +
# code_execution — both native Gemini built-ins, fully compatible).
#
# When adding a new subject (e.g. Physics):
#   physics_pipeline = SequentialAgent(
#       name='physics_pipeline',
#       sub_agents=[physics_tutor_agent, response_formatter_agent],
#   )
#   root_agent tools=[AgentTool(math_pipeline), AgentTool(physics_pipeline)]
math_pipeline = SequentialAgent(
    name='math_pipeline',
    description=(
        "Complete mathematics tutoring pipeline. Solves any math question across all "
        "domains — arithmetic, algebra, geometry, trigonometry, calculus, linear algebra, "
        "discrete mathematics, probability, statistics, and number theory — using verified "
        "computation and Google Search, then returns a clean textbook-style formatted answer. "
        "Call this for any mathematics question."
    ),
    sub_agents=[math_tutor_agent, response_formatter_agent],
)

# Root Tutor Agent — LlmAgent orchestrator.
# Understands the student's query and routes to the correct subject pipeline.
# Handles out-of-scope queries directly without calling any tool.
#
# WHY AgentTool instead of sub_agents:
# sub_agents uses transfer_to_agent (one-way hand-off), which makes every agent
# in the SequentialAgent produce its own visible chat turn — the student sees
# the raw math_solution before the formatted_response.
# AgentTool wraps the pipeline as a callable tool: the pipeline runs silently,
# and only root_agent's final response (relaying formatted_response) is shown.
root_agent = Agent(
    model='gemini-2.5-flash',
    name='root_tutor_agent',
    description=(
        "Root orchestrator for an AI-powered school tutoring platform. Routes student "
        "questions to the appropriate subject pipeline (currently: Mathematics). "
        "Handles out-of-scope queries gracefully by listing supported subjects."
    ),
    instruction=ROOT_AGENT_INSTRUCTION,
    tools=[AgentTool(agent=math_pipeline)],
)
