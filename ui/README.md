# Coding Agent UI

A Streamlit-based chat User Interface (UI) for interacting with the coding agent, providing a Claude Desktop-like experience with a session sidebar and local chat history.

## Features

- **Sidebar**: View, select, create, or delete past coding sessions and tasks.
- **Main Chat Window**: Chat interface for submitting tasks and viewing agent responses.
- **Working Indicator**: Real-time status container while the agent processes requests.
- **Local History**: Stored automatically in `.ui_data/sessions.json`.

## Installation

1. Install dependencies:
   ```bash
   pip install -r ui/requirements.txt
   ```
   *(Ensure main project requirements are also installed: `pip install -r requirements.txt`)*

## Running the UI

Start the Streamlit application:
```bash
streamlit run ui/app.py
```
