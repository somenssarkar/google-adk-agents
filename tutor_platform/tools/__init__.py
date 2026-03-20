# Shared tool instances for the tutor_platform.
#
# All tools here are stateless — they only modify the LLM request object
# and hold no mutable state. A single instance is safe to share across
# any number of agents concurrently.
#
# Import from here in every subagent that needs these tools so there is
# one source of truth and no duplicate instantiation.

# Grounding with Google Search (Gemini-native).
# Injects real-time web search results directly into the model's context.
# Use on any subject-tutor agent that may encounter recent facts,
# named theorems, or information it cannot confidently verify from training.
#
# bypass_multi_tools_limit=True: keeps google_search as a native Gemini
# built-in even when other tools are present in the same request, preventing
# ADK from wrapping it in a function-call agent tool.
from google.adk.tools.google_search_tool import GoogleSearchTool

google_search = GoogleSearchTool(bypass_multi_tools_limit=True)

# url_context: disabled until further testing confirms it works correctly
# with the current multi-agent architecture.
# from google.adk.tools import url_context  # noqa: F401

# Built-in code executor (Gemini 2+ native).
# Runs Python inside Gemini's managed sandbox. No local execution.
# Use on subject-tutor agents that need verified computation
# (math, physics, data analysis, etc.).
from google.adk.code_executors.built_in_code_executor import BuiltInCodeExecutor

code_executor = BuiltInCodeExecutor()
