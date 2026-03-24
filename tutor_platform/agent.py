from google.adk.agents.llm_agent import Agent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from .subagents.math_tutor import math_tutor_agent
from .subagents.physics_tutor import physics_tutor_agent
from .subagents.science_tutor import science_tutor_agent
from .subagents.quiz_agent import quiz_agent
from .subagents.response_formatter import make_response_formatter
from .prompts.root_agent_prompt import ROOT_AGENT_INSTRUCTION

# ---------------------------------------------------------------------------
# Student profile initializer — before_agent_callback on root_agent
#
# WHY: root_agent_prompt.py references {user:name}, {user:grade_level},
# {user:preferred_language} via ADK template substitution. These keys live
# in session state with the "user:" prefix (cross-session persistence via
# DatabaseSessionService). On the very first request from a new student
# (before the Streamlit UI sends a stateDelta with profile values), ADK
# would substitute empty strings, causing a blank template or broken prompt.
# This callback sets safe defaults before the LLM call so substitution
# always succeeds. Once the student saves their profile in the UI, the
# stateDelta from the frontend overwrites these defaults.
# ---------------------------------------------------------------------------
_PROFILE_DEFAULTS = {
    "user:name": "Student",
    "user:grade_level": "Not specified",
    "user:preferred_language": "English",
}


def _init_student_profile(callback_context: CallbackContext) -> genai_types.Content | None:
    """Initialize user: state keys with safe defaults if not yet set.

    Returns None to let the agent proceed normally.
    """
    state = callback_context.state
    for key, default in _PROFILE_DEFAULTS.items():
        if not state.get(key):
            state[key] = default
    return None


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
# Quiz Pipeline — SequentialAgent for interactive student assessment
#
# WHY MCPToolset only on quiz_agent (no code_executor, no google_search):
# MCP tools are function-calling tools. The Gemini API rejects combining
# code_execution (built-in) with any function-calling tool in the same request.
# The quiz_agent therefore uses MCPToolset exclusively — it fetches questions
# from AlloyDB via MCP Toolbox and evaluates answers from the database.
# See CLAUDE.md §6.1 Constraint 3 for full details.
#
# The MCP Toolbox server must be running before this pipeline can function:
#   bash scripts/infra/start_toolbox.sh      (Cloud Shell / Linux / macOS)
#   .\scripts\infra\start_toolbox.ps1        (Windows)
# ---------------------------------------------------------------------------
quiz_pipeline = SequentialAgent(
    name='quiz_pipeline',
    description=(
        "Interactive quiz and practice pipeline for student assessment across all subjects. "
        "Fetches questions from the database, evaluates student answers, provides adaptive "
        "feedback, and offers easier reinforcement questions when a student is struggling. "
        "Covers: math, physics, biology, chemistry, and environmental science. "
        "Call this for any quiz, test, practice session, or 'quiz me on X' request."
    ),
    # WHY no response_formatter here (unlike subject pipelines):
    # The response_formatter is designed to reformat tutoring SOLUTIONS (step-by-step math,
    # LaTeX → Unicode, TYPE A/B/C classification). Quiz output is conversational —
    # questions with MCQ options, answer evaluations, hints, feedback. The formatter
    # classifies quiz content as a malformed solution and returns its fallback message.
    # The quiz_agent already produces clear, student-ready output that needs no transformation.
    sub_agents=[quiz_agent],
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
        "(Biology, Chemistry, Environmental Science). Routes quiz and practice requests "
        "to the quiz pipeline. Handles out-of-scope queries gracefully."
    ),
    instruction=ROOT_AGENT_INSTRUCTION,
    before_agent_callback=_init_student_profile,
    tools=[
        AgentTool(agent=math_pipeline),
        AgentTool(agent=physics_pipeline),
        AgentTool(agent=science_pipeline),
        AgentTool(agent=quiz_pipeline),
    ],
)
