"""Run this to remove the most recently entered match (any league) and roll back its Elo effect."""
import db

conn = db.get_connection()
cur = conn.cursor()

last = cur.execute("SELECT * FROM matches ORDER BY id DESC LIMIT 1").fetchone()
if not last:
    print("No matches to undo.")
else:
    league = cur.execute("SELECT name FROM leagues WHERE id=?", (last["league_id"],)).fetchone()["name"]
    p1n = cur.execute("SELECT name FROM players WHERE id=?", (last["player1_id"],)).fetchone()["name"]
    p2n = cur.execute("SELECT name FROM players WHERE id=?", (last["player2_id"],)).fetchone()["name"]
    print(f"About to delete [{league}]: {p1n} {last['score1']}-{last['score2']} {p2n} on {last['match_date']}")
    confirm = input("Type 'yes' to confirm: ")
    if confirm.strip().lower() == "yes":
        db.delete_match(last["id"])
        print("Deleted and Elo ratings recalculated.")
    else:
        print("Cancelled.")
conn.close()
