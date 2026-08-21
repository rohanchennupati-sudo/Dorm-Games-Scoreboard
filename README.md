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

## Architecture

```
tt-tracker/
├── app.py          # Streamlit UI (5 tabs: Entry, Standings, H2H, Elo, Log)
├── db.py           # SQLite schema + all stats/Elo logic (no UI code here)
├── data/
│   └── tt_tracker.db
└── requirements.txt
```

**Data model**
- `leagues` — one row per league/tournament; everything below belongs to one.
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

- **Score-margin regression**: predict expected final score margin, not just
  win probability, using each player's recent point-differential trend.
- **Form-weighted Elo**: exponentially weight recent matches more heavily so
  a hot streak or slump shows up faster.
- **Series-level analytics**: use the `series_id` field to chart
  round-robin-over-round-robin momentum instead of only cumulative totals.
- **Auth + Google Form front end**: swap the Streamlit form for a Google
  Form + Apps Script webhook into this same DB, so matches can be logged from
  a phone without opening the app.
