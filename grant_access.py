"""
grant_access.py — emergency fix if you've lost access to your own leagues
(e.g. you can't remember which account, or its password, originally
claimed them via the "first user" bootstrap).

This makes an EXISTING account the owner of every league in the database,
regardless of who technically claimed them first. It does not touch any
match data, players, or stats — only league_members rows.

Usage:
    python grant_access.py <username>

The account must already exist — sign up for a NEW account with a
username/password you'll actually remember, then run this with that
username to hand it ownership of all your old leagues.
"""
import sys
import db

if len(sys.argv) != 2:
    sys.exit("Usage: python grant_access.py <username>")

username = sys.argv[1].strip()

conn = db.get_connection()
cur = conn.cursor()

user = cur.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
if not user:
    conn.close()
    sys.exit(f"No account called '{username}' exists yet. Sign up in the app first, then rerun this.")

user_id = user["id"]

leagues = cur.execute("SELECT id, name FROM leagues").fetchall()
if not leagues:
    conn.close()
    sys.exit("No leagues exist in the database at all — nothing to grant access to.")

granted, upgraded = [], []
for lg in leagues:
    existing = cur.execute(
        "SELECT role FROM league_members WHERE league_id=? AND user_id=?", (lg["id"], user_id)
    ).fetchone()
    if existing:
        if existing["role"] != "owner":
            cur.execute(
                "UPDATE league_members SET role='owner' WHERE league_id=? AND user_id=?",
                (lg["id"], user_id),
            )
            upgraded.append(lg["name"])
    else:
        cur.execute(
            "INSERT INTO league_members (league_id, user_id, role) VALUES (?,?,'owner')",
            (lg["id"], user_id),
        )
        granted.append(lg["name"])

conn.commit()
conn.close()

print(f"'{username}' is now OWNER of every league in the database.")
if granted:
    print("Newly granted access to:", ", ".join(granted))
if upgraded:
    print("Upgraded to owner on (was already a member of):", ", ".join(upgraded))
if not granted and not upgraded:
    print("(Was already owner of everything — nothing changed.)")
