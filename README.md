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
- `players` — the 4 players and their team nicknames.
- `baseline` / `baseline_h2h` — the legacy 60-game season totals from the
  original spreadsheet, frozen as a starting point.
- `matches` — every game entered going forward (one row per game).
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
3. Done — you get a public URL. Note: Streamlit Cloud's filesystem resets on
   redeploy, so the DB will re-seed to baseline if the app restarts. For a
   tournament you actually want persisted long-term, swap `data/tt_tracker.db`
   for a hosted SQLite (e.g. Turso) or Postgres (e.g. Supabase/Neon free
   tier) — `db.py` is a thin enough layer that only `get_connection()` needs
   to change.

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
