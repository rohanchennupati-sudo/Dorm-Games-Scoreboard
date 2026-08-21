"""
add_auth_tables_to_turso.py — run this ONCE if you already migrated to Turso
before account/login support existed.

It only adds the two new tables (`users`, `league_members`) using
CREATE TABLE IF NOT EXISTS — it does not touch or duplicate any existing
data. If you're setting up Turso for the first time, you don't need this;
migrate_to_turso.py already includes these tables.
"""
import db

db.init_db()
print("Done — users and league_members tables now exist on Turso (if they didn't already).")
print("Sign up as yourself in the app first — the first account created automatically")
print("becomes owner of any leagues that don't have members yet.")
