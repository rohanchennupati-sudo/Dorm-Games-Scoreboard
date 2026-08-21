import streamlit as st
import pandas as pd
from datetime import date
import db

st.set_page_config(page_title="Table Tennis Tracker", page_icon="🏓", layout="wide")

db.init_db()
db.seed_if_empty()

st.title("🏓 Table Tennis Tournament Tracker")

# ------------------------------------------------------------- LEAGUE PICKER
leagues = db.get_leagues()
league_names = [l["name"] for l in leagues]

with st.sidebar:
    st.header("League")
    selected_name = st.selectbox("Active league", options=league_names, key="league_select")
    active_league = next(l for l in leagues if l["name"] == selected_name)

    with st.expander("➕ Create a new league"):
        new_league_name = st.text_input("League name", key="new_league_name")
        if st.button("Create league"):
            try:
                db.create_league(new_league_name)
                st.success(f"Created '{new_league_name}'. Select it above.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    with st.expander("➕ Add a player to this league"):
        new_player_name = st.text_input("Player name", key="new_player_name")
        new_player_nick = st.text_input("Nickname / team name (optional)", key="new_player_nick")
        if st.button("Add player"):
            try:
                db.add_player(active_league["id"], new_player_name, new_player_nick)
                st.success(f"Added '{new_player_name}' to {active_league['name']}.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

league_id = active_league["id"]
st.caption(f"Viewing: **{active_league['name']}**")

players = db.get_players(league_id)
name_to_id = {p["name"]: p["id"] for p in players}

if len(players) < 2:
    st.warning("This league needs at least 2 players before you can record a match. Add players from the sidebar.")
    st.stop()

tab_entry, tab_standings, tab_h2h, tab_elo, tab_log = st.tabs(
    ["➕ Enter Match", "📊 Standings", "🤝 Head-to-Head", "📈 Elo & Predictions", "📜 Match Log"]
)

# ---------------------------------------------------------------- ENTRY TAB
with tab_entry:
    st.subheader("Record a game")
    st.write("One row = one game between two players. This instantly updates MP/W/L/GF/GA/GD/Points, H2H, and Elo for both players.")

    col1, col2 = st.columns(2)
    with col1:
        p1_name = st.selectbox("Player 1", options=list(name_to_id.keys()), key="p1")
        score1 = st.number_input("Player 1 score", min_value=0, max_value=99, value=11, key="s1")
    with col2:
        remaining = [n for n in name_to_id.keys() if n != p1_name]
        p2_name = st.selectbox("Player 2", options=remaining, key="p2")
        score2 = st.number_input("Player 2 score", min_value=0, max_value=99, value=7, key="s2")

    match_date = st.date_input("Date", value=date.today())
    series_id = st.text_input("Series # (optional — groups the 6 games of a round-robin)", value="")

    if st.button("Save match", type="primary"):
        try:
            result = db.record_match(
                league_id=league_id,
                match_date=str(match_date),
                player1_id=name_to_id[p1_name],
                player2_id=name_to_id[p2_name],
                score1=int(score1),
                score2=int(score2),
                series_id=int(series_id) if series_id.strip() else None,
            )
            winner_name = p1_name if result["winner_id"] == name_to_id[p1_name] else p2_name
            st.success(f"Saved! {winner_name} wins {max(score1, score2)}-{min(score1, score2)}. Stats updated below.")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

# ------------------------------------------------------------ STANDINGS TAB
with tab_standings:
    st.subheader(f"Current standings — {active_league['name']}")
    standings = db.get_standings(league_id)
    df = pd.DataFrame(standings)[["name", "nickname", "mp", "w", "l", "gf", "ga", "gd", "points", "elo"]]
    df.columns = ["Player", "Team", "MP", "W", "L", "GF", "GA", "GD", "Points", "Elo"]
    df.insert(0, "Rank", range(1, len(df) + 1))
    st.dataframe(df, hide_index=True, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.write("**Points**")
        st.bar_chart(df.set_index("Player")["Points"])
    with c2:
        st.write("**Goal Difference**")
        st.bar_chart(df.set_index("Player")["GD"])

# ------------------------------------------------------------------ H2H TAB
with tab_h2h:
    st.subheader(f"Head-to-head record — {active_league['name']}")
    h2h = db.get_h2h_matrix(league_id)
    names = sorted(name_to_id.keys())
    matrix = pd.DataFrame("—", index=names, columns=names, dtype=object)
    for (a, b), wins in h2h.items():
        matrix.loc[a, b] = wins[a]
        matrix.loc[b, a] = wins[b]
    st.write("Cell = row player's wins **against** column player.")
    st.dataframe(matrix, use_container_width=True)

# -------------------------------------------------------- ELO / PREDICT TAB
with tab_elo:
    st.subheader(f"Elo ratings — {active_league['name']}")
    st.caption("Every player starts at 1500 when added to a league; ratings move ±up to 32 pts per game based on expected vs actual result.")
    standings = db.get_standings(league_id)
    elo_df = pd.DataFrame(standings)[["name", "elo"]].sort_values("elo", ascending=False)
    elo_df.columns = ["Player", "Elo"]
    st.dataframe(elo_df, hide_index=True, use_container_width=True)
    st.bar_chart(elo_df.set_index("Player")["Elo"])

    st.divider()
    st.subheader("Win predictor")
    pc1, pc2 = st.columns(2)
    with pc1:
        pred_a = st.selectbox("Player A", options=names, key="pred_a")
    with pc2:
        pred_b = st.selectbox("Player B", options=[n for n in names if n != pred_a], key="pred_b")
    prob_a, prob_b = db.win_probability(name_to_id[pred_a], name_to_id[pred_b])
    st.write(f"**{pred_a}: {prob_a*100:.1f}%** win probability  ·  **{pred_b}: {prob_b*100:.1f}%**")
    st.progress(prob_a)

# ------------------------------------------------------------------ LOG TAB
with tab_log:
    st.subheader(f"Match log — {active_league['name']}")
    log = db.get_match_log(league_id)
    if not log:
        st.info("No matches recorded yet. Add one in the Enter Match tab.")
    else:
        for m in log:
            c1, c2 = st.columns([5, 1])
            with c1:
                st.write(
                    f"**{m['match_date']}** — {m['p1_name']} {m['score1']}-{m['score2']} {m['p2_name']}"
                    + (f"  ·  series {m['series_id']}" if m["series_id"] else "")
                )
            with c2:
                if st.button("🗑️ Delete", key=f"del_{m['id']}"):
                    db.delete_match(m["id"])
                    st.success("Deleted and Elo recalculated.")
                    st.rerun()
            st.divider()
