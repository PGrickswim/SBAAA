"""
SBAAA Win Probability
=====================

Live win probability for the favorite, anchored to the dice engine so the
pregame number is exactly what Need says it is.

  pregame   P(favorite wins | Need) read off the dice distribution
  in-game   the lead, plus the margin still expected to be built, against
            the spread of what a game of this length can still produce

A dice-determined result the model thought unlikely still happens. That is
the point: the curve says how the game LOOKED, not what the dice already
decided.
"""

import math
from itertools import product
from typing import Dict, List, Tuple

from sbaaa_pbp import GAME_SECONDS

# Spread of final margin, in points, over a full 25 minutes.
MARGIN_SD_FULL = 9.0
SD_PER_ROOT_MIN = MARGIN_SD_FULL / math.sqrt(GAME_SECONDS / 60.0)


def _dice_win_prob() -> Dict[int, float]:
    """P(favorite reaches Need), straight from the dice."""
    def redmod(r: int) -> int:
        return 4 if r == 6 else (-3 if r == 1 else 0)
    tot: Dict[int, int] = {}
    n = 0
    for w in product(range(1, 7), repeat=3):
        for r in range(1, 7):
            v = sum(w) + redmod(r)
            tot[v] = tot.get(v, 0) + 1
            n += 1
    return {need: sum(c for v, c in tot.items() if v >= need) / n
            for need in range(4, 12)}


DICE_WP = _dice_win_prob()


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _z(p: float) -> float:
    """Inverse normal, Acklam's approximation; plenty for a chart."""
    p = min(1 - 1e-9, max(1e-9, p))
    a = [-39.69683028665376, 220.9460984245205, -275.9285104469687,
         138.3577518672690, -30.66479806614716, 2.506628277459239]
    b = [-54.47609879822406, 161.5858368580409, -155.6989798598866,
         66.80131188771972, -13.28068155288572]
    c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996,
         3.754408661907416]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def win_probability(fav_lead: int, seconds_left: float, need: int) -> float:
    """
    Favorite's win probability.
      fav_lead      favorite's score minus the underdog's, right now
      seconds_left  game seconds remaining
      need          the dice Need for this matchup
    """
    p0 = DICE_WP.get(int(need), 0.5)
    mins_left = max(0.0, seconds_left) / 60.0
    full = GAME_SECONDS / 60.0

    # Pregame edge expressed as points of expected final margin.
    mu_full = _z(p0) * MARGIN_SD_FULL

    if mins_left <= 0:
        return 1.0 if fav_lead > 0 else (0.0 if fav_lead < 0 else p0)

    mu_rest = mu_full * (mins_left / full)
    sd_rest = SD_PER_ROOT_MIN * math.sqrt(mins_left)
    return _phi((fav_lead + mu_rest) / sd_rest)


# Having the ball is worth about a point, and worth more as the clock dies,
# because there is less time for the other team to answer.
POSSESSION_VALUE = 0.95


def curve(flow: List[Tuple[float, int, int]], need: int,
          fav_is_away: bool, total_minutes: float) -> List[float]:
    """Win probability for the favorite at each sampled point of the flow."""
    out = []
    for elapsed, away_pts, home_pts in flow:
        lead = (away_pts - home_pts) if fav_is_away else (home_pts - away_pts)
        left = (total_minutes - elapsed) * 60.0
        out.append(100.0 * win_probability(lead, left, need))
    return out


def curve_by_possession(poss_list, need: int, fav_team: str,
                        away_name: str, home_name: str,
                        total_seconds: float) -> List[Tuple[float, float]]:
    """
    Win probability after every possession, which is where the jitter in a
    real chart comes from: the number moves on each score, each stop and
    each change of hands, not on a timer.

    Returns (minutes elapsed, favorite win %) per possession.
    """
    score = {away_name: 0, home_name: 0}
    other = {away_name: home_name, home_name: away_name}
    out: List[Tuple[float, float]] = []

    # pregame
    out.append((0.0, 100.0 * win_probability(0, total_seconds, need)))

    for i, p in enumerate(poss_list):
        score[p.team] += p.points
        left = max(0.0, p.clock_start - p.duration)
        lead = score[fav_team] - score[other[fav_team]]

        # Who has the ball next, and what that is worth. Late, it matters.
        nxt = poss_list[i + 1].team if i + 1 < len(poss_list) else None
        edge = 0.0
        if nxt and left > 0:
            urgency = min(1.0, 180.0 / max(20.0, left))
            edge = POSSESSION_VALUE * urgency * (1 if nxt == fav_team else -1)

        wp = win_probability(lead + edge, left, need)
        out.append(((total_seconds - left) / 60.0, 100.0 * wp))
    return out


if __name__ == "__main__":
    print("pregame win probability by Need (from the dice):")
    for n in range(4, 12):
        print(f"  Need {n:>2}: {100*DICE_WP[n]:.1f}%")
    print("\nfavorite with Need 9, trailing by 4:")
    for m in (20, 15, 10, 5, 2, 1):
        print(f"  {m:>2} min left: "
              f"{100*win_probability(-4, m*60, 9):.0f}%")
