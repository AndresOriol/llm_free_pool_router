"""Streamlit Chat User Interface for the Coding Agent.

Provides Claude Desktop-like experience with a sidebar for past tasks/sessions
and a main chat window interacting with the agent as a subprocess (`python -m agent.code`).
"""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path
import streamlit as st

from ui import storage

st.set_page_config(
    page_title="Coding Agent UI",
    page_icon="🤖",
    layout="wide",
)

# Initialize session state for current session ID
if "current_session_id" not in st.session_state:
    sessions = storage.get_all_sessions()
    if sessions:
        st.session_state.current_session_id = sessions[0]["id"]
    else:
        st.session_state.current_session_id = storage.create_session("New Task")


def run_agent_task(combined_prompt: str, workdir: Path | None = None, placeholder=None) -> tuple[str, int]:
    """Execute python -m agent.code with the combined prompt on stdin, streaming output live."""
    workdir = workdir or Path.cwd()
    cmd = [sys.executable, "-m", "agent.code", str(workdir)]
    output_lines = []
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace"
        )
        
        if process.stdin:
            process.stdin.write(combined_prompt)
            process.stdin.close()

        if placeholder is not None:
            def generate_stream():
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        output_lines.append(line)
                        yield "".join(output_lines)
            try:
                st.write_stream(generate_stream())
            except Exception:
                # Fallback if write_stream behaves unexpectedly or if we prefer manual updating
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        output_lines.append(line)
                        placeholder.markdown("".join(output_lines))
        else:
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    output_lines.append(line)

        returncode = process.wait()
        output = "".join(output_lines)
        return output, returncode
    except Exception as e:
        err_msg = f"Error executing agent: {e}"
        output_lines.append(err_msg)
        if placeholder is not None:
            placeholder.markdown("".join(output_lines))
        return "".join(output_lines), -1


# --- Sidebar ---
with st.sidebar:
    st.title("💬 Agent Sessions")
    
    if st.button("➕ New Task / Chat", use_container_width=True):
        new_id = storage.create_session("New Task")
        st.session_state.current_session_id = new_id
        st.rerun()

    st.markdown("---")
    
    sessions = storage.get_all_sessions()
    for s in sessions:
        sid = s["id"]
        title = s["title"]
        is_selected = sid == st.session_state.current_session_id
        
        col1, col2 = st.columns([0.8, 0.2])
        with col1:
            btn_type = "primary" if is_selected else "secondary"
            if st.button(title[:30] + ("..." if len(title) > 30 else ""), key=f"sess_{sid}", use_container_width=True, type=btn_type):
                st.session_state.current_session_id = sid
                st.rerun()
        with col2:
            if st.button("🗑️", key=f"del_{sid}", help="Delete session"):
                storage.delete_session(sid)
                remaining = storage.get_all_sessions()
                if remaining:
                    st.session_state.current_session_id = remaining[0]["id"]
                else:
                    st.session_state.current_session_id = storage.create_session("New Task")
                st.rerun()


# --- Main Window ---
current_id = st.session_state.current_session_id
session_data = storage.get_session(current_id)

if not session_data:
    current_id = storage.create_session("New Task")
    st.session_state.current_session_id = current_id
    session_data = storage.get_session(current_id)

st.header(session_data.get("title", "Coding Agent"))

# Display chat messages
messages = session_data.get("messages", [])
for msg in messages:
    role = msg.get("role", "user")
    content = msg.get("content", "")
    with st.chat_message(role):
        st.markdown(content)

# Chat input
if prompt := st.chat_input("Enter your coding task..."):
    # If this is the first message, update session title
    if not messages:
        title = (prompt[:40] + "...") if len(prompt) > 40 else prompt
        storage.update_session(current_id, messages, title=title)

    # Append user message
    messages.append({"role": "user", "content": prompt})
    storage.update_session(current_id, messages, status="working")

    with st.chat_message("user"):
        st.markdown(prompt)

    # Working indicator & live agent execution
    with st.chat_message("assistant"):
        with st.status("Working...", expanded=True) as status_box:
            st.write("Executing coding agent...")
            
            output_placeholder = st.empty()
            
            # Format ENTIRE conversation history + new message
            history_parts = []
            # Note: messages already has the newly appended user message at the end
            # So history_parts will include all previous messages plus the current prompt
            for msg in messages[:-1]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                role_label = "User" if role == "user" else "Assistant"
                history_parts.append(f"{role_label}: {content}")
            
            history_parts.append(f"User: {prompt}")
            combined_prompt = "\n\n".join(history_parts)

            # Run agent as subprocess with Popen streaming
            output, returncode = run_agent_task(combined_prompt, placeholder=output_placeholder)
            
            if returncode == 0:
                status_box.update(label="Complete!", state="complete", expanded=False)
            else:
                status_box.update(label="Completed with errors / non-zero exit", state="error", expanded=True)

        if not output.strip():
            output = "(Agent completed with no output)"

        # If write_stream / placeholder streaming didn't output full text, display it
        # (Though output_placeholder already streamed it live)

    messages.append({"role": "assistant", "content": output})
    storage.update_session(current_id, messages, status="completed" if returncode == 0 else "error")
    st.rerun()
