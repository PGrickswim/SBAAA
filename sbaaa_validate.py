"""
SBAAA Validation Suite
======================
Runs the full chain over many random matchups and checks every invariant
the system is supposed to hold. Exit code 0 means all green.
"""

import random
import statistics as st
import sys
from typing import Dict, List

from sbaaa_data import League
from sbaaa_gv import all_team_oq, gv_base
from sbaaa_dice_engine import (TeamState, simulate_game, fatigue_value,
                               locpoints_from_miles, OT_LOSER_BY_RED)
def _otd(g):
    """Overtime split from the dice, so regulation can finish level."""
    if not g["result"]["OT"]:
        return None
    d = dict(g["ot"]); d["OT_Margin"] = g["result"]["Margin"]
    return d

from sbaaa_pbp import build_possessions, GAME_SECONDS, CLEAR_DEADLINE
from sbaaa_events import expand, box_from_events
from sbaaa_boxscore import assign_minutes, apply_exits, GAME_MINUTES

GAMES = 300
FAILURES: List[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILURES.append(msg)


def run() -> Dict[str, float]:
    lg = League()
    oq = all_team_oq(lg.roster, lg.rstar, lg.coaches).set_index("Team").OQ.to_dict()
    teams = list(lg.rstar.Team)
    pickrng = random.Random(2027)

    agg: Dict[str, List[float]] = {k: [] for k in (
        "pts", "poss", "ppp", "fg", "three", "ft", "reb", "ast", "stl",
        "blk", "tov", "pf", "starter_min", "top_share", "secs")}
    ot_games = fouled_out = bonus = 0

    for i in range(GAMES):
        A, H = pickrng.sample(teams, 2)
        rng = random.Random(i)
        ap, hp = lg.active_five(A), lg.active_five(H)
        assign_minutes(ap); assign_minutes(hp)

        series = pickrng.randint(1, 52)
        a_state = TeamState(lg.code(A), gv_base(series, lg.r_star(A), None),
                            oq[A], fatigue=fatigue_value(
                                consecutive_away=pickrng.randint(0, 4)))
        h_state = TeamState(lg.code(H), gv_base(series, lg.r_star(H), None), oq[H])
        g = simulate_game(a_state, h_state, pickrng.randint(1, 10))

        apts, hpts = g["result"]["Away"], g["result"]["Home"]
        ot = g["result"]["OT"]
        ot_games += ot
        ot_secs = pickrng.uniform(110, 190) if ot else 0.0

        # --- dice engine invariants ---
        check(apts > 0 and hpts > 0, f"game {i}: non-positive score")
        check(apts != hpts, f"game {i}: tied final score")
        check(abs(apts - hpts) == g["result"]["Margin"],
              f"game {i}: margin does not match the score")
        if ot:
            d = g["ot"]
            check(d["RegTotal"] % 2 == 0, f"game {i}: OT regulation total is odd")
            check(min(apts, hpts) == d["RegEach"] + d["OT_Loser_Points"],
                  f"game {i}: loser total does not match the OT rule")
            check(4 <= g["result"]["Margin"] <= 6,
                  f"game {i}: OT margin outside 4-6")

        # --- possessions ---
        poss = build_possessions(A, H, ap, hp, apts, hpts,
                                 [g["dice"]["W1"], g["dice"]["W2"],
                                  g["dice"]["W3"]],
                                 A if g["setup"]["Favorite"] == lg.code(A) else H,
                                 rng, ot_seconds=ot_secs,
                                 ot_detail=_otd(g))
        pa = sum(p.points for p in poss if p.team == A)
        ph = sum(p.points for p in poss if p.team == H)
        check(pa == apts and ph == hpts,
              f"game {i}: possessions do not sum to the dice score")
        total_secs = sum(p.duration for p in poss)
        check(abs(total_secs - (GAME_SECONDS + ot_secs)) < 0.05,
              f"game {i}: clock does not fill the game")
        check(all(p.duration > 0 for p in poss), f"game {i}: non-positive duration")
        if ot:
            # regulation must finish level, and the extra period must end
            # on the basket that puts a team four up
            reg = [p for p in poss if p.period == 0]
            check(sum(p.points for p in reg if p.team == A)
                  == sum(p.points for p in reg if p.team == H),
                  f"game {i}: regulation did not end tied")
            ex = [p for p in poss if p.period == 1]
            w = A if apts > hpts else H
            check(bool(ex) and ex[-1].team == w and ex[-1].points > 0,
                  f"game {i}: overtime did not end on the go-ahead score")
        clocks = [p.clock_start for p in poss]
        check(clocks == sorted(clocks, reverse=True),
              f"game {i}: clock is not monotonic")
        # check rule, and the possession chain itself
        for p in poss:
            if p.start == "oreb":
                check(not p.cleared, f"game {i}: offensive rebound required a clear")
        for k in range(len(poss) - 1):
            # The first overtime possession comes off a coin flip, so the
            # regulation-to-overtime seam is not a chain error.
            if poss[k + 1].period != poss[k].period:
                continue
            if poss[k].points > 0:
                check(poss[k + 1].team != poss[k].team,
                      f"game {i}: a made basket did not change hands")
            if poss[k + 1].team == poss[k].team:
                check(poss[k + 1].start == "oreb",
                      f"game {i}: same team twice without an offensive rebound")

        # --- events and box score ---
        ev, exits = expand(poss, A, H, ap, hp, rng,
                           foul_out=6 if ot else 5,
                           edge=lg.r_star(A) - lg.r_star(H))
        apply_exits(ap, exits); apply_exits(hp, exits)
        fouled_out += len(exits)

        for players, team, target in ((ap, A, apts), (hp, H, hpts)):
            b = box_from_events(ev, players, team)
            tot = {k: sum(x[k] for x in b.values()) for k in
                   ("PTS", "FGM", "FGA", "3PM", "3PA", "FTM", "FTA",
                    "REB", "AST", "STL", "BLK", "TOV", "PF")}
            check(tot["PTS"] == target,
                  f"game {i} {team}: box score {tot['PTS']} != dice {target}")
            check(tot["FGM"] <= tot["FGA"], f"game {i} {team}: FGM > FGA")
            check(tot["3PM"] <= tot["3PA"], f"game {i} {team}: 3PM > 3PA")
            check(tot["3PM"] <= tot["FGM"], f"game {i} {team}: 3PM > FGM")
            check(tot["FTM"] <= tot["FTA"], f"game {i} {team}: FTM > FTA")
            check(tot["AST"] <= tot["FGM"], f"game {i} {team}: more assists than makes")
            check(2 * (tot["FGM"] - tot["3PM"]) + 3 * tot["3PM"] + tot["FTM"]
                  == tot["PTS"], f"game {i} {team}: points do not reconcile")
            limit = 6 if ot else 5
            for p in players:
                check(b[p.name]["PF"] <= limit,
                      f"game {i} {team}: {p.name} over the foul limit")
                check(b[p.name]["MIN"] <= GAME_MINUTES + ot_secs / 60 + 0.2,
                      f"game {i} {team}: {p.name} over maximum minutes")
            if tot["PF"] >= 10:
                bonus += 1

            n = sum(1 for p in poss if p.team == team)
            agg["pts"].append(target); agg["poss"].append(n)
            agg["ppp"].append(target / n)
            if tot["FGA"]:
                agg["fg"].append(tot["FGM"] / tot["FGA"])
                agg["top_share"].append(
                    max(x["FGA"] for x in b.values()) / tot["FGA"])
            if tot["3PA"]:
                agg["three"].append(tot["3PM"] / tot["3PA"])
            if tot["FTA"]:
                agg["ft"].append(tot["FTM"] / tot["FTA"])
            for k, c in (("reb", "REB"), ("ast", "AST"), ("stl", "STL"),
                         ("blk", "BLK"), ("tov", "TOV"), ("pf", "PF")):
                agg[k].append(tot[c])
            agg["starter_min"].append(
                sum(p.minutes for p in players if p.role in ("C", "W", "P")))
        agg["secs"].append(total_secs / len(poss))

    return {
        "ot_rate": ot_games / GAMES,
        "foul_outs": fouled_out / GAMES,
        "bonus_rate": bonus / (GAMES * 2),
        **{k: st.mean(v) for k, v in agg.items() if v},
    }


if __name__ == "__main__":
    m = run()
    print(f"SBAAA validation — {GAMES} games, {GAMES*2} team-games\n")
    print("  points        %.1f   possessions %.0f   PPP %.2f   sec/poss %.1f"
          % (m["pts"], m["poss"], m["ppp"], m["secs"]))
    print("  FG%% %.1f   3P%% %.1f   FT%% %.1f"
          % (100*m["fg"], 100*m["three"], 100*m["ft"]))
    print("  REB %.1f  AST %.1f  STL %.1f  BLK %.1f  TOV %.1f  PF %.1f"
          % (m["reb"], m["ast"], m["stl"], m["blk"], m["tov"], m["pf"]))
    print("  starter minutes %.1f of 75 (%.0f%%)   top option %.0f%% of FGA"
          % (m["starter_min"], 100*m["starter_min"]/75, 100*m["top_share"]))
    print("  overtime %.1f%% of games   foul-outs %.2f/game   bonus %.0f%% of team-games"
          % (100*m["ot_rate"], m["foul_outs"], 100*m["bonus_rate"]))

    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} invariant violations")
        for f in FAILURES[:12]:
            print("   ", f)
        if len(FAILURES) > 12:
            print(f"    ... and {len(FAILURES)-12} more")
        sys.exit(1)
    print("All invariants hold.")
