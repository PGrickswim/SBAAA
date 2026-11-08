"""
SBAAA Play-by-Play Layer
========================

Sits under the dice engine. The dice decide winner, margin and final score;
this builds the possessions that land exactly on those numbers.

Rebuilt from the October spec, with the check rule added.

THE CHECK RULE
  Every change of possession must be cleared to the check zone: the area
  8 feet behind the arc, straight back only, not the sides.
    - defensive rebound, steal, turnover, opponent's made basket -> clear
      required, shot clock 24
    - offensive rebound -> NO clear, shot clock 14
    - not cleared by :14 -> turnover
    - shoot while uncleared: a miss is live and STILL uncleared;
      a make is a dead-ball turnover and the basket is waved off

TIMING
  25:00 = 1500 s. Median team scores ~49 at ~0.95 PPP, so ~52 possessions
  each, ~103 in the game, ~14.6 s per possession including the clear.
  Clear ~4 s, offense ~10.6 s.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import random

from sbaaa_boxscore import Player, arch, GAME_MINUTES

# ======================================================
# TIMING
# ======================================================

GAME_SECONDS = GAME_MINUTES * 60
SHOT_CLOCK = 24
CLEAR_DEADLINE = 14          # not cleared by this -> turnover
OREB_SHOT_CLOCK = 14

CLEAR_BASE = 4.0             # seconds for a typical clear
OFFENSE_BASE = 10.6          # seconds of offense after the clear

# Archetypes that move the ball up quickly, and those that don't.
CLEAR_SPEED = {
    "Tempo Pusher": 0.70, "Floor General": 0.78, "Drive-and-Kick Guard": 0.82,
    "Pick-and-Roll Maestro": 0.85, "Combo Guard": 0.88, "Shot Creator": 0.90,
    "High-Assist Risk Taker": 0.90, "Energy Spark": 0.92, "Slasher": 0.95,
    "Two-Way Wing": 1.00, "Swiss Army Forward": 1.00, "Movement Shooter": 1.02,
    "Pure Shooter": 1.05, "Sixth-Man Gunner": 1.03, "Isolation Specialist": 1.05,
    "Clutch Scorer": 1.02, "Defensive Stopper": 1.05, "Stretch Big": 1.12,
    "Glass Cleaner": 1.20, "Post Scorer": 1.22, "Rim Protector": 1.25,
    "Paint Enforcer": 1.30, "Lob Threat": 1.22, "Low-Usage Connector": 1.10,
}

MEDIA_TIMEOUTS = (11 * 60, 6 * 60)   # first stoppage under each


PRESSURE_RATE = 0.07         # defense actively contests the clear
SCRAMBLE_RATE = 0.017        # loose ball, deflection, bobble on the way back
PHANTOM_RATE  = 0.0048       # team believes it cleared and never crossed the line

def clear_seconds(players: List[Player], rng: random.Random,
                  pressured: Optional[bool] = None) -> float:
    """
    How long this lineup takes to clear. The quickest handler on the floor
    leads it, so the best clearer dominates rather than the average.

    An unpressured clear is quick and tightly spread, which on its own makes
    a :14 violation impossible. Real pressure is what puts the deadline in
    play, so a minority of clears are contested and draw from a long tail.
    """
    speeds = sorted(CLEAR_SPEED.get(p.archetype, 1.0) for p in players)
    lead = speeds[0] * 0.7 + (speeds[1] if len(speeds) > 1 else speeds[0]) * 0.3
    if pressured is None:
        pressured = rng.random() < PRESSURE_RATE
    if pressured:
        # contested: the handler gets turned back, sometimes repeatedly
        return max(2.0, rng.gauss(CLEAR_BASE * lead * 1.9, 3.4))
    if rng.random() < SCRAMBLE_RATE:
        # nobody steals it, but the ball gets away: deflection, bobble,
        # a loose ball on the way back. No defender earns a steal for it.
        return max(2.0, rng.gauss(CLEAR_BASE * lead + 7.0, 4.2))
    return max(1.5, rng.gauss(CLEAR_BASE * lead, 0.9))


# ======================================================
# POSSESSION OUTCOMES
# ======================================================

START_DEFENSIVE_REBOUND = "dreb"
START_STEAL = "steal"
START_TURNOVER = "turnover"
START_MADE_BASKET = "made"
START_OFFENSIVE_REBOUND = "oreb"

NEEDS_CLEAR = {START_DEFENSIVE_REBOUND, START_STEAL,
               START_TURNOVER, START_MADE_BASKET}


@dataclass
class Possession:
    team: str
    start: str                 # how it began
    cleared: bool
    clock_start: float         # game seconds remaining when it began
    duration: float
    points: int
    events: List[str] = field(default_factory=list)
    period: int = 0          # 0 regulation, 1 overtime


# ======================================================
# BRIDGE SAMPLING
# ======================================================
# Each team's per-possession points are drawn conditioned on its exact
# final total, so the sum always matches the dice. Dynamic programming over
# (possessions remaining, points remaining) gives the exact distribution.

POINT_OUTCOMES = (0, 1, 2, 3, 4)     # 4 = three-point play or two trips


def _base_weights(three_lean: float) -> Dict[int, float]:
    """Prior on what one possession is worth, before conditioning."""
    return {
        0: 0.52,
        1: 0.05,
        2: 0.28,
        3: 0.13 * three_lean,
        4: 0.02,
    }


def bridge_sample_scoring(
    n_poss: int, total: int, three_lean: float, rng: random.Random
) -> List[int]:
    """
    Same as bridge_sample but every possession scores at least 1. Used once
    the chain has already decided WHICH possessions scored, so the values
    have to agree with it.
    """
    opts = (1, 2, 3, 4)
    w = _base_weights(three_lean)
    wt = {p: w[p] for p in opts}
    reach: List[Dict[int, float]] = [dict() for _ in range(n_poss + 1)]
    reach[0][0] = 1.0
    for i in range(1, n_poss + 1):
        prev, cur = reach[i - 1], reach[i]
        for r, v in prev.items():
            for p in opts:
                nr = r + p
                if nr > total or total - nr > (n_poss - i) * 4:
                    continue
                if total - nr < (n_poss - i):
                    continue
                cur[nr] = cur.get(nr, 0.0) + v * wt[p]
    if total not in reach[n_poss]:
        raise ValueError(f"cannot score {total} in {n_poss} scoring possessions")
    out, remaining = [], total
    for i in range(n_poss, 0, -1):
        choices, weights = [], []
        for p in opts:
            nr = remaining - p
            if nr < 0:
                continue
            ways = reach[i - 1].get(nr)
            if not ways:
                continue
            choices.append(p); weights.append(wt[p] * ways)
        pick = rng.choices(choices, weights=weights, k=1)[0]
        out.append(pick); remaining -= pick
    return out


def bridge_sample(
    n_poss: int, total: int, three_lean: float, rng: random.Random
) -> List[int]:
    """
    Draw n_poss per-possession point values summing EXACTLY to total.
    Backward DP for reachability, forward sampling weighted by the prior.
    """
    w = _base_weights(three_lean)
    maxpt = max(POINT_OUTCOMES)

    # reachable[i][r] = ways to score r in i possessions (capped, as floats)
    reach: List[Dict[int, float]] = [dict() for _ in range(n_poss + 1)]
    reach[0][0] = 1.0
    for i in range(1, n_poss + 1):
        prev = reach[i - 1]
        cur = reach[i]
        for r, v in prev.items():
            for p in POINT_OUTCOMES:
                nr = r + p
                if nr > total:
                    continue
                if total - nr > (n_poss - i) * maxpt:
                    continue
                cur[nr] = cur.get(nr, 0.0) + v * w[p]

    if total not in reach[n_poss]:
        # Unreachable: too few possessions for the score. Caller should
        # have sized n_poss from the score, so this is a guard, not a path.
        raise ValueError(f"cannot score {total} in {n_poss} possessions")

    out: List[int] = []
    remaining = total
    for i in range(n_poss, 0, -1):
        choices, weights = [], []
        for p in POINT_OUTCOMES:
            nr = remaining - p
            if nr < 0:
                continue
            ways = reach[i - 1].get(nr)
            if not ways:
                continue
            choices.append(p)
            weights.append(w[p] * ways)
        pick = rng.choices(choices, weights=weights, k=1)[0]
        out.append(pick)
        remaining -= pick
    return out


# ======================================================
# PHASE SHAPING
# ======================================================
# W1/W2/W3 say who controlled the opening, middle and ending thirds,
# RELATIVE TO EXPECTATION. 1-2 underdog, 3-4 even, 5-6 favorite.
#
# This shapes the ORDER of possessions, never the totals. Control means
# dictating tempo, not outscoring: an underdog can own all three phases
# and still lose, and a favorite can own all three and still be tied.
# Phase balance is NOT constrained to match the margin.

def phase_of(die: int) -> int:
    return -1 if die <= 2 else (0 if die <= 4 else 1)


def shape_by_phase(
    values: List[int], whites: List[int], is_favorite: bool, rng: random.Random
) -> List[int]:
    """
    Reorder a team's scoring possessions so its good stretches land in the
    thirds it controlled. Totals are untouched; only the sequence moves.

    Control TILTS scoring, it does not monopolise it. An earlier version
    sorted the possessions and handed the controlled third the best ones
    outright, which produced teams scoring 45 in fifteen minutes and nothing
    afterwards. Each third now gets a target share of the points — about
    38/33/29 across controlled, even and ceded — and possessions are dealt
    to whichever third is furthest below its target.
    """
    n = len(values)
    if n < 6:
        return values
    cuts = [0, n // 3, 2 * n // 3, n]
    sizes = [cuts[i + 1] - cuts[i] for i in range(3)]
    sign = 1 if is_favorite else -1

    # Target shares are about 38 / 33 / 29 across controlled, even and
    # ceded, each with a standard deviation of 5 points of share, so no two
    # games split the same way.
    tilt = []
    for d in whites:
        share = 33.3 + 6.0 * phase_of(d) * sign + rng.gauss(0, 6.2)
        tilt.append(max(12.0, share) / 33.3)
    weighted = [t * sz for t, sz in zip(tilt, sizes)]
    total = sum(values)
    targets = [total * w / sum(weighted) for w in weighted]

    pool = list(values)
    rng.shuffle(pool)
    pool.sort(reverse=True)          # deal the big ones first
    got = [0.0, 0.0, 0.0]
    chunks: List[List[int]] = [[], [], []]
    for v in pool:
        avail = [i for i in range(3) if len(chunks[i]) < sizes[i]]
        pick_i = max(avail, key=lambda i: (targets[i] - got[i]) / max(1, sizes[i]))
        chunks[pick_i].append(v)
        got[pick_i] += v

    out: List[int] = []
    for c in chunks:
        rng.shuffle(c)
        out.extend(c)
    return out


# ======================================================
# GAME BUILD
# ======================================================

def build_possessions(
    away_name: str, home_name: str,
    away_players: List[Player], home_players: List[Player],
    away_points: int, home_points: int,
    whites: List[int], favorite: str,
    rng: random.Random,
    ot_seconds: float = 0.0,
    ot_detail: Optional[Dict[str, int]] = None,
    span_seconds: Optional[float] = None,
) -> List[Possession]:
    """
    Build the full possession sequence for a game whose final score is
    already fixed by the dice.

    For an overtime game, pass `ot_detail` from the dice engine. Regulation
    and overtime are then built as two separate runs, so the teams are
    actually LEVEL at the final buzzer of regulation and the extra period
    ends on the basket that puts a team up four. Building one continuous run
    instead spread the points evenly and left regulation finishing 53-55 in
    a game the dice said was tied.
    """
    if ot_detail and ot_seconds > 0:
        each = ot_detail["RegEach"]
        loser_ot = ot_detail["OT_Loser_Points"]
        margin = ot_detail["OT_Margin"]
        away_won = away_points > home_points
        assert away_points != home_points, "an overtime game cannot be tied"
        reg = build_possessions(away_name, home_name, away_players,
                                home_players, each, each, whites, favorite,
                                rng)
        # The extra period is its OWN length. Sizing it from the full 25
        # minutes asked for a hundred possessions to carry nine points,
        # which is impossible and fell through to a fallback that re-marked
        # the chain — reintroducing made baskets that kept the ball.
        a_ot = loser_ot + (margin if away_won else 0)
        h_ot = loser_ot + (0 if away_won else margin)
        extra = build_possessions(away_name, home_name, away_players,
                                  home_players, a_ot, h_ot, whites,
                                  favorite, rng, span_seconds=ot_seconds)
        # lay the extra period after the final buzzer of regulation
        out = list(reg)
        clock = ot_seconds
        for p in extra:
            p.clock_start = clock
            clock -= p.duration
        # The period ends the moment a team leads by four, so it has to
        # FINISH on the winner's go-ahead score. Moving that possession to
        # the end would break the chain, so the period is rebuilt until it
        # ends that way on its own.
        # The period ends the instant a team leads by four, so it must
        # FINISH on the winner's go-ahead score. Rather than rebuild until
        # that happens by luck, the period is built a basket short and the
        # go-ahead score is appended. Points still add up and the chain
        # stays valid; earlier attempts that reordered or trimmed the tail
        # broke one or the other.
        winner = away_name if away_won else home_name
        g = 3 if (a_ot if away_won else h_ot) >= 3 else 2
        ra = max(0, a_ot - (g if away_won else 0))
        rh = max(0, h_ot - (0 if away_won else g))
        extra = build_possessions(away_name, home_name, away_players,
                                  home_players, ra, rh, whites, favorite,
                                  rng, span_seconds=ot_seconds)
        loser = home_name if away_won else away_name
        prev = extra[-1] if extra else None
        if prev is not None and prev.team == winner and prev.points > 0:
            # The winner cannot score twice in a row. Give the loser an
            # empty possession in between so the chain stays legal.
            lp = away_players if not away_won else home_players
            lclr = clear_seconds(lp, rng)
            extra.append(Possession(
                loser, START_MADE_BASKET, True, 0.0,
                lclr + max(2.0, rng.gauss(OFFENSE_BASE, 2.0)), 0,
                [f"cleared in {lclr:.1f}s"]))
            prev = extra[-1]
        if prev is None:
            st = START_TURNOVER
        elif prev.team == winner:
            st = START_OFFENSIVE_REBOUND
        else:
            st = (START_MADE_BASKET if prev.points > 0
                  else START_DEFENSIVE_REBOUND)
        shooters = away_players if away_won else home_players
        clr = clear_seconds(shooters, rng) if st in NEEDS_CLEAR else 0.0
        extra.append(Possession(
            winner, st, st in NEEDS_CLEAR, 0.0,
            clr + max(2.0, rng.gauss(OFFENSE_BASE, 2.0)),
            (a_ot if away_won else h_ot) - (ra if away_won else rh),
            [f"cleared in {clr:.1f}s"] if st in NEEDS_CLEAR else []))

        scale = ot_seconds / max(1e-6, sum(p.duration for p in extra))
        clock = ot_seconds
        for p in extra:
            p.duration *= scale
            p.clock_start = clock
            clock -= p.duration
        # regulation keeps its own 25 minutes; overtime sits beneath it
        for p in out:
            p.clock_start += ot_seconds
        for p in extra:
            p.period = 1
        assert extra[-1].team == winner and extra[-1].points > 0, (
            f"OT must end on the winner's score: last={extra[-1].team}/"
            f"{extra[-1].points} winner={winner} away_won={away_won} "
            f"a_ot={a_ot} h_ot={h_ot} ra={ra} rh={rh}")
        return out + extra

    seconds = span_seconds if span_seconds else GAME_SECONDS + ot_seconds

    # --- how many possessions fit, given each team's clearing speed ---
    a_clear = clear_seconds(away_players, rng)
    h_clear = clear_seconds(home_players, rng)
    avg_poss = ((CLEAR_BASE + OFFENSE_BASE) *
                ((a_clear + h_clear) / 2 / CLEAR_BASE * 0.30 + 0.70))
    floor_poss = 40 if span_seconds is None else 4
    total_poss = max(floor_poss, int(round(seconds / avg_poss)))
    # A side cannot score more than 4 in a possession, so the count has to
    # be able to carry the points however short the period is. A 17-point
    # overtime in 130 seconds needs more possessions than the clock implies.
    need = max((away_points + 3) // 4, (home_points + 3) // 4)
    total_poss = max(total_poss, 2 * need + 2)
    a_poss = total_poss // 2 + (total_poss % 2)
    h_poss = total_poss // 2

    # Each team needs enough possessions to reach its score.
    while a_points_infeasible(away_points, a_poss):
        a_poss += 1
    while a_points_infeasible(home_points, h_poss):
        h_poss += 1

    a_lean = team_three_lean(away_players)
    h_lean = team_three_lean(home_players)
    a_vals = bridge_sample(a_poss, away_points, a_lean, rng)
    h_vals = bridge_sample(h_poss, home_points, h_lean, rng)

    a_vals = shape_by_phase(a_vals, whites, favorite == away_name, rng)
    h_vals = shape_by_phase(h_vals, whites, favorite == home_name, rng)

    # --- build the possession CHAIN first ---
    # Who has the ball, and how each possession began, is decided before any
    # points are assigned. Doing it the other way round let the two disagree:
    # a team could turn the ball over and still have the next possession, or
    # take several in a row once the other team's list ran out.
    # Which possessions score is decided WITH the chain, never after it:
    # re-marking afterwards left made baskets that did not change hands.
    # If a team's scoring-possession count cannot carry its points, the
    # whole chain is rebuilt rather than patched.
    p_score = 0.43
    # A team can finish a short overtime with no points at all, so the rate
    # is per team: zero points means none of its possessions score.
    rate = {away_name: 0.0 if away_points == 0 else p_score,
            home_name: 0.0 if home_points == 0 else p_score}

    def make_chain():
        out = []
        team = away_name             # away team starts with the ball
        start = START_TURNOVER       # the opening tip clears like any other
        for _ in range(total_poss):
            scored = rng.random() < rate[team]
            out.append((team, start, scored))
            if scored:
                start = START_MADE_BASKET
                team = home_name if team == away_name else away_name
            else:
                roll = rng.random()
                if roll < 0.24:
                    start = START_OFFENSIVE_REBOUND   # the only way to keep it
                elif roll < 0.36:
                    start = START_STEAL
                    team = home_name if team == away_name else away_name
                else:
                    start = START_DEFENSIVE_REBOUND
                    team = home_name if team == away_name else away_name
        return out

    def feasible(ch):
        for name, pts in ((away_name, away_points), (home_name, home_points)):
            k = sum(1 for t, _, sc in ch if t == name and sc)
            if pts == 0:
                if k:
                    return False
                continue
            if k == 0 or pts < k or pts > 4 * k:
                return False
            # Keep the points per scoring possession in a sane band. Merely
            # being arithmetically possible let a chain mark 30 scoring
            # possessions for 42 points, which the sampler could only fill
            # with ones — and every one is a trip to the line. That put a
            # team on 48 free throw attempts.
            # Only for totals big enough for the band to mean anything; a
            # short overtime may legitimately be 1 point in 3 possessions.
            if pts >= 10 and not (1.85 <= pts / k <= 3.1):
                return False
        return True

    chain = make_chain()
    for _ in range(40):
        if feasible(chain):
            break
        chain = make_chain()
    else:
        # Still not feasible: set each team's OWN scoring rate from its own
        # points and rebuild. Sharing one rate across both sides left
        # chains asking a team to score 48 in 53 scoring possessions.
        for target in (2.4, 2.2, 2.6, 2.0, 2.8, 1.95, 3.0):
            per_team = max(1, total_poss / 2)
            for nm, pts in ((away_name, away_points),
                            (home_name, home_points)):
                rate[nm] = (0.0 if pts == 0 else
                            min(0.95, max(0.05, (pts / target) / per_team)))
            ok = False
            for _ in range(40):
                chain = make_chain()
                if feasible(chain):
                    ok = True
                    break
            if ok:
                break
        else:
            # Last resort: lay the chain out deterministically so it is
            # always playable. Alternating possessions, every k-th one
            # scoring, keeps the hand-over rules intact.
            chain = []
            team = away_name
            start = START_TURNOVER
            need_k = {nm: max(1, min(pts, round(pts / 2.4)))
                      for nm, pts in ((away_name, away_points),
                                      (home_name, home_points))}
            seen = {away_name: 0, home_name: 0}
            mine = {away_name: 0, home_name: 0}
            for nm in (away_name, home_name):
                mine[nm] = sum(1 for k in range(total_poss) if k % 2 ==
                               (0 if nm == away_name else 1))
            for k in range(total_poss):
                seen[team] += 1
                left = mine[team] - seen[team] + 1
                scored = need_k[team] > 0 and (
                    need_k[team] >= left or rng.random() < need_k[team] / left)
                if scored:
                    need_k[team] -= 1
                chain.append((team, start, scored))
                start = (START_MADE_BASKET if scored
                         else START_DEFENSIVE_REBOUND)
                team = home_name if team == away_name else away_name

    def fit(name: str, points: int) -> List[int]:
        allidx = [i for i, (t, _, _) in enumerate(chain) if t == name]
        scoring = [i for i in allidx if chain[i][2]]
        if not scoring:
            return [0 for _ in allidx]
        vals = bridge_sample_scoring(
            len(scoring), points,
            team_three_lean(away_players if name == away_name
                            else home_players), rng)
        # Phase shaping moves the bigger scores into the thirds the team
        # controlled. It is applied ACROSS THE SCORING POSSESSIONS ONLY:
        # permuting all of them would move points onto possessions the
        # chain says did not score, which breaks who gets the ball next.
        vals = shape_by_phase(vals, whites, favorite == name, rng)
        out = {i: 0 for i in allidx}
        for i, v in zip(scoring, vals):
            out[i] = v
        return [out[i] for i in allidx]

    a_vals = fit(away_name, away_points)
    h_vals = fit(home_name, home_points)

    # --- walk the chain, applying the check rule ---
    poss: List[Possession] = []
    clock = seconds
    ai = hi = 0
    for ci, (team_i, start_i, _) in enumerate(chain):
        nxt_same = (ci + 1 < len(chain)
                    and chain[ci + 1][0] == team_i
                    and chain[ci + 1][1] == START_OFFENSIVE_REBOUND)
        is_away = team_i == away_name
        pts = (a_vals[ai] if is_away else h_vals[hi])
        if is_away:
            ai += 1
        else:
            hi += 1
        players = away_players if is_away else home_players

        needs = start_i in NEEDS_CLEAR
        clr = clear_seconds(players, rng) if needs else 0.0

        # A failed clear scores nothing, so it can only happen on a
        # possession the sampler already gave zero points — otherwise those
        # points would simply vanish from the game.
        if needs and pts == 0 and not nxt_same and clr > CLEAR_DEADLINE:
            dur = SHOT_CLOCK - CLEAR_DEADLINE + clr
            poss.append(Possession(team_i, start_i, False, clock, dur, 0,
                                   ["failed to clear by :14 — turnover"]))
            clock -= dur
            continue

        dur = clr + max(2.0, rng.gauss(OFFENSE_BASE, 3.2))
        phantom = (needs and pts == 0 and not nxt_same
                   and rng.random() < PHANTOM_RATE)
        p = Possession(team_i, start_i, needs and not phantom, clock, dur, pts)
        if phantom:
            if rng.random() < 0.45:
                p.events.append("never crossed the check line — "
                                "basket waved off, dead-ball turnover")
            else:
                p.events.append("never crossed the check line — "
                                "shot missed, live ball, still uncleared")
        elif needs:
            p.events.append(f"cleared in {clr:.1f}s")
        clock -= dur
        poss.append(p)

    # Every sampled possession is played, so the score is always exact.
    # Durations are then scaled to fill the clock precisely, which keeps
    # both the final score and the final buzzer honest.
    nominal = sum(p.duration for p in poss)
    if nominal > 0:
        scale = seconds / nominal
        clock = seconds
        for p in poss:
            p.duration *= scale
            p.clock_start = clock
            clock -= p.duration
    return poss


def a_points_infeasible(points: int, n_poss: int) -> bool:
    return points > n_poss * max(POINT_OUTCOMES)


def team_three_lean(players: List[Player]) -> float:
    return sum(arch(p.archetype)["thr"] for p in players) / max(1, len(players))


# ======================================================
# DEMO
# ======================================================

if __name__ == "__main__":
    rng = random.Random(4)
    from sbaaa_data import League
    lg = League()
    A, H = "South Bend IN", "Naperville IL"
    ap, hp = lg.active_five(A), lg.active_five(H)

    poss = build_possessions(A, H, ap, hp, 52, 47, [4, 3, 5], A, rng)
    a = sum(p.points for p in poss if p.team == A)
    h = sum(p.points for p in poss if p.team == H)
    print(f"possessions: {len(poss)}  |  {A} {a}, {H} {h}")
    print(f"PPP: {A} {a/sum(1 for p in poss if p.team==A):.3f}, "
          f"{H} {h/sum(1 for p in poss if p.team==H):.3f}")
    cleared = [p for p in poss if p.cleared]
    print(f"possessions requiring a clear: {len(cleared)} of {len(poss)}")
    print(f"failed clears: {sum(1 for p in poss if 'failed' in ' '.join(p.events))}")
    print(f"avg seconds per possession: "
          f"{sum(p.duration for p in poss)/len(poss):.1f}")
    print("\nfirst 8 possessions:")
    for p in poss[:8]:
        m, s = divmod(max(0, p.clock_start), 60)
        print(f"  {int(m):>2}:{s:04.1f}  {p.team:<16} {p.start:<9} "
              f"{p.points}p  {p.duration:4.1f}s  {'; '.join(p.events)}")
