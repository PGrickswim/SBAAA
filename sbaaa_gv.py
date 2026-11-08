"""
SBAAA 2027 — Offensive Quality and the GV blend curve
=====================================================
"""

from typing import Dict, List, Optional
import hashlib
import pandas as pd

from sbaaa_boxscore import arch

# ======================================================
# GV BY SERIES  (Rick's table, verbatim)
# ======================================================
# R Weight = share from 2027 R*; C Weight = share from calculated CRIA.

R_WEIGHT: Dict[int, float] = {
    1: 1.000,  2: 1.000,  3: 0.990,  4: 0.970,  5: 0.940,  6: 0.900,
    7: 0.850,  8: 0.810,  9: 0.780, 10: 0.760, 11: 0.750, 12: 0.742,
    13: 0.734, 14: 0.726, 15: 0.718, 16: 0.710, 17: 0.700, 18: 0.670,
    19: 0.620, 20: 0.550, 21: 0.500, 22: 0.470, 23: 0.460, 24: 0.452,
    25: 0.444, 26: 0.436, 27: 0.428, 28: 0.420, 29: 0.412, 30: 0.404,
    31: 0.394, 32: 0.364, 33: 0.314, 34: 0.284, 35: 0.274, 36: 0.266,
    37: 0.258, 38: 0.250, 39: 0.242, 40: 0.234, 41: 0.226, 42: 0.218,
    43: 0.210, 44: 0.202, 45: 0.200, 46: 0.200, 47: 0.200, 48: 0.200,
    49: 0.200, 50: 0.200, 51: 0.200, 52: 0.200,
}


def gv_base(series: int, r_star: float, cria: Optional[float]) -> float:
    """
    Blend R* and CRIA by series number. Before CRIA exists (or series 1-2),
    this is pure R*.
    """
    rw = R_WEIGHT.get(int(series), 0.200)
    if cria is None or rw >= 1.0:
        return float(r_star)
    return rw * float(r_star) + (1.0 - rw) * float(cria)


# ======================================================
# OFFENSIVE QUALITY
# ======================================================
# Built from the roster, mostly the starters.
#
#   Weight by slot:  starters (C/W/P) 70%, Bench1-2 22%, Bench3-7 8%.
#   Within a slot group, a player's share leans on his rating, since the
#   better player shapes more of how the team plays.
#
# A player's offensive lean comes from his archetype quotients:
#     offense  = Scoring + half of Assists
#     defense  = Blocks  + half of Steals
# The difference, normalized, is how far he pushes the team's scoring
# environment up or down. Rebounding is left out: it feeds both ends.

SLOT_WEIGHT = {"C": 0.70 / 3, "W": 0.70 / 3, "P": 0.70 / 3,
               "Bench1": 0.11, "Bench2": 0.11}
RESERVE_TOTAL = 0.08          # Bench3-7 share this

OQ_CENTER = 0.55              # league mean; tuned so median team scores ~49
OQ_SPREAD = 0.34               # scales the lean into the band
OQ_FLOOR, OQ_CEIL = 0.20, 0.80
IDENTITY_LEAN = 0.022         # program and coach, each at most this far


def _player_lean(archetype: str) -> float:
    """Offensive minus defensive tilt, roughly -1 to +1."""
    w = arch(archetype)
    off = w["use"] + 0.5 * w["ast"]
    deff = w["blk"] + 0.5 * w["stl"]
    return (off - deff) / 3.0


def _stable_offset(key: str, span: float) -> float:
    """
    A small, permanent lean per program or coach. Derived from the ID so it
    never drifts between runs and never needs storing.
    """
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return ((h % 2001) / 1000.0 - 1.0) * span


def team_oq(
    roster: pd.DataFrame,
    team: str,
    coach_id: Optional[str] = None,
    team_code: Optional[str] = None,
) -> float:
    """Offensive quality for one team, 0.20-0.80."""
    df = roster.loc[roster.Team == team]
    if df.empty:
        raise ValueError(f"no roster for {team!r}")

    reserves = df[~df.CurrentRole.isin(SLOT_WEIGHT)]
    per_reserve = RESERVE_TOTAL / max(1, len(reserves))

    num = den = 0.0
    for _, r in df.iterrows():
        w = SLOT_WEIGHT.get(r.CurrentRole, per_reserve)
        # better players shape the identity more, within their slot group
        w *= 0.70 + 0.12 * float(r.Rating)
        num += w * _player_lean(r.Archetype)
        den += w

    lean = num / den if den else 0.0
    oq = OQ_CENTER + OQ_SPREAD * lean
    if team_code:
        oq += _stable_offset("prog:" + team_code, IDENTITY_LEAN)
    if coach_id:
        oq += _stable_offset("coach:" + str(coach_id), IDENTITY_LEAN)
    return round(min(OQ_CEIL, max(OQ_FLOOR, oq)), 3)


def all_team_oq(roster: pd.DataFrame, rstar: pd.DataFrame,
                coaches: pd.DataFrame) -> pd.DataFrame:
    code = dict(zip(rstar.Team, rstar.Team_Code))
    coach = dict(zip(coaches.Team, coaches.CoachID))
    rows = []
    for t in sorted(roster.Team.unique()):
        rows.append({
            "Team": t,
            "Team_Code": code.get(t, ""),
            "OQ": team_oq(roster, t, coach.get(t), code.get(t)),
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    from sbaaa_data import League
    lg = League()
    oq = all_team_oq(lg.roster, lg.rstar, lg.coaches)
    s = oq.OQ
    print("OQ across 240 teams")
    print("  min %.3f  p25 %.3f  median %.3f  p75 %.3f  max %.3f  sd %.3f"
          % (s.min(), s.quantile(.25), s.median(), s.quantile(.75), s.max(), s.std()))
    per_team = (60 + (s - 0.30) * 160) / 2
    print("  implied points per team: %.0f to %.0f, median %.0f"
          % (per_team.min(), per_team.max(), per_team.median()))
    print("\nmost offensive:")
    print(oq.nlargest(5, "OQ").to_string(index=False))
    print("\nmost defensive:")
    print(oq.nsmallest(5, "OQ").to_string(index=False))
