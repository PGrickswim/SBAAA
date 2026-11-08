"""
SBAAA 2027 data loader
======================
Reads the FINAL_REALIGNED drop and hands back rosters, R*, identity and coaches.
"""

from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd

from sbaaa_boxscore import Player

# Everything the engine needs sits beside it, so the bundle runs wherever
# it is unpacked — no absolute paths into anyone's machine.
DATA = Path(__file__).resolve().parent / "data"

ROSTER   = DATA / "roster_2027.csv"
RSTAR    = DATA / "rstar_2027.csv"
COACHES  = DATA / "coaches_2027.csv"
IDENTITY = DATA / "teams_2027.csv"
SCHEDULE = DATA / "schedule_2027.csv"

# The roster calls them Bench1/Bench2; the box score builder wants B1/B2.
ROLE_MAP = {"C": "C", "W": "W", "P": "P", "Bench1": "B1", "Bench2": "B2"}
ACTIVE_FIVE = ["C", "W", "P", "Bench1", "Bench2"]


class League:
    def __init__(self) -> None:
        self.roster = pd.read_csv(ROSTER)
        self.rstar = pd.read_csv(RSTAR)
        self.coaches = pd.read_csv(COACHES)
        self.identity = pd.read_csv(IDENTITY)
        self.identity = self.identity.rename(columns={
            "Region (2027 final)": "Region", "Franchise Code": "Code"})

    # ---- lookups ----
    def code(self, team: str) -> str:
        row = self.rstar.loc[self.rstar.Team == team]
        return row.Team_Code.iloc[0] if len(row) else ""

    def r_star(self, team: str) -> float:
        return float(self.rstar.loc[self.rstar.Team == team,
                                    "R_Star27_fromRoster"].iloc[0])

    def rank(self, team: str) -> int:
        return int(self.rstar.loc[self.rstar.Team == team, "RRnk"].iloc[0])

    def nickname(self, team: str) -> str:
        row = self.identity.loc[self.identity.City == team]
        return row.Nickname.iloc[0] if len(row) else ""

    def coach(self, team: str) -> Dict:
        row = self.coaches.loc[self.coaches.Team == team]
        if not len(row):
            return {}
        r = row.iloc[0]
        return {"id": r.CoachID, "tenure": int(r.Tenure_2027),
                "rating": float(r.Coach_Rating)}

    # ---- rosters ----
    def active_five(self, team: str, rest: Optional[List[str]] = None) -> List[Player]:
        """
        The five who play. `rest` names starters sitting out (decided game 3),
        and the next bench man steps in by positional fit, then rating.
        """
        df = self.roster.loc[self.roster.Team == team].copy()
        if df.empty:
            raise ValueError(f"no roster for {team!r}")
        rest = rest or []
        slots = [s for s in ACTIVE_FIVE if s not in rest]
        chosen = df[df.CurrentRole.isin(slots)]

        if rest:
            pool = (df[~df.CurrentRole.isin(ACTIVE_FIVE)]
                    .sort_values("Rating", ascending=False))
            fill = pool.head(len(rest))
            chosen = pd.concat([chosen, fill])

        out = []
        for _, r in chosen.iterrows():
            out.append(Player(
                name=r.Name,
                role=ROLE_MAP.get(r.CurrentRole, "B2"),
                rating=float(r.Rating),
                archetype=r.Archetype,
            ))
        return out

    # Weight of each slot in a team's roster strength. Calibrated so the
    # normal active five reproduces the team's published R* exactly; any
    # other five scales from there.
    SLOT_W = {"C": 1.0, "W": 1.0, "P": 1.0, "B1": 0.55, "B2": 0.45}

    def _strength(self, players: List[Player]) -> float:
        return sum(self.SLOT_W.get(p.role, 0.45) * (p.rating + 0.9)
                   for p in players)

    def active_r_star(self, team: str, players: List[Player]) -> float:
        """
        R* for the five who are actually playing.

        The published R* is a full-strength figure. A team missing its
        centre to a foul-out, or resting its starters in a decided game 3,
        is not that team — so the rating is scaled by how the lineup on the
        floor compares with the normal one. At full strength this returns
        the published number unchanged.
        """
        base = self.r_star(team)
        normal = self._strength(self.active_five(team))
        now = self._strength(players)
        if normal <= 0:
            return base
        return round(base * (now / normal), 3)

    def bench_depth(self, team: str) -> List[Player]:
        """Players 6-10, for foul-outs and injuries."""
        df = self.roster.loc[(self.roster.Team == team)
                             & (~self.roster.CurrentRole.isin(ACTIVE_FIVE))]
        df = df.sort_values("Rating", ascending=False)
        return [Player(r.Name, "B2", float(r.Rating), r.Archetype)
                for _, r in df.iterrows()]


if __name__ == "__main__":
    lg = League()
    for t in ["South Bend IN", "Naperville IL"]:
        print(f"{t} ({lg.code(t)}) — R* {lg.r_star(t):.1f}, #{lg.rank(t)}, "
              f"the {lg.nickname(t)}")
        for p in lg.active_five(t):
            print(f"   {p.role:<3} {p.name:<20} {p.rating:>4.1f}  {p.archetype}")
        c = lg.coach(t)
        print(f"   coach {c['id']}, season {c['tenure']}, rated {c['rating']}\n")
