"""
db.py — SQLite data layer for the Table Tennis Tracker.

Design:
- `players`      : the 4 (or more) players.
- `baseline`      : legacy season totals from the old Excel sheet, frozen as a
                    starting point (MP/W/L/GF/GA). New matches are added on
                    top of this, never mutate it.
- `baseline_h2h`  : legacy head-to-head win counts per pair, same idea.
- `matches`       : every individual game entered from now on (source of truth
                    going forward). One row = one game between two players.
- `elo`           : current Elo rating per player. Starts at 1500 for
                    everyone the day tracking began (we cannot honestly
                    reconstruct Elo history from aggregate legacy totals).

All "current" stats (standings, H2H, Elo) are computed as
baseline + aggregation-over-matches, so the DB never stores a stale derived
number — it's always recomputed from raw match rows.
"""

import sqlite3
from pathlib import Path
from datetime import date

DB_PATH = Path(__file__).parent / "data" / "tt_tracker.db"
DB_PATH.parent.mkdir(exist_ok=True)

STARTING_ELO = 1500
K_FACTOR = 32


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            nickname TEXT
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
        """
    )
    conn.commit()
    conn.close()


def seed_if_empty():
    """Seed players + legacy baseline (from the original Excel) exactly once."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM players")
    if cur.fetchone()["c"] > 0:
        conn.close()
        return

    # name -> (nickname, mp, w, l, gf, ga), decoded from the original sheet
    players = {
        "Rohan":    ("Ronchester Utd",   60, 25, 35, 592, 625),
        "Ganesh":   ("Bastard Munchen",  60, 33, 27, 667, 633),
        "Saad":     ("Saadio Mane",      60, 30, 30, 615, 589),
        "Shashank": ("Ripper Rails",     60, 32, 28, 596, 568),
    }
    ids = {}
    for name, (nick, mp, w, l, gf, ga) in players.items():
        cur.execute("INSERT INTO players (name, nickname) VALUES (?, ?)", (name, nick))
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


def get_players():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM players ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _pair_key(p1, p2):
    return (p1, p2) if p1 < p2 else (p2, p1)


def record_match(match_date: str, player1_id: int, player2_id: int,
                  score1: int, score2: int, series_id=None):
    if player1_id == player2_id:
        raise ValueError("A player cannot play themselves.")
    if score1 == score2:
        raise ValueError("Table tennis games can't end in a draw — scores must differ.")

    winner_id = player1_id if score1 > score2 else player2_id
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO matches (match_date, series_id, player1_id, player2_id, score1, score2, winner_id)
           VALUES (?,?,?,?,?,?,?)""",
        (match_date, series_id, player1_id, player2_id, score1, score2, winner_id),
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


def get_standings():
    """Baseline + all matches, combined into current MP/W/L/GF/GA/GD/Points."""
    conn = get_connection()
    players = conn.execute("SELECT * FROM players").fetchall()
    baseline = {r["player_id"]: dict(r) for r in conn.execute("SELECT * FROM baseline").fetchall()}
    elo_rows = {r["player_id"]: r["rating"] for r in conn.execute("SELECT * FROM elo").fetchall()}
    matches = conn.execute("SELECT * FROM matches").fetchall()
    conn.close()

    stats = {}
    for p in players:
        b = baseline[p["id"]]
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


def get_h2h_matrix():
    """Returns {(name1, name2): {name1: wins, name2: wins}} combining baseline + matches."""
    conn = get_connection()
    players = {r["id"]: r["name"] for r in conn.execute("SELECT * FROM players").fetchall()}
    baseline_h2h = conn.execute("SELECT * FROM baseline_h2h").fetchall()
    matches = conn.execute("SELECT * FROM matches").fetchall()
    conn.close()

    pair_wins = {}  # (pid_a, pid_b) sorted -> {pid: wins}
    for row in baseline_h2h:
        key = _pair_key(row["player1_id"], row["player2_id"])
        pair_wins.setdefault(key, {}).setdefault(row["player1_id"], 0)
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


def get_match_log(limit=200):
    conn = get_connection()
    rows = conn.execute(
        """SELECT m.*, p1.name AS p1_name, p2.name AS p2_name
           FROM matches m
           JOIN players p1 ON p1.id = m.player1_id
           JOIN players p2 ON p2.id = m.player2_id
           ORDER BY m.match_date DESC, m.id DESC LIMIT ?""",
        (limit,),
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
