# 🏓 Table Tennis Tournament Tracker

A self-hosted league tracker for a 4-player table tennis tournament: automatic
standings (MP/W/L/GF/GA/GD/Points), a live head-to-head matrix, and an Elo
rating system with a win-probability predictor — all recomputed automatically
from raw match results, nothing hardcoded.

## Why this exists

The original tracking was a manually-updated Excel sheet — every match meant
retyping totals by hand across three tables. This rebuilds it as a proper
small data application: one source-of-truth database, one form to enter a
result, everything else derived.

## Multiple leagues

The app supports more than just the original 4 players. From the sidebar you
can:
- **Create a new league** — a completely separate set of players, matches,
  standings, H2H, and Elo ratings. Your original 60-game data lives in the
  "Original Squad" league and is untouched by any league you create.
- **Add a player to the active league** — new players start at 0 MP/W/L and
  Elo 1500, and immediately appear in the match-entry dropdowns for that
  league.

Everything (`standings`, `H2H`, `Elo`, `match log`) is always scoped to
whichever league is selected at the top of the sidebar.

## The trained model (not just Elo)

Elo (in `db.py`) is a hand-written update rule — useful, but not a learned
model. `ml.py` adds a real supervised learning pipeline on top of the same
match data:

- **Features**, computed using only information available *before* each
  match (no lookahead): Elo gap, each player's win rate over their last 5
  games, their head-to-head win rate against this specific opponent so far,
  and their recent average point margin. Every match also contributes a
  mirrored row (players swapped, features negated, label flipped) so the
  model can't learn a spurious "player 1 wins more" bias from column order.
- **Model**: logistic regression (`scikit-learn`), retrained on the fly from
  current match data each time it's used — the dataset is small enough that
  this is cheap, and it means the model is always up to date.
- **Evaluation**: with 10+ matches, uses `TimeSeriesSplit` cross-validation
  — each fold is only tested on matches that happened *after* its training
  data, so the reported accuracy/log-loss can't be inflated by leakage. With
  fewer than 10 matches, the app says so explicitly and shows training-set
  fit only, rather than presenting an unreliable number with false
  confidence.
- **Minimum data**: needs 6+ matches logged in a league before it will train
  at all; below that, `ml.py` returns a clear error instead of a garbage
  prediction.

Like the Elo backfill decision, this only trains on matches entered through
this app (the `matches` table) — the legacy 60-game baseline is
aggregate-only, so there's no per-game data to learn from there.

## Accounts & access control

The app is gated behind sign-up/login (username + password, salted and
hashed with PBKDF2-HMAC-SHA256 — no external auth service, just Python's
stdlib). This is what stops a random person with the URL from creating fake
matches in your league:

- **Leagues are private by default.** The league picker only ever shows
  leagues you're a member of. Someone else's league simply doesn't appear
  in your list — there's no way to select or edit it.
- **Anyone can sign up and create their own league(s)**, which they own.
- **Owners invite members by username** (sidebar → Members), so a league
  can be shared with specific people without opening it to everyone.
- **Bootstrapping your existing data:** since your original league(s) were
  created before accounts existed, they start with zero members. The very
  first account created on a given database automatically becomes owner of
  every such "orphaned" league. **Sign up as yourself before sharing the
  app with anyone else** — otherwise someone else's signup could claim your
  data instead.

If you already deployed before this update, see "Upgrading an existing
deployment" below for the one extra step needed.

## Upgrading an existing deployment

- **Local database, pre multi-league** (the very first version you ran):
  run `python upgrade_local_schema.py` once.
- **Local or Turso database, multi-league but pre-auth** (anything from
  before this update): nothing extra needed for local — just run the app,
  since `db.init_db()` adds the new `users`/`league_members` tables
  automatically (they use `CREATE TABLE IF NOT EXISTS`, so nothing existing
  is touched). For an **already-migrated Turso database**, run
  `python add_auth_tables_to_turso.py` once instead.
- Either way, **sign up as yourself first** so you automatically become
  owner of your existing league(s) — see above.

## Architecture

```
tt-tracker/
├── app.py          # Streamlit UI: login gate + 6 tabs (Entry, Standings, H2H, Elo, ML Model, Log)
├── auth.py         # Account creation, login verification, legacy-league claiming
├── db.py           # SQLite/Turso schema + all stats/Elo/league/membership logic
├── ml.py           # Feature engineering + trained logistic regression model
├── data/
│   └── tt_tracker.db
└── requirements.txt
```

**Data model**
- `users` — one row per account (username + salted/hashed password).
- `leagues` — one row per league/tournament; everything below belongs to one.
- `league_members` — who can see/edit which league, and whether they're the
  owner (can invite others) or a regular member.
- `players` — scoped to a league (name unique within a league, not globally).
- `baseline` / `baseline_h2h` — the legacy 60-game season totals from the
  original spreadsheet, frozen as a starting point for the original league.
  New leagues/players simply start at zero.
- `matches` — every game entered going forward (one row per game), tagged
  with its league.
- `elo` — current Elo rating per player.

Current standings are **never stored** as a static number — `get_standings()`
in `db.py` recomputes MP/W/L/GF/GA/GD/Points as `baseline + aggregate(matches)`
every time it's called. Same for the H2H matrix. This means the numbers can
never drift out of sync with the match log, which was the core problem with
the spreadsheet.

**Why Elo starts at 1500 for everyone, not backfilled:** the original sheet
only had aggregate win/loss totals, not the actual chronological order of the
60 legacy games. Elo is order-dependent (who you beat and when matters), so
backfilling it from an aggregate would just be fabricated precision dressed
up as a real rating. Elo tracking starts cleanly from the first match logged
in this system.

**Point system:** 2 points per win, 0 for a loss (matches the original
sheet's convention — no draws in table tennis).

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens at `http://localhost:8501`. The database seeds itself automatically on
first run with the legacy baseline totals.

## Deploying (free, so you can link it on a resume)

1. Push this folder to a public GitHub repo.
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with
   GitHub, "New app", point it at `app.py`.
3. Done — you get a public URL.

**Important:** by default this uses a local SQLite file, and Streamlit
Cloud's filesystem resets on every restart/redeploy — so without the step
below, any matches entered on the live app can be lost. Set up persistent
storage (next section) before relying on the live app for real matches.

## Persistent storage with Turso (do this for a real tournament)

The app automatically switches from local SQLite to a hosted database the
moment it finds Turso credentials — no code changes needed, just secrets.

1. **Create a free Turso database.** Go to [turso.tech](https://turso.tech),
   sign up, and either use their web dashboard to create a database, or
   install their CLI and run:
   ```bash
   turso db create tt-tracker
   turso db show tt-tracker --url
   turso db tokens create tt-tracker
   ```
   The `--url` command gives you `TURSO_DATABASE_URL` (starts with
   `libsql://...`); the `tokens create` command gives you `TURSO_AUTH_TOKEN`.

2. **Add credentials locally.** Create `.streamlit/secrets.toml` in this
   folder (this file is gitignored — never commit it):
   ```toml
   TURSO_DATABASE_URL = "libsql://your-db-name.turso.io"
   TURSO_AUTH_TOKEN = "your-token-here"
   ```

3. **Migrate your existing local matches:**
   ```bash
   pip install -r requirements.txt
   python migrate_to_turso.py
   ```
   This copies everything from your local `data/tt_tracker.db` into Turso,
   preserving IDs so foreign keys stay correct. Run it once, on an empty
   Turso database only.

4. **Add the same secrets to Streamlit Cloud:** on your app's page, go to
   Settings → Secrets, and paste the same two lines from step 2.

5. **Redeploy.** From then on, both your local app and the live app read and
   write the *same* Turso database — no more local/live split, and data
   survives restarts.

## Possible extensions (good "future work" bullet points)

- **Password reset** — currently there's no recovery flow if someone forgets
  their password; would need an email-based reset or an admin override.
- **Per-action permissions** — right now any league member can add matches
  or players; only inviting new members is owner-restricted. Could add a
  read-only "viewer" role for people who just want to watch standings.
- **Richer features for the trained model**: rest days between matches,
  opponent-strength-adjusted form, series-level momentum.
- **Try other model families**: gradient boosting (XGBoost/LightGBM) often
  handles small tabular datasets with non-linear interactions better than
  logistic regression — worth comparing once there's more match data.
- **Score-margin regression**: predict expected point margin, not just
  win/loss, using the same feature set.
- **Auth + Google Form front end**: swap the Streamlit form for a Google
  Form + Apps Script webhook into this same DB, so matches can be logged from
  a phone without opening the app.
