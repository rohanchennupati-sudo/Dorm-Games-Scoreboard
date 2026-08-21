"""
db.py — SQLite/Turso data layer for the Table Tennis Tracker.

Design:
- `leagues`       : one row per league/tournament. Everything else belongs
                    to a league.
- `players`       : players, scoped to a league (a name is unique within a
                    league, not globally).
- `baseline`      : legacy season totals frozen as a starting point per
                    player. New matches are added on top of this, never
                    mutate it. New leagues/players start at all zeros.
- `baseline_h2h`  : legacy head-to-head win counts per pair, same idea.
- `matches`       : every individual game entered (source of truth going
                    forward). One row = one game between two players in a
                    league.
- `elo`           : current Elo rating per player. Starts at 1500 the day a
                    player is added.

All "current" stats (standings, H2H, Elo) are computed as
baseline + aggregation-over-matches, so the DB never stores a stale derived
number — it's always recomputed from raw match rows, scoped to whichever
league is asked for.
"""

import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tt_tracker.db"
DB_PATH.parent.mkdir(exist_ok=True)

STARTING_ELO = 1500
K_FACTOR = 32


# ---------------------------------------------------------------------------
# Turso (hosted libSQL) support. If TURSO_DATABASE_URL / TURSO_AUTH_TOKEN are
# available (via Streamlit secrets or environment variables), every
# get_connection() call returns a lightweight wrapper around a shared Turso
# client that mimics the sqlite3 connection/cursor API used throughout this
# file. Otherwise we fall back to the local SQLite file, unchanged.
# ---------------------------------------------------------------------------

_turso_client = None


def _get_turso_creds():
    try:
        import streamlit as st
        if "TURSO_DATABASE_URL" in st.secrets and "TURSO_AUTH_TOKEN" in st.secrets:
            return st.secrets["TURSO_DATABASE_URL"], st.secrets["TURSO_AUTH_TOKEN"]
    except Exception:
        pass
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if url and token:
        return url, token
    return None, None


def _get_turso_client(url, token):
    global _turso_client
    if _turso_client is None:
        import libsql_client
        _turso_client = libsql_client.create_client_sync(url=url, auth_token=token)
    return _turso_client


class _TursoCursor:
    """Mimics enough of sqlite3.Cursor for this file's needs."""

    def __init__(self, client):
        self._client = client
        self.lastrowid = None
        self._rows = []

    def execute(self, sql, params=()):
        result = self._client.execute(sql, list(params) if params else [])
        self.lastrowid = result.last_insert_rowid
        self._rows = [row.asdict() for row in result.rows]
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _TursoConnection:
    """Mimics enough of sqlite3.Connection for this file's needs."""

    def __init__(self, client):
        self._client = client

    def execute(self, sql, params=()):
        return _TursoCursor(self._client).execute(sql, params)

    def executescript(self, script):
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                self._client.execute(stmt)

    def cursor(self):
        return _TursoCursor(self._client)

    def commit(self):
        pass  # Turso commits each statement immediately over HTTP

    def close(self):
        pass  # underlying client is a shared singleton — don't tear it down


def get_connection():
    url, token = _get_turso_creds()
    if url and token:
        return _TursoConnection(_get_turso_client(url, token))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Schema + seeding
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS leagues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL REFERENCES leagues(id),
    name TEXT NOT NULL,
    nickname TEXT,
    UNIQUE(league_id, name)
);

CREATE TABLE IF NOT EXISTS baseline (
    player_id INTEGER PRIMARY KEY REFERENCES players(id),
    mp INTEGER NOT NULL DEFAULT 0,
    w INTEGER NOT NULL DEFAULT 0,
    l INTEGER NOT NULL DEFAULT 0,
    gf INTEGER NOT NULL DEFAULT 0,
    ga INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS baseline_h2h (
    player1_id INTEGER REFERENCES players(id),
    player2_id INTEGER REFERENCES players(id),
    player1_wins INTEGER NOT NULL DEFAULT 0,
    player2_wins INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player1_id, player2_id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL REFERENCES leagues(id),
    match_date TEXT NOT NULL,
    series_id INTEGER,
    player1_id INTEGER NOT NULL REFERENCES players(id),
    player2_id INTEGER NOT NULL REFERENCES players(id),
    score1 INTEGER NOT NULL,
    score2 INTEGER NOT NULL,
    winner_id INTEGER NOT NULL REFERENCES players(id),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS elo (
    player_id INTEGER PRIMARY KEY REFERENCES players(id),
    rating REAL NOT NULL DEFAULT 1500
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS league_members (
    league_id INTEGER REFERENCES leagues(id),
    user_id INTEGER REFERENCES users(id),
    role TEXT NOT NULL DEFAULT 'member',
    PRIMARY KEY (league_id, user_id)
);
"""


def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def seed_if_empty():
    """Seed the original league + its 4 players + legacy baseline, exactly once."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM leagues")
    if cur.fetchone()["c"] > 0:
        conn.close()
        return

    cur.execute("INSERT INTO leagues (name) VALUES (?)", ("Original Squad",))
    league_id = cur.lastrowid

    # name -> (nickname, mp, w, l, gf, ga), decoded from the original sheet
    players = {
        "Rohan":    ("Ronchester Utd",   60, 25, 35, 592, 625),
        "Ganesh":   ("Bastard Munchen",  60, 33, 27, 667, 633),
        "Saad":     ("Saadio Mane",      60, 30, 30, 615, 589),
        "Shashank": ("Ripper Rails",     60, 32, 28, 596, 568),
    }
    ids = {}
    for name, (nick, mp, w, l, gf, ga) in players.items():
        cur.execute(
            "INSERT INTO players (league_id, name, nickname) VALUES (?, ?, ?)",
            (league_id, name, nick),
        )
        pid = cur.lastrowid
        ids[name] = pid
        cur.execute(
            "INSERT INTO baseline (player_id, mp, w, l, gf, ga) VALUES (?,?,?,?,?,?)",
            (pid, mp, w, l, gf, ga),
        )
        cur.execute("INSERT INTO elo (player_id, rating) VALUES (?, ?)", (pid, STARTING_ELO))

    # legacy head-to-head wins, decoded from the manual H2H block
    h2h_legacy = [
        ("Ganesh", "Saad", 13, 7),
        ("Rohan", "Shashank", 7, 13),
        ("Ganesh", "Rohan", 6, 14),
        ("Rohan", "Saad", 4, 16),
        ("Ganesh", "Shashank", 14, 6),
        ("Shashank", "Saad", 13, 7),
    ]
    for p1, p2, w1, w2 in h2h_legacy:
        cur.execute(
            "INSERT INTO baseline_h2h (player1_id, player2_id, player1_wins, player2_wins) VALUES (?,?,?,?)",
            (ids[p1], ids[p2], w1, w2),
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Leagues
# ---------------------------------------------------------------------------

def get_leagues():
    """All leagues, regardless of membership — used internally (e.g. by the
    'claim orphaned leagues' bootstrap in auth.py). Not for display to users."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM leagues ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_leagues_for_user(user_id: int):
    """Only leagues this user is a member of — this is what the UI should show."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT l.* FROM leagues l
           JOIN league_members lm ON lm.league_id = l.id
           WHERE lm.user_id = ?
           ORDER BY l.created_at""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_league(name: str, owner_user_id: int):
    name = name.strip()
    if not name:
        raise ValueError("League name can't be empty.")
    conn = get_connection()
    cur = conn.cursor()
    existing = cur.execute("SELECT id FROM leagues WHERE name=?", (name,)).fetchone()
    if existing:
        conn.close()
        raise ValueError(f"A league called '{name}' already exists.")
    cur.execute("INSERT INTO leagues (name) VALUES (?)", (name,))
    league_id = cur.lastrowid
    cur.execute(
        "INSERT INTO league_members (league_id, user_id, role) VALUES (?,?,'owner')",
        (league_id, owner_user_id),
    )
    conn.commit()
    conn.close()
    return league_id


def is_league_member(league_id: int, user_id: int) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM league_members WHERE league_id=? AND user_id=?", (league_id, user_id)
    ).fetchone()
    conn.close()
    return row is not None


def is_league_owner(league_id: int, user_id: int) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM league_members WHERE league_id=? AND user_id=? AND role='owner'",
        (league_id, user_id),
    ).fetchone()
    conn.close()
    return row is not None


def get_league_members(league_id: int):
    conn = get_connection()
    rows = conn.execute(
        """SELECT u.username, lm.role FROM league_members lm
           JOIN users u ON u.id = lm.user_id
           WHERE lm.league_id = ? ORDER BY lm.role, u.username""",
        (league_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_league_member(league_id: int, username: str, role: str = "member"):
    conn = get_connection()
    cur = conn.cursor()
    user = cur.execute("SELECT id FROM users WHERE username=?", (username.strip(),)).fetchone()
    if not user:
        conn.close()
        raise ValueError(f"No user called '{username}' exists yet — they need to sign up first.")
    existing = cur.execute(
        "SELECT 1 FROM league_members WHERE league_id=? AND user_id=?", (league_id, user["id"])
    ).fetchone()
    if existing:
        conn.close()
        raise ValueError(f"'{username}' is already in this league.")
    cur.execute(
        "INSERT INTO league_members (league_id, user_id, role) VALUES (?,?,?)",
        (league_id, user["id"], role),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

def get_players(league_id: int):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM players WHERE league_id=? ORDER BY name", (league_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_player(league_id: int, name: str, nickname: str = ""):
    name = name.strip()
    if not name:
        raise ValueError("Player name can't be empty.")
    conn = get_connection()
    cur = conn.cursor()
    existing = cur.execute(
        "SELECT id FROM players WHERE league_id=? AND name=?", (league_id, name)
    ).fetchone()
    if existing:
        conn.close()
        raise ValueError(f"'{name}' is already a player in this league.")
    cur.execute(
        "INSERT INTO players (league_id, name, nickname) VALUES (?,?,?)",
        (league_id, name, nickname.strip() or None),
    )
    pid = cur.lastrowid
    cur.execute("INSERT INTO baseline (player_id, mp, w, l, gf, ga) VALUES (?,0,0,0,0,0)", (pid,))
    cur.execute("INSERT INTO elo (player_id, rating) VALUES (?, ?)", (pid, STARTING_ELO))
    conn.commit()
    conn.close()
    return pid


# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------

def _pair_key(p1, p2):
    return (p1, p2) if p1 < p2 else (p2, p1)


def record_match(league_id: int, match_date: str, player1_id: int, player2_id: int,
                  score1: int, score2: int, series_id=None):
    if player1_id == player2_id:
        raise ValueError("A player cannot play themselves.")
    if score1 == score2:
        raise ValueError("Table tennis games can't end in a draw — scores must differ.")

    winner_id = player1_id if score1 > score2 else player2_id
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO matches (league_id, match_date, series_id, player1_id, player2_id, score1, score2, winner_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (league_id, match_date, series_id, player1_id, player2_id, score1, score2, winner_id),
    )

    # --- Elo update ---
    r1 = cur.execute("SELECT rating FROM elo WHERE player_id=?", (player1_id,)).fetchone()["rating"]
    r2 = cur.execute("SELECT rating FROM elo WHERE player_id=?", (player2_id,)).fetchone()["rating"]
    exp1 = 1 / (1 + 10 ** ((r2 - r1) / 400))
    exp2 = 1 - exp1
    actual1 = 1.0 if winner_id == player1_id else 0.0
    actual2 = 1.0 - actual1
    new_r1 = r1 + K_FACTOR * (actual1 - exp1)
    new_r2 = r2 + K_FACTOR * (actual2 - exp2)
    cur.execute("UPDATE elo SET rating=? WHERE player_id=?", (new_r1, player1_id))
    cur.execute("UPDATE elo SET rating=? WHERE player_id=?", (new_r2, player2_id))

    conn.commit()
    conn.close()
    return {"winner_id": winner_id, "new_elo": {player1_id: new_r1, player2_id: new_r2}}


def recompute_elo(league_id: int):
    """Replay every remaining match in a league, in chronological order, and
    rebuild Elo from STARTING_ELO. Call after deleting/editing any match."""
    conn = get_connection()
    cur = conn.cursor()
    player_ids = [r["id"] for r in cur.execute(
        "SELECT id FROM players WHERE league_id=?", (league_id,)
    ).fetchall()]
    ratings = {pid: STARTING_ELO for pid in player_ids}

    matches = cur.execute(
        "SELECT * FROM matches WHERE league_id=? ORDER BY match_date ASC, id ASC", (league_id,)
    ).fetchall()
    for m in matches:
        p1, p2, winner = m["player1_id"], m["player2_id"], m["winner_id"]
        r1, r2 = ratings[p1], ratings[p2]
        exp1 = 1 / (1 + 10 ** ((r2 - r1) / 400))
        exp2 = 1 - exp1
        actual1 = 1.0 if winner == p1 else 0.0
        actual2 = 1.0 - actual1
        ratings[p1] = r1 + K_FACTOR * (actual1 - exp1)
        ratings[p2] = r2 + K_FACTOR * (actual2 - exp2)

    for pid, rating in ratings.items():
        cur.execute("UPDATE elo SET rating=? WHERE player_id=?", (rating, pid))
    conn.commit()
    conn.close()


def delete_match(match_id: int):
    conn = get_connection()
    cur = conn.cursor()
    row = cur.execute("SELECT league_id FROM matches WHERE id=?", (match_id,)).fetchone()
    if not row:
        conn.close()
        return
    league_id = row["league_id"]
    cur.execute("DELETE FROM matches WHERE id=?", (match_id,))
    conn.commit()
    conn.close()
    recompute_elo(league_id)


# ---------------------------------------------------------------------------
# Derived stats
# ---------------------------------------------------------------------------

def get_standings(league_id: int):
    """Baseline + all matches in this league, combined into current stats."""
    conn = get_connection()
    players = conn.execute("SELECT * FROM players WHERE league_id=?", (league_id,)).fetchall()
    player_ids = [p["id"] for p in players]
    baseline = {r["player_id"]: dict(r) for r in conn.execute("SELECT * FROM baseline").fetchall()
                if r["player_id"] in player_ids}
    elo_rows = {r["player_id"]: r["rating"] for r in conn.execute("SELECT * FROM elo").fetchall()
                if r["player_id"] in player_ids}
    matches = conn.execute("SELECT * FROM matches WHERE league_id=?", (league_id,)).fetchall()
    conn.close()

    stats = {}
    for p in players:
        b = baseline.get(p["id"], {"mp": 0, "w": 0, "l": 0, "gf": 0, "ga": 0})
        stats[p["id"]] = {
            "id": p["id"], "name": p["name"], "nickname": p["nickname"],
            "mp": b["mp"], "w": b["w"], "l": b["l"], "gf": b["gf"], "ga": b["ga"],
        }

    for m in matches:
        for pid, gf, ga in [
            (m["player1_id"], m["score1"], m["score2"]),
            (m["player2_id"], m["score2"], m["score1"]),
        ]:
            s = stats[pid]
            s["mp"] += 1
            s["gf"] += gf
            s["ga"] += ga
            if m["winner_id"] == pid:
                s["w"] += 1
            else:
                s["l"] += 1

    result = []
    for s in stats.values():
        s["gd"] = s["gf"] - s["ga"]
        s["points"] = 2 * s["w"]
        s["elo"] = round(elo_rows.get(s["id"], STARTING_ELO), 1)
        result.append(s)

    result.sort(key=lambda r: (-r["points"], -r["gd"]))
    return result


def get_h2h_matrix(league_id: int):
    """Returns {(name1, name2): {name1: wins, name2: wins}} for this league."""
    conn = get_connection()
    players = {r["id"]: r["name"] for r in conn.execute(
        "SELECT * FROM players WHERE league_id=?", (league_id,)
    ).fetchall()}
    player_ids = set(players.keys())
    baseline_h2h = [r for r in conn.execute("SELECT * FROM baseline_h2h").fetchall()
                     if r["player1_id"] in player_ids and r["player2_id"] in player_ids]
    matches = conn.execute("SELECT * FROM matches WHERE league_id=?", (league_id,)).fetchall()
    conn.close()

    pair_wins = {}
    for row in baseline_h2h:
        key = _pair_key(row["player1_id"], row["player2_id"])
        pair_wins.setdefault(key, {})
        pair_wins[key][row["player1_id"]] = pair_wins[key].get(row["player1_id"], 0) + row["player1_wins"]
        pair_wins[key][row["player2_id"]] = pair_wins[key].get(row["player2_id"], 0) + row["player2_wins"]

    for m in matches:
        key = _pair_key(m["player1_id"], m["player2_id"])
        pair_wins.setdefault(key, {})
        pair_wins[key][m["winner_id"]] = pair_wins[key].get(m["winner_id"], 0) + 1
        loser_id = m["player1_id"] if m["winner_id"] == m["player2_id"] else m["player2_id"]
        pair_wins[key].setdefault(loser_id, 0)

    named = {}
    for (a, b), wins in pair_wins.items():
        named[(players[a], players[b])] = {players[a]: wins.get(a, 0), players[b]: wins.get(b, 0)}
    return named


def get_match_log(league_id: int, limit=200):
    conn = get_connection()
    rows = conn.execute(
        """SELECT m.*, p1.name AS p1_name, p2.name AS p2_name
           FROM matches m
           JOIN players p1 ON p1.id = m.player1_id
           JOIN players p2 ON p2.id = m.player2_id
           WHERE m.league_id=?
           ORDER BY m.match_date DESC, m.id DESC LIMIT ?""",
        (league_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def win_probability(player1_id, player2_id):
    conn = get_connection()
    r1 = conn.execute("SELECT rating FROM elo WHERE player_id=?", (player1_id,)).fetchone()["rating"]
    r2 = conn.execute("SELECT rating FROM elo WHERE player_id=?", (player2_id,)).fetchone()["rating"]
    conn.close()
    p1 = 1 / (1 + 10 ** ((r2 - r1) / 400))
    return p1, 1 - p1
