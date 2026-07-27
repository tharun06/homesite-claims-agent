FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The copilot spawns mcp_server.py as a stdio SUBPROCESS (agent.py ->
# StdioServerParameters), so the MCP server ships inside this same image rather
# than being a separate service. That changes when it becomes a remote HTTP
# server — see ROADMAP.md Phase 3.
COPY *.py ./

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

# LangGraph's AsyncSqliteSaver writes checkpoints relative to the working
# directory. On ephemeral container storage that means conversation history and
# any pending human-approval state are lost on restart. Acceptable for the pilot;
# fixed by moving the checkpointer to a networked database.
ENV PORT=8000
EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
