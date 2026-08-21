"""
migrate_to_turso.py — one-time migration of your LOCAL match data into Turso.

Prerequisites:
1. You've created a Turso database and have its URL + auth token
   (see README.md, "Persistent storage with Turso" section).
2. Put those credentials in .streamlit/secrets.toml (create the file if it
   doesn't exist):

    TURSO_DATABASE_URL = "libsql://your-db-name.turso.io"
    TURSO_AUTH_TOKEN = "your-token-here"

3. Run: pip install -r requirements.txt   (adds the libsql-client package)
4. Run: python migrate_to_turso.py

This reads every row from your local data/tt_tracker.db and writes it into
Turso, preserving IDs so foreign keys (player_id references) stay correct.
Safe to run only once on an EMPTY Turso database — running it twice will
create duplicate rows.
"""

import sqlite3
import sys
import tomllib
from pathlib import Path

LOCAL_DB = Path(__file__).parent / "data" / "tt_tracker.db"
SECRETS = Path(__file__).parent / ".streamlit" / "secrets.toml"


class _TursoConnLite:
    """Just enough of the connection API for executescript, used only here."""
    def __init__(self, client):
        self._client = client

    def executescript(self, script):
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                self._client.execute(stmt)


def load_turso_creds():
    if not SECRETS.exists():
        sys.exit(
            "Missing .streamlit/secrets.toml. Create it with TURSO_DATABASE_URL "
            "and TURSO_AUTH_TOKEN — see the top of this file for the format."
        )
    with open(SECRETS, "rb") as f:
        secrets = tomllib.load(f)
    url = secrets.get("TURSO_DATABASE_URL")
    token = secrets.get("TURSO_AUTH_TOKEN")
    if not url or not token:
        sys.exit("secrets.toml is missing TURSO_DATABASE_URL or TURSO_AUTH_TOKEN.")
    return url, token


def main():
    if not LOCAL_DB.exists():
        sys.exit(f"No local database found at {LOCAL_DB} — nothing to migrate.")

    url, token = load_turso_creds()

    import libsql_client
    turso = libsql_client.create_client_sync(url=url, auth_token=token)

    # Build the schema on Turso (safe no-op if it already exists)
    schema_conn = _TursoConnLite(turso)
    _create_schema(schema_conn)

    src = sqlite3.connect(LOCAL_DB)
    src.row_factory = sqlite3.Row

    print("Migrating leagues...")
    for r in src.execute("SELECT * FROM leagues").fetchall():
        turso.execute(
            "INSERT INTO leagues (id, name, created_at) VALUES (?,?,?)",
            [r["id"], r["name"], r["created_at"]],
        )

    print("Migrating players...")
    for r in src.execute("SELECT * FROM players").fetchall():
        turso.execute(
            "INSERT INTO players (id, league_id, name, nickname) VALUES (?,?,?,?)",
            [r["id"], r["league_id"], r["name"], r["nickname"]],
        )

    print("Migrating baseline stats...")
    for r in src.execute("SELECT * FROM baseline").fetchall():
        turso.execute(
            "INSERT INTO baseline (player_id, mp, w, l, gf, ga) VALUES (?,?,?,?,?,?)",
            [r["player_id"], r["mp"], r["w"], r["l"], r["gf"], r["ga"]],
        )

    print("Migrating legacy head-to-head...")
    for r in src.execute("SELECT * FROM baseline_h2h").fetchall():
        turso.execute(
            "INSERT INTO baseline_h2h (player1_id, player2_id, player1_wins, player2_wins) VALUES (?,?,?,?)",
            [r["player1_id"], r["player2_id"], r["player1_wins"], r["player2_wins"]],
        )

    print("Migrating matches...")
    for r in src.execute("SELECT * FROM matches").fetchall():
        turso.execute(
            """INSERT INTO matches (id, league_id, match_date, series_id, player1_id, player2_id, score1, score2, winner_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [r["id"], r["league_id"], r["match_date"], r["series_id"], r["player1_id"], r["player2_id"],
             r["score1"], r["score2"], r["winner_id"], r["created_at"]],
        )

    print("Migrating Elo ratings...")
    for r in src.execute("SELECT * FROM elo").fetchall():
        turso.execute(
            "INSERT INTO elo (player_id, rating) VALUES (?,?) "
            "ON CONFLICT(player_id) DO UPDATE SET rating=excluded.rating",
            [r["player_id"], r["rating"]],
        )

    src.close()
    print("\nDone. Your live app will now read/write Turso instead of local SQLite")
    print("as soon as the same secrets are added to Streamlit Cloud's app settings.")


def _create_schema(conn):
    import db
    conn.executescript(db.SCHEMA)


if __name__ == "__main__":
    main()
