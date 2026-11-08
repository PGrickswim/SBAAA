"""
SBAAA Dice Engine — dice result to final score
===============================================

Base: SeriesSim v3.0 (Drive: SeriesSim.ipynb), preserved as-is for
favorite selection, Need, margin table, and total points.

Change from v3.0: overtime scoring. See resolve_overtime() for the
old vs new behavior.

Dice order (as rolled and entered): White1, White2, White3, Red, Green, Blue.
v3.0 called Green and Blue "greens"; they are the same two dice.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import math
import random

# ======================================================
# 1. DICE HELPERS
# ======================================================

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def roll_d6(n: int, rng: Optional[random.Random] = None) -> List[int]:
    r = rng or random
    return [r.randint(1, 6) for _ in range(n)]


def red_mod(red: int) -> int:
    """Volatility die. Only the extremes move the result."""
    if red == 6:
        return 4
    if red == 1:
        return -3
    return 0


# ======================================================
# 2. LOCATION AND TRAVEL  (v3.0, unchanged)
# ======================================================

def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    R = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def locpoints_from_miles(miles: float) -> int:
    """Trip distance bucketed 1-10."""
    for i, cutoff in enumerate([200, 500, 800, 1100, 1400, 1700, 2000, 2300, 2600], start=1):
        if miles < cutoff:
            return i
    return 10


def travel_penalty(loc_points: int) -> int:
    """Applied to the AWAY team only. This is the home-court edge."""
    return max(2, int(loc_points) + 1)


def travel_fatigue(miles: float) -> int:
    """
    Fatigue from the trip just made, 0 to -3. This is separate from the
    home-court edge above: the HOME team can arrive tired too, coming back
    off its own road swing, and the schedule carries its mileage in
    `Distance Traveled H`. A team that did not travel gets nothing.
    """
    if miles < 250:
        return 0
    if miles < 800:
        return -1
    if miles < 1800:
        return -2
    return -3


# ======================================================
# 3. NEED AND MARGIN  (v3.0, unchanged)
# ======================================================

def dice_need_from_dr(dR: float) -> int:
    """Rating gap to the dice total the favorite must reach."""
    need = 11 - int(dR // 6)
    return int(clamp(need, 4, 11))


def green_bucket(gsum: int) -> str:
    """gsum = green die + blue die."""
    if gsum <= 3:
        return "2-3"
    if gsum <= 6:
        return "4-6"
    if gsum <= 8:
        return "7-8"
    if gsum <= 10:
        return "9-10"
    return "11-12"


MARGIN_TABLE = {
    1:       {"2-3": 1,  "4-6": 1,  "7-8": 2,  "9-10": 3,  "11-12": 4},
    2:       {"2-3": 2,  "4-6": 3,  "7-8": 4,  "9-10": 5,  "11-12": 5},
    3:       {"2-3": 5,  "4-6": 6,  "7-8": 7,  "9-10": 8,  "11-12": 8},
    4:       {"2-3": 7,  "4-6": 8,  "7-8": 9,  "9-10": 10, "11-12": 10},
    5:       {"2-3": 8,  "4-6": 9,  "7-8": 10, "9-10": 11, "11-12": 11},
    6:       {"2-3": 11, "4-6": 12, "7-8": 13, "9-10": 14, "11-12": 14},
    "7-9":   {"2-3": 13, "4-6": 15, "7-8": 17, "9-10": 19, "11-12": 21},
    "10-13": {"2-3": 19, "4-6": 22, "7-8": 25, "9-10": 28, "11-12": 31},
    "14-18": {"2-3": 23, "4-6": 27, "7-8": 31, "9-10": 35, "11-12": 39},
}


def margin_from_delta_and_green(abs_delta: int, gsum: int) -> int:
    b = green_bucket(gsum)
    if abs_delta <= 6:
        return MARGIN_TABLE[abs_delta][b]
    if abs_delta <= 9:
        return MARGIN_TABLE["7-9"][b]
    if abs_delta <= 13:
        return MARGIN_TABLE["10-13"][b]
    return MARGIN_TABLE["14-18"][b]


# ======================================================
# 4. OVERTIME  (CHANGED from v3.0)
# ======================================================

# Loser's overtime points, by the red die.
OT_LOSER_BY_RED = {1: 0, 2: 0, 3: 2, 4: 5, 5: 8, 6: 12}

# v3.0 behavior (replaced):
#     total = base_total + adjustment + OT_ADD_BY_RED[red]   # {1:4,2:6,3:8,4:11,5:14,6:17}
#     loser = (total - margin) // 2
#   i.e. the red value was added ONCE to the game and then split.
#
# Current behavior:
#   Regulation ends tied at T (rounded DOWN to even), each team at T/2.
#   The red die sets how many points the LOSER scores in overtime.
#   The winner scores that plus the margin.
#     loser  = T/2 + R
#     winner = T/2 + R + margin
#   An OT game adds 2R + margin.
#
# Why the red table was re-mapped: v3.0's values were a whole-game add that
# got split between the teams. Reused as a per-team figure they roughly
# doubled, so an average overtime added ~24 points to a 25-minute game.
# Overtime ends the moment a team leads by 4, so the loser often never
# scores at all. The current table gives that outcome 1 time in 3 and
# averages ~13 added points, about a two-to-three-minute period.


def resolve_overtime(reg_total: int, red: int, margin: int) -> Tuple[int, int, Dict[str, int]]:
    """Return (winner_score, loser_score, detail) for an overtime game."""
    # Regulation must split evenly between the two tied teams.
    T = reg_total if reg_total % 2 == 0 else reg_total - 1
    each = T // 2
    R = OT_LOSER_BY_RED[red]
    loser_score = each + R
    winner_score = each + R + margin
    detail = {
        "RegTotal": T,
        "RegEach": each,
        "OT_Loser_Points": R,
        "OT_Margin": margin,
        "OT_PointsAdded": 2 * R + margin,
    }
    return winner_score, loser_score, detail


# ======================================================
# 5. INJURY DICE
# ======================================================
# Trigger: Red = Green = Blue = 6  (1 in 216 per game).
# The three white dice then resolve it.

INJ_PLAYER = {1: "C", 2: "W", 3: "P", 4: "B1", 5: "B2", 6: "SCARE"}
INJ_DURATION = {1: 1, 2: 2, 3: 4, 4: 6, 5: 10, 6: "SEASON"}


def injury_check(whites: List[int], red: int, green: int, blue: int) -> Optional[Dict[str, Any]]:
    """Return injury detail, or None if no injury check was triggered."""
    if not (red == 6 and green == 6 and blue == 6):
        return None
    w1, w2, w3 = whites
    slot = INJ_PLAYER[w2]
    return {
        "Team": "AWAY" if w1 <= 3 else "HOME",
        "Slot": slot,                       # C / W / P / B1 / B2, or SCARE
        "Scare": slot == "SCARE",
        # Duration counts the REST of the current series plus this many more.
        "Duration": None if slot == "SCARE" else INJ_DURATION[w3],
    }


# ======================================================
# 6. GV MODIFIERS
# ======================================================
# All modifiers are whole rating points, on the same scale as R*.
# Reference: Need steps once per 6 points of gap, and one step is worth
# about 10 percentage points of win probability.

FORM_WINDOW = 5      # form = sum of the last 5 series results, so it caps at +/-5
FATIGUE_FLOOR = -4
MOMENTUM_MARGIN = 10  # a previous game won by this much carries +/-1


def form_from_history(results: List[str], noise: int = 0) -> int:
    """
    form = sum of the last FORM_WINDOW series results.
      win or sweep  -> +1
      loss or swept -> -1
    Old results fall off the back, so it self-decays and self-caps at +/-5.
    (Replaces a decay-per-series rule, which cancelled out a +1 result.)
    `noise` is an optional -1/0/+1 the caller may pass in.
    """
    recent = results[-FORM_WINDOW:]
    val = sum(1 if r in ("win", "sweep") else -1 for r in recent)
    return int(clamp(val + noise, -FORM_WINDOW, FORM_WINDOW))


def fatigue_value(
    consecutive_away: int = 0,
    ot_games: int = 0,
    rested_g3: bool = False,
) -> int:
    """
    Never positive, floored at -4.
      -1 per consecutive away series past the first
      -1 per overtime game in the recent stretch
      +1 back if starters were rested in a decided game 3

    No term for stretches without a day off: every team has those, so they
    separate nobody. Resets to 0 after a home series or a bye.
    """
    f = -max(0, consecutive_away - 1) - ot_games
    if rested_g3:
        f += 1
    return int(clamp(f, FATIGUE_FLOOR, 0))


def momentum_value(prev_game_margin: Optional[int], won_prev: bool) -> int:
    """
    Within a series only. No carry unless the previous game was a blowout.
      previous game decided by 10+  ->  +1 winner, -1 loser
      anything else                 ->  0
    """
    if prev_game_margin is None or prev_game_margin < MOMENTUM_MARGIN:
        return 0
    return 1 if won_prev else -1


# ======================================================
# 7. TEAM STATE
# ======================================================

@dataclass
class TeamState:
    team_id: str
    score: float          # live rating from R*/CRIA blend, 0-100
    oq: float             # offensive quality, 0.20-0.80
    form: int = 0         # form_from_history()
    fatigue: int = 0      # fatigue_value(), <= 0
    momentum: int = 0     # momentum_value()
    injury: int = 0       # full R* drop of the active five, no blend
    spot: int = 0         # manual override for anything the model can't know

    def r_live(self) -> float:
        return clamp(
            self.score + self.form + self.fatigue + self.momentum
            + self.injury + self.spot,
            0, 100,
        )


# ======================================================
# 8. GAME SIMULATION
# ======================================================

def simulate_game(
    away: TeamState,
    home: TeamState,
    loc_points: int,
    neutral: bool = False,
    dice: Optional[List[int]] = None,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """
    Resolve one game to a final score.

    dice: optional [W1, W2, W3, Red, Green, Blue] to enter a roll by hand.
          Omit to roll.
    rng:  pass the caller's generator so a game is reproducible from its
          seed. Without it the dice came from Python's global random while
          everything downstream used a seeded one, so the same seed gave
          different games depending on what else had run first.
    """
    r = rng or random
    away_live = away.r_live()
    home_live = home.r_live()

    # --- Effective ratings: travel hits the away team only ---
    if neutral:
        away_eff, home_eff = away_live, home_live
        fav_tiebreak = "RANDOM"
    else:
        away_eff = away_live - travel_penalty(loc_points)
        home_eff = home_live
        fav_tiebreak = "HOME"

    # --- Favorite ---
    if home_eff > away_eff:
        favorite_team, dog_team = home.team_id, away.team_id
        fav_eff, dog_eff = home_eff, away_eff
    elif away_eff > home_eff:
        favorite_team, dog_team = away.team_id, home.team_id
        fav_eff, dog_eff = away_eff, home_eff
    elif fav_tiebreak == "HOME":
        favorite_team, dog_team = home.team_id, away.team_id
        fav_eff, dog_eff = home_eff, away_eff
    else:
        favorite_team, dog_team = r.choice(
            [(home.team_id, away.team_id), (away.team_id, home.team_id)]
        )
        fav_eff, dog_eff = (
            (home_eff, away_eff) if favorite_team == home.team_id else (away_eff, home_eff)
        )

    dR = fav_eff - dog_eff
    need = dice_need_from_dr(dR)

    # --- Dice ---
    if dice is None:
        whites = roll_d6(3, r)
        red, green, blue = roll_d6(3, r)
    else:
        whites = list(dice[:3])
        red, green, blue = dice[3], dice[4], dice[5]

    gsum = green + blue
    actual = sum(whites) + red_mod(red)
    delta = actual - need

    favorite_wins = delta >= 0
    winner = favorite_team if favorite_wins else dog_team
    loser = dog_team if favorite_wins else favorite_team

    # --- Margin, and whether it goes to overtime ---
    ot = False
    if delta == 0:
        if gsum in (2, 3):
            margin = 1                      # 1-point regulation win
        elif 4 <= gsum <= 8:
            margin, ot = 4, True
        elif 9 <= gsum <= 11:
            margin, ot = 5, True
        else:
            margin, ot = 6, True
    else:
        margin = margin_from_delta_and_green(abs(delta), gsum)

    # --- Total points ---
    oq_avg = (away.oq + home.oq) / 2
    base_total = 60 + (oq_avg - 0.30) * 160
    adjustment = (sum(whites) + red + green + blue) - 21
    pre_ot_total = int(round(base_total + adjustment))

    if ot:
        winner_score, loser_score, ot_detail = resolve_overtime(pre_ot_total, red, margin)
    else:
        # Derive the winner from the loser plus the margin. Taking
        # winner = total - loser instead lets integer division shift the
        # actual margin by one whenever (total - margin) is odd.
        loser_score = (pre_ot_total - margin) // 2
        winner_score = loser_score + margin
        ot_detail = {}

    if winner == home.team_id:
        home_score, away_score = winner_score, loser_score
    else:
        away_score, home_score = winner_score, loser_score

    injury = injury_check(whites, red, green, blue)

    return {
        "setup": {
            "Away": away.team_id, "Home": home.team_id,
            "Neutral": neutral, "LocPoints": loc_points,
            "AwayEff": away_eff, "HomeEff": home_eff,
            "Favorite": favorite_team, "dR": dR, "Need": need,
        },
        "dice": {
            "W1": whites[0], "W2": whites[1], "W3": whites[2],
            "Red": red, "Green": green, "Blue": blue,
            "WhiteSum": sum(whites), "RedMod": red_mod(red),
            "Actual": actual, "Delta": delta, "GreenBlueSum": gsum,
        },
        "result": {
            "Winner": winner, "Loser": loser, "Margin": margin,
            "OT": ot, "Total": away_score + home_score,
            "Away": away_score, "Home": home_score,
        },
        "ot": ot_detail,
        "injury": injury,
    }


# ======================================================
# 9. SERIES
# ======================================================

def simulate_series(
    away: TeamState, home: TeamState, loc_points: int,
    neutral: bool = False, seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Three games, always all three played. Best-of-three decides the series."""
    if seed is not None:
        random.seed(seed)
    games, wins = [], {away.team_id: 0, home.team_id: 0}
    for _ in range(3):
        g = simulate_game(away, home, loc_points, neutral)
        wins[g["result"]["Winner"]] += 1
        games.append(g)
    series_winner = max(wins, key=wins.get)
    return {
        "games": games,
        "wins": wins,
        "SeriesWinner": series_winner,
        "SeriesScore": f"{max(wins.values())}-{min(wins.values())}",
    }


# ======================================================
# 10. DEMO
# ======================================================

if __name__ == "__main__":
    random.seed(7)
    away = TeamState("AWAY01", score=56.0, oq=0.42)
    home = TeamState("HOME01", score=54.0, oq=0.38)

    print("--- Regulation example ---")
    g = simulate_game(away, home, loc_points=4, dice=[4, 3, 5, 2, 4, 3])
    print(" Need:", g["setup"]["Need"], "| Actual:", g["dice"]["Actual"],
          "| Delta:", g["dice"]["Delta"])
    print(" ", g["result"]["Winner"], "wins",
          f'{max(g["result"]["Away"], g["result"]["Home"])}-'
          f'{min(g["result"]["Away"], g["result"]["Home"])}')

    print("\n--- Overtime example (delta 0, red 4) ---")
    g = simulate_game(away, home, loc_points=4, dice=[4, 3, 4, 4, 3, 3])
    print(" ", g["ot"])
    print(" Final:", g["result"]["Away"], "-", g["result"]["Home"],
          "| Total:", g["result"]["Total"])

    print("\n--- GV modifiers ---")
    print(" form, last 5 = W W W L W:",
          form_from_history(["win","win","win","loss","win"]))
    print(" fatigue, 3rd straight away series + an OT game:",
          fatigue_value(consecutive_away=3, ot_games=1))
    print(" momentum after a 14-point win:", momentum_value(14, won_prev=True))
    hot = TeamState("HOT", score=62.0, oq=0.42,
                    form=form_from_history(["win"]*5),
                    fatigue=fatigue_value(consecutive_away=3),
                    momentum=momentum_value(14, True))
    print(" live rating 62.0 ->", hot.r_live())

    print("\n--- Injury example (red/green/blue all 6) ---")
    g = simulate_game(away, home, loc_points=4, dice=[2, 1, 3, 6, 6, 6])
    print(" ", g["injury"])
