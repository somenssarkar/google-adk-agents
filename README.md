# Google ADK Agents

A collection of AI agents built with [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/) on Gemini 2.5 Flash.

## Repository Structure

```
google-adk-agents/
├── tutor_platform/      # AI tutoring platform — orchestrates subject-tutor agents
└── .venv/               # Python virtual environment
```

## Agents

| Agent | Description |
|---|---|
| [tutor_platform](tutor_platform/) | Multi-subject AI tutoring platform. Root orchestrator routes student questions to specialized subject-tutor agents (currently: Mathematics) and returns clean, textbook-style responses |

## Prerequisites

- Python 3.12+
- Google ADK 1.26+
- Google Gemini API key

## Setup

```powershell
# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install dependencies
pip install google-adk
```

## Running an Agent

```powershell
# From the repository root (not inside the agent folder)
adk web
```

Then open `http://127.0.0.1:8000/dev-ui/` and select the agent from the dropdown.

> **Important:** Always run `adk web` from the repository root directory. ADK discovers agents by scanning for Python packages (folders with `__init__.py`) in the current directory.

## Environment Variables

Each agent folder contains a `.env` file:

```
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=<your-api-key>
```
