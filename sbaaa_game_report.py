"""
SBAAA Game Report
=================

One game, both teams, rendered: score, box scores, and the play-by-play.

This used to build box scores with `build_team_box`, which generated stats
beside the game instead of reading them off it. That path predates the
event layer and knew nothing about the check rule, substitutions or the
possession chain, so it quietly produced different numbers from everything
else. It now runs the same pipeline as the rest of the engine.
"""

import random
from typing import Any, Dict, List, Optional

from sbaaa_boxscore import Player, assign_minutes, GAME_MINUTES
from sbaaa_dice_engine import TeamState, simulate_game
from sbaaa_pbp import build_possessions, GAME_SECONDS
from sbaaa_events import (expand, box_from_events, render, render_pbp,
                          BOX_COLS)


def _otd(g):
    """Overtime split from the dice, so regulation can finish level."""
    if not g["result"]["OT"]:
        return None
    d = dict(g["ot"])
    d["OT_Margin"] = g["result"]["Margin"]
    return d


def build_game(
    away_name: str, home_name: str,
    away_players: List[Player], home_players: List[Player],
    away_state: TeamState, home_state: TeamState,
    loc_points: int,
    away_rank: Optional[int] = None, home_rank: Optional[int] = None,
    dice: Optional[List[int]] = None,
    neutral: bool = False,
    series_note: str = "",
    edge: float = 0.0,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    rng = rng or random.Random()
    g = simulate_game(away_state, home_state, loc_points,
                      neutral=neutral, dice=dice)
    ot = g["result"]["OT"]
    ot_seconds = rng.uniform(110, 190) if ot else 0.0
    apts, hpts = g["result"]["Away"], g["result"]["Home"]
    fav = (away_name if g["setup"]["Favorite"] == away_state.team_id
           else home_name)

    assign_minutes(away_players); assign_minutes(home_players)
    poss = build_possessions(away_name, home_name, away_players, home_players,
                             apts, hpts,
                             [g["dice"][k] for k in ("W1", "W2", "W3")],
                             fav, rng, ot_seconds=ot_seconds,
                             ot_detail=_otd(g))
    events, exits = expand(poss, away_name, home_name, away_players,
                           home_players, rng, foul_out=6 if ot else 5,
                           edge=edge)

    return {
        "away_name": away_name, "home_name": home_name,
        "away_rank": away_rank, "home_rank": home_rank,
        "away": away_players, "home": home_players,
        "away_box": box_from_events(events, away_players, away_name),
        "home_box": box_from_events(events, home_players, home_name),
        "away_pts": apts, "home_pts": hpts,
        "engine": g, "ot": ot, "ot_seconds": ot_seconds,
        "events": events, "poss": poss, "favorite": fav,
        "series_note": series_note,
        "total_seconds": GAME_SECONDS + ot_seconds,
    }


def _label(name: str, rank: Optional[int]) -> str:
    return f"#{rank} {name}" if rank else name


def render_game_report(game: Dict[str, Any], play_by_play: bool = False) -> str:
    a, h = game["away_name"], game["home_name"]
    ap, hp = game["away_pts"], game["home_pts"]
    tag = "  (OT)" if game["ot"] else ""
    head = f"{_label(a, game['away_rank'])} {ap}, {_label(h, game['home_rank'])} {hp}{tag}"
    out = [head, "=" * len(head)]
    if game["series_note"]:
        out.append(game["series_note"])
    out.append("")
    out.append(render(_label(a, game["away_rank"]), game["away"], game["away_box"]))
    out.append("")
    out.append(render(_label(h, game["home_rank"]), game["home"], game["home_box"]))
    if play_by_play:
        out.append("")
        out.append(render_pbp(game["events"], a, h,
                              game["total_seconds"], game["poss"]))
    return "\n".join(out)


def leaders(box: Dict[str, Dict[str, float]]) -> Dict[str, str]:
    return {k: max(box, key=lambda n: box[n][c])
            for k, c in (("pts", "PTS"), ("reb", "REB"), ("ast", "AST"))}


if __name__ == "__main__":
    from sbaaa_data import League
    from sbaaa_gv import all_team_oq

    rng = random.Random(21)
    lg = League()
    oq = all_team_oq(lg.roster, lg.rstar, lg.coaches).set_index("Team").OQ.to_dict()
    A, H = "Spokane WA", "Carmel IN"
    game = build_game(
        A, H, lg.active_five(A), lg.active_five(H),
        TeamState(lg.code(A), lg.r_star(A), oq[A], form=2, fatigue=-2),
        TeamState(lg.code(H), lg.r_star(H), oq[H], form=-1),
        loc_points=5, away_rank=lg.rank(A), home_rank=lg.rank(H),
        series_note="Series game 2 of 3.",
        edge=lg.r_star(A) - lg.r_star(H), rng=rng)
    print(render_game_report(game))
    print("\nleaders:", leaders(game["away_box"]), leaders(game["home_box"]))
