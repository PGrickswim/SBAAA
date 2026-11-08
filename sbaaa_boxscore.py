"""
SBAAA Box Score Builder
=======================

Takes a final score from the dice engine and builds a full box score under it.

Design:
  * The final score is FIXED. Everything here distributes it.
  * Each archetype carries a weight vector across the stat categories.
    Weights are relative, 1.00 = league average.
  * Shares are drawn from a Dirichlet, with archetype weights setting the
    MEAN and a concentration constant setting how much games vary. Same
    player, same archetype, different night, different line.

Knob: CONCENTRATION. Higher = players hit their archetype every night.
Lower = wilder games. See VARIABILITY below.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import random

# ======================================================
# VARIABILITY
# ======================================================

CONCENTRATION = 30.0   # night-to-night swing; lower = wilder games
PPP_TARGET = 0.95      # points per possession
GAME_MINUTES = 25


# ======================================================
# ARCHETYPES
# ======================================================
# From Archetype_Meaning.xlsx (24 archetypes). The file gives five quotients
# per archetype — Scoring, Rebounding, Assists, Blocks, Steals — each on a
# scale whose league mean is exactly 5, so weight = quotient / 5 and 1.00 is
# average. Those five are authoritative.
#
# The file does NOT cover three-point lean (thr), free throws drawn (ftd),
# turnovers (tov) or shooting efficiency (eff). Those four are estimated from
# the archetype name and its interior signal, and are the numbers to argue
# with. The five from the file are not.
#   use  = shot attempts        (Scoring_Quotient)
#   orb/drb = rebounding        (Rebounding_Quotient)
#   ast  = assists              (Assists_Quotient)
#   blk  = blocks               (Blocks_Quotient)
#   stl  = steals               (Steals_Quotient)

ARCHETYPES: Dict[str, Dict[str, float]] = {
    "Glass Cleaner":
        dict(use=1.2, thr=0.3, ftd=1.2, ast=0.6, orb=2.0, drb=2.0, stl=0.8, blk=1.4, tov=0.95, eff=1.02),
    "Rim Protector":
        dict(use=0.6, thr=0.2, ftd=1.0, ast=0.6, orb=1.8, drb=1.8, stl=1.0, blk=2.0, tov=0.9, eff=1.0),
    "Swiss Army Forward":
        dict(use=1.2, thr=0.9, ftd=1.0, ast=1.2, orb=1.2, drb=1.2, stl=1.2, blk=1.2, tov=1.0, eff=1.0),
    "Energy Spark":
        dict(use=1.2, thr=0.8, ftd=1.1, ast=1.0, orb=1.4, drb=1.4, stl=1.4, blk=0.8, tov=1.1, eff=1.0),
    "Post Scorer":
        dict(use=1.8, thr=0.3, ftd=1.45, ast=0.8, orb=1.4, drb=1.4, stl=0.8, blk=1.0, tov=1.05, eff=1.08),
    "High-Assist Risk Taker":
        dict(use=0.8, thr=1.0, ftd=0.9, ast=2.0, orb=0.6, drb=0.6, stl=1.4, blk=0.8, tov=1.6, eff=0.92),
    "Paint Enforcer":
        dict(use=0.4, thr=0.15, ftd=1.2, ast=0.4, orb=1.8, drb=1.8, stl=1.0, blk=1.8, tov=0.9, eff=0.98),
    "Pick-and-Roll Maestro":
        dict(use=0.8, thr=0.85, ftd=0.95, ast=1.6, orb=1.0, drb=1.0, stl=1.2, blk=0.8, tov=1.2, eff=0.98),
    "Stretch Big":
        dict(use=1.2, thr=1.3, ftd=0.95, ast=1.0, orb=1.4, drb=1.4, stl=0.8, blk=1.0, tov=0.9, eff=1.02),
    "Defensive Stopper":
        dict(use=0.6, thr=0.85, ftd=0.9, ast=0.6, orb=1.2, drb=1.2, stl=1.4, blk=1.4, tov=0.9, eff=0.96),
    "Lob Threat":
        dict(use=0.6, thr=0.15, ftd=1.3, ast=0.6, orb=1.6, drb=1.6, stl=0.8, blk=1.6, tov=1.0, eff=1.15),
    "Slasher":
        dict(use=1.0, thr=0.65, ftd=1.4, ast=1.0, orb=1.0, drb=1.0, stl=1.4, blk=0.8, tov=1.15, eff=1.02),
    "Combo Guard":
        dict(use=1.0, thr=1.15, ftd=1.0, ast=1.4, orb=0.8, drb=0.8, stl=1.0, blk=0.8, tov=1.1, eff=1.0),
    "Floor General":
        dict(use=0.8, thr=1.0, ftd=0.85, ast=2.0, orb=0.6, drb=0.6, stl=0.8, blk=0.8, tov=1.25, eff=0.96),
    "Shot Creator":
        dict(use=0.8, thr=1.0, ftd=1.1, ast=2.0, orb=0.6, drb=0.6, stl=0.8, blk=0.8, tov=1.3, eff=0.95),
    "Sixth-Man Gunner":
        dict(use=1.2, thr=1.5, ftd=0.95, ast=1.0, orb=1.0, drb=1.0, stl=1.0, blk=0.8, tov=0.95, eff=1.0),
    "Two-Way Wing":
        dict(use=1.2, thr=1.15, ftd=1.0, ast=0.6, orb=0.8, drb=0.8, stl=1.6, blk=0.8, tov=0.95, eff=1.0),
    "Drive-and-Kick Guard":
        dict(use=0.8, thr=1.0, ftd=0.95, ast=1.8, orb=0.6, drb=0.6, stl=0.8, blk=0.8, tov=1.2, eff=0.96),
    "Movement Shooter":
        dict(use=1.6, thr=1.8, ftd=0.8, ast=1.0, orb=0.4, drb=0.4, stl=0.8, blk=0.8, tov=0.85, eff=1.1),
    "Pure Shooter":
        dict(use=1.6, thr=1.9, ftd=0.75, ast=0.6, orb=0.6, drb=0.6, stl=0.8, blk=0.8, tov=0.8, eff=1.12),
    "Tempo Pusher":
        dict(use=0.8, thr=1.1, ftd=1.0, ast=0.8, orb=0.6, drb=0.6, stl=1.2, blk=1.0, tov=1.3, eff=0.95),
    "Clutch Scorer":
        dict(use=1.2, thr=1.2, ftd=1.25, ast=0.6, orb=0.6, drb=0.6, stl=0.8, blk=0.8, tov=0.95, eff=1.05),
    "Isolation Specialist":
        dict(use=1.2, thr=1.0, ftd=1.2, ast=0.4, orb=0.6, drb=0.6, stl=0.8, blk=0.8, tov=1.05, eff=0.92),
    "Low-Usage Connector":
        dict(use=0.4, thr=0.7, ftd=0.6, ast=0.4, orb=0.4, drb=0.4, stl=0.4, blk=0.4, tov=1.1, eff=0.9),
}

NEUTRAL = dict(use=1.0, thr=1.0, ftd=1.0, ast=1.0, orb=1.0, drb=1.0, stl=1.0, blk=1.0, tov=1.0, eff=1.0)


def arch(name: str) -> Dict[str, float]:
    """Unknown archetype falls back to league average rather than failing."""
    return ARCHETYPES.get(name, NEUTRAL)


# ======================================================
# LEAGUE BASELINES
# ======================================================

BASE_3PT_SHARE = 0.30   # share of FGA that are threes
BASE_2PT_PCT   = 0.52
BASE_3PT_PCT   = 0.34
BASE_FT_PCT    = 0.70
BASE_AST_RATE  = 0.55   # share of made FG that are assisted
BASE_ORB_RATE  = 0.28   # share of own misses rebounded by the offense
BASE_TOV_RATE  = 0.12   # turnovers per possession
BASE_STL_SHARE = 0.60   # share of turnovers that are steals
BASE_BLK_RATE  = 0.055  # share of opponent 2PA blocked
BASE_FOULS     = 14     # team fouls per game


# ======================================================
# PLAYER
# ======================================================

@dataclass
class Player:
    name: str
    role: str            # P, W, C, B1, B2
    rating: float        # 0.0-5.0
    archetype: str
    minutes: float = 0.0
    stats: Dict[str, float] = field(default_factory=lambda: {
        "PTS": 0, "FGM": 0, "FGA": 0, "3PM": 0, "3PA": 0, "FTM": 0, "FTA": 0,
        "OREB": 0, "DREB": 0, "REB": 0, "AST": 0, "STL": 0, "BLK": 0,
        "TOV": 0, "PF": 0,
    })

    @property
    def w(self) -> Dict[str, float]:
        return arch(self.archetype)


# ======================================================
# ALLOCATION HELPERS
# ======================================================

def dirichlet(weights: List[float], concentration: float = CONCENTRATION) -> List[float]:
    """
    Draw shares that average to the weights but vary night to night.
    Uses gamma variates, so no numpy dependency.
    """
    total = sum(weights)
    if total <= 0:
        return [1.0 / len(weights)] * len(weights)
    alphas = [max(0.05, concentration * w / total) for w in weights]
    draws = [random.gammavariate(a, 1.0) for a in alphas]
    s = sum(draws)
    return [d / s for d in draws]


def apportion(total: int, shares: List[float]) -> List[int]:
    """Split an integer total by shares, largest-remainder, so it sums exactly."""
    raw = [total * s for s in shares]
    out = [int(r) for r in raw]
    rem = total - sum(out)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in range(rem):
        out[order[i % len(order)]] += 1
    return out


def assign_minutes(players: List[Player], ot_minutes: float = 0.0,
                   exits: Optional[Dict[str, float]] = None) -> None:
    """
    Three on the floor for 25 minutes = 75 player-minutes.

    Starters (C/W/P) take about 80% of that — 60 of 75, sd 10 — and B1/B2
    split the rest. `exits` maps a player to the game-minute he was lost
    (foul-out or injury); he accrues nothing after it and his remaining
    share is redistributed among whoever is still available.
    """
    starters = [p for p in players if p.role in ("P", "W", "C")]
    bench = [p for p in players if p.role not in ("P", "W", "C")]
    floor_minutes = GAME_MINUTES * 3

    target = min(floor_minutes, max(30.0, random.gauss(60.0, 10.0)))
    if not bench:
        target = floor_minutes

    for p, s in zip(starters, dirichlet([1.0] * len(starters), 26.0)):
        p.minutes = target * s
    leftover = floor_minutes - target
    if bench:
        for p, s in zip(bench, dirichlet([1.0] * len(bench), 10.0)):
            p.minutes = leftover * s
    else:
        for p in bench:
            p.minutes = 0.0

    if ot_minutes > 0:
        ot_floor = ot_minutes * 3
        for p, s in zip(starters, dirichlet([1.0] * len(starters), 40.0)):
            p.minutes += ot_floor * 0.90 * s
        if bench:
            for p, s in zip(bench, dirichlet([1.0] * len(bench), 10.0)):
                p.minutes += ot_floor * 0.10 * s

    _cap_minutes(players, GAME_MINUTES + ot_minutes)
    for p in players:
        p.minutes = round(p.minutes, 1)


def _cap_minutes(players: List[Player], ceiling: float) -> None:
    """
    Nobody plays more than the game is long. Overflow goes to whoever has
    room, so the floor total is preserved.
    """
    for _ in range(6):
        over = sum(max(0.0, p.minutes - ceiling) for p in players)
        if over <= 1e-9:
            return
        for p in players:
            if p.minutes > ceiling:
                p.minutes = ceiling
        room = [p for p in players if p.minutes < ceiling - 1e-9]
        if not room:
            return
        head = sum(ceiling - p.minutes for p in room)
        for p in room:
            p.minutes += over * (ceiling - p.minutes) / head


def apply_exits(players: List[Player], exits: Dict[str, float]) -> None:
    """
    Adjust minutes already assigned, for players lost to a foul-out or an
    injury. This MUST NOT redraw the distribution: expansion already picked
    shooters and rebounders against the original minutes, so redrawing would
    leave a 5-minute bench player with 27 shot attempts.

    The lost player is capped at his exit, and his remaining minutes go to
    whoever is still available, in proportion to what they were already
    playing.
    """
    if not exits:
        return
    lost = 0.0
    for p in players:
        cap = exits.get(p.name)
        if cap is not None and p.minutes > cap:
            lost += p.minutes - cap
            p.minutes = cap
    survivors = [p for p in players if p.name not in exits]
    base = sum(p.minutes for p in survivors)
    if survivors and lost > 0 and base > 0:
        for p in survivors:
            p.minutes += lost * (p.minutes / base)
    ceiling = max(p.minutes for p in players) if players else GAME_MINUTES
    _cap_minutes(players, max(GAME_MINUTES, ceiling if ceiling <= GAME_MINUTES
                              else GAME_MINUTES))
    for p in players:
        p.minutes = round(p.minutes, 1)


def opportunity(players: List[Player], key: str) -> List[float]:
    """
    Weight for a stat = archetype weight x minutes, and for usage also rating.
    Rating sets HOW MUCH a player produces; archetype sets WHAT KIND.
    """
    out = []
    for p in players:
        w = p.w[key] * (p.minutes / GAME_MINUTES)
        if key == "use":
            w *= 0.55 + 0.30 * p.rating      # a 4.0 carries ~2.2x a 1.0's load
        out.append(max(0.01, w))
    return out


# ======================================================
# TEAM BOX SCORE
# ======================================================

def _backfill(players: List[Player], target: int, made: str, att: str) -> None:
    """Hand back any makes lost to the attempts cap."""
    short = target - sum(p.stats[made] for p in players)
    if short <= 0:
        return
    for p in sorted(players, key=lambda x: x.stats[att] - x.stats[made], reverse=True):
        while short > 0 and p.stats[made] < p.stats[att]:
            p.stats[made] += 1
            short -= 1
        if short <= 0:
            return


def _assign_fouls(players: List[Player], total: int, limit: int = 5) -> None:
    """
    Fouls go by archetype and minutes, then get capped at the foul-out limit
    (5 in regulation, 6 if the game reached overtime). Overflow spills to
    players who still have fouls to give.
    """
    w = [max(0.01, (p.minutes / GAME_MINUTES) * (1.25 if p.w["blk"] > 1.3 else 1.0))
         for p in players]
    parts = apportion(total, dirichlet(w, 25.0))
    for p, v in zip(players, parts):
        p.stats["PF"] += v
    spill = 0
    for p in players:
        if p.stats["PF"] > limit:
            spill += p.stats["PF"] - limit
            p.stats["PF"] = limit
    for p in sorted(players, key=lambda x: x.stats["PF"]):
        while spill > 0 and p.stats["PF"] < limit:
            p.stats["PF"] += 1
            spill -= 1


def build_team_box(
    players: List[Player],
    team_points: int,
    opp_points: int,
    ot_minutes: float = 0.0,
    foul_out: int = 5,
) -> Dict[str, Any]:
    """Build one team's box score so the points sum exactly to team_points."""
    assign_minutes(players, ot_minutes)

    possessions = max(1, round(team_points / PPP_TARGET))

    # ---- Team-level shape of the scoring ----
    tov = max(0, round(possessions * BASE_TOV_RATE * random.uniform(0.75, 1.25)))
    fta = max(0, round(team_points * 0.16 * random.uniform(0.70, 1.30)))
    ftm = min(fta, round(fta * BASE_FT_PCT * random.uniform(0.85, 1.15)))

    pts_from_fg = team_points - ftm
    # Solve the 2s/3s split from the team's blended three-point lean.
    thr_lean = sum(p.w["thr"] * p.minutes for p in players) / max(1e-9, sum(p.minutes for p in players))
    three_share = BASE_3PT_SHARE * thr_lean * random.uniform(0.80, 1.20)
    three_share = min(0.50, max(0.18, three_share))

    # pts = 2*(FGM-3PM) + 3*3PM  ->  FGM = (pts - 3PM) / 2
    fgm_guess = pts_from_fg / (2 + three_share)
    tpm = round(fgm_guess * three_share)
    fgm = round((pts_from_fg - tpm) / 2)
    # Repair any rounding drift so points land exactly.
    while 2 * (fgm - tpm) + 3 * tpm + ftm != team_points:
        diff = team_points - (2 * (fgm - tpm) + 3 * tpm + ftm)
        if diff >= 2:
            fgm += 1
        elif diff == 1 and tpm < fgm:
            tpm += 1
        elif diff <= -2:
            fgm -= 1
        elif diff == -1 and tpm > 0:
            tpm -= 1
        else:
            ftm += diff
            fta = max(fta, ftm)

    tpa = max(tpm, round(tpm / (BASE_3PT_PCT * random.uniform(0.80, 1.25))))
    two_m = fgm - tpm
    two_a = max(two_m, round(two_m / (BASE_2PT_PCT * random.uniform(0.85, 1.15))))
    fga = two_a + tpa

    misses = fga - fgm
    oreb = round(misses * BASE_ORB_RATE * random.uniform(0.7, 1.3))
    # Defensive boards come off the OTHER team's misses; approximated here
    # from their scoring, then reconciled by the caller if needed.
    opp_misses = round((opp_points / PPP_TARGET) * 0.52)
    dreb = max(0, round(opp_misses * (1 - BASE_ORB_RATE) * random.uniform(0.85, 1.15)))

    ast = round(fgm * BASE_AST_RATE * random.uniform(0.80, 1.20))
    ast = min(ast, fgm)
    stl = round((opp_points / PPP_TARGET) * BASE_TOV_RATE * BASE_STL_SHARE * random.uniform(0.6, 1.4))
    blk = round(((opp_points / PPP_TARGET) * 0.55) * BASE_BLK_RATE * random.uniform(0.5, 1.6))
    pf = round(BASE_FOULS * random.uniform(0.75, 1.25))

    # ---- Distribute to players ----
    def give(stat: str, total: int, key: str, conc: float = CONCENTRATION):
        shares = dirichlet(opportunity(players, key), conc)
        for p, v in zip(players, apportion(total, shares)):
            p.stats[stat] += v

    give("FGA", fga, "use")
    # Makes track each player's OWN attempts: his share of the team's makes is
    # his share of attempts, tilted by archetype efficiency and rating. This
    # keeps 9-for-9 next to 7-for-23 from happening.
    make_w = [max(0.001, p.stats["FGA"] * p.w["eff"] * (0.85 + 0.06 * p.rating))
              for p in players]
    for p, v in zip(players, apportion(fgm, dirichlet(make_w, 90.0))):
        p.stats["FGM"] += min(v, int(p.stats["FGA"]))
    _backfill(players, fgm, "FGM", "FGA")

    three_w = [max(0.001, p.stats["FGA"] * p.w["thr"]) for p in players]
    for p, v in zip(players, apportion(tpa, dirichlet(three_w, 60.0))):
        p.stats["3PA"] += min(v, int(p.stats["FGA"]))
    tp_make_w = [max(0.001, p.stats["3PA"] * p.w["eff"]) for p in players]
    for p, v in zip(players, apportion(tpm, dirichlet(tp_make_w, 90.0))):
        p.stats["3PM"] += min(v, int(min(p.stats["3PA"], p.stats["FGM"])))
    _backfill(players, tpm, "3PM", "3PA")

    give("FTA", fta, "ftd")
    ft_make_w = [max(0.001, p.stats["FTA"] * (0.70 + 0.08 * p.rating)) for p in players]
    for p, v in zip(players, apportion(ftm, dirichlet(ft_make_w, 90.0))):
        p.stats["FTM"] += min(v, int(p.stats["FTA"]))
    _backfill(players, ftm, "FTM", "FTA")

    give("OREB", oreb, "orb")
    give("DREB", dreb, "drb")
    give("AST", ast, "ast", conc=55.0)
    give("STL", stl, "stl")
    give("BLK", blk, "blk")
    give("TOV", tov, "tov")
    _assign_fouls(players, pf, foul_out)

    for p in players:
        p.stats["REB"] = p.stats["OREB"] + p.stats["DREB"]
        p.stats["PTS"] = (
            2 * (p.stats["FGM"] - p.stats["3PM"]) + 3 * p.stats["3PM"] + p.stats["FTM"]
        )

    scored = sum(p.stats["PTS"] for p in players)
    return {
        "players": players,
        "team_points": scored,
        "target_points": team_points,
        "possessions": possessions,
        "ppp": round(scored / possessions, 3),
        "balanced": scored == team_points,
    }


# ======================================================
# RENDER
# ======================================================

COLS = ["MIN", "PTS", "FGM", "FGA", "3PM", "3PA", "FTM", "FTA",
        "REB", "AST", "STL", "BLK", "TOV", "PF"]


def render_box(team_name: str, box: Dict[str, Any]) -> str:
    lines = [f"{team_name}  —  {box['team_points']} pts, "
             f"{box['possessions']} poss, {box['ppp']} PPP"]
    head = f"{'':<18}{'POS':<5}" + "".join(f"{c:>5}" for c in COLS)
    lines.append(head)
    lines.append("-" * len(head))
    tot = {c: 0 for c in COLS}
    for p in box["players"]:
        row = f"{p.name:<18}{p.role:<5}"
        vals = [p.minutes] + [p.stats[c] for c in COLS[1:]]
        for c, v in zip(COLS, vals):
            tot[c] += v
            row += f"{v:>5.0f}" if c != "MIN" else f"{v:>5.1f}"
        lines.append(row)
    lines.append("-" * len(head))
    trow = f"{'TEAM':<18}{'':<5}" + "".join(
        f"{tot[c]:>5.0f}" if c != "MIN" else f"{tot[c]:>5.0f}" for c in COLS)
    lines.append(trow)
    return "\n".join(lines)


# ======================================================
# DEMO
# ======================================================

if __name__ == "__main__":
    random.seed(12)

    def team():
        return [
            Player("Mack Franklin",   "P",  4.0, "Floor General"),
            Player("Jaxon Russell",   "W",  4.0, "Two-Way Wing"),
            Player("Damian Ramirez",  "C",  3.0, "Rim Protector"),
            Player("Benjamin Foster", "B1", 3.0, "Combo Guard"),
            Player("Lynn Page",       "B2", 2.5, "Glass Cleaner"),
        ]

    box = build_team_box(team(), team_points=47, opp_points=43)
    print(render_box("Rancho Cucamonga", box))
    print("\npoints land exactly:", box["balanced"])

    print("\n--- same roster, three different nights ---")
    for i in range(3):
        b = build_team_box(team(), 47, 43)
        star = b["players"][0]
        big = b["players"][2]
        print(f"  game {i+1}: Franklin {star.stats['PTS']:.0f}p "
              f"{star.stats['AST']:.0f}a | Ramirez {big.stats['PTS']:.0f}p "
              f"{big.stats['REB']:.0f}r {big.stats['BLK']:.0f}b")
