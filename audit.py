"""Wide audit of the game simulation: logical consistency, not just totals."""
import random, statistics as st
from collections import Counter
from sbaaa_data import League
from sbaaa_gv import all_team_oq, gv_base
from sbaaa_dice_engine import TeamState, simulate_game, fatigue_value
from sbaaa_pbp import build_possessions, GAME_SECONDS
from sbaaa_events import expand, box_from_events, _spread_clocks
from sbaaa_boxscore import assign_minutes

FAIL = Counter()
def bad(k): FAIL[k] += 1

def audit(n=400, seed=2027):
    lg = League()
    oq = all_team_oq(lg.roster, lg.rstar, lg.coaches).set_index("Team").OQ.to_dict()
    teams = list(lg.rstar.Team); pr = random.Random(seed)
    stats = Counter(); games = 0
    for i in range(n):
        A, H = pr.sample(teams, 2); rng = random.Random(i)
        ap, hp = lg.active_five(A), lg.active_five(H)
        assign_minutes(ap); assign_minutes(hp)
        g = simulate_game(
            TeamState(lg.code(A), gv_base(pr.randint(1,52), lg.r_star(A), None), oq[A],
                      fatigue=fatigue_value(consecutive_away=pr.randint(0,4))),
            TeamState(lg.code(H), gv_base(1, lg.r_star(H), None), oq[H]), pr.randint(1,10))
        apts, hpts = g["result"]["Away"], g["result"]["Home"]
        ot = g["result"]["OT"]; ots = pr.uniform(110,190) if ot else 0.0
        d = None
        if ot:
            d = dict(g["ot"]); d["OT_Margin"] = g["result"]["Margin"]
        fav = A if g["setup"]["Favorite"] == lg.code(A) else H
        poss = build_possessions(A,H,ap,hp,apts,hpts,
            [g["dice"][k] for k in ("W1","W2","W3")], fav, rng,
            ot_seconds=ots, ot_detail=d)
        ev, ex = expand(poss, A, H, ap, hp, rng, foul_out=6 if ot else 5)
        games += 1
        T = GAME_SECONDS + ots
        names = {A: {p.name for p in ap}, H: {p.name for p in hp}}
        boxes = {t: box_from_events(ev, pl, t) for pl, t in ((ap,A),(hp,H))}

        # ---- totals ----
        for t, target in ((A, apts), (H, hpts)):
            if sum(x["PTS"] for x in boxes[t].values()) != target: bad("box != dice")
            b = {k: sum(x[k] for x in boxes[t].values()) for k in
                 ("PTS","FGM","FGA","3PM","3PA","FTM","FTA","AST","REB","PF")}
            if 2*(b["FGM"]-b["3PM"]) + 3*b["3PM"] + b["FTM"] != b["PTS"]: bad("points don't reconcile")
            if b["FGM"] > b["FGA"] or b["3PM"] > b["3PA"] or b["FTM"] > b["FTA"]: bad("makes > attempts")
            if b["3PM"] > b["FGM"] or b["3PA"] > b["FGA"]: bad("threes exceed field goals")
            if b["AST"] > b["FGM"]: bad("assists > makes")

        # ---- event-level logic ----
        gone = set(); onfloor_err = 0
        prev_ev = None
        misses = rebs = 0
        for j, e in enumerate(ev):
            if e.kind == "out": gone.add(e.player)
            if e.kind == "sub": gone.discard(e.player)
            if e.player in gone and e.kind in ("shot_make","shot_miss","ft","rebound","turnover","foul"):
                if not any(x.kind == "forfeit" for x in ev[:j]): bad("removed player acted")
            if e.kind == "shot_make" and e.assist:
                if e.assist == e.player: bad("assisted own basket")
                if e.assist not in names[e.team]: bad("assist from the other team")
            if e.kind == "shot_miss":
                misses += 1
                if e.secondary and e.secondary in names[e.team]: bad("blocked by a teammate")
            if e.kind == "rebound": rebs += 1
            if e.kind == "turnover" and e.secondary:
                if e.secondary in names[e.team]: bad("stole from themselves")
            if e.kind == "ft":
                # a free throw must trace to a foul: in this possession, the
                # one before it, or named in the event (a bonus trip)
                if "bonus" not in e.detail:
                    back = [x for x in ev[max(0,j-8):j] if x.kind == "foul"]
                    if not back: bad("free throw with no foul")
            if e.points and e.kind not in ("shot_make","ft"): bad("points from a non-scoring event")
            prev_ev = e
        if abs(misses - rebs) > misses * 0.45: bad("rebounds don't track misses")

        # ---- clock ----
        sp = _spread_clocks(ev, T, poss)
        cl = [t for t,_ in sp]
        if any(cl[k] < cl[k+1] for k in range(len(cl)-1)): bad("clock ran backwards")
        if cl and (cl[0] > T + 0.01 or cl[-1] < -0.01): bad("clock outside the game")

        # ---- possession chain ----
        for k in range(len(poss)-1):
            if poss[k+1].period != poss[k].period: continue
            if poss[k].points > 0 and poss[k+1].team == poss[k].team: bad("made basket kept the ball")
            if poss[k+1].team == poss[k].team and poss[k+1].start != "oreb": bad("same team, no oreb")
            if poss[k].start == "oreb" and poss[k].cleared: bad("oreb required a clear")
        if abs(sum(p.duration for p in poss) - T) > 0.05: bad("clock doesn't fill the game")

        # ---- minutes ----
        for pl, t in ((ap,A),(hp,H)):
            tot = sum(p.minutes for p in pl)
            if tot > 75 + ots*3/60 + 0.2: bad("too many floor minutes")
            for p in pl:
                if p.minutes > 25 + ots/60 + 0.2: bad("a player exceeded the game length")
                if boxes[t][p.name]["PF"] > (6 if ot else 5): bad("over the foul limit")

        for k, v in (("pts",apts+hpts),("poss",len(poss))): stats[k]+=v
    return games, stats

if __name__ == "__main__":
    for sd in (2027, 11, 909):
        FAIL.clear()
        n, s = audit(300, sd)
        print(f"seed {sd}: {n} games — " + ("CLEAN" if not FAIL else "ISSUES"))
        for k, v in FAIL.most_common(): print(f"    {k}: {v}")
