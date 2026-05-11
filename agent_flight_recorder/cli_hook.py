"""Entry point for the Claude Code Stop hook: `python -m agent_flight_recorder.cli_hook`."""
from .db import get_connection, init_db
from .ingester import ingest_claude

if __name__ == "__main__":
    conn = get_connection()
    init_db(conn)
    conn.close()
    new, skipped = ingest_claude()
    print(f"afr: {new} new, {skipped} skipped")
