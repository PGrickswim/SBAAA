"""
CRIA — Computer Rankings Index Average
======================================

The official method, as specified:

    CRIA_raw = (SWP + 2*OWP + OOWP)/4 + 0.18*(GWP - SWP) + 0.04*DomIndex
    CRIA     = 100 * CRIA_raw

Series weighting (applied to the SERIES result, not the games inside it):
    home series win   0.6 win     away series win   1.4 win
    away series loss  0.6 loss    home series loss  1.4 loss

Game weighting uses the same numbers at game level, for GWP.

OWP and OOWP are INCLUSIVE: the average of opponents' actual weighted SWP,
with nothing removed for games against the team being rated. This is a
league design choice and is deliberate — it is not college RPI.

Dominance: +1 for a win by 7 or more, -1 for a loss by 7 or more, averaged
over games played.

Regular season only; tournament games are excluded unless asked for.
"""

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ALPHA = 0.18     # weight on the game-level adjustment
BETA = 0.04      # weight on dominance
DOM_MARGIN = 7   # a win or loss by this much counts as dominant

W_HOME_WIN, W_AWAY_WIN = 0.6, 1.4
W_AWAY_LOSS, W_HOME_LOSS = 0.6, 1.4


def compute(games: pd.DataFrame, verbose: bool = True) -> Dict[str, Any]:
    """Take a ledger of games and return the CRIA table plus validation."""
    g = games.copy()
    n_rows = len(g)

    # --- series grouping ---
    # A series is one pairing at one host within one series slot. The ledger
    # already stores one row per physical game, so no de-duplication is
    # needed here (the schedule file itself lists every game twice).
    g["SeriesKey"] = (g.Series.astype(str) + "|" + g.Away + "|" + g.Home)

    # --- weighted game record ---
    gw = defaultdict(float); gl = defaultdict(float)
    gp = defaultdict(int); dom = defaultdict(int)
    for r in g.itertuples():
        margin = abs(r.AwayPts - r.HomePts)
        away_won = r.AwayPts > r.HomePts
        gp[r.Away] += 1; gp[r.Home] += 1
        if away_won:
            gw[r.Away] += W_AWAY_WIN;  gl[r.Home] += W_HOME_LOSS
        else:
            gw[r.Home] += W_HOME_WIN;  gl[r.Away] += W_AWAY_LOSS
        if margin >= DOM_MARGIN:
            dom[r.Away if away_won else r.Home] += 1
            dom[r.Home if away_won else r.Away] -= 1

    # --- weighted series record ---
    sw = defaultdict(float); sl = defaultdict(float)
    sp = defaultdict(int)
    opponents: Dict[str, List[str]] = defaultdict(list)
    complete = 0
    for key, sub in g.groupby("SeriesKey"):
        away = sub.Away.iloc[0]; home = sub.Home.iloc[0]
        a_wins = int((sub.AwayPts > sub.HomePts).sum())
        h_wins = int((sub.HomePts > sub.AwayPts).sum())
        if a_wins + h_wins < 2:
            continue                      # not yet decided
        complete += 1
        sp[away] += 1; sp[home] += 1
        opponents[away].append(home); opponents[home].append(away)
        if a_wins > h_wins:
            sw[away] += W_AWAY_WIN; sl[home] += W_HOME_LOSS
        else:
            sw[home] += W_HOME_WIN; sl[away] += W_AWAY_LOSS

    teams = sorted(set(list(gp) + list(sp)))

    def pct(w: Dict[str, float], l: Dict[str, float], t: str) -> float:
        d = w[t] + l[t]
        return w[t] / d if d else 0.0

    swp = {t: pct(sw, sl, t) for t in teams}
    gwp = {t: pct(gw, gl, t) for t in teams}

    # inclusive: the plain average of opponents' own weighted SWP
    owp = {t: (sum(swp.get(o, 0.0) for o in opponents[t]) / len(opponents[t])
               if opponents[t] else 0.0) for t in teams}
    oowp = {}
    for t in teams:
        vals = [owp.get(o, 0.0) for o in opponents[t]]
        oowp[t] = sum(vals) / len(vals) if vals else 0.0

    rows = []
    for t in teams:
        d_index = dom[t] / gp[t] if gp[t] else 0.0
        base = (swp[t] + 2 * owp[t] + oowp[t]) / 4
        raw = base + ALPHA * (gwp[t] - swp[t]) + BETA * d_index
        rows.append({
            "Team": t, "SeriesPlayed": sp[t], "GamesPlayed": gp[t],
            "WSeriesW": round(sw[t], 2), "WSeriesL": round(sl[t], 2),
            "SWP": round(swp[t], 5), "OWP": round(owp[t], 5),
            "OOWP": round(oowp[t], 5), "GWP": round(gwp[t], 5),
            "DomIndex": round(d_index, 5),
            "CRIA_base": round(base, 5),
            "GameAdj": round(ALPHA * (gwp[t] - swp[t]), 5),
            "DomAdj": round(BETA * d_index, 5),
            "CRIA": round(100 * raw, 3),
        })
    df = pd.DataFrame(rows).sort_values("CRIA", ascending=False)
    df.insert(0, "Rank", range(1, len(df) + 1))

    report = {
        "games_in_ledger": n_rows,
        "series_complete": complete,
        "series_incomplete": g.SeriesKey.nunique() - complete,
        "teams": len(teams),
        "avg_SWP": round(df.SWP.mean(), 4),
        "avg_OWP": round(df.OWP.mean(), 4),
        "avg_OOWP": round(df.OOWP.mean(), 4),
        "avg_GWP": round(df.GWP.mean(), 4),
        "avg_CRIA": round(df.CRIA.mean(), 3),
        "nulls": int(df.isna().sum().sum()),
    }
    if verbose:
        print("CRIA validation")
        for k, v in report.items():
            print(f"  {k:<20} {v}")
    return {"table": df, "report": report}


def explain(df: pd.DataFrame, games: pd.DataFrame, team: str) -> str:
    """Hand-trace one team's number from its actual opponents."""
    r = df[df.Team == team].iloc[0]
    sub = games[(games.Away == team) | (games.Home == team)]
    out = [f"{team} — CRIA {r.CRIA:.2f} (rank {int(r.Rank)})",
           f"  {int(r.SeriesPlayed)} series, {int(r.GamesPlayed)} games",
           f"  weighted series record {r.WSeriesW:.1f}-{r.WSeriesL:.1f}"
           f"  ->  SWP {r.SWP:.4f}",
           f"  OWP {r.OWP:.4f}   OOWP {r.OOWP:.4f}   GWP {r.GWP:.4f}",
           f"  base (SWP + 2*OWP + OOWP)/4 = {r.CRIA_base:.4f}",
           f"  game adj {ALPHA}*(GWP-SWP) = {r.GameAdj:+.4f}",
           f"  dom adj {BETA}*{r.DomIndex:.3f} = {r.DomAdj:+.4f}",
           f"  CRIA = 100 * {r.CRIA/100:.5f} = {r.CRIA:.2f}"]
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/season/games.csv"
    games = pd.read_csv(path)
    res = compute(games)
    print("\nTop 15")
    print(res["table"].head(15)[["Rank","Team","SeriesPlayed","SWP","OWP",
                                 "OOWP","GWP","CRIA"]].to_string(index=False))
