"""
SBAAA daily results PDF: score, box score, game flow and win probability.
"""

import io
import random
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, PageBreak, KeepTogether)

from sbaaa_data import League
from sbaaa_gv import all_team_oq, gv_base
from sbaaa_dice_engine import (TeamState, simulate_game, fatigue_value,
                               locpoints_from_miles)
def _otd(g):
    """Overtime split from the dice, so regulation can finish level."""
    if not g["result"]["OT"]:
        return None
    d = dict(g["ot"]); d["OT_Margin"] = g["result"]["Margin"]
    return d

from sbaaa_pbp import build_possessions, GAME_SECONDS
from sbaaa_events import expand, box_from_events
from sbaaa_boxscore import assign_minutes, apply_exits
from sbaaa_winprob import curve_by_possession

from sbaaa_data import SCHEDULE as SCHED

INK = colors.HexColor("#1A1A1A")
MUTED = colors.HexColor("#6B7280")
RULE = colors.HexColor("#D1D5DB")
AWAY_C = "#B45309"
HOME_C = "#1D4ED8"


# ======================================================
# SIM
# ======================================================

def sim_game(lg: League, oq: Dict[str, float], row, seed: int) -> Dict[str, Any]:
    A, H = ((row.City2, row.City1) if row.Location == "H"
            else (row.City1, row.City2))
    S = int(row.Series)
    lp = locpoints_from_miles(float(row["Distance Traveled A"]))
    rng = random.Random(seed)
    ap, hp = lg.active_five(A), lg.active_five(H)
    assign_minutes(ap); assign_minutes(hp)

    g = simulate_game(
        TeamState(lg.code(A), gv_base(S, lg.r_star(A), None), oq[A],
                  fatigue=fatigue_value(consecutive_away=int(row.AwaySeriesInRow))),
        TeamState(lg.code(H), gv_base(S, lg.r_star(H), None), oq[H]), lp)

    apts, hpts = g["result"]["Away"], g["result"]["Home"]
    ot = g["result"]["OT"]
    ots = rng.uniform(110, 190) if ot else 0.0
    fav = A if g["setup"]["Favorite"] == lg.code(A) else H
    need = g["setup"]["Need"]

    poss = build_possessions(A, H, ap, hp, apts, hpts,
                             [g["dice"][k] for k in ("W1", "W2", "W3")],
                             fav, rng, ot_seconds=ots,
                                 ot_detail=_otd(g))
    ev, exits = expand(poss, A, H, ap, hp, rng, foul_out=6 if ot else 5,
                       edge=lg.r_star(A) - lg.r_star(H))
    apply_exits(ap, exits); apply_exits(hp, exits)

    T = GAME_SECONDS + ots
    run = {A: 0, H: 0}
    flow = []
    for p in poss:
        run[p.team] += p.points
        flow.append(((T - (p.clock_start - p.duration)) / 60,
                     run[A], run[H]))
    wp = curve_by_possession(poss, need, fav, A, H, T)

    return dict(row=row, A=A, H=H, ap=ap, hp=hp, apts=apts, hpts=hpts,
                ot=ot, fav=fav, need=need, T=T / 60,
                abox=box_from_events(ev, ap, A),
                hbox=box_from_events(ev, hp, H),
                flow=flow, wp=wp, events=ev, lg=lg)


# ======================================================
# CHARTS
# ======================================================

def _style(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#9CA3AF")
    ax.tick_params(colors="#4B5563", labelsize=7.5)
    ax.grid(axis="y", color="#E5E7EB", lw=0.6)
    ax.set_axisbelow(True)


def flow_png(d: Dict[str, Any]) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(6.9, 2.0), dpi=200)
    x = [p[0] for p in d["flow"]]
    ax.plot(x, [p[1] for p in d["flow"]], color=AWAY_C, lw=1.6,
            label=_city(d["A"]))
    ax.plot(x, [p[2] for p in d["flow"]], color=HOME_C, lw=1.6,
            label=_city(d["H"]))
    ax.set_xlim(0, d["T"]); ax.set_ylim(0, None)
    ax.set_xlabel("Minutes elapsed", fontsize=7.5, color="#4B5563")
    ax.set_ylabel("Points", fontsize=7.5, color="#4B5563")
    ax.legend(frameon=False, fontsize=7.5, loc="upper left")
    _style(ax)
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png")
    plt.close(fig); buf.seek(0)
    return buf


def wp_png(d: Dict[str, Any]) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(6.9, 2.0), dpi=200)
    x = [p[0] for p in d["wp"]]
    y = [p[1] for p in d["wp"]]
    fav_c = HOME_C if d["fav"] == d["H"] else AWAY_C
    ax.axhline(50, color="#9CA3AF", lw=0.9, ls="--")
    ax.plot(x, y, color=fav_c, lw=1.3)
    ax.fill_between(x, 50, y, where=[v >= 50 for v in y],
                    color=fav_c, alpha=0.16, interpolate=True)
    ax.fill_between(x, 50, y, where=[v <= 50 for v in y],
                    color="#4B5563", alpha=0.12, interpolate=True)
    ax.set_xlim(0, d["T"]); ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Minutes elapsed", fontsize=7.5, color="#4B5563")
    ax.set_ylabel(f"{_city(d['fav'])} win %", fontsize=7.5,
                  color="#4B5563")
    _style(ax)
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png")
    plt.close(fig); buf.seek(0)
    return buf


# ======================================================
# BOX SCORE TABLE
# ======================================================

HEAD = ["", "POS", "MIN", "PTS", "FG", "3PT", "FT", "REB", "AST",
        "STL", "BLK", "TO", "PF"]

# Well-known cities stand alone; the rest keep their state, and so does any
# city two teams share.
SHARED = {"Arlington", "Wilmington", "Aurora", "Springfield", "Columbus",
          "Kansas City", "Portland", "Glendale", "Pasadena", "Charlotte",
          "Rochester", "Lakewood", "Peoria", "Richmond", "Alexandria"}
PLAIN = {"Chicago", "Boston", "Philadelphia", "Houston", "Phoenix", "Detroit",
         "Seattle", "Denver", "Atlanta", "Miami", "Dallas", "Baltimore",
         "Milwaukee", "Cleveland", "Pittsburgh", "Nashville", "Memphis",
         "Brooklyn", "Manhattan", "Newark", "Cincinnati", "Minneapolis",
         "Sacramento", "Oakland", "Tampa", "Orlando", "Buffalo", "Omaha",
         "Tucson", "Akron", "Spokane", "Reno", "Boise", "Fresno", "Anaheim",
         "Honolulu", "Louisville", "Indianapolis", "Albuquerque"}


def _city(name: str) -> str:
    """Drop the state for well-known cities, keep it where it disambiguates."""
    base, _, state = name.rpartition(" ")
    if not base:
        return name
    if base in SHARED:
        return f"{base} {state}"
    return base if base in PLAIN else f"{base} {state}"


def box_table(name: str, players, box, lg: League) -> Table:
    rows = [[f"{_city(name)}  \u2013  {lg.nickname(name)}"] + HEAD[1:]]
    tot = {k: 0 for k in ("PTS", "FGM", "FGA", "3PM", "3PA", "FTM", "FTA",
                          "REB", "AST", "STL", "BLK", "TOV", "PF", "MIN")}
    for p in players:
        b = box[p.name]
        for k in tot:
            tot[k] += b[k]
        rows.append([p.name, p.role, f"{b['MIN']:.1f}", f"{b['PTS']:.0f}",
                     f"{b['FGM']:.0f}-{b['FGA']:.0f}",
                     f"{b['3PM']:.0f}-{b['3PA']:.0f}",
                     f"{b['FTM']:.0f}-{b['FTA']:.0f}",
                     f"{b['REB']:.0f}", f"{b['AST']:.0f}", f"{b['STL']:.0f}",
                     f"{b['BLK']:.0f}", f"{b['TOV']:.0f}", f"{b['PF']:.0f}"])
    rows.append(["TEAM", "", f"{tot['MIN']:.0f}", f"{tot['PTS']:.0f}",
                 f"{tot['FGM']:.0f}-{tot['FGA']:.0f}",
                 f"{tot['3PM']:.0f}-{tot['3PA']:.0f}",
                 f"{tot['FTM']:.0f}-{tot['FTA']:.0f}",
                 f"{tot['REB']:.0f}", f"{tot['AST']:.0f}", f"{tot['STL']:.0f}",
                 f"{tot['BLK']:.0f}", f"{tot['TOV']:.0f}", f"{tot['PF']:.0f}"])

    w = [1.92*inch, 0.34*inch, 0.40*inch, 0.37*inch, 0.54*inch, 0.50*inch,
         0.50*inch, 0.35*inch, 0.35*inch, 0.35*inch, 0.35*inch, 0.33*inch,
         0.33*inch]
    t = Table(rows, colWidths=w, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.6),
        ("FONT", (0, 1), (-1, -2), "Helvetica", 7.6),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 7.6),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F3F4F6")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (0, -1), 5),
    ]))
    return t


# ======================================================
# PAGE
# ======================================================

def page(d: Dict[str, Any], st) -> List:
    lg = d["lg"]
    A, H = d["A"], d["H"]
    row = d["row"]
    win, lose = ((A, H) if d["apts"] > d["hpts"] else (H, A))
    ws, ls = max(d["apts"], d["hpts"]), min(d["apts"], d["hpts"])

    out = []
    tag = "  (OT)" if d["ot"] else ""
    # City leads, bold; nickname follows in lighter type.
    out.append(Paragraph(
        f'<b>{_city(win)}</b> <font color="#6B7280" size="13">'
        f'{lg.nickname(win)}</font> <b>{ws}</b>, '
        f'<b>{_city(lose)}</b> <font color="#6B7280" size="13">'
        f'{lg.nickname(lose)}</font> <b>{ls}</b>{tag}', st["h"]))
    tv = f" · National TV" if isinstance(row.TV, str) else ""
    out.append(Paragraph(
        f"{A} at {H} &nbsp;·&nbsp; {row.Date.date():%B %-d, %Y} &nbsp;·&nbsp; "
        f"Series {int(row.Series)}, Game {int(row.GameNumberInSeries)} "
        f"&nbsp;·&nbsp; {row.GameTime} {row.Timezone}{tv}", st["sub"]))
    out.append(Paragraph(
        f"R* {lg.r_star(A):.1f} (#{lg.rank(A)}) vs {lg.r_star(H):.1f} "
        f"(#{lg.rank(H)}) &nbsp;·&nbsp; favorite {_city(d['fav'])}, "
        f"Need {d['need']}", st["sub"]))
    out.append(Spacer(1, 9))

    out.append(box_table(A, d["ap"], d["abox"], lg))
    out.append(Spacer(1, 7))
    out.append(box_table(H, d["hp"], d["hbox"], lg))
    out.append(Spacer(1, 11))

    out.append(Paragraph("GAME FLOW", st["lbl"]))
    out.append(Image(flow_png(d), width=6.9*inch, height=2.0*inch))
    out.append(Spacer(1, 7))
    out.append(Paragraph("WIN PROBABILITY", st["lbl"]))
    out.append(Image(wp_png(d), width=6.9*inch, height=2.0*inch))
    return out


def main(n: int = 4, out: str = "/mnt/user-data/outputs/SBAAA_Results.pdf"):
    lg = League()
    oq = all_team_oq(lg.roster, lg.rstar, lg.coaches).set_index("Team").OQ.to_dict()
    sch = pd.read_csv(SCHED, parse_dates=["Date"])

    day = sch[sch.Date == pd.Timestamp("2027-06-12")]
    picks = day.sample(n, random_state=11)

    styles = getSampleStyleSheet()
    st = {
        "h": ParagraphStyle("h", parent=styles["Normal"], fontName="Helvetica-Bold",
                            fontSize=18, leading=21, textColor=INK, spaceAfter=2),
        "sub": ParagraphStyle("sub", parent=styles["Normal"], fontName="Helvetica",
                              fontSize=8.5, leading=11.5, textColor=MUTED),
        "lbl": ParagraphStyle("lbl", parent=styles["Normal"],
                              fontName="Helvetica-Bold", fontSize=7.8,
                              textColor=MUTED, spaceAfter=3),
        "title": ParagraphStyle("t", parent=styles["Normal"],
                                fontName="Helvetica-Bold", fontSize=22,
                                leading=26, spaceAfter=3, textColor=INK),
    }

    doc = SimpleDocTemplate(out, pagesize=letter,
                            leftMargin=0.55*inch, rightMargin=0.55*inch,
                            topMargin=0.5*inch, bottomMargin=0.5*inch,
                            title="SBAAA Results")
    story = [Paragraph("SBAAA &nbsp;·&nbsp; Saturday, June 12, 2027", st["title"]),
             Paragraph("Series 28 results", st["sub"]), Spacer(1, 14)]

    for i, (_, row) in enumerate(picks.iterrows()):
        d = sim_game(lg, oq, row, seed=4000 + i)
        if i:
            story.append(PageBreak())
        story.extend(page(d, st))
        print(f"  {d['A']} {d['apts']} at {d['H']} {d['hpts']}"
              + ("  OT" if d["ot"] else ""))

    doc.build(story)
    print("wrote", out)


if __name__ == "__main__":
    main()
