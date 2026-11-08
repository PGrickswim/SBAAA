# SBAAA 2027 Simulation Engine

Locked 7 November 2026. Validated over 300 games with every invariant holding.

A dice roll goes in. A final score, a possession sequence, an event log and two
box scores come out, all on the real 2027 league data.

---

## The chain

```
dice  ->  final score  ->  possessions  ->  events  ->  box score
```

Each stage takes the one before it as fixed. The dice decide who wins and by
how much; nothing downstream can change that. The possession layer distributes
the score; the event layer explains each possession; the box score is read off
the events, so the log and the box score cannot disagree.

| Module | Role |
|---|---|
| `sbaaa_data.py` | Loads rosters, R*, identity, coaches from the FINAL_REALIGNED drop |
| `sbaaa_gv.py` | Offensive quality, and the R*/CRIA blend by series |
| `sbaaa_dice_engine.py` | Favorite, Need, margin, final score, overtime, injury dice, GV modifiers |
| `sbaaa_pbp.py` | Possession sequence with the check rule, bridge-sampled to the exact score |
| `sbaaa_events.py` | Shots, fouls, rebounds, assists, free throws; box score from the log |
| `sbaaa_boxscore.py` | Archetype weight table, minutes, rendering |
| `sbaaa_game_report.py` | One game rendered: score, box scores, play-by-play |
| `sbaaa_series.py` | Three games as one series: carryover, rest, travel decay |
| `sbaaa_winprob.py` | Live win probability, anchored to the dice |
| `sbaaa_validate.py` | The invariant suite |

---

## 1. Game value

A team's live rating is its R* (or an R*/CRIA blend once the season is running)
plus five modifiers, all in whole rating points on the same scale.

| Modifier | Range | How it moves |
|---|---|---|
| Form | −5 to +5 | Sum of the last five series results, +1 a win, −1 a loss. Old results fall off the back, so it self-decays and self-caps |
| Fatigue | −4 to 0 | −1 per consecutive away series past the first, −1 per overtime game, −1 per 500-mile jump with no day off. Resets after a home series or bye |
| Momentum | −1 to +1 | Only when the previous game in the series was won by 10 or more |
| Injury | negative | Built into the active-five R*, below — the full drop, no blending |
| Spot | any | Manual override for anything the model cannot know |

**Game value follows the five who actually play.** The published R* is a
full-strength number; a team missing its centre, or resting starters in a
decided game 3, is not that team. The lineup is chosen before the rating,
and R* is scaled by how that lineup compares with the normal one — exact at
full strength. South Bend at 88.7 rates 66.5 with reserves; a 2-0 series
lead can turn a +10 edge into +7.

**The R*/CRIA blend** follows the By Series table verbatim: pure R* through
series 2, even at series 21, and settling at 20/80 from series 45 onward.

---

## 2. Offensive quality

OQ sets the scoring environment and is derived from the roster, not supplied.

Starters carry 70% of the weight, Bench1 and Bench2 22%, the five reserves 8%
between them. A player's offensive tilt comes from his archetype — Scoring plus
half of Assists, against Blocks plus half of Steals. Rebounding is excluded
because it feeds both ends. A small permanent lean per program and per coach is
derived from their IDs, so it never drifts between runs.

Centred at 0.55, which puts the median team at 49 points and the range at
roughly 38 to 57.

---

## 3. The dice

Six dice: three white, then red, green, blue.

- **Favorite** is the higher live rating, with a travel penalty of
  `max(2, LocPoints + 1)` applied to the road team. Trip mileage comes from the
  schedule's `Distance Traveled A`.
- **Need** is `11 − floor(gap / 6)`, clamped to 4–11. One step is about ten
  percentage points of win probability.
- **Result**: white sum plus the red modifier (6 is +4, 1 is −3) against Need.
  The favorite wins on meeting or beating it.
- **Margin** comes from how far the roll beat or missed Need, crossed with the
  green plus blue sum.
- **Exact tie** gives a one-point win on green+blue of 2–3, otherwise overtime.

**Phase shares.** The controlled third takes about 38% of a team's points,
the even third 33% and the ceded third 29%, each with a standard deviation of
about 5 points of share. Control tilts the scoring; it never monopolises it.

**Phases.** W1, W2 and W3 say who controlled the opening, middle and ending
thirds *relative to expectation* — 1–2 the underdog, 3–4 even, 5–6 the
favorite. Control means dictating tempo, not outscoring. Phase balance is
deliberately **not** constrained to agree with the margin: a favorite can own
all three thirds and still be tied, and an underdog can own all three and lose.
The disagreement is the story.

**Overtime** is built as its own run, separate from regulation, so the teams
are genuinely level at the final buzzer of regulation and the extra period
ends on the basket that puts a team four up. Building one continuous run
instead spread the points across the whole game and left a dice-tied
regulation finishing 53-55.

Regulation ends tied at an even total. The red die sets what the
loser scores in overtime (0, 0, 2, 5, 8, 12), the winner adds the margin on top.
One continuous period, ending the instant a team leads by 4 — remaining free
throws are not taken.

**Injuries.** Red, green and blue all 6, about 1 in 216. The white dice then
set the team, the player (C/W/P/B1/B2, with a 6 being a scare) and the duration
(1, 2, 4, 6 or 10 series, or season-ending). The player is lost at a random live
moment and goes to reserve; nobody is promoted.

---

## 4. The check rule

Every change of possession must be cleared to the **check zone** — 8 feet
behind the arc, straight back only, not the sides.

| Possession starts with | Clear | Shot clock |
|---|---|---|
| Defensive rebound | Required | 24 |
| Steal or turnover | Required | 24 |
| Opponent's made basket | Required (inbound under the basket) | 24 |
| Offensive rebound | Not required | 14 |

- Not cleared by :14 is a turnover.
- Shooting while uncleared: a **miss** is live and the possession stays
  uncleared; a **make** is a dead-ball turnover and the basket is waved off.

**Timing.** 1,500 seconds, about 103 possessions, 14.5 seconds each — roughly
4 for the clear and 10.6 of offense. Clearing speed comes from the quickest
handler on the floor, so a Tempo Pusher clears in about 2.8 seconds and a Paint
Enforcer takes 5.2. That difference is worth about seven possessions a game.

**Failures.** Three separate causes, each rare:

| Cause | Rate |
|---|---|
| Pressure — the defense turns the handler back | 7% of clears are contested |
| Scramble — deflection or bobble, no steal credited | 1.7% |
| Phantom — they think they cleared and never crossed | 0.22 per game |

Failed clears run about 0.36 a game. A phantom only ever lands on a possession
the sampler already scored zero, so a waved-off basket costs nothing the totals
were counting on.

---

## 5. Possessions

Each team's per-possession points are drawn by **bridge sampling**: dynamic
programming over (possessions remaining, points remaining), conditioned on the
exact dice-determined total. The sum therefore always matches the dice.

The white dice then reorder those possessions into thirds, so a team's better
stretches land in the phases it controlled. Totals are never touched, only the
sequence.

Every sampled possession is played, and durations are scaled afterward to fill
the clock exactly. Both the final score and the final buzzer are honest.

---

## 6. Events and attribution

Rating sets how much a player produces; archetype sets what kind.

| Stat | Driven by |
|---|---|
| Usage | Rating and archetype, compressed, then sharpened per game |
| Shot type | Archetype lean, shifted by the clock and the score |
| Assists | Shot type, shifted by the shooter's archetype |
| Blocks | Defender archetype and height, tied to shot type |
| Rebounds | Archetype, height, shot location, lineup quality |
| Free throws | Rating and archetype |
| Fouls | Team total, then archetype and minutes, capped at the limit |
| Turnovers | Ten types; the three live-ball ones credit a steal |

**Points per scoring possession** are held between about 1.85 and 3.1. Merely
being arithmetically possible let a chain mark 30 scoring possessions for 42
points, which the sampler could only fill with ones — and every one-point
possession is a trip to the line. That put teams on 48 free throw attempts.

**The three-point budget.** Each team gets a budget for the night: how many
threes it will make, bounded by how many of its possessions were actually
worth three or four points, and how many it will take, set from the makes at
a percentage drawn around 39. Deciding makes and attempts independently used
to put a single game's 3P% anywhere from 0 to 100, and let a team finish
15-of-19 from three with one two-pointer. Per game it now runs 17% at the
fifth percentile to 47% at the ninety-fifth, median 36.

**Usage sharpness** is drawn per game, and a rating edge pushes toward riding
the star. The median top option takes 38% of his team's shots, the ninetieth
percentile 54%, and the top end reaches 83%.

**Media timeouts.** Two per game: the first made basket or clock stoppage at
or below 11:00 (a 7:00 break) and at or below 6:00 (3:30). They are broadcast
breaks in real time and consume no game clock, so neither the possession count
nor the score is affected. In practice they land around 10:40 and 5:36.

**Minutes.** Three on the floor for 25 minutes is 75 player-minutes. Starters
take about 60 of them, sd 10, with B1 and B2 splitting the rest. Nobody exceeds
the game length.

**Substitutions.** Three of the active five are on the floor at any moment,
and minutes are a *result* of real floor time rather than an allocation made
before the game. Subs happen at dead balls on the October rule: a bench man
must have sat 90 seconds and a man on the floor must have played 120, and the
urge to sub is stint length plus foul trouble plus the talent gap plus noise,
against a threshold that drops at a media break. About 12 a game.

**Other in-game events.** Held balls run about 1.6 a game. They are only
rendered where the ball already changes hands and the arrow points at the
team receiving it, since a held ball that does not move the ball is just
decoration. The arrow starts with the home team and alternates. End-game fouling follows NCAA patterns: down one possession a
team plays straight defence, down two it fouls to extend, and against a poor
free-throw shooting side it starts almost a minute earlier. About 0.3 a
game, and only 0.08 in one-possession games. A missed last
free throw is a live rebound, offensive about a quarter of the time. Team timeouts
are three a side in regulation — two 30-second and one full — with one more
added in overtime and anything unused carrying over. A coach spends the
full one to stop a run once it passes his own threshold, drawn from 6 to 12
unanswered points, and the short ones to settle things down or stop the
clock late. About 1.9 a team per game.

**The bonus** is two shots at the 10th team foul or the 2nd foul inside the
last two minutes. Because the dice fix the score, a foul in the bonus does
not add points: the fouled team's next scoring possession is paid out at the
line instead of from the floor. Intentional fouls work the same way.

**Removal.** A foul-out or an injury moves the player to reserve for the rest of
the game. Nobody is promoted — only C, W, P, B1 and B2 ever play, and the team
finishes with whoever is left. R1 through R5 never enter a game.

---

## 7. The series

Three games, always all three played, with state carrying between them.

- **Travel** is felt on arrival and wears off: game 1 takes the full fatigue
  hit, game 2 a third of it, game 3 none. **Both sides can arrive tired** —
  the home team may be coming back off its own road swing, which the
  schedule carries as `Distance Traveled H` and which happens in a quarter
  of all series. Trip fatigue runs 0 to −3 by distance, and is separate
  from the home-court edge, which still applies to the away team only.
- **Momentum** passes from the previous game in the series: a win by 10 or
  more, or any overtime result. Surviving an overtime lifts a team by a
  point and losing one costs a point, whatever the final margin was.
- **Injuries** carry: a player hurt in game 1 misses the rest of the series
  and the recorded number of series beyond it.
- **A decided series** rests the starters in game 3. B1 and B2 are promoted
  into the five about 85% of the time, and reserves fill the rest. Dressing
  is not the same as starting: three of the five open on the floor and two
  sit as B1/B2, and about a quarter of the time a coach opens with a reserve
  instead. In practice a promoted man starts 88% of the time he dresses.

Across 80 test series, 64% were decided after two games, splitting 35 sweeps
to 45 that went 2-1.

## 8. Win probability

`sbaaa_winprob.py` gives the favorite's live win probability. The pregame
number is read straight off the dice — P(reaching Need) — so the curve starts
at exactly what the matchup says:

| Need | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
|---|---|---|---|---|---|---|---|---|
| Favorite | 98% | 96% | 93% | 88% | 81% | 72% | 62% | 51% |

In-game it compares the current lead plus the margin still expected to be
built against the spread of what the remaining minutes can produce, with a
full-game margin sd of 9 points. The curve describes how the game *looked*,
not what the dice already decided — so an unlikely-looking win is a real
result, not an error.

## 9. League averages

Per team per game, over 300 validated games:

| | |
|---|---|
| Points | 49.0 |
| Possessions | 53 |
| Points per possession | 0.94 |
| FG% | 45.6 |
| 3P% | 35.7 |
| FT% | 77.8 |
| Rebounds | 21.6 |
| Assists | 11.4 |
| Steals | 5.3 |
| Blocks | 1.9 |
| Turnovers | 8.6 |
| Fouls | 8.7 |
| Starter minutes | 57.4 of 75 (77%) |
| Overtime | 7.7% of games |
| Foul-outs | 0.64 per game |
| Reached the bonus | 37% of team-games |

---

## 10. Validation

`python3 sbaaa_validate.py` runs 300 random matchups and checks:

- the margin equals the score difference, and no game ends tied
- overtime regulation totals are even and the loser total matches the red die
- possessions sum to the dice score
- the clock fills the game exactly and runs monotonically
- offensive rebounds never require a clear
- the box score reconciles: points from makes, threes and free throws
- makes never exceed attempts, assists never exceed makes
- no player over the foul limit or over the game length

Exit code 0 means all green.

---

## Judgement calls worth revisiting

These are mine, not derived from the source files:

1. **Four archetype columns.** `Archetype_Meaning.xlsx` gives Scoring,
   Rebounding, Assists, Blocks and Steals — those are authoritative. Three-point
   lean, free throws drawn, turnovers and shooting efficiency are estimated from
   archetype names.
2. **Usage compression.** Raw quotients gave a 5.0 Post Scorer three-quarters of
   his team's shots. The damping is tuned, not derived.
3. **Clear-failure rates.** Pressure, scramble and phantom rates were tuned to
   the per-game targets rather than reasoned from first principles.
4. **The missed-three boost.** A miss is 1.42 times likelier to be a three than
   a make is, which is what brings 3P% to 36.

## Open

- CRIA recalculation as results land
- The season results ledger
- The daily report layout
