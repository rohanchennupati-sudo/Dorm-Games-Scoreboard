"""
auth.py — username/password accounts using only the Python standard library
(PBKDF2-HMAC-SHA256 with a random salt per user).

Design:
- Every league requires membership to view or edit (`db.league_members`).
  The league picker in app.py only shows leagues the logged-in user belongs
  to, so anyone can create an account and their own leagues, but can't see
  or change another league unless they are invited.
- Bootstrap: leagues created before accounts existed have no members. The
  first account created on a database becomes owner of every league with no
  members. On a fresh deployment the original owner should sign up first.

Password hash format:
- New hashes are stored as "pbkdf2_sha256$<iterations>$<hex digest>", so the
  iteration count can be raised later without breaking existing accounts.
- Older accounts store a bare hex digest made with 200,000 iterations. They
  still log in, and their hash is upgraded to the current format on the next
  successful login.
"""

import hashlib
import hmac
import os
import binascii
import db

PBKDF2_ITERATIONS = 600_000         # OWASP guidance for PBKDF2-HMAC-SHA256
LEGACY_ITERATIONS = 200_000         # hashes stored before the format change
HASH_PREFIX = "pbkdf2_sha256"


def _hash_password(password: str, salt: bytes = None, iterations: int = PBKDF2_ITERATIONS):
    if salt is None:
        salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return salt, pwd_hash


def _encode_hash(pwd_hash: bytes, iterations: int = PBKDF2_ITERATIONS) -> str:
    return f"{HASH_PREFIX}${iterations}${binascii.hexlify(pwd_hash).decode()}"


def _decode_hash(stored: str):
    """Return (iterations, hex digest) for both the new and the legacy format."""
    if stored.startswith(HASH_PREFIX + "$"):
        _, iterations, digest = stored.split("$")
        return int(iterations), digest
    return LEGACY_ITERATIONS, stored


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
        (username, _encode_hash(pwd_hash), binascii.hexlify(salt).decode()),
    )
    user_id = cur.lastrowid

    if is_first_ever_user:
        # Claim every league that currently has no members, so leagues created
        # before accounts existed get an owner.
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
    iterations, stored_digest = _decode_hash(row["password_hash"])
    _, computed = _hash_password(password, salt, iterations)

    # Constant-time comparison so response time doesn't leak how much matched.
    if not hmac.compare_digest(binascii.hexlify(computed).decode(), stored_digest):
        return None

    if iterations < PBKDF2_ITERATIONS:
        _upgrade_hash(row["id"], password)
    return (row["id"], row["username"])


def _upgrade_hash(user_id: int, password: str):
    """Re-hash a correct password with the current settings and a new salt."""
    salt, pwd_hash = _hash_password(password)
    conn = db.get_connection()
    conn.execute(
        "UPDATE users SET password_hash=?, salt=? WHERE id=?",
        (_encode_hash(pwd_hash), binascii.hexlify(salt).decode(), user_id),
    )
    conn.commit()
    conn.close()
