"""
SBAAA Event Expansion
=====================

Turns each possession into the events that happened inside it, attributed to
players by archetype and rating. The box score is then READ OFF the log, so
the log and the box score can never disagree.

Attribution follows the rules as settled:
  usage      rating sets how much, archetype sets what kind
  shot type  archetype mix, shifted by the clock and the score
  assists    by shot type, shifted by the shooter's archetype
  blocks     defender archetype and height, tied to shot type
  rebounds   archetype, height, shot location, lineup quality
  free throws  rating and archetype
  and-ones   shot type and archetype
  fouls      team total, then archetype and minutes, capped
  turnovers  the ten chosen types, live-ball ones credit a steal
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import random

from sbaaa_boxscore import (Player, arch, GAME_MINUTES, assign_minutes,
                            apply_exits, BASE_FT_PCT)
from sbaaa_pbp import (Possession, START_OFFENSIVE_REBOUND, START_STEAL,
                       START_TURNOVER, START_DEFENSIVE_REBOUND,
                       START_MADE_BASKET, GAME_SECONDS)

# ======================================================
# TURNOVER TYPES
# ======================================================
# Live-ball types credit a steal to a defender. The rest do not.

LIVE_BALL = {
    "intercepted pass": 0.26,
    "stripped while dribbling": 0.22,
    "stripped on the drive": 0.14,
}
NO_STEAL = {
    "bad pass out of bounds": 0.12,
    "traveling": 0.08,
    "shot clock violation": 0.05,
    "charge": 0.05,
    "illegal screen": 0.03,
    "inbound violation": 0.03,
    "offensive goaltending": 0.02,
}
ALL_TURNOVERS = {**LIVE_BALL, **NO_STEAL}

# Media timeouts: the first made basket or clock stoppage at or below each
# mark. They are broadcast breaks in real time and consume NO game clock, so
# they never change the possession count or the score.
MEDIA_MARKS = ((11 * 60, "7:00"), (6 * 60, "3:30"))

# Team timeouts. Each coach has his own patience, drawn once per game from
# 6 to 12 unanswered points, so the same team does not always call it at the
# same moment. Trailing teams also spend them late to stop the clock.
# Three in regulation — two 30-second and one full — plus one more in
# overtime, with anything unused carrying over.
SHORT_TIMEOUTS = 2
FULL_TIMEOUTS = 1
OT_TIMEOUTS = 1
RUN_MIN, RUN_MAX = 6, 12
LATE_CLOCK = 75.0          # inside this, a trailing team burns one to stop play
LATE_DEFICIT = 8           # ...if it is within this many points
PERIODIC_TO = 0.012        # a coach calls one simply to settle things down

# --- substitutions (October rotation rules) ---
# A bench player must have sat 90s; a man on the floor must have played 120s.
# The urge to sub is stint length, plus foul trouble, plus the talent gap,
# plus a little noise, against a threshold that drops at a media break.
MIN_REST = 90.0
MIN_STINT = 120.0
SUB_THRESHOLD = 1.00
SUB_THRESHOLD_MEDIA = 0.75
FOUL_TROUBLE = {0: 0.0, 1: 0.0, 2: 0.10, 3: 0.45, 4: 1.05, 5: 2.0, 6: 2.0}

# --- held balls ---
HELD_BALL_RATE = 0.075     # tuned to about 1.5 a game (only where the ball turns over to the arrow team)

# --- end-of-game fouling (NCAA patterns) ---
# Down one possession, a team plays straight defence and takes its chances.
# Down two, it fouls to extend the game. Against a poor free-throw shooting
# side it starts earlier, because the trade is better.
HACK_ONE_POSS = 3          # within this, play defence
HACK_TWO_POSS = 7          # two possessions down: foul
HACK_WINDOW = 35.0         # seconds, for an average free-throw team
HACK_WINDOW_POOR = 95.0    # ...and against a poor one
POOR_FT_TEAM = 0.70        # below this expected team FT%, start early
HACK_MUST_FOUL = 24.0      # one possession down and without the ball inside
                           # the shot clock: playing defence just loses, the
                           # other side runs it out

FOUL_RATE_PER_POSS = 0.135        # (shooting fouls only; see NONSHOOTING_FOUL_RATE)
NONSHOOTING_FOUL_RATE = 0.019     # floor fouls, added to reach ~8-9 team fouls
BLOCK_RATE_2PT = 0.165       # tuned to ~2.2 blocks per team per game
AND_ONE_BASE = 0.045


@dataclass
class Event:
    clock: float                  # game seconds remaining
    team: str
    kind: str                     # shot_make, shot_miss, foul, ft, reb, ...
    player: Optional[str] = None
    detail: str = ""
    points: int = 0
    assist: Optional[str] = None
    secondary: Optional[str] = None   # blocker, stealer, rebounder
    pi: int = -1                      # which possession it belongs to


# ======================================================
# WEIGHTED PICKING
# ======================================================

def usage_sharpness(rng: random.Random, edge: float = 0.0) -> float:
    """
    How hard a team leans on its best option tonight. Drawn per game, so the
    same roster sometimes spreads the ball and sometimes rides one man for
    60-70% of the offense. A rating edge pushes toward riding him.
    """
    base = rng.gauss(1.05, 0.38) + min(0.45, max(-0.25, edge / 40.0))
    return max(0.45, min(2.3, base))


def pick(players: List[Player], key: str, rng: random.Random,
         use_rating: bool = False, exclude: Optional[str] = None,
         sharp: float = 1.0) -> Player:
    """Choose a player weighted by archetype, minutes, and sometimes rating."""
    pool = [p for p in players if p.name != exclude] or players
    w = []
    for p in pool:
        x = (arch(p.archetype)[key] ** 0.62) * max(0.15, p.minutes / GAME_MINUTES)
        if use_rating:
            # `sharp` only bites on shot selection: above 1 the top option
            # takes over, below 1 the ball moves.
            x = (x * (0.78 + 0.12 * p.rating)) ** sharp
        w.append(max(0.0001, x))
    return rng.choices(pool, weights=w, k=1)[0]


MISS_THREE_BOOST = 1.50      # tuned so league 3P% lands near 36


def three_point_odds(shooter: Player, clock_left: float, trailing: bool,
                     missed: bool = False) -> float:
    """
    Archetype sets the base; the clock and the score shift it.

    A miss is likelier to have been a three than a make is, which is what
    pulls 3P% down to a realistic number. Because the points were already
    fixed upstream, relabelling misses changes the percentage without
    touching the score or the possession count.
    """
    base = 0.30 * arch(shooter.archetype)["thr"]
    if missed:
        base *= MISS_THREE_BOOST
    if trailing and clock_left < 120:
        base *= 1.9                      # chasing late
    elif clock_left < 30:
        base *= 1.3
    return min(0.80, max(0.03, base))


# ======================================================
# FLOOR STATE
# ======================================================


class Floor:
    """
    Who is actually on the court. Three play at a time out of the active
    five; minutes come from real floor time rather than being assigned up
    front, so the box score matches what the log shows.
    """

    def __init__(self, players: List[Player], rng: random.Random):
        self.rng = rng
        self.all = list(players)
        starters = [p for p in players if p.role in ("C", "W", "P")]
        self.on = {p.name for p in starters[:3]} or {players[0].name}
        self.secs = {p.name: 0.0 for p in players}
        self.stint0 = {p.name: 0.0 for p in players}
        self.rest0 = {p.name: 0.0 for p in players}
        self.lost: set = set()
        self.fouls: Dict[str, int] = {p.name: 0 for p in players}

    def by_name(self, n: str) -> Optional[Player]:
        return next((p for p in self.all if p.name == n), None)

    def active(self) -> List[Player]:
        return [p for p in self.all if p.name in self.on and p.name not in self.lost]

    def bench(self) -> List[Player]:
        return [p for p in self.all
                if p.name not in self.on and p.name not in self.lost]

    def credit(self, seconds: float) -> None:
        for n in list(self.on):
            if n not in self.lost:
                self.secs[n] += seconds

    def lose(self, name: str, elapsed: float) -> Optional[Player]:
        """Remove a player for good; the next man up takes the spot."""
        self.lost.add(name)
        self.on.discard(name)
        b = self.bench()
        if b:
            pick_in = max(b, key=lambda p: p.rating)
            self.on.add(pick_in.name)
            self.stint0[pick_in.name] = elapsed
            return pick_in
        return None

    def rotate(self, elapsed: float, threshold: float):
        """One substitution decision, October rules. Returns (out, in) or None."""
        bench = [b for b in self.bench() if elapsed - self.rest0[b.name] >= MIN_REST]
        if not bench:
            return None
        best = None
        for p in self.active():
            if elapsed - self.stint0[p.name] < MIN_STINT:
                continue
            b = max(bench, key=lambda x: x.rating
                    + 0.15 * min((elapsed - self.rest0[x.name]) / 60.0, 6))
            sc = ((elapsed - self.stint0[p.name]) / 540.0
                  + FOUL_TROUBLE.get(min(self.fouls[p.name], 6), 0.0)
                  + 0.35 * (b.rating - p.rating)
                  + self.rng.gauss(0, 0.06))
            if best is None or sc > best[0]:
                best = (sc, p, b)
        if best and best[0] >= threshold:
            _, out, inn = best
            self.on.discard(out.name); self.rest0[out.name] = elapsed
            self.on.add(inn.name); self.stint0[inn.name] = elapsed
            return out, inn
        return None

    def last_resort(self) -> List[Player]:
        """
        Everyone is gone. The game is over as a contest, but the log still
        needs an actor, so the most recently lost player stands in. This
        is a forfeit in the rules and is flagged as such.
        """
        if not self.all:
            return []
        gone = [p for p in self.all if p.name in self.lost]
        return [gone[-1]] if gone else [self.all[0]]

    def minutes(self) -> Dict[str, float]:
        return {n: round(v / 60.0, 1) for n, v in self.secs.items()}


# ======================================================
# EXPANSION
# ======================================================

def expand(
    poss_list: List[Possession],
    away_name: str, home_name: str,
    away_players: List[Player], home_players: List[Player],
    rng: random.Random,
    foul_out: int = 5,
    edge: float = 0.0,
    injury: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Event], Dict[str, float]]:
    squads = {away_name: away_players, home_name: home_players}
    fouls_committed: Dict[str, int] = {}
    team_fouls = {away_name: 0, home_name: 0}
    score = {away_name: 0, home_name: 0}
    events: List[Event] = []

    # Only C/W/P/B1/B2 ever play. A player lost to a foul-out or an injury
    # goes to reserve for the rest of the game and nobody is promoted in his
    # place — the team finishes with whoever is left, down to two.
    floors = {away_name: Floor(away_players, rng),
              home_name: Floor(home_players, rng)}
    exits: Dict[str, float] = {}
    forfeit: Dict[str, bool] = {}
    total_secs = sum(p.duration for p in poss_list) or GAME_SECONDS
    arrow = home_name            # home holds the first arrow

    def lose(team: str, p: Player, clock: float, why: str) -> None:
        elapsed = total_secs - clock
        events.append(Event(clock, team, "out", p.name, why, 0))
        came = floors[team].lose(p.name, elapsed)
        exits[p.name] = elapsed / 60.0
        if came:
            events.append(Event(clock, team, "sub", came.name,
                                f"in for {p.name}", 0))

    a_sharp = usage_sharpness(rng, edge)
    h_sharp = usage_sharpness(rng, -edge)

    # --- three-point budget ---
    # Makes and attempts used to be decided independently, which let a team
    # finish 15-of-19 from three with a single two-pointer, and put the
    # per-game 3P% anywhere from 0 to 100. Each team now gets a budget for
    # the night: how many threes it will make, and how many it will take.
    # A scoring possession worth 3 becomes a made three only while the
    # budget lasts; after that it is scored as a two plus a free throw.
    def budget(players, team):
        # How many threes a team can make is bounded by how many of its
        # possessions were actually worth three or four points. The attempt
        # budget is then set from the makes, so the percentage lands in a
        # sane range instead of anywhere between 0 and 100.
        lean = (sum(arch(p.archetype)["thr"] for p in players)
                / max(1, len(players)))
        n3 = sum(1 for p in poss_list if p.team == team and p.points >= 3)
        # Most possessions worth three points were a made three; the rest
        # were a two plus the foul shot. Archetype lean tilts this, but only
        # mildly — multiplying by it directly starved threes league-wide.
        share = min(0.95, max(0.35,
                              rng.gauss(0.72 * (0.78 + 0.22 * lean), 0.12)))
        makes = int(round(n3 * share))
        pct = min(0.60, max(0.16, rng.gauss(0.415, 0.070)))
        attempts = max(makes, int(round(makes / pct)) if makes else
                       max(2, int(round(rng.gauss(5 * lean, 1.5)))))
        return {"made": 0, "att": 0, "max_made": makes, "max_att": attempts}

    three_budget = {away_name: budget(away_players, away_name),
                    home_name: budget(home_players, home_name)}

    # Timeouts: a per-coach run threshold, and a budget each.
    patience = {away_name: rng.randint(RUN_MIN, RUN_MAX),
                home_name: rng.randint(RUN_MIN, RUN_MAX)}
    # Two kinds, tracked separately. A full timeout is the one a coach
    # spends to stop a run; a 30-second is what he uses to stop the clock
    # or settle things down.
    short_to = {away_name: SHORT_TIMEOUTS, home_name: SHORT_TIMEOUTS}
    full_to = {away_name: FULL_TIMEOUTS, home_name: FULL_TIMEOUTS}
    ot_added = {away_name: False, home_name: False}

    def take(team: str, want_full: bool, clock_left: float) -> Optional[str]:
        """Spend a timeout, preferring the kind that suits the moment."""
        order = (("full", full_to), ("30", short_to)) if want_full else \
                (("30", short_to), ("full", full_to))
        for label, pool in order:
            if pool[team] > 0:
                pool[team] -= 1
                return label
        return None

    def left(team: str) -> int:
        return short_to[team] + full_to[team]
    run = {away_name: 0, home_name: 0}
    late_used = {away_name: False, home_name: False}
    hack_ft = {away_name: None, home_name: None}
    late_fouls = {away_name: 0, home_name: 0}

    def in_bonus(team: str, clock_left: float) -> bool:
        """Two shots at the 10th team foul, or the 2nd inside the last two."""
        return (team_fouls[team] >= 10
                or (clock_left <= 120 and late_fouls[team] >= 2))

    # The injury dice (red/green/blue all 6) already named the team, the
    # slot and the length. All that is decided here is WHEN in the game it
    # happens, which is a random live moment.
    inj_at = None
    if injury and not injury.get("Scare"):
        inj_at = rng.randrange(max(1, int(len(poss_list) * 0.08)),
                               max(2, int(len(poss_list) * 0.92)))
    scare_at = None
    if injury and injury.get("Scare"):
        scare_at = rng.randrange(1, max(2, len(poss_list) - 1))

    def injured_player(team: str) -> Optional[Player]:
        slot = injury.get("Slot") if injury else None
        return next((p for p in squads[team] if p.role == slot), None)

    cur_pi = -1

    def stamp():
        for e in events:
            if e.pi < 0:
                e.pi = cur_pi

    for pi, ps in enumerate(poss_list):
        stamp()
        cur_pi = pi
        dteam = home_name if ps.team == away_name else away_name
        # Never fall back to the full squad: `or squads[...]` resurrected
        # players who had already fouled out or been injured. A side that
        # runs out finishes with whoever is left — down to one — and a team
        # with nobody left has forfeited.
        off = floors[ps.team].active() or floors[ps.team].last_resort()
        deff = floors[dteam].active() or floors[dteam].last_resort()
        sharp = a_sharp if ps.team == away_name else h_sharp
        for t in (ps.team, dteam):
            if not floors[t].active() and not forfeit.get(t):
                forfeit[t] = True
                events.append(Event(
                    max(0.0, ps.clock_start - ps.duration), t, "forfeit",
                    None, f"{t} has no eligible players left — forfeit", 0))
        # Overtime adds one timeout per side, on top of whatever was left.
        if ps.period == 1:
            for t in (away_name, home_name):
                if not ot_added[t]:
                    ot_added[t] = True
                    short_to[t] += OT_TIMEOUTS

        elapsed = total_secs - max(0.0, ps.clock_start - ps.duration)
        floors[ps.team].credit(ps.duration)
        floors[dteam].credit(ps.duration)
        clock = ps.clock_start - ps.duration
        trailing = score[ps.team] < score[dteam]

        # --- a clear that failed, or one that never happened ---
        failed = any("failed to clear" in e for e in ps.events)
        phantom = any("never crossed" in e for e in ps.events)
        if failed:
            handler = pick(off, "ast", rng)
            events.append(Event(clock, ps.team, "turnover", handler.name,
                                "failed to clear by :14", 0))
            continue
        if phantom:
            shooter = pick(off, "use", rng, use_rating=True, sharp=sharp)
            if "waved off" in " ".join(ps.events):
                events.append(Event(clock, ps.team, "turnover", shooter.name,
                                    "basket waved off, never cleared", 0))
            else:
                events.append(Event(clock, ps.team, "shot_miss", shooter.name,
                                    "shot while uncleared", 0))
                rb = pick(deff if rng.random() > 0.28 else off, "drb", rng)
                events.append(Event(clock, rb.name and (
                    ps.team if rb in off else dteam), "rebound", rb.name,
                    "live ball, still uncleared", 0))
            continue

        # --- the injury moment ---
        if inj_at is not None and pi == inj_at:
            iteam = away_name if injury["Team"] == "AWAY" else home_name
            hurt = injured_player(iteam)
            if hurt and hurt.name in floors[iteam].on:
                dur = injury["Duration"]
                howlong = ("out for the season" if dur == "SEASON"
                           else f"out {dur} series")
                lose(iteam, hurt, clock, f"injured, {howlong}")
            inj_at = None
        if scare_at is not None and pi == scare_at:
            iteam = away_name if injury["Team"] == "AWAY" else home_name
            events.append(Event(clock, iteam, "scare", None,
                                "injury scare, stays in the game", 0))
            scare_at = None

        # --- a floor foul can happen on any possession ---
        if rng.random() < NONSHOOTING_FOUL_RATE:
            # No fallback to the full squad here: a man already at the limit
            # cannot pick up another foul.
            elig = [p for p in deff
                    if fouls_committed.get(p.name, 0) < foul_out]
            if elig:
                fl = pick(elig, "blk", rng)
                fouls_committed[fl.name] = fouls_committed.get(fl.name, 0) + 1
                floors[dteam].fouls[fl.name] = fouls_committed[fl.name]
                team_fouls[dteam] += 1
                if clock <= 120:
                    late_fouls[dteam] += 1
                inbonus = in_bonus(dteam, clock)
                d = "floor foul" + (" (bonus)" if inbonus else "")
                events.append(Event(clock, dteam, "foul", fl.name, d, 0))
                # In the bonus a non-shooting foul is a trip to the line.
                # The points are fixed by the dice, so the fouled team's
                # next scoring possession is paid out as free throws.
                if inbonus:
                    hack_ft[ps.team] = (fl.name, dteam)
                if fouls_committed[fl.name] >= foul_out:
                    lose(dteam, fl, clock, f"fouled out ({foul_out})")

        # --- the clear ---
        # Every change of possession has to be taken back behind the check
        # line, so it belongs in the log as its own action.
        if ps.cleared:
            handler = pick(off, "ast", rng)
            secs = next((e for e in ps.events if "cleared in" in e), "")
            events.append(Event(ps.clock_start, ps.team, "clear", handler.name,
                                secs or "cleared", 0))

        pts = ps.points

        # --- scoreless possession: a miss, or a turnover ---
        if pts == 0:
            nxt = poss_list[pi + 1] if pi + 1 < len(poss_list) else None
            keeps_ball = nxt is not None and nxt.start == START_OFFENSIVE_REBOUND
            stolen = nxt is not None and nxt.start == START_STEAL
            # The chain already decided how this possession ended. A team
            # that keeps the ball cannot have turned it over, and a steal
            # must be a turnover — otherwise the log contradicts itself.
            if stolen or (not keeps_ball and rng.random() < 0.28):
                pool = LIVE_BALL if stolen else ALL_TURNOVERS
                kind, wt = zip(*pool.items())
                t = rng.choices(kind, weights=wt, k=1)[0]
                giver = pick(off, "tov", rng)
                ev = Event(clock, ps.team, "turnover", giver.name, t, 0)
                if t in LIVE_BALL:
                    ev.secondary = pick(deff, "stl", rng).name
                events.append(ev)
            else:
                shooter = pick(off, "use", rng, use_rating=True, sharp=sharp)
                mb = three_budget[ps.team]
                is_three = (mb["att"] < mb["max_att"]
                            and rng.random() < 0.72)
                if is_three:
                    mb["att"] += 1
                blocked = rng.random() < (BLOCK_RATE_2PT if not is_three
                                          else BLOCK_RATE_2PT * 0.22)
                ev = Event(clock, ps.team, "shot_miss", shooter.name,
                           "three" if is_three else "two", 0)
                if blocked:
                    ev.secondary = pick(deff, "blk", rng).name
                    ev.detail += ", blocked"
                events.append(ev)
                oreb = keeps_ball
                rteam = ps.team if oreb else dteam
                # Read the floor again: `off`/`deff` were captured at the
                # top of the possession, so a player removed part-way
                # through could still come down with the rebound.
                pool = (floors[rteam].active()
                        or floors[rteam].last_resort())
                rb = pick(pool, "orb" if oreb else "drb", rng)
                events.append(Event(clock, rteam, "rebound", rb.name,
                                    "offensive" if oreb else "defensive", 0))
            continue

        # --- a scoring possession ---
        shooter = pick(off, "use", rng, use_rating=True, sharp=sharp)
        tb = three_budget[ps.team]
        is_three = tb["made"] < tb["max_made"] and rng.random() < 0.90

        # Fouled deliberately on the previous stop: this one is free throws.
        if hack_ft.get(ps.team) and pts in (1, 2):
            fname, fteam = hack_ft[ps.team]
            hack_ft[ps.team] = None
            for i in range(2):
                good = i < pts
                events.append(Event(
                    clock, ps.team, "ft", shooter.name,
                    ("good" if good else "missed")
                    + f" (bonus, foul on {fname})", 1 if good else 0))
            if pts < 2:
                # Decide the team first, then read that team's CURRENT
                # floor — the captured lists can hold a player who was
                # removed earlier in this same possession.
                off_board = rng.random() < 0.24
                rteam = ps.team if off_board else dteam
                pool = floors[rteam].active() or floors[rteam].last_resort()
                rb = pick(pool, "orb" if off_board else "drb", rng)
                events.append(Event(
                    clock, rteam, "rebound", rb.name,
                    ("offensive" if off_board else "defensive")
                    + ", off the missed free throw", 0))
            score[ps.team] += pts
            continue
        w = arch(shooter.archetype)

        if pts == 1:
            # a split trip to the line
            events.extend(_foul_and_ft(clock, ps.team, dteam, shooter, deff,
                                       2, 1, rng, team_fouls, fouls_committed,
                                       foul_out))
        elif pts in (2, 3) and not (pts == 3 and is_three):
            if pts == 2 and rng.random() < 0.14:
                events.extend(_foul_and_ft(clock, ps.team, dteam, shooter,
                                           deff, 2, 2, rng, team_fouls,
                                           fouls_committed, foul_out))
            elif pts == 3:
                # Three points without a three: a two and the foul shot.
                # Rendering it as a "two" worth three broke the arithmetic
                # 2*(FGM-3PM) + 3*3PM + FTM = PTS.
                events.append(_make(clock, ps.team, shooter, off, 2, False, rng))
                events.extend(_foul_and_ft(clock, ps.team, dteam, shooter,
                                           deff, 1, 1, rng, team_fouls,
                                           fouls_committed, foul_out,
                                           and_one=True))
            else:
                events.append(_make(clock, ps.team, shooter, off, pts,
                                    False, rng))
        elif pts == 3:
            if is_three:
                tb["made"] += 1; tb["att"] += 1
                events.append(_make(clock, ps.team, shooter, off, 3, True, rng))
            else:
                # budget spent: a two and the foul shot instead
                events.append(_make(clock, ps.team, shooter, off, 2, False, rng))
                events.extend(_foul_and_ft(clock, ps.team, dteam, shooter,
                                           deff, 1, 1, rng, team_fouls,
                                           fouls_committed, foul_out,
                                           and_one=True))
        else:   # 4 — a three and the foul shot, or a two and three free throws
            if is_three:
                tb["made"] += 1; tb["att"] += 1
                events.append(_make(clock, ps.team, shooter, off, 3, True, rng))
                events.extend(_foul_and_ft(clock, ps.team, dteam, shooter,
                                           deff, 1, 1, rng, team_fouls,
                                           fouls_committed, foul_out,
                                           and_one=True))
            else:
                # budget spent: two from the floor, two from the line
                events.append(_make(clock, ps.team, shooter, off, 2, False, rng))
                events.extend(_foul_and_ft(clock, ps.team, dteam, shooter,
                                           deff, 2, 2, rng, team_fouls,
                                           fouls_committed, foul_out,
                                           and_one=True))

        if clock <= 120:
            late_fouls[dteam] += sum(
                1 for e in events[-6:]
                if e.kind == "foul" and e.team == dteam and e.pi < 0)

        # A foul-out raised inside a shooting-foul trip still has to remove
        # the player. _foul_and_ft only LOGS it, so the removal happens here;
        # the placeholder is dropped first so the log shows it once.
        pending = [e for e in events[-6:]
                   if e.kind == "out" and e.player not in exits]
        for e in pending:
            gone = next((q for q in squads[e.team] if q.name == e.player), None)
            events.remove(e)
            if gone:
                lose(e.team, gone, e.clock, e.detail)

        score[ps.team] += pts

        # --- the run, and whether anyone calls time ---
        if pts > 0:
            run[ps.team] += pts
            run[dteam] = 0
        clock_after = max(0.0, ps.clock_start - ps.duration)

        # A coach stops a run against his team once it passes his threshold.
        for t, opp in ((ps.team, dteam), (dteam, ps.team)):
            if run[opp] >= patience[t] and left(t) > 0 and clock_after > 5:
                kind = take(t, True, clock_after)
                events.append(Event(
                    clock_after, t, "timeout", None,
                    f"{kind} timeout, {t} ({run[opp]}-0 run, "
                    f"{left(t)} left)", 0))
                run[opp] = 0
                break
        else:
            for t in (ps.team, dteam):
                if left(t) > 0 and clock_after > 90 and rng.random() < PERIODIC_TO:
                    kind = take(t, False, clock_after)
                    events.append(Event(clock_after, t, "timeout", None,
                                        f"{kind} timeout, {t} "
                                        f"({left(t)} left)", 0))
                    break

        # --- held ball ---
        # A held ball changes who has the ball, so it can only be rendered
        # where the chain already hands it over AND the arrow points at the
        # team about to receive. Logging one anywhere else was cosmetic:
        # 249 in 150 games, none of which actually moved the ball.
        nxt_p = poss_list[pi + 1] if pi + 1 < len(poss_list) else None
        if (nxt_p is not None and nxt_p.team == dteam and arrow == dteam
                and clock_after > 2 and rng.random() < HELD_BALL_RATE):
            a_pl = pick(off, "orb", rng)
            d_pl = pick(deff, "orb", rng)
            events.append(Event(
                clock_after, dteam, "held", None,
                f"held ball, {a_pl.name} and {d_pl.name} — "
                f"arrow gives it to {dteam}", 0))
            arrow = ps.team

        # --- intentional foul: NCAA end-game patterns ---
        deficit = score[ps.team] - score[dteam]
        ft_ability = (sum(0.56 + 0.055 * p.rating for p in off)
                      / max(1, len(off)))
        window = HACK_WINDOW_POOR if ft_ability < POOR_FT_TEAM else HACK_WINDOW
        if deficit > HACK_TWO_POSS:
            window *= 1.6          # three scores down: no choice but to foul
        # Down one possession and without the ball inside 24 seconds, a
        # team has to foul — the other side simply runs out the shot clock.
        must_foul = (0 < deficit <= HACK_ONE_POSS
                     and 0 < clock_after <= HACK_MUST_FOUL)
        if must_foul or (HACK_ONE_POSS < deficit and 0 < clock_after <= window):
            elig = [p for p in floors[dteam].active()
                    if fouls_committed.get(p.name, 0) < foul_out]
            if elig and rng.random() < (0.90 if must_foul else 0.55):
                fl = pick(elig, "blk", rng)
                fouls_committed[fl.name] = fouls_committed.get(fl.name, 0) + 1
                floors[dteam].fouls[fl.name] = fouls_committed[fl.name]
                team_fouls[dteam] += 1
                events.append(Event(clock_after, dteam, "foul", fl.name,
                                    "intentional foul, stopping the clock", 0))
                # In the bonus it is a trip to the line. The points are
                # already fixed by the dice, so the NEXT possession for the
                # fouled team is paid out as free throws rather than a field
                # goal — the score is untouched, only how it was scored.
                if clock_after <= 120:
                    late_fouls[dteam] += 1
                if in_bonus(dteam, clock_after):
                    hack_ft[ps.team] = (fl.name, dteam)
                if fouls_committed[fl.name] >= foul_out:
                    lose(dteam, fl, clock_after, f"fouled out ({foul_out})")

        # --- substitutions at the dead ball ---
        dead = pts > 0 or any(e.kind in ("foul", "ft", "turnover", "held",
                                         "timeout", "out")
                              for e in events[-4:])
        if dead:
            thr = SUB_THRESHOLD
            for t in (ps.team, dteam):
                swap = floors[t].rotate(elapsed, thr)
                if swap:
                    out_p, in_p = swap
                    events.append(Event(
                        clock_after, t, "sub", in_p.name,
                        f"in for {out_p.name}"
                        + (" (foul trouble)"
                           if floors[t].fouls[out_p.name] >= 3
                           else " (rotation)"), 0))

        # Late, a team within reach spends one to stop the clock.
        for t, opp in ((ps.team, dteam), (dteam, ps.team)):
            if (not late_used[t] and left(t) > 0
                    and 0 < clock_after <= LATE_CLOCK
                    and 0 < score[opp] - score[t] <= LATE_DEFICIT):
                kind = take(t, False, clock_after)
                late_used[t] = True
                events.append(Event(
                    clock_after, t, "timeout", None,
                    f"{kind} timeout, {t} (stopping the clock, "
                    f"{left(t)} left)", 0))
                break

        # --- media timeout ---
        # Fires on the first made basket or stoppage at or below the mark.
    stamp()

    # Minutes are now a RESULT of the game, read off real floor time, not an
    # allocation made before it. A player who fouled out simply stops being
    # credited at that moment.
    for team, fl in floors.items():
        mins = fl.minutes()
        for pl in fl.all:
            pl.minutes = mins.get(pl.name, 0.0)

    return _insert_media(events), exits


STOPPAGES = {"shot_make", "foul", "ft", "turnover", "out"}


def _insert_media(events: List[Event]) -> List[Event]:
    """
    Place the media timeouts over the finished log: the first made basket or
    clock stoppage at or below each mark. Done as a pass rather than inside
    the possession loop, where scoreless possessions short-circuit before the
    check and one of the two marks could be missed.

    These are real-time breaks. They consume no game clock, so the possession
    count and the score are untouched.
    """
    out: List[Event] = []
    pending = list(MEDIA_MARKS)
    for e in events:
        out.append(e)
        while pending and e.clock <= pending[0][0] and e.kind in STOPPAGES:
            mark, length = pending.pop(0)
            out.append(Event(e.clock, "", "media", None,
                             f"media timeout ({length}) at the "
                             f"{mark // 60}:00 mark", 0))
    return out


def _make(clock: float, team: str, shooter: Player, off: List[Player],
          pts: int, three: bool, rng: random.Random) -> Event:
    """A made field goal, assisted or not by shot type and archetype."""
    base = 0.62 if three else 0.46
    base *= 1.0 / max(0.55, arch(shooter.archetype)["use"] * 0.75)
    ev = Event(clock, team, "shot_make", shooter.name,
               "three" if three else "two", pts)
    if len(off) > 1 and rng.random() < min(0.74, base):
        ev.assist = pick(off, "ast", rng, exclude=shooter.name).name
    return ev


def _foul_and_ft(clock: float, team: str, dteam: str, shooter: Player,
                 deff: List[Player], attempts: int, makes: int,
                 rng: random.Random, team_fouls: Dict[str, int],
                 committed: Dict[str, int], foul_out: int,
                 and_one: bool = False) -> List[Event]:
    """A shooting foul and the trip to the line that follows."""
    out: List[Event] = []
    eligible = [p for p in deff if committed.get(p.name, 0) < foul_out]
    detail = "shooting foul" + (" (and one)" if and_one else "")
    if eligible:
        fouler = pick(eligible, "blk", rng)
        committed[fouler.name] = committed.get(fouler.name, 0) + 1
        team_fouls[dteam] += 1
        out.append(Event(clock, dteam, "foul", fouler.name, detail, 0))
        if committed[fouler.name] >= foul_out:
            out.append(Event(clock, dteam, "out", fouler.name,
                             f"fouled out ({foul_out})", 0))
    else:
        # Everyone still on the floor is at the limit. The foul is charged
        # to the team rather than a player — the free throws must still be
        # taken, or their points would disappear from the game.
        team_fouls[dteam] += 1
        out.append(Event(clock, dteam, "foul", None,
                         detail + ", charged to the team", 0))
    for i in range(attempts):
        good = i < makes
        out.append(Event(clock, team, "ft", shooter.name,
                         "good" if good else "missed", 1 if good else 0))
    # A missed last free throw is live: either team can rebound it.
    if makes < attempts:
        if rng.random() < 0.24:
            out.append(Event(clock, team, "rebound", shooter.name,
                             "offensive, off the missed free throw", 0))
        elif deff:
            out.append(Event(clock, dteam, "rebound",
                             pick(deff, "drb", rng).name,
                             "defensive, off the missed free throw", 0))
    return out


# ======================================================
# BOX SCORE FROM THE LOG
# ======================================================

BOX_COLS = ["MIN", "PTS", "FGM", "FGA", "3PM", "3PA", "FTM", "FTA",
            "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF"]


def box_from_events(events: List[Event], players: List[Player],
                    team: str) -> Dict[str, Dict[str, float]]:
    box = {p.name: {c: 0.0 for c in BOX_COLS} for p in players}
    for p in players:
        box[p.name]["MIN"] = p.minutes
    names = set(box)

    for e in events:
        if e.player in names:
            b = box[e.player]
            if e.kind == "shot_make":
                b["FGA"] += 1; b["FGM"] += 1; b["PTS"] += e.points
                if e.detail.startswith("three"):
                    b["3PA"] += 1; b["3PM"] += 1
            elif e.kind == "shot_miss":
                b["FGA"] += 1
                if e.detail.startswith("three"):
                    b["3PA"] += 1
            elif e.kind == "ft":
                b["FTA"] += 1
                # the detail may carry a note (a bonus trip names the foul)
                if e.detail.startswith("good"):
                    b["FTM"] += 1; b["PTS"] += 1
            elif e.kind == "rebound" and e.team == team:
                if e.detail.startswith("offensive"):
                    b["OREB"] += 1
                else:
                    b["DREB"] += 1
            elif e.kind == "turnover":
                b["TOV"] += 1
            elif e.kind == "foul":
                b["PF"] += 1
        if e.assist in names:
            box[e.assist]["AST"] += 1
        if e.secondary in names:
            if e.kind == "turnover":
                box[e.secondary]["STL"] += 1
            elif e.kind == "shot_miss":
                box[e.secondary]["BLK"] += 1

    for b in box.values():
        b["REB"] = b["OREB"] + b["DREB"]
    return box


def render(team: str, players: List[Player],
           box: Dict[str, Dict[str, float]]) -> str:
    cols = [c for c in BOX_COLS if c not in ("OREB", "DREB")]
    head = f"{team:<20}{'POS':<5}" + "".join(f"{c:>5}" for c in cols)
    lines = [head, "-" * len(head)]
    tot = {c: 0.0 for c in cols}
    for p in players:
        b = box[p.name]
        row = f"{p.name:<20}{p.role:<5}"
        for c in cols:
            tot[c] += b[c]
            row += f"{b[c]:>5.1f}" if c == "MIN" else f"{b[c]:>5.0f}"
        lines.append(row)
    lines.append("-" * len(head))
    lines.append(f"{'TEAM':<20}{'':<5}" + "".join(
        f"{tot[c]:>5.0f}" for c in cols))
    return "\n".join(lines)


if __name__ == "__main__":
    from sbaaa_data import League
    from sbaaa_pbp import build_possessions

    rng = random.Random(11)
    lg = League()
    A, H = "South Bend IN", "Naperville IL"
    ap, hp = lg.active_five(A), lg.active_five(H)
    assign_minutes(ap); assign_minutes(hp)

    poss = build_possessions(A, H, ap, hp, 52, 47, [4, 3, 5], A, rng)
    inj = {"Team": "HOME", "Slot": "W", "Scare": False, "Duration": 6}
    ev, exits = expand(poss, A, H, ap, hp, rng, edge=20.0, injury=inj)
    # adjust (never redraw) minutes for anyone lost to a foul-out
    apply_exits(ap, exits); apply_exits(hp, exits)

    abox = box_from_events(ev, ap, A)
    hbox = box_from_events(ev, hp, H)
    print(render(A, ap, abox)); print()
    print(render(H, hp, hbox))
    print(f"\nevents: {len(ev)}  |  "
          f"{A} {sum(b['PTS'] for b in abox.values()):.0f}, "
          f"{H} {sum(b['PTS'] for b in hbox.values()):.0f}")
    print("\nfirst 12 events:")
    for e in ev[:12]:
        m, s = divmod(max(0.0, e.clock), 60)
        extra = f" (ast {e.assist})" if e.assist else (
            f" (by {e.secondary})" if e.secondary else "")
        print(f"  {int(m):>2}:{s:04.1f}  {e.team:<16} {e.kind:<10} "
              f"{str(e.player):<18} {e.detail}{extra}")


# ======================================================
# PLAY-BY-PLAY RENDERING
# ======================================================
# Events inside a possession all carry that possession's end clock, so the
# raw log shows several things at the same time. For a readable play-by-play
# they are spread back across the possession and given a running score.

def _spread_clocks(events: List[Event], total: float,
                   poss_list=None) -> List[tuple]:
    """
    Give each event its own time, in order.

    Events are grouped by the possession they belong to and laid out across
    that possession's own span, so a clear sits at the top of its possession
    and the shot and rebound land near the end. Grouping by identical clock
    instead used to crush four events into a hundredth of a second, because
    a clear carries its possession's START time and everything else carries
    the END.
    """
    spans = {}
    if poss_list:
        for i, p in enumerate(poss_list):
            spans[i] = (p.clock_start, max(0.0, p.clock_start - p.duration))

    out = []
    i = 0
    while i < len(events):
        j = i
        key = events[i].pi
        while j + 1 < len(events) and events[j + 1].pi == key:
            j += 1
        group = events[i:j + 1]
        n = len(group)
        if key in spans:
            hi, lo = spans[key]
        else:
            hi = lo = events[i].clock
        span = max(0.0, hi - lo)
        # A possession is not evenly busy. The clear happens at the top and
        # takes the seconds it took; everything else — the shot, the foul,
        # the rebound — happens at the end, about a second apart. Spreading
        # evenly instead left a shot and its rebound five seconds apart.
        head = []
        rest = list(group)
        if rest and rest[0].kind == "clear":
            secs = 3.5
            try:
                secs = float(rest[0].detail.replace("cleared in ", "")
                             .replace("s", ""))
            except ValueError:
                pass
            head = [[max(lo, hi - min(secs, span * 0.9)), rest.pop(0)]]
        m = len(rest)
        gap = min(1.3, max(0.15, (span * 0.55) / max(1, m)))
        for k, e in enumerate(rest):
            out_t = lo + gap * (m - 1 - k)
            head.append([out_t, e])
        out.extend(head)
        i = j + 1

    eps = 0.01
    for k in range(1, len(out)):
        if out[k][0] >= out[k - 1][0]:
            out[k][0] = max(0.0, out[k - 1][0] - eps)
    return [(t, e) for t, e in out]


def _clock(t: float) -> str:
    t = max(0.0, t)
    m, s = divmod(t, 60)
    return f"{int(m):>2}:{s:05.2f}"


def _short(team: str) -> str:
    base = team.rsplit(" ", 1)[0]
    return base


def describe(e: Event, away: str, home: str) -> str:
    """One line of plain broadcast English for a single event."""
    who = e.player or ""
    d = e.detail

    if e.kind == "shot_make":
        shot = "three" if d.startswith("three") else "two"
        s = f"{who} makes a {shot}"
        if e.assist:
            s += f", assist {e.assist}"
        return s
    if e.kind == "shot_miss":
        if "uncleared" in d:
            return f"{who} shoots without clearing — miss, ball still live"
        shot = "three" if d.startswith("three") else "two"
        s = f"{who} misses a {shot}"
        if e.secondary:
            s += f", blocked by {e.secondary}"
        return s
    if e.kind == "ft":
        note = ""
        if "(" in d:
            note = " " + d[d.index("("):]
        return (f"{who} free throw "
                f"{'good' if d.startswith('good') else 'missed'}{note}")
    if e.kind == "rebound":
        if "free throw" in d:
            kind = "offensive" if d.startswith("offensive") else "defensive"
            return f"{who} rebounds the missed free throw ({kind})"
        if "uncleared" in d:
            return f"{who} rebounds — the possession is still uncleared"
        return f"{who} {d} rebound"
    if e.kind == "turnover":
        if "waved off" in d:
            return f"{who} scores without clearing — waved off, turnover"
        if "failed to clear" in d:
            return f"{who} fails to clear by :14 — turnover"
        if e.secondary:
            return f"{who} turnover ({d}), stolen by {e.secondary}"
        return f"{who} turnover ({d})"
    if e.kind == "foul":
        return f"{who} — {d}"
    if e.kind == "out":
        return f"{who} {d}"
    if e.kind == "sub":
        return f"{who} {d}"
    if e.kind == "clear":
        secs = d.replace("cleared in ", "").replace("s", "")
        try:
            return f"{who} clears the check line ({float(secs):.1f}s)"
        except ValueError:
            return f"{who} clears the check line"
    if e.kind == "held":
        return d
    if e.kind == "timeout":
        return d
    if e.kind == "media":
        return d
    if e.kind == "scare":
        return d
    return d


def render_pbp(events: List[Event], away: str, home: str,
               total_seconds: float, poss_list=None) -> str:
    """The full play-by-play: clock, team, what happened, running score."""
    score = {away: 0, home: 0}
    a, h = _short(away), _short(home)
    rows = []
    for t, e in _spread_clocks(events, total_seconds, poss_list):
        if e.points and e.team in score:
            score[e.team] += e.points
        tm = _short(e.team) if e.team in (away, home) else ""
        rows.append((_clock(t), tm, describe(e, away, home),
                     f"{score[away]}-{score[home]}"))

    wteam = max([len(r[1]) for r in rows] + [4])
    wplay = max([len(r[2]) for r in rows] + [4])
    head = (f"{'CLOCK':>8}  {'TEAM':<{wteam}}  {'PLAY':<{wplay}}  "
            f"{a} - {h}")
    lines = [head, "-" * len(head)]
    for c, tm, play, sc in rows:
        lines.append(f"{c}  {tm:<{wteam}}  {play:<{wplay}}  {sc}")
    return "\n".join(lines)
