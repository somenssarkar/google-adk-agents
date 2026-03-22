import os
import re

from google.adk.agents.llm_agent import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from ..prompts.quiz_agent_prompt import QUIZ_AGENT_INSTRUCTION

# MCP Toolbox server URL.
# Local dev:   http://127.0.0.1:5000/mcp  (start with scripts/infra/start_toolbox.sh)
# Cloud Run:   set MCP_TOOLBOX_URL env var to the Cloud Run service URL (Phase 4)
_MCP_TOOLBOX_URL = os.environ.get('MCP_TOOLBOX_URL', 'http://127.0.0.1:5000/mcp')

_quiz_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url=_MCP_TOOLBOX_URL),
    tool_filter=['get-quiz-question', 'get-quiz-answer', 'find-similar-easier-problems'],
)


# ---------------------------------------------------------------------------
# check_answer — deterministic answer comparison tool
#
# WHY this tool exists:
# LLM-based answer comparison is probabilistic. When the agent compares a
# student's answer to the correct answer using its own reasoning, it may:
#   - Compute the answer itself from training knowledge (wrong if DB differs)
#   - Accept "close enough" answers inconsistently
#   - Get confused by phrasing differences ("3 mpg" vs "3 miles per gallon")
#
# This tool gives the agent a deterministic verdict (CORRECT / INCORRECT) with
# exact values extracted. The agent only needs to formulate the feedback
# response — it never needs to decide correctness on its own.
#
# Compatible with MCPToolset: this is a Python function tool, not code_execution
# (built-in). Python function tools can coexist with MCP tools.
# See CLAUDE.md §6.1 Constraint 3.
# ---------------------------------------------------------------------------
def check_answer(student_answer: str, correct_answer: str) -> str:
    """Deterministically compare the student's answer to the correct database answer.

    Call this immediately after get-quiz-answer returns, passing the student's
    raw answer text and the correct_option from the database. Returns a clear
    CORRECT or INCORRECT verdict with extracted values so you never need to
    reason about correctness yourself.

    Args:
        student_answer: The student's answer exactly as they wrote it.
        correct_answer: The correct_option field returned by get-quiz-answer.

    Returns:
        A verdict string: "CORRECT. ..." or "INCORRECT. ..." with extracted values.
    """
    correct = correct_answer.strip()

    # --- MCQ branch: correct_option is a single letter A–D ---
    if re.match(r'^[A-Da-d]$', correct):
        match_obj = re.search(r'\b([A-Da-d])\b', student_answer)
        if match_obj:
            student_letter = match_obj.group(1).upper()
            if student_letter == correct.upper():
                return f"CORRECT. Student chose '{student_letter}' which matches the correct option '{correct.upper()}'."
            else:
                return (
                    f"INCORRECT. Student chose '{student_letter}', "
                    f"but the correct option is '{correct.upper()}'."
                )
        else:
            return (
                f"INCORRECT. Could not extract a letter choice from the student's answer "
                f"'{student_answer}'. The correct option is '{correct.upper()}'."
            )

    # --- Numerical branch: extract first number from each answer ---
    student_nums = re.findall(r'-?\d+(?:\.\d+)?', student_answer)
    correct_nums = re.findall(r'-?\d+(?:\.\d+)?', correct)

    if student_nums and correct_nums:
        try:
            s_val = float(student_nums[0])
            c_val = float(correct_nums[0])
            # Allow up to 2% relative tolerance or 0.5 absolute (handles rounding).
            # This does NOT accept wrong calculations — only minor rounding differences.
            tolerance = max(abs(c_val) * 0.02, 0.5)
            if abs(s_val - c_val) <= tolerance:
                return (
                    f"CORRECT. Student's value {s_val} matches the correct value {c_val}."
                )
            else:
                return (
                    f"INCORRECT. Student answered {s_val}, "
                    f"but the correct answer is {c_val}."
                )
        except ValueError:
            pass

    # --- Text fallback: normalised string comparison ---
    if student_answer.strip().lower() == correct.strip().lower():
        return f"CORRECT. Student's answer matches '{correct}'."
    else:
        return (
            f"INCORRECT. Student answered '{student_answer}', "
            f"but the correct answer is '{correct}'."
        )


# ---------------------------------------------------------------------------
# save_quiz_question_state — state persistence tool
#
# WHY this tool exists:
# The quiz is multi-turn: Turn 1 fetches and presents a question; Turn 2 the
# student answers. Each turn, the root_agent calls quiz_pipeline as a fresh
# AgentTool invocation. The quiz_agent therefore cannot reliably see its own
# previous tool call results (problem_id from get-quiz-question) across turns.
#
# This tool writes problem_id, problem_text, and resets quiz_hint_given to
# session state immediately after get-quiz-question returns. On the next turn,
# ADK's template substitution injects {quiz_pending_id}, {quiz_pending_text},
# and {quiz_hint_given} into the instruction.
#
# ADK recognises `tool_context` typed as ToolContext and injects it automatically.
# It is NOT exposed to Gemini as a parameter.
# ---------------------------------------------------------------------------
def save_quiz_question_state(
    problem_id: str,
    problem_text: str,
    tool_context: ToolContext,
) -> str:
    """Save the current quiz question to session state for multi-turn answer evaluation.

    Call this immediately after get-quiz-question returns and BEFORE presenting
    the question to the student. Also resets quiz_hint_given so each new question
    starts fresh.

    Args:
        problem_id:   The UUID returned by get-quiz-question.
        problem_text: The full question text (stored for context in the next turn).
    """
    tool_context.state['quiz_pending_id'] = problem_id
    tool_context.state['quiz_pending_text'] = problem_text
    tool_context.state['quiz_hint_given'] = False
    return f"Saved. problem_id={problem_id}"


def mark_hint_given(tool_context: ToolContext) -> str:
    """Record that a hint has been given for the current quiz question.

    Call this immediately after delivering a hint on the student's first wrong
    attempt. On the next turn, {quiz_hint_given} will be True — the agent then
    knows to reveal the full answer instead of hinting again.
    """
    tool_context.state['quiz_hint_given'] = True
    return "Hint recorded."


def clear_quiz_state(tool_context: ToolContext) -> str:
    """Clear the pending quiz question from session state after evaluation is complete.

    Call this after all feedback is delivered so the next student message is not
    mistakenly treated as an answer to the already-evaluated question.
    """
    tool_context.state['quiz_pending_id'] = ''
    tool_context.state['quiz_pending_text'] = ''
    tool_context.state['quiz_hint_given'] = False
    return "Quiz state cleared."


def _init_quiz_state(callback_context: CallbackContext) -> types.Content | None:
    """before_agent_callback: initialise quiz state keys if not present.

    Ensures {quiz_pending_id}, {quiz_pending_text}, and {quiz_hint_given} are
    always defined before ADK performs template substitution in the instruction.

    Returns None always — never short-circuits the agent.
    """
    if 'quiz_pending_id' not in callback_context.state:
        callback_context.state['quiz_pending_id'] = ''
    if 'quiz_pending_text' not in callback_context.state:
        callback_context.state['quiz_pending_text'] = ''
    if 'quiz_hint_given' not in callback_context.state:
        callback_context.state['quiz_hint_given'] = False
    return None


# Quiz agent — fetches questions, evaluates answers, drives adaptive difficulty.
#
# CONSTRAINT — MCPToolset only (no code_executor, no google_search):
# MCP tools are function-calling tools. The Gemini API rejects combining
# code_execution (built-in) with any function-calling tool. quiz_agent therefore
# uses MCPToolset + Python function tools only. See CLAUDE.md §6.1 Constraint 3.
#
# Writes directly to 'formatted_response' — no response_formatter in quiz_pipeline.
# Quiz output is conversational (questions, MCQ options, evaluations, hints) and
# does not need the academic formatting the response_formatter applies to solutions.
quiz_agent = Agent(
    model='gemini-2.5-flash',
    name='quiz_agent',
    description=(
        "Interactive quiz agent for student assessment and practice. Fetches quiz questions "
        "from the database across all subjects (math, physics, biology, chemistry, "
        "environmental science), evaluates student answers against the correct database "
        "answer, and provides adaptive feedback — offering easier reinforcement problems "
        "when a student is struggling. "
        "Call this for any quiz, test, practice, or 'quiz me' request."
    ),
    instruction=QUIZ_AGENT_INSTRUCTION,
    tools=[_quiz_toolset, check_answer, save_quiz_question_state, mark_hint_given, clear_quiz_state],
    output_key='formatted_response',
    before_agent_callback=_init_quiz_state,
)
