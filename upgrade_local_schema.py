"""
upgrade_local_schema.py — one-time upgrade of an OLD local database (from
before multi-league support existed) to the new schema.

What it does:
1. Creates the new `leagues` table.
2. Inserts a single league called "Original Squad".
3. Adds a `league_id` column to your existing `players` and `matches`
   tables, and backfills every existing row to point at "Original Squad".

Safe to run only once. If your local data/tt_tracker.db was created AFTER
the multi-league update, this script will detect that and do nothing.

Run this BEFORE migrate_to_turso.py.
"""

import sqlite3
import sys
from pathlib import Path

LOCAL_DB = Path(__file__).parent / "data" / "tt_tracker.db"


def main():
    if not LOCAL_DB.exists():
        sys.exit(f"No local database found at {LOCAL_DB} — nothing to upgrade.")

    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    existing_tables = {
        r["name"] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    if "leagues" in existing_tables:
        print("Already on the new schema (leagues table exists). Nothing to do.")
        conn.close()
        return

    if "players" not in existing_tables:
        print("No players table found either — this looks like a brand new database.")
        print("Nothing to upgrade; the app will create the current schema on next run.")
        conn.close()
        return

    print("Old schema detected. Upgrading in place...")

    cur.execute(
        """CREATE TABLE leagues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    cur.execute("INSERT INTO leagues (name) VALUES ('Original Squad')")
    league_id = cur.lastrowid
    print(f"Created league 'Original Squad' (id={league_id}).")

    cur.execute("ALTER TABLE players ADD COLUMN league_id INTEGER")
    cur.execute("UPDATE players SET league_id = ?", (league_id,))
    n_players = cur.execute("SELECT COUNT(*) AS c FROM players").fetchone()["c"]
    print(f"Backfilled league_id for {n_players} players.")

    cur.execute("ALTER TABLE matches ADD COLUMN league_id INTEGER")
    cur.execute("UPDATE matches SET league_id = ?", (league_id,))
    n_matches = cur.execute("SELECT COUNT(*) AS c FROM matches").fetchone()["c"]
    print(f"Backfilled league_id for {n_matches} matches.")

    conn.commit()
    conn.close()
    print("\nDone. Your local database is now on the new multi-league schema.")
    print("You can now run: python migrate_to_turso.py")


if __name__ == "__main__":
    main()
