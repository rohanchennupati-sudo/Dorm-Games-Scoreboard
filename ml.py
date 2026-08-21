"""
ml.py — a real trained win-probability model, separate from the Elo formula
in db.py.

Why this exists: Elo is a hand-written update rule, not a learned model.
This module builds an actual supervised learning pipeline on top of the
same match data:

1. FEATURE ENGINEERING (_build_dataset): walks through a league's matches
   in chronological order, and for each match computes four features using
   ONLY information available *before* that match was played (no leakage):
     - elo_diff:    Elo rating gap between the two players at that point
     - form_diff:   difference in each player's win rate over their last
                     FORM_WINDOW games
     - h2h_diff:    each player's head-to-head win rate against this
                     specific opponent, up to that point
     - margin_diff: difference in each player's average recent point margin
   The match's actual result becomes the label. Each match also contributes
   a mirrored row (players swapped, features negated, label flipped) so the
   model can't pick up a spurious "player 1 wins more" bias from column
   ordering.

2. TRAINING + EVALUATION (train_and_evaluate): fits a logistic regression on
   this data. With enough matches, it evaluates using TimeSeriesSplit
   cross-validation (never testing on a fold using data from the future,
   which a plain random split would allow) and reports accuracy + log loss.
   With too few matches for a trustworthy held-out test, it says so plainly
   instead of reporting a misleadingly confident number.

3. LIVE PREDICTION (predict_with_model): reuses the same feature pipeline
   on the CURRENT state of the league (after all matches) to predict a
   hypothetical next match between two players.

Note: only matches logged through this app (the `matches` table) have
per-game data to learn from — the legacy 60-game baseline is aggregate-only,
so it's correctly excluded here, same reasoning as the Elo backfill.
"""

import numpy as np
import db

FORM_WINDOW = 5
MIN_MATCHES_TO_TRAIN = 6
MIN_MATCHES_FOR_CV = 10

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import accuracy_score, log_loss
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

FEATURE_NAMES = ["elo_diff", "form_diff", "h2h_diff", "margin_diff"]


def _new_state(league_id):
    players = db.get_players(league_id)
    return {
        "elo": {p["id"]: db.STARTING_ELO for p in players},
        "recent_results": {p["id"]: [] for p in players},
        "recent_margins": {p["id"]: [] for p in players},
        "h2h_wins": {},
    }


def _form(state, pid):
    results = state["recent_results"].get(pid, [])[-FORM_WINDOW:]
    return sum(results) / len(results) if results else 0.5


def _margin(state, pid):
    margins = state["recent_margins"].get(pid, [])[-FORM_WINDOW:]
    return sum(margins) / len(margins) if margins else 0.0


def _h2h_rate(state, p1, p2):
    key = tuple(sorted((p1, p2)))
    wins = state["h2h_wins"].get(key, {})
    w1, w2 = wins.get(p1, 0), wins.get(p2, 0)
    total = w1 + w2
    return w1 / total if total else 0.5


def _features_for(state, p1, p2):
    return [
        state["elo"].get(p1, db.STARTING_ELO) - state["elo"].get(p2, db.STARTING_ELO),
        _form(state, p1) - _form(state, p2),
        _h2h_rate(state, p1, p2) - 0.5,
        _margin(state, p1) - _margin(state, p2),
    ]


def _apply_result(state, p1, p2, winner_id, score1, score2):
    for pid in (p1, p2):
        state["elo"].setdefault(pid, db.STARTING_ELO)
        state["recent_results"].setdefault(pid, [])
        state["recent_margins"].setdefault(pid, [])

    r1, r2 = state["elo"][p1], state["elo"][p2]
    exp1 = 1 / (1 + 10 ** ((r2 - r1) / 400))
    actual1 = 1.0 if winner_id == p1 else 0.0
    state["elo"][p1] = r1 + db.K_FACTOR * (actual1 - exp1)
    state["elo"][p2] = r2 + db.K_FACTOR * ((1 - actual1) - (1 - exp1))

    state["recent_results"][p1].append(1 if winner_id == p1 else 0)
    state["recent_results"][p2].append(1 if winner_id == p2 else 0)
    state["recent_margins"][p1].append(score1 - score2)
    state["recent_margins"][p2].append(score2 - score1)

    key = tuple(sorted((p1, p2)))
    state["h2h_wins"].setdefault(key, {})
    state["h2h_wins"][key][winner_id] = state["h2h_wins"][key].get(winner_id, 0) + 1


def _build_dataset(league_id):
    """Returns (X, y, n_matches, final_state). X/y include mirrored rows."""
    conn = db.get_connection()
    matches = conn.execute(
        "SELECT * FROM matches WHERE league_id=? ORDER BY match_date ASC, id ASC",
        (league_id,),
    ).fetchall()
    conn.close()

    state = _new_state(league_id)
    X, y = [], []

    for m in matches:
        p1, p2, winner = m["player1_id"], m["player2_id"], m["winner_id"]
        feats = _features_for(state, p1, p2)
        label = 1 if winner == p1 else 0

        X.append(feats)
        y.append(label)
        X.append([-f for f in feats])
        y.append(1 - label)

        _apply_result(state, p1, p2, winner, m["score1"], m["score2"])

    return np.array(X), np.array(y), len(matches), state


def train_and_evaluate(league_id):
    if not SKLEARN_AVAILABLE:
        return {"error": "scikit-learn isn't installed in this environment."}

    X, y, n_matches, _ = _build_dataset(league_id)
    if n_matches < MIN_MATCHES_TO_TRAIN:
        return {
            "error": f"Need at least {MIN_MATCHES_TO_TRAIN} matches logged in this "
                     f"league to train a model (currently {n_matches}).",
            "n_matches": n_matches,
        }

    model = LogisticRegression()
    model.fit(X, y)

    metrics = {"n_matches": n_matches, "n_samples": len(y)}
    train_preds = model.predict(X)
    metrics["train_accuracy"] = float(accuracy_score(y, train_preds))
    metrics["coefficients"] = dict(zip(FEATURE_NAMES, model.coef_[0].tolist()))

    if n_matches >= MIN_MATCHES_FOR_CV:
        n_splits = max(2, min(5, n_matches // 4))
        tscv = TimeSeriesSplit(n_splits=n_splits)
        accs, losses = [], []
        for train_idx, test_idx in tscv.split(X):
            if len(set(y[train_idx])) < 2:
                continue
            m = LogisticRegression().fit(X[train_idx], y[train_idx])
            preds = m.predict(X[test_idx])
            probs = m.predict_proba(X[test_idx])[:, 1]
            accs.append(accuracy_score(y[test_idx], preds))
            try:
                losses.append(log_loss(y[test_idx], probs, labels=[0, 1]))
            except Exception:
                pass
        if accs:
            metrics["cv_accuracy"] = float(np.mean(accs))
            metrics["cv_log_loss"] = float(np.mean(losses)) if losses else None
            metrics["cv_folds"] = len(accs)
        else:
            metrics["cv_accuracy"] = None
            metrics["note"] = "Cross-validation folds didn't have both outcomes present; add more matches."
    else:
        metrics["cv_accuracy"] = None
        metrics["note"] = (
            f"Need {MIN_MATCHES_FOR_CV}+ matches for a reliable held-out evaluation "
            f"(currently {n_matches}) — showing training-set fit only, which tends "
            f"to look better than real-world performance."
        )

    return {"model": model, "metrics": metrics}


def predict_with_model(league_id, player1_id, player2_id):
    """Returns (prob_player1_wins, error_message). Exactly one will be None."""
    result = train_and_evaluate(league_id)
    if "error" in result:
        return None, result["error"]

    _, _, _, state = _build_dataset(league_id)
    feats = np.array([_features_for(state, player1_id, player2_id)])
    prob1 = float(result["model"].predict_proba(feats)[0][1])
    return prob1, None
