"""
SBAAA Series
============

A series is three games, not three independent games. Injuries, momentum
and fatigue carry across them, travel wears off, and a side that has already
won or lost the series rests its starters in game 3.
"""

import random
from typing import Any, Dict, List, Optional

from sbaaa_data import League, ACTIVE_FIVE, ROLE_MAP
from sbaaa_boxscore import Player
from sbaaa_dice_engine import (TeamState, simulate_game, locpoints_from_miles,
                               momentum_value, injury_check)
def _otd(g):
    """Overtime split from the dice, so regulation can finish level."""
    if not g["result"]["OT"]:
        return None
    d = dict(g["ot"]); d["OT_Margin"] = g["result"]["Margin"]
    return d

from sbaaa_pbp import build_possessions, GAME_SECONDS
from sbaaa_events import expand, box_from_events

# Travel is felt on arrival and wears off. Game 1 takes the full hit, game 2
# a third of it, game 3 none.
TRAVEL_BY_GAME = (1.00, 0.35, 0.00)


class SeriesState:
    """Carries between games: injuries, momentum, fatigue, the series score."""

    def __init__(self, away: str, home: str, base_fatigue: int = 0):
        self.away, self.home = away, home
        self.wins = {away: 0, home: 0}
        self.margins: List[Dict[str, Any]] = []
        self.out_until: Dict[str, int] = {}     # player -> game he returns
        self.injuries: List[Dict[str, Any]] = []
        self.base_fatigue = base_fatigue

    def decided(self) -> Optional[str]:
        for t, w in self.wins.items():
            if w >= 2:
                return t
        return None

    def momentum(self, team: str, game_no: int) -> int:
        """
        Carries from the previous game in the series: a double-digit win, or
        surviving an overtime. Winning one in overtime lifts a team and
        losing one deflates it, whatever the final margin was.
        """
        if not self.margins:
            return 0
        last = self.margins[-1]
        won = last["winner"] == team
        m = momentum_value(abs(last["margin"]), won)
        if m == 0 and last.get("ot"):
            m = 1 if won else -1
        return m

    def travel_fatigue(self, game_no: int) -> int:
        return int(round(self.base_fatigue * TRAVEL_BY_GAME[game_no - 1]))

    def unavailable(self, game_no: int) -> set:
        return {n for n, until in self.out_until.items() if game_no < until}


def lineup(lg: League, team: str, game_no: int, state: SeriesState,
           rng: random.Random) -> List[Player]:
    """
    The five who dress. Anyone hurt earlier in the series is out. If the
    series is already decided, the starters rest and three reserves come up.
    """
    df = lg.roster.loc[lg.roster.Team == team].copy()
    hurt = state.unavailable(game_no)
    df = df[~df.Name.isin(hurt)]

    resting = state.decided() is not None and game_no == 3
    if resting:
        # The three starters sit. B1 and B2 usually move into the lineup and
        # usually start, but a coach may hand a reserve the night instead,
        # so it is not fully predictable.
        bench = [r for _, r in
                 df[df.CurrentRole.isin(["Bench1", "Bench2"])].iterrows()]
        reserves = [r for _, r in
                    df[~df.CurrentRole.isin(ACTIVE_FIVE)]
                    .sort_values("Rating", ascending=False).iterrows()]
        rng.shuffle(reserves)
        promoted = [r for r in bench if rng.random() < 0.85]
        chosen = (promoted + reserves[: max(0, 5 - len(promoted))])[:5]
        # Dressing in the five is not the same as starting: the first three
        # of `chosen` take C/W/P and the last two sit as B1/B2. Usually the
        # promoted men start, but about a quarter of the time a coach opens
        # with a reserve and brings a promoted man off the bench instead.
        if len(chosen) == 5 and rng.random() >= 0.75:
            rng.shuffle(chosen)
    else:
        slots = df[df.CurrentRole.isin(ACTIVE_FIVE)]
        chosen = [r for _, r in slots.iterrows()]
        if len(chosen) < 5:
            fill = (df[~df.CurrentRole.isin(ACTIVE_FIVE)]
                    .sort_values("Rating", ascending=False))
            chosen += [r for _, r in fill.head(5 - len(chosen)).iterrows()]

    roles = ["C", "W", "P", "B1", "B2"]
    out = []
    for i, r in enumerate(chosen[:5]):
        # In a rested game 3 the promoted men actually start, so roles are
        # assigned by order of selection rather than kept from the roster.
        role = roles[i] if resting else ROLE_MAP.get(r.CurrentRole, roles[i])
        out.append(Player(name=r.Name, role=role,
                          rating=float(r.Rating), archetype=r.Archetype))
    # make sure the three roles exist
    seen = set()
    for i, p in enumerate(out):
        if p.role in seen or p.role not in roles:
            p.role = next(x for x in roles if x not in seen)
        seen.add(p.role)
    return out


def run_series(lg: League, oq: Dict[str, float], away: str, home: str,
               series_no: int, loc_points: int, base_fatigue: int,
               r_star: Dict[str, float], seed: int = 0,
               home_fatigue: int = 0) -> Dict[str, Any]:
    """Simulate all three games. All three are always played."""
    rng = random.Random(seed)
    state = SeriesState(away, home, base_fatigue)
    games = []

    for gno in (1, 2, 3):
        ap = lineup(lg, away, gno, state, rng)
        hp = lineup(lg, home, gno, state, rng)

        # Momentum must be read BEFORE this game's result is recorded,
        # or every game inherits its own margin.
        mom = {away: state.momentum(away, gno),
               home: state.momentum(home, gno)}
        fat = state.travel_fatigue(gno)
        rested = state.decided() is not None and gno == 3

        # Both sides can arrive tired: the home team may be coming back off
        # its own road swing. Each decays over the three games the same way.
        hfat = int(round(home_fatigue * TRAVEL_BY_GAME[gno - 1]))
        # Game value follows the five who actually play, so a rested or
        # depleted lineup is rated as the lineup it is.
        a = TeamState(lg.code(away), lg.active_r_star(away, ap), oq[away],
                      fatigue=fat, momentum=mom[away])
        h = TeamState(lg.code(home), lg.active_r_star(home, hp), oq[home],
                      fatigue=hfat, momentum=mom[home])

        g = simulate_game(a, h, loc_points, rng=rng)
        apts, hpts = g["result"]["Away"], g["result"]["Home"]
        ot = g["result"]["OT"]
        ots = rng.uniform(110, 190) if ot else 0.0
        fav = away if g["setup"]["Favorite"] == lg.code(away) else home

        inj = injury_check([g["dice"]["W1"], g["dice"]["W2"], g["dice"]["W3"]],
                           g["dice"]["Red"], g["dice"]["Green"],
                           g["dice"]["Blue"])

        poss = build_possessions(away, home, ap, hp, apts, hpts,
                                 [g["dice"][k] for k in ("W1", "W2", "W3")],
                                 fav, rng, ot_seconds=ots,
                                 ot_detail=_otd(g))
        ev, exits = expand(poss, away, home, ap, hp, rng,
                           foul_out=6 if ot else 5, injury=inj)

        winner = away if apts > hpts else home
        state.wins[winner] += 1
        state.margins.append({"winner": winner, "margin": abs(apts - hpts),
                              "ot": ot})

        # An injury takes the player out for the rest of this series and
        # beyond; the duration in series is recorded for the season ledger.
        if inj and not inj.get("Scare"):
            team = away if inj["Team"] == "AWAY" else home
            squad = ap if team == away else hp
            hurt = next((p for p in squad if p.role == inj["Slot"]), None)
            if hurt:
                state.out_until[hurt.name] = 99
                state.injuries.append({"team": team, "player": hurt.name,
                                       "duration": inj["Duration"],
                                       "game": gno})

        games.append({"game": gno, "away_pts": apts, "home_pts": hpts,
                      "ot": ot, "winner": winner, "events": ev,
                      "away": ap, "home": hp,
                      "away_box": box_from_events(ev, ap, away),
                      "home_box": box_from_events(ev, hp, home),
                      "rested": rested, "momentum": mom,
                      "fatigue": {away: fat, home: hfat},
                      # kept so a report can redraw the game
                      "poss": poss, "need": g["setup"]["Need"],
                      "fav": fav, "ot_seconds": ots})

    champ = max(state.wins, key=state.wins.get)
    return {"away": away, "home": home, "series": series_no,
            "games": games, "wins": state.wins, "winner": champ,
            "score": f"{max(state.wins.values())}-{min(state.wins.values())}",
            "injuries": state.injuries}


if __name__ == "__main__":
    from sbaaa_gv import all_team_oq, gv_base
    lg = League()
    oq = all_team_oq(lg.roster, lg.rstar, lg.coaches).set_index("Team").OQ.to_dict()
    A, H = "Spokane WA", "Carmel IN"
    rs = {A: lg.r_star(A), H: lg.r_star(H)}
    s = run_series(lg, oq, A, H, 17, locpoints_from_miles(1750),
                   base_fatigue=-3, r_star=rs, seed=9)
    print(f"{A} at {H} — series {s['score']} to {s['winner']}\n")
    for g in s["games"]:
        tag = " OT" if g["ot"] else ""
        rest = "   (starters rested)" if g["rested"] else ""
        print(f"  G{g['game']}: {A} {g['away_pts']}, {H} {g['home_pts']}{tag}"
              f"   fatigue {g['fatigue'][A]}/{g['fatigue'][H]}  momentum "
              f"{g['momentum'][A]:+d}/{g['momentum'][H]:+d}{rest}")
    if s["injuries"]:
        for i in s["injuries"]:
            print(f"\n  injury: {i['player']} ({i['team']}), "
                  f"game {i['game']}, out {i['duration']} series")
    print("\n  game 3 lineup for", H)
    for p in s["games"][2]["home"]:
        print(f"    {p.role:<3} {p.name:<20} {p.rating:.1f}")
