# 🏓 Dorm Games Scoreboard

A table tennis league tracker for my dorm, built with Streamlit. Log a game, and the standings (MP/W/L/GF/GA/GD/Points), head-to-head matrix, Elo ratings and win predictions all update from the raw match results.

## Why I built it

We tracked our 4-player league in an Excel sheet. Every game meant retyping totals by hand across three tables, and they kept drifting out of sync. This app keeps one source of truth, the match log, and derives everything else from it.

## Features

- **Standings and head-to-head**, recomputed from the match log every time they're shown, so they can't drift from the games played.
- **Elo ratings** (start 1500, K = 32) and an Elo-based win probability.
- **A trained win predictor** (logistic regression) alongside Elo, with time-series cross-validation.
- **Multiple private leagues** with accounts: each league is visible only to its members, and owners invite others by username.
- **Undo a wrong entry** by deleting it from the match log (Elo is rebuilt automatically).
- **Persistent storage** on a hosted Turso (libSQL) database, or a local SQLite file.

## How it works

### Data model

| Table | Holds |
| --- | --- |
| `users` | accounts (username, salted password hash) |
| `leagues` | one row per league |
| `league_members` | which users belong to which league, and who owns it |
| `players` | players, unique by name within a league |
| `baseline`, `baseline_h2h` | season totals and head-to-head wins from the original spreadsheet, kept as a fixed starting point |
| `matches` | one row per game: date, players, scores, winner |
| `elo` | current Elo rating per player |

Standings are never stored. `get_standings()` adds the baseline to an aggregate over `matches` each time it's called; the head-to-head matrix works the same way.

### Elo

Each game moves both ratings by K × (actual − expected), where expected = 1 / (1 + 10^((R_opponent − R_player) / 400)). Because Elo depends on the order of games, ratings are rebuilt by replaying all of a league's matches in date order after every insert or delete. That keeps them correct even if a game is logged late with an earlier date.

The original season (60 games per player, 120 games in total) only exists as totals, not as an ordered list of games, so Elo isn't back-filled from it. Ratings start from the first game logged in the app.

**Points:** 2 for a win, 0 for a loss (table tennis has no draws).

### The trained model

`ml.py` trains a logistic regression on each league's own match history:

- **Features**, computed only from games before the one being predicted: Elo difference, recent form (win rate over the last 5 games), head-to-head record, and recent average point margin.
- **Mirrored rows:** each game is also added with the players swapped, so the model can't learn that whoever is entered as "player 1" tends to win.
- **Scaling:** features are standardised inside a pipeline, so the learned weights are comparable.
- **Evaluation:** with 10+ games, `TimeSeriesSplit` cross-validation always tests on later games than it trained on. Folds are split by game and mirrored inside the training part, so a game and its mirror never land on opposite sides of a split. With fewer than 10 games the app says so and shows training fit only; below 6 it doesn't train.
- The trained model is cached and only retrains when a game is added or deleted.

The model only uses games logged in the app, since the original season has no per-game data.

### Accounts

- Passwords are hashed with PBKDF2-HMAC-SHA256 (600,000 iterations, random salt per user) from Python's standard library, and checked with a constant-time comparison. Older hashes are upgraded automatically on the next login.
- The league picker only shows leagues you're a member of.
- Leagues created before accounts existed have no members. The first account created on a database becomes owner of all of them, so the original owner should sign up first on a new deployment.

## Project structure

```
Dorm-Games-Scoreboard/
├── app.py                        # Streamlit UI: login, sidebar, 6 tabs
├── db.py                         # schema, queries, standings, H2H, Elo, Turso adapter
├── auth.py                       # sign-up, login, password hashing
├── ml.py                         # features, model training and evaluation
├── migrate_to_turso.py           # copy a local SQLite database into Turso
├── upgrade_local_schema.py       # upgrade a pre-multi-league local database
├── add_auth_tables_to_turso.py   # add account tables to an older Turso database
├── grant_access.py               # make a user owner of every league
├── undo_last_match.py            # delete the most recent match from the command line
└── requirements.txt
```

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

It opens at `http://localhost:8501`. On first run it creates `data/tt_tracker.db` and seeds the original league.

## Deploying

The app runs on [Streamlit Community Cloud](https://share.streamlit.io): point a new app at `app.py` in this repo.

Streamlit Cloud's filesystem resets on every restart, so a deployed app needs a hosted database. If `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` are set (in Streamlit secrets or environment variables), `db.py` uses Turso instead of the local file. A small adapter makes the Turso client look like Python's `sqlite3`, so the rest of the code doesn't change.

### Setting up Turso

1. Create a database at [turso.tech](https://turso.tech) (or with the CLI: `turso db create tt-tracker`, `turso db show tt-tracker --url`, `turso db tokens create tt-tracker`).
2. Put the credentials in `.streamlit/secrets.toml` (gitignored):
   ```toml
   TURSO_DATABASE_URL = "libsql://your-db-name.turso.io"
   TURSO_AUTH_TOKEN = "your-token-here"
   ```
3. To copy existing local data across, run `python migrate_to_turso.py` once, on an empty Turso database (requires Python 3.11+).
4. Add the same two secrets in the Streamlit Cloud app settings and redeploy.

### Upgrading an older database

- Local database from before multi-league support: run `python upgrade_local_schema.py` once.
- Turso database from before accounts: run `python add_auth_tables_to_turso.py` once. Local databases get the new tables automatically.

## Limitations and next steps

- **No password reset** yet; `grant_access.py` is the admin fallback.
- **Permissions are coarse:** any member can add players or delete matches; only invites are owner-only. A read-only viewer role would help.
- **Turso writes aren't transactional:** each statement commits on its own. Because Elo is rebuilt from the match log, an interrupted write corrects itself on the next insert or delete.
- **Model:** more features (rest days, opponent-adjusted form), comparing against gradient boosting once there's more data, and predicting point margin as well as the winner.
