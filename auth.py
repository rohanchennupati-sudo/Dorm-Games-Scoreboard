"""
auth.py — username/password accounts, using only the Python standard
library (hashlib's PBKDF2-HMAC-SHA256, salted per user — no external auth
service or dependency needed for a small app like this).

Design:
- Every league requires membership to view or edit (`db.league_members`).
  The league picker in app.py only ever shows leagues the logged-in user
  belongs to, so a random person with the URL can create their own account
  and their own leagues, but can't see or touch anyone else's data unless
  explicitly invited.
- Bootstrap problem: leagues created before accounts existed (your original
  60-game "Original Squad" league, and anything else you made while testing)
  have zero members, so nobody could see them. `create_user()` handles this:
  the FIRST account ever created on a given database is automatically made
  owner of every currently-ownerless league. In practice, sign up as
  yourself first — you'll get ownership of all your existing data — *then*
  share the app with friends.
"""

import hashlib
import os
import binascii
import db

PBKDF2_ITERATIONS = 200_000


def _hash_password(password: str, salt: bytes = None):
    if salt is None:
        salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return salt, pwd_hash


def create_user(username: str, password: str):
    username = username.strip()
    if not username:
        raise ValueError("Username can't be empty.")
    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")

    conn = db.get_connection()
    cur = conn.cursor()

    existing = cur.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        conn.close()
        raise ValueError(f"Username '{username}' is already taken.")

    is_first_ever_user = cur.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0

    salt, pwd_hash = _hash_password(password)
    cur.execute(
        "INSERT INTO users (username, password_hash, salt) VALUES (?,?,?)",
        (username, binascii.hexlify(pwd_hash).decode(), binascii.hexlify(salt).decode()),
    )
    user_id = cur.lastrowid

    if is_first_ever_user:
        # Claim every league that currently has no members — this is how
        # pre-auth data (your original league) gets attached to an owner.
        orphaned = cur.execute(
            """SELECT l.id FROM leagues l
               LEFT JOIN league_members lm ON lm.league_id = l.id
               WHERE lm.league_id IS NULL"""
        ).fetchall()
        for row in orphaned:
            cur.execute(
                "INSERT INTO league_members (league_id, user_id, role) VALUES (?,?,'owner')",
                (row["id"], user_id),
            )

    conn.commit()
    conn.close()
    return user_id


def verify_user(username: str, password: str):
    """Returns (user_id, username) on success, or None on failure."""
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    conn.close()
    if not row:
        return None
    salt = binascii.unhexlify(row["salt"])
    _, computed_hash = _hash_password(password, salt)
    if binascii.hexlify(computed_hash).decode() == row["password_hash"]:
        return (row["id"], row["username"])
    return None
