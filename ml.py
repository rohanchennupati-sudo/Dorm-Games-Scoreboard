"""
ml.py — a trained win-probability model, separate from the Elo formula in db.py.

Elo is a fixed update rule; this module learns from the match history:

1. FEATURES (_build_dataset): replay a league's matches in date order and,
   for each match, compute four features from the state BEFORE that match
   (no lookahead):
     - elo_diff:    Elo rating gap between the two players at that point
     - form_diff:   difference in win rate over each player's last FORM_WINDOW games
     - h2h_diff:    player 1's share of their previous head-to-head wins, minus 0.5
     - margin_diff: difference in average point margin over the last FORM_WINDOW games
   The label is 1 if player 1 won.

2. MIRRORING (_mirror): every match is also added with the players swapped
   (features negated, label flipped), so the model can't learn a "player 1
   usually wins" bias from the order players were entered.

3. TRAINING + EVALUATION (train_and_evaluate): StandardScaler + logistic
   regression. Scaling puts the features on the same scale, so the learned
   weights can be compared and regularisation treats them equally. With
   enough matches it reports TimeSeriesSplit cross-validation (always tested
   on later matches than it trained on). Folds are split by match and
   mirrored inside each training fold, so a match and its mirror can never
   end up on opposite sides of a split.

4. PREDICTION (predict_with_model): the same features computed from the
   current state, for a hypothetical next match.

Only matches logged in the app (the `matches` table) are used; the legacy
baseline has season totals but no per-game data.
"""

import numpy as np
import db

FORM_WINDOW = 5
MIN_MATCHES_TO_TRAIN = 6
MIN_MATCHES_FOR_CV = 10

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
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

    state["elo"][p1], state["elo"][p2] = db._elo_step(
        state["elo"][p1], state["elo"][p2], winner_id == p1)

    state["recent_results"][p1].append(1 if winner_id == p1 else 0)
    state["recent_results"][p2].append(1 if winner_id == p2 else 0)
    state["recent_margins"][p1].append(score1 - score2)
    state["recent_margins"][p2].append(score2 - score1)

    key = tuple(sorted((p1, p2)))
    state["h2h_wins"].setdefault(key, {})
    state["h2h_wins"][key][winner_id] = state["h2h_wins"][key].get(winner_id, 0) + 1


def _build_dataset(league_id):
    """Returns (X, y, n_matches, final_state) with ONE row per match, in date order."""
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
        X.append(_features_for(state, p1, p2))      # computed before the result is known
        y.append(1 if winner == p1 else 0)
        _apply_result(state, p1, p2, winner, m["score1"], m["score2"])

    return np.array(X, dtype=float).reshape(-1, len(FEATURE_NAMES)), np.array(y), len(matches), state


def _mirror(X, y):
    """Add each row again with the players swapped: features negated, label flipped."""
    return np.vstack([X, -X]), np.concatenate([y, 1 - y])


def _new_model():
    return make_pipeline(StandardScaler(), LogisticRegression())


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

    X_all, y_all = _mirror(X, y)
    model = _new_model().fit(X_all, y_all)

    metrics = {"n_matches": n_matches, "n_samples": len(y_all)}
    metrics["train_accuracy"] = float(accuracy_score(y_all, model.predict(X_all)))
    # Weights are per standard deviation of each feature, so they are comparable.
    metrics["coefficients"] = dict(zip(FEATURE_NAMES, model[-1].coef_[0].tolist()))

    if n_matches >= MIN_MATCHES_FOR_CV:
        n_splits = max(2, min(5, n_matches // 4))
        accs, losses = [], []
        # Split by match (one row each), then mirror only the training part.
        for train_idx, test_idx in TimeSeriesSplit(n_splits=n_splits).split(X):
            X_tr, y_tr = _mirror(X[train_idx], y[train_idx])
            fold_model = _new_model().fit(X_tr, y_tr)
            accs.append(accuracy_score(y[test_idx], fold_model.predict(X[test_idx])))
            probs = fold_model.predict_proba(X[test_idx])[:, 1]
            losses.append(log_loss(y[test_idx], probs, labels=[0, 1]))
        metrics["cv_accuracy"] = float(np.mean(accs))
        metrics["cv_log_loss"] = float(np.mean(losses))
        metrics["cv_folds"] = len(accs)
    else:
        metrics["cv_accuracy"] = None
        metrics["note"] = (
            f"Need {MIN_MATCHES_FOR_CV}+ matches for a reliable held-out evaluation "
            f"(currently {n_matches}) — showing training-set fit only, which tends "
            f"to look better than real-world performance."
        )

    return {"model": model, "metrics": metrics}


def predict_with_model(league_id, player1_id, player2_id, trained=None):
    """Returns (prob_player1_wins, error_message). Exactly one will be None.

    Pass the result of train_and_evaluate() as `trained` to avoid retraining.
    """
    result = trained if trained is not None else train_and_evaluate(league_id)
    if "error" in result:
        return None, result["error"]

    _, _, _, state = _build_dataset(league_id)
    feats = np.array([_features_for(state, player1_id, player2_id)])
    prob1 = float(result["model"].predict_proba(feats)[0][1])
    return prob1, None
