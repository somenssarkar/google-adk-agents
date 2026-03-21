from google.adk.agents.llm_agent import Agent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.tools.agent_tool import AgentTool

from .subagents.math_tutor import math_tutor_agent
from .subagents.physics_tutor import physics_tutor_agent
from .subagents.science_tutor import science_tutor_agent
from .subagents.response_formatter import make_response_formatter
from .prompts.root_agent_prompt import ROOT_AGENT_INSTRUCTION

# ---------------------------------------------------------------------------
# Subject Pipelines — SequentialAgent pattern
#
# WHY SequentialAgent instead of direct sub_agents on root LlmAgent:
# When an LlmAgent orchestrates subagents, ADK injects a transfer_to_agent
# function-call tool into each subagent for routing. The Gemini API rejects
# combining code_execution (built-in) with any function-calling tool in the
# same request — 400 INVALID_ARGUMENT.
# SequentialAgent uses no LLM routing and injects no function-call tools,
# so subject-tutor agents see only their own defined tools (native Gemini
# built-ins), avoiding the conflict entirely.
#
# Each pipeline:
#   1. Runs the subject tutor → writes raw solution to session state['subject_solution']
#   2. Runs response_formatter → reads {subject_solution}, writes formatted_response
#   3. Root agent relays formatted_response verbatim to the student
# ---------------------------------------------------------------------------

math_pipeline = SequentialAgent(
    name='math_pipeline',
    description=(
        "Complete mathematics tutoring pipeline. Solves any math question across all "
        "domains — arithmetic, algebra, geometry, trigonometry, calculus, linear algebra, "
        "discrete mathematics, probability, statistics, and number theory — using verified "
        "computation and Google Search, then returns a clean textbook-style formatted answer. "
        "Call this for any mathematics question."
    ),
    sub_agents=[math_tutor_agent, make_response_formatter('math')],
)

physics_pipeline = SequentialAgent(
    name='physics_pipeline',
    description=(
        "Complete physics tutoring pipeline. Solves any physics question across all "
        "school and introductory undergraduate topics — mechanics (kinematics, dynamics, "
        "energy, momentum, rotational motion), thermodynamics, waves and optics, "
        "electromagnetism (circuits, electric and magnetic fields), and modern physics "
        "(atomic structure, radioactivity, quantum basics) — using verified numerical "
        "computation, unit analysis, and Google Search, then returns a clean formatted answer "
        "with diagrams where helpful. "
        "Call this for any physics question."
    ),
    sub_agents=[physics_tutor_agent, make_response_formatter('physics')],
)

science_pipeline = SequentialAgent(
    name='science_pipeline',
    description=(
        "Complete science tutoring pipeline covering Biology, Chemistry, and Environmental "
        "Science. Biology topics: cells, genetics, evolution, ecology, human body systems, "
        "microbiology. Chemistry topics: atomic structure, periodic table, chemical bonding, "
        "reactions, stoichiometry, acids and bases, organic chemistry basics. Environmental "
        "Science: ecosystems, climate change, pollution, natural resources, conservation. "
        "Uses authoritative sources for accurate explanations with diagrams where helpful. "
        "Call this for any Biology, Chemistry, or Environmental Science question."
    ),
    sub_agents=[science_tutor_agent, make_response_formatter('science')],
)

# ---------------------------------------------------------------------------
# Root Tutor Agent — LlmAgent orchestrator
#
# WHY AgentTool instead of sub_agents:
# sub_agents uses transfer_to_agent (one-way hand-off), which makes every agent
# in the SequentialAgent produce its own visible chat turn — the student sees
# the raw subject_solution before the formatted_response.
# AgentTool wraps the pipeline as a callable tool: the pipeline runs silently,
# and only root_agent's final response (relaying formatted_response) is shown.
# ---------------------------------------------------------------------------
root_agent = Agent(
    model='gemini-2.5-flash',
    name='root_tutor_agent',
    description=(
        "Root orchestrator for an AI-powered school tutoring platform. Routes student "
        "questions to the appropriate subject pipeline: Mathematics, Physics, or Science "
        "(Biology, Chemistry, Environmental Science). Handles out-of-scope queries "
        "gracefully by listing supported subjects."
    ),
    instruction=ROOT_AGENT_INSTRUCTION,
    tools=[
        AgentTool(agent=math_pipeline),
        AgentTool(agent=physics_pipeline),
        AgentTool(agent=science_pipeline),
    ],
)
