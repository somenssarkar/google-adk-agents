"""AI Tutoring Platform — Student Web UI (Phase 3)

Streamlit frontend that communicates with the ADK FastAPI backend (main.py)
via the /run_sse SSE endpoint for real-time streaming responses.

Start backend first:
    uvicorn main:app --reload --port 8000

Then run this UI:
    streamlit run streamlit_app.py
"""

import base64
import json
import re
import uuid

import os

import httpx
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
APP_NAME = "tutor_platform"

LANGUAGES = [
    "English",
    "Hindi (हिंदी)",
    "Bengali (বাংলা)",
    "Tamil (தமிழ்)",
    "Telugu (తెలుగు)",
    "Indonesian (Bahasa Indonesia)",
    "Thai (ภาษาไทย)",
    "Vietnamese (Tiếng Việt)",
    "Filipino",
    "Chinese (中文)",
    "Japanese (日本語)",
    "Korean (한국어)",
]

GRADE_LEVELS = [
    "Not specified",
    "Grade 5",
    "Grade 6",
    "Grade 7",
    "Grade 8",
    "Grade 9",
    "Grade 10",
    "Grade 11",
    "Grade 12",
    "Undergraduate",
]

SUBJECTS = ["Math", "Physics", "Biology", "Chemistry", "Environmental Science"]

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

def _init_session() -> None:
    """Initialise session IDs (persisted in URL query params) and chat state."""
    params = st.query_params

    # Persist user_id across browser sessions; generate once per new browser
    if "uid" not in st.session_state:
        st.session_state.uid = params.get("uid") or str(uuid.uuid4())
    st.query_params["uid"] = st.session_state.uid

    # Persist session_id across page refreshes; new session = new conversation
    if "sid" not in st.session_state:
        st.session_state.sid = params.get("sid") or str(uuid.uuid4())
    st.query_params["sid"] = st.session_state.sid

    # Chat history for this session
    if "messages" not in st.session_state:
        st.session_state.messages = []  # list of {"role": "user"|"assistant", "content": str}

    # Student profile (loaded from URL params if previously saved, else defaults)
    if "student_name" not in st.session_state:
        st.session_state.student_name = params.get("name", "")
    if "grade_level" not in st.session_state:
        saved_grade = params.get("grade", "Not specified")
        st.session_state.grade_level = saved_grade if saved_grade in GRADE_LEVELS else "Not specified"
    if "language" not in st.session_state:
        saved_lang = params.get("lang", "English")
        st.session_state.language = saved_lang if saved_lang in LANGUAGES else "English"

    # Sidebar quick-action trigger (set by sidebar buttons, consumed by main flow)
    if "quick_action" not in st.session_state:
        st.session_state.quick_action = None


def _build_state_delta() -> dict:
    """Build stateDelta for run_sse to persist student profile in ADK session state.

    Uses "user:" prefix so ADK stores these in StorageUserState — they persist
    across sessions for the same user_id (via DatabaseSessionService).

    Reads current widget values (name_input, grade_input, lang_input) so that
    profile changes take effect on the next message without requiring an explicit
    Save Profile click.
    """
    return {
        "user:name": (
            st.session_state.get("name_input") or st.session_state.student_name or "Student"
        ),
        "user:grade_level": (
            st.session_state.get("grade_input") or st.session_state.grade_level or "Not specified"
        ),
        "user:preferred_language": (
            st.session_state.get("lang_input") or st.session_state.language or "English"
        ),
    }


# ---------------------------------------------------------------------------
# Streaming helper
# ---------------------------------------------------------------------------

def _stream_agent_response(message: str, state_delta: dict | None = None):
    """Generator that yields text tokens from the ADK /run_sse SSE endpoint.

    Filters to root_tutor_agent author events only — intermediate agent events
    (math_tutor_agent, quiz_agent, etc.) are discarded so the student only sees
    the final relayed response.
    """
    payload: dict = {
        "appName": APP_NAME,
        "userId": st.session_state.uid,
        "sessionId": st.session_state.sid,
        "newMessage": {
            "role": "user",
            "parts": [{"text": message}],
        },
        "streaming": False,
    }
    if state_delta:
        payload["stateDelta"] = state_delta

    try:
        with httpx.Client(timeout=180.0) as client:
            with client.stream("POST", f"{BACKEND_URL}/run_sse", json=payload) as resp:
                resp.raise_for_status()
                for raw_line in resp.iter_lines():
                    if not raw_line.startswith("data: "):
                        continue
                    data_str = raw_line[6:].strip()
                    if not data_str:
                        continue
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Skip turn-complete events — content already streamed via partials
                    if event.get("turnComplete"):
                        continue

                    is_root = event.get("author") == "root_tutor_agent"

                    # Artifact delta — code executor saves images to ADK artifact service.
                    # SSE sends a content-less event with actions.artifactDelta carrying
                    # {filename: version} for each new artifact. Fetch and yield inline.
                    artifact_delta = event.get("actions", {}).get("artifactDelta", {})
                    for filename in artifact_delta:
                        art_url = (
                            f"{BACKEND_URL}/apps/{APP_NAME}"
                            f"/users/{st.session_state.uid}"
                            f"/sessions/{st.session_state.sid}"
                            f"/artifacts/{filename}"
                        )
                        try:
                            art_resp = client.get(art_url, timeout=10.0)
                            if art_resp.status_code == 200:
                                part = art_resp.json()
                                inline = part.get("inlineData") or part.get("inline_data")
                                if inline:
                                    mime = inline.get("mimeType") or inline.get("mime_type", "image/png")
                                    data = inline.get("data", "")
                                    if data:
                                        yield f"\n![diagram](data:{mime};base64,{data})\n"
                        except Exception:
                            pass

                    # ADK sends two root agent events per turn:
                    #   1. partial=True  — incremental streaming chunks  → yield
                    content = event.get("content")
                    if not content:
                        continue

                    for part in content.get("parts", []):
                        # Text: root agent only (avoid surfacing intermediate agent text)
                        if is_root:
                            text = part.get("text")
                            if text:
                                yield text

    except httpx.ConnectError:
        yield (
            "⚠️ **Cannot connect to the tutor backend.**\n\n"
            "Make sure the backend is running:\n"
            "```\nuvicorn main:app --reload --port 8000\n```"
        )
    except httpx.HTTPStatusError as exc:
        yield f"⚠️ Backend error ({exc.response.status_code}): {exc.response.text}"
    except Exception as exc:
        yield f"⚠️ Unexpected error: {exc}"


# ---------------------------------------------------------------------------
# Artifact image rendering
# ---------------------------------------------------------------------------

# Matches ADK artifact filenames: YYYYMMDD_NNNN[.V].png
_ARTIFACT_RE = re.compile(r'\b(\d{8}_\d+(?:\.\d+)?\.png)\b')


def _fetch_artifact(filename: str) -> bytes | None:
    """Fetch a single artifact from the ADK artifact service. Returns raw PNG bytes."""
    art_url = (
        f"{BACKEND_URL}/apps/{APP_NAME}"
        f"/users/{st.session_state.uid}"
        f"/sessions/{st.session_state.sid}"
        f"/artifacts/{filename}"
    )
    try:
        resp = httpx.get(art_url, timeout=10.0)
        if resp.status_code == 200:
            inline = resp.json().get("inlineData") or resp.json().get("inline_data")
            if inline:
                data = inline.get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except Exception:
        pass
    return None


def _list_session_artifacts() -> list[str]:
    """List all artifact filenames for the current session."""
    list_url = (
        f"{BACKEND_URL}/apps/{APP_NAME}"
        f"/users/{st.session_state.uid}"
        f"/sessions/{st.session_state.sid}"
        f"/artifacts"
    )
    try:
        resp = httpx.get(list_url, timeout=10.0)
        if resp.status_code == 200:
            return resp.json() if isinstance(resp.json(), list) else []
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def _render_sidebar() -> None:
    """Render student profile form and subject quick-action buttons."""
    with st.sidebar:
        st.title("👤 Student Profile")

        # Profile form
        name_input = st.text_input(
            "Your Name",
            value=st.session_state.student_name,
            placeholder="Enter your name",
            key="name_input",
        )
        grade_input = st.selectbox(
            "Grade Level",
            GRADE_LEVELS,
            index=GRADE_LEVELS.index(st.session_state.grade_level),
            key="grade_input",
        )
        lang_input = st.selectbox(
            "Preferred Language",
            LANGUAGES,
            index=LANGUAGES.index(st.session_state.language)
            if st.session_state.language in LANGUAGES
            else 0,
            key="lang_input",
        )

        if st.button("💾 Save Profile", type="primary", use_container_width=True):
            st.session_state.student_name = name_input
            st.session_state.grade_level = grade_input
            st.session_state.language = lang_input
            # Persist profile in URL so it survives hard refreshes
            st.query_params["name"] = name_input
            st.query_params["grade"] = grade_input
            st.query_params["lang"] = lang_input
            st.success("Profile saved! ✅")

        st.divider()

        # Quick-start actions
        st.caption("**Quick Start**")
        for subject in SUBJECTS:
            col_tutor, col_quiz = st.columns(2)
            with col_tutor:
                if st.button(
                    f"📚 {subject}",
                    key=f"tutor_{subject}",
                    use_container_width=True,
                    help=f"Get tutoring on {subject}",
                ):
                    grade_ctx = st.session_state.grade_level
                    st.session_state.quick_action = (
                        f"Can you explain a key concept in {subject}? "
                        f"I'm at {grade_ctx} level."
                    )
            with col_quiz:
                if st.button(
                    f"🎯 Quiz",
                    key=f"quiz_{subject}",
                    use_container_width=True,
                    help=f"Practice {subject} with a quiz",
                ):
                    grade_ctx = st.session_state.grade_level
                    st.session_state.quick_action = (
                        f"Quiz me on {subject}. I'm at {grade_ctx} level."
                    )

        st.divider()

        # Session controls
        st.caption(f"Session: `{st.session_state.sid[:8]}…`")
        if st.button("🔄 New Session", use_container_width=True, help="Clear chat and start fresh"):
            st.session_state.sid = str(uuid.uuid4())
            st.session_state.messages = []
            st.query_params["sid"] = st.session_state.sid
            st.rerun()

        # Backend status check
        try:
            r = httpx.get(f"{BACKEND_URL}/health", timeout=2.0)
            if r.status_code == 200:
                st.caption("🟢 Backend connected")
            else:
                st.caption("🔴 Backend error")
        except Exception:
            st.caption("🔴 Backend offline")


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="AI Tutor — School Tutoring Platform",
        page_icon="🎓",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _init_session()
    _render_sidebar()

    # Header
    st.title("🎓 AI Tutoring Platform")
    greeting_name = st.session_state.student_name or "there"
    st.caption(
        f"Hi {greeting_name}! Ask me anything about **Math, Physics, Biology, Chemistry, "
        "or Environmental Science** — or say **'quiz me on [topic]'** to test yourself."
    )

    # Starter ideas — always visible so testers can scroll up and refer to them
    st.markdown("#### 💡 Try asking...")
    st.markdown(
        """
| # | What to try | Sample input |
|---|-------------|-------------|
| 1 | **Math tutoring** | *"What is the pythagorean theorem?"* |
| 2 | **Physics tutoring** | *"Explain Newton's second law"* |
| 3 | **Science tutoring** | *"How does photosynthesis work?"* |
| 4 | **Chemistry** | *"What is the periodic table trend for electronegativity?"* |
| 5 | **Environmental Science** | *"Explain the greenhouse effect"* |
| 6 | **Quiz mode** | *"Quiz me on algebra"* or use a sidebar 🎯 button |
"""
    )
    st.divider()

    # Replay chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            diagram_marker = re.search(r'📊\s*Diagram', msg["content"])
            msg_images = msg.get("images", [])
            if msg_images and diagram_marker:
                before = msg["content"][:diagram_marker.start()].strip()
                after = msg["content"][diagram_marker.end():].strip()
                if before:
                    st.markdown(before)
                for img_bytes in msg_images:
                    st.image(img_bytes)
                if after:
                    st.markdown(after)
            else:
                st.markdown(msg["content"])
                for img_bytes in msg_images:
                    st.image(img_bytes)

    # Resolve message: explicit user input takes priority over quick-action
    prompt: str | None = None

    if st.session_state.quick_action:
        prompt = st.session_state.quick_action
        st.session_state.quick_action = None

    user_input = st.chat_input("Ask a question or say 'quiz me on math'…")
    if user_input:
        prompt = user_input

    if prompt:
        # Show user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Stream assistant response
        with st.chat_message("assistant"):
            state_delta = _build_state_delta()

            # Stream into a replaceable slot so we can re-render with inline images
            slot = st.empty()
            with slot.container():
                full_response: str = st.write_stream(
                    _stream_agent_response(prompt, state_delta)
                )

            # Fetch any artifacts created during this turn (list endpoint is
            # more reliable than regex — formatter may drop the filename from text).
            artifacts_before = set(st.session_state.get("known_artifacts", []))
            all_artifacts = _list_session_artifacts()
            new_artifacts = [f for f in all_artifacts if f not in artifacts_before
                             and f.endswith(".png")]
            st.session_state["known_artifacts"] = all_artifacts

            img_list: list[bytes] = []
            if new_artifacts:
                images = {f: _fetch_artifact(f) for f in new_artifacts}
                images = {f: b for f, b in images.items() if b}
                if images:
                    img_list = list(images.values())
                    # Find where "📊 Diagram" appears in the response and insert
                    # images there; if no marker found, append after the text.
                    diagram_marker = re.search(r'📊\s*Diagram', full_response)
                    slot.empty()
                    with slot.container():
                        if diagram_marker:
                            before = full_response[:diagram_marker.start()].strip()
                            after = full_response[diagram_marker.end():].strip()
                            if before:
                                st.markdown(before)
                            for img_bytes in img_list:
                                st.image(img_bytes)
                            if after:
                                st.markdown(after)
                        else:
                            st.markdown(full_response)
                            for img_bytes in img_list:
                                st.image(img_bytes)

        st.session_state.messages.append({
            "role": "assistant",
            "content": full_response,
            "images": img_list,
        })


if __name__ == "__main__":
    main()
