"""
SBAAA 2027 Season Runner
========================

Walks the schedule a day at a time and keeps a lightweight CSV ledger.

Each day, some games may be handed in by Rick — either the six dice he
rolled, or just a final score. Everything else on that day's slate is
simulated when the day is closed.

    season = Season()
    season.set_dice(gameid, [4, 3, 5, 2, 6, 1])   # he rolled them
    season.set_score(gameid, away=52, home=48)    # he has the result
    season.run_day("2027-05-03")                  # sim the rest, write rows

Ledger files, both plain CSV:
    games.csv   one row per game
    boxes.csv   one row per player-game (optional, for season leaders)
"""

import csv
import os
import random
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from sbaaa_data import League
from sbaaa_gv import all_team_oq, gv_base
from sbaaa_dice_engine import (TeamState, simulate_game, locpoints_from_miles,
                               travel_fatigue, momentum_value, fatigue_value,
                               injury_check, form_from_history)
from sbaaa_pbp import build_possessions, GAME_SECONDS
from sbaaa_events import expand, box_from_events
from sbaaa_boxscore import Player
from sbaaa_series import lineup, SeriesState, TRAVEL_BY_GAME


def clampi(x: float, lo: int = -4, hi: int = 0) -> int:
    return int(max(lo, min(hi, x)))

from sbaaa_data import SCHEDULE as SCHED

GAME_COLS = ["Date", "Series", "GameNo", "GameID", "Away", "Home",
             "AwayPts", "HomePts", "OT", "Winner", "Margin",
             "Favorite", "Need", "Dice", "Source",
             "AwayFatigue", "HomeFatigue", "AwayForm", "HomeForm",
             "AwayMomentum", "HomeMomentum", "Rested", "Neutral"]

BOX_COLS = ["Date", "GameID", "Team", "Player", "Role", "MIN", "PTS",
            "FGM", "FGA", "3PM", "3PA", "FTM", "FTA", "OREB", "DREB",
            "REB", "AST", "STL", "BLK", "TOV", "PF"]


# ======================================================
# REVERSE DICE
# ======================================================

def solve_dice(away: TeamState, home: TeamState, loc_points: int,
               target_away: int, target_home: int,
               rng: random.Random, neutral: bool = False
               ) -> Tuple[Optional[List[int]], bool]:
    """
    Find a dice roll that produces a given final score.

    Used when Rick supplies a result rather than dice: the game still runs
    through the same engine, so the engine needs a roll consistent with it.
    All 46,656 combinations are checked.

    A score is not always reachable — the total points a game can produce
    are bounded by the two teams' offensive quality, so 61-44 (105 points)
    is impossible between two defensive sides. When nothing matches, the
    CLOSEST roll is returned with exact=False: the score Rick gave still
    stands, and the dice only supply the shape of the game.

    Returns (dice, exact).
    """
    best, best_cost = None, None
    hits = []
    want_margin = abs(target_away - target_home)
    want_total = target_away + target_home
    want_away_wins = target_away > target_home
    for w in product(range(1, 7), repeat=3):
        for r, g, b in product(range(1, 7), repeat=3):
            dice = [w[0], w[1], w[2], r, g, b]
            res = simulate_game(away, home, loc_points, neutral=neutral,
                                dice=dice)
            a, h = res["result"]["Away"], res["result"]["Home"]
            if a == target_away and h == target_home:
                hits.append(dice)
                continue
            if hits:
                continue
            cost = (abs(abs(a - h) - want_margin) * 3
                    + abs((a + h) - want_total)
                    + (0 if (a > h) == want_away_wins else 60))
            if best_cost is None or cost < best_cost:
                best, best_cost = dice, cost
    if hits:
        return rng.choice(hits), True
    return best, False


# ======================================================
# LEDGER
# ======================================================

class Ledger:
    def __init__(self, folder: str = "/home/claude/season"):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.games = self.dir / "games.csv"
        self.boxes = self.dir / "boxes.csv"
        for path, cols in ((self.games, GAME_COLS), (self.boxes, BOX_COLS)):
            if not path.exists():
                with open(path, "w", newline="") as f:
                    csv.writer(f).writerow(cols)

    def append(self, path: Path, cols: List[str], rows: List[Dict]) -> None:
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            for r in rows:
                w.writerow(r)

    def played(self) -> set:
        if not self.games.exists():
            return set()
        try:
            df = pd.read_csv(self.games, usecols=["GameID"])
        except Exception:
            return set()
        return set(df.GameID.astype(str))

    def results(self) -> pd.DataFrame:
        return pd.read_csv(self.games)


# ======================================================
# SEASON
# ======================================================

class Season:
    def __init__(self, folder: str = "/home/claude/season", seed: int = 2027):
        self.lg = League()
        self.oq = all_team_oq(self.lg.roster, self.lg.rstar,
                              self.lg.coaches).set_index("Team").OQ.to_dict()
        self.sched = pd.read_csv(SCHED, parse_dates=["Date"])
        # one row per physical game: each appears twice, once per team
        self.sched["Key"] = self.sched.apply(
            lambda r: "|".join(sorted([r.City1, r.City2]))
            + f"|{r.Series}|{r.GameNumberInSeries}", axis=1)
        self.games = self.sched.drop_duplicates("Key").copy()
        self.ledger = Ledger(folder)
        self.rng = random.Random(seed)
        self.manual_dice: Dict[str, List[int]] = {}
        self.manual_score: Dict[str, Tuple[int, int]] = {}
        # season state carried between series
        self.form_history: Dict[str, List[str]] = {}
        self.out_until: Dict[str, int] = {}     # player -> series he returns
        self.last_series: Dict[str, Dict] = {}  # team -> last game's result
        self.series_wins: Dict[tuple, Dict[str, int]] = {}
        self.recent_ot: Dict[str, int] = {}     # overtimes just played

    # ---- manual input ----
    def set_dice(self, game_id: Any, dice: List[int]) -> None:
        """Rick rolled these six: W1 W2 W3 Red Green Blue."""
        self.manual_dice[str(game_id)] = list(dice)

    def set_score(self, game_id: Any, away: int, home: int) -> None:
        """Rick has the final; the engine will find dice that produce it."""
        self.manual_score[str(game_id)] = (int(away), int(home))

    # ---- context ----
    def _travel(self, row) -> Tuple[int, int]:
        """Fatigue for both sides. The home team can be back off a trip too."""
        g1 = self.games[(self.games.Series == row.Series)
                        & (self.games.Key == row.Key)]
        am = float(row["Distance Traveled A"])
        hm = float(row["Distance Traveled H"])
        return travel_fatigue(am), travel_fatigue(hm)

    def _form(self, team: str) -> int:
        return form_from_history(self.form_history.get(team, []))

    def _momentum(self, team: str, series: int, game_no: int) -> int:
        last = self.last_series.get(team)
        if not last or game_no == 1 or last["series"] != series:
            return 0
        m = momentum_value(last["margin"], last["won"])
        if m == 0 and last["ot"]:
            m = 1 if last["won"] else -1
        return m

    # ---- one game ----
    def play(self, row, day_rng: random.Random) -> Dict[str, Any]:
        A, H = ((row.City2, row.City1) if row.Location == "H"
                else (row.City1, row.City2))
        S, gno = int(row.Series), int(row.GameNumberInSeries)
        gid = str(row.GameID)
        am = float(row["Distance Traveled A"])
        lp = locpoints_from_miles(am)
        a_fat_base, h_fat_base = self._travel(row)
        decay = TRAVEL_BY_GAME[gno - 1]
        a_fat = int(round(a_fat_base * decay))
        h_fat = int(round(h_fat_base * decay))

        # The five who will actually play are chosen FIRST, because game
        # value has to reflect them: a team missing its centre, or resting
        # starters in a decided game 3, is not the team its published R*
        # describes. Injuries therefore hit at full strength, as specified.
        state = SeriesState(A, H)
        state.out_until = {n: 99 for n, until in self.out_until.items()
                           if S < until}
        state.wins = dict(self.series_wins.get((S, A, H), {A: 0, H: 0}))
        ap = lineup(self.lg, A, gno, state, day_rng)
        hp = lineup(self.lg, H, gno, state, day_rng)
        rested = state.decided() is not None and gno == 3

        # fatigue: travel, plus any overtime this team has just played,
        # less a point back if it rested starters in a decided game 3
        a_fat = int(clampi(a_fat - self.recent_ot.get(A, 0)
                           + (1 if rested else 0)))
        h_fat = int(clampi(h_fat - self.recent_ot.get(H, 0)
                           + (1 if rested else 0)))

        a_state = TeamState(self.lg.code(A),
                            gv_base(S, self.lg.active_r_star(A, ap), None),
                            self.oq[A], form=self._form(A), fatigue=a_fat,
                            momentum=self._momentum(A, S, gno))
        h_state = TeamState(self.lg.code(H),
                            gv_base(S, self.lg.active_r_star(H, hp), None),
                            self.oq[H], form=self._form(H), fatigue=h_fat,
                            momentum=self._momentum(H, S, gno))

        source = "sim"
        dice = None
        if gid in self.manual_dice:
            dice = self.manual_dice.pop(gid); source = "dice"
        forced = None
        if gid in self.manual_score:
            ta, th = self.manual_score.pop(gid)
            dice, exact = solve_dice(a_state, h_state, lp, ta, th, day_rng)
            source = "score" if exact else "score-forced"
            if not exact:
                # Rick's score stands even when no roll produces it; the
                # closest roll supplies the shape of the game. Dropping the
                # game instead made it disappear from the ledger entirely.
                forced = (ta, th)

        g = simulate_game(a_state, h_state, lp, dice=dice, rng=day_rng)
        apts, hpts = g["result"]["Away"], g["result"]["Home"]
        if forced:
            apts, hpts = forced
        ot = g["result"]["OT"] and not forced
        ots = day_rng.uniform(110, 190) if ot else 0.0
        fav = A if g["setup"]["Favorite"] == self.lg.code(A) else H
        otd = None
        if ot and not forced:
            otd = dict(g["ot"]); otd["OT_Margin"] = g["result"]["Margin"]

        poss = build_possessions(A, H, ap, hp, apts, hpts,
                                 [g["dice"][k] for k in ("W1", "W2", "W3")],
                                 fav, day_rng, ot_seconds=ots, ot_detail=otd)
        inj = injury_check([g["dice"][k] for k in ("W1", "W2", "W3")],
                           g["dice"]["Red"], g["dice"]["Green"],
                           g["dice"]["Blue"])
        events, exits = expand(poss, A, H, ap, hp, day_rng,
                               foul_out=6 if ot else 5, injury=inj,
                               edge=self.lg.r_star(A) - self.lg.r_star(H))

        if inj and not inj.get("Scare"):
            team = A if inj["Team"] == "AWAY" else H
            squad = ap if team == A else hp
            hurt = next((p for p in squad if p.role == inj["Slot"]), None)
            if hurt:
                d = inj["Duration"]
                self.out_until[hurt.name] = 999 if d == "SEASON" else S + d

        win = A if apts > hpts else H
        key = (S, A, H)
        w = self.series_wins.setdefault(key, {A: 0, H: 0})
        w[win] += 1
        for t in (A, H):
            self.recent_ot[t] = (self.recent_ot.get(t, 0) + 1) if ot else 0
        for t, won in ((A, win == A), (H, win == H)):
            self.last_series[t] = {"series": S, "won": won,
                                   "margin": abs(apts - hpts), "ot": ot}

        return {
            "row": row, "A": A, "H": H, "ap": ap, "hp": hp, "g": g,
            "apts": apts, "hpts": hpts, "ot": ot, "fav": fav, "poss": poss,
            "events": events, "source": source, "rested": rested,
            "a_fat": a_fat, "h_fat": h_fat,
            "a_form": a_state.form, "h_form": h_state.form,
            "a_mom": a_state.momentum, "h_mom": h_state.momentum,
            "box": {A: box_from_events(events, ap, A),
                    H: box_from_events(events, hp, H)},
        }

    # ---- a day ----
    def run_day(self, date: str, write_boxes: bool = True) -> Dict[str, Any]:
        day = self.games[self.games.Date == pd.Timestamp(date)]
        done = self.ledger.played()
        day_rng = random.Random(f"{date}|{len(done)}")
        grows, brows, out = [], [], []

        for _, row in day.sort_values(["Series", "GameNumberInSeries"]).iterrows():
            if str(row.GameID) in done:
                continue
            r = self.play(row, day_rng)
            A, H = r["A"], r["H"]
            grows.append({
                "Date": date, "Series": int(row.Series),
                "GameNo": int(row.GameNumberInSeries), "GameID": row.GameID,
                "Away": A, "Home": H, "AwayPts": r["apts"],
                "HomePts": r["hpts"], "OT": int(r["ot"]),
                "Winner": A if r["apts"] > r["hpts"] else H,
                "Margin": abs(r["apts"] - r["hpts"]),
                "Favorite": r["fav"], "Need": r["g"]["setup"]["Need"],
                "Dice": "".join(str(r["g"]["dice"][k]) for k in
                                ("W1", "W2", "W3", "Red", "Green", "Blue")),
                "Source": r["source"],
                "AwayFatigue": r["a_fat"], "HomeFatigue": r["h_fat"],
                "AwayForm": r["a_form"], "HomeForm": r["h_form"],
                "AwayMomentum": r["a_mom"], "HomeMomentum": r["h_mom"],
                # NeutralSite is the string "No"/"Yes", so bool() on it is
                # always True — every game came out flagged neutral.
                "Rested": int(r.get("rested", False)),
                "Neutral": int(str(row.get("NeutralSite", "No")).strip().lower()
                               in ("yes", "y", "true", "1")),
            })
            if write_boxes:
                for team, players in ((A, r["ap"]), (H, r["hp"])):
                    for p in players:
                        b = r["box"][team][p.name]
                        brows.append({"Date": date, "GameID": row.GameID,
                                      "Team": team, "Player": p.name,
                                      "Role": p.role,
                                      **{k: b[k] for k in BOX_COLS[5:]}})
            # form updates on the third game of a series
            if int(row.GameNumberInSeries) == 3:
                self._close_series(int(row.Series), A, H)
            out.append(r)

        self.ledger.append(self.ledger.games, GAME_COLS, grows)
        if write_boxes:
            self.ledger.append(self.ledger.boxes, BOX_COLS, brows)
        return {"date": date, "played": len(grows), "games": out}

    def _close_series(self, series: int, A: str, H: str) -> None:
        """Record the series result in each team's rolling form window."""
        df = self.ledger.results() if self.ledger.games.exists() else None
        wins = {A: 0, H: 0}
        if df is not None and len(df):
            sub = df[(df.Series == series) & (df.Away == A) & (df.Home == H)]
            for _, r in sub.iterrows():
                wins[r.Winner] = wins.get(r.Winner, 0) + 1
        for t, o in ((A, H), (H, A)):
            res = "win" if wins[t] > wins[o] else "loss"
            self.form_history.setdefault(t, []).append(res)


if __name__ == "__main__":
    s = Season(folder="/home/claude/season_demo")
    d = s.sched.Date.min().strftime("%Y-%m-%d")
    r = s.run_day(d)
    print(f"{d}: played {r['played']} games")
    print(pd.read_csv(s.ledger.games).head(6).to_string(index=False))
