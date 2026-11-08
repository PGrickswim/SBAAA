"""
SBAAA daily report.

Top of the page: the five biggest upsets, the five biggest games, and the
day's five leading scorers. Then every game of the day in time order:
away team and score first, home team and score second, each with its CRIA
rank where it has one, then the series tally and an upset flag.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle)

from sbaaa_data import League
from sbaaa_cria import compute
from sbaaa_winprob import DICE_WP
from make_report import _city, INK, MUTED, RULE

from sbaaa_data import SCHEDULE as SCHED
UPSET = colors.HexColor("#B45309")
WIN_BG = colors.HexColor("#DCFCE7")     # light green behind the winner


def _name(lg: League, team: str, rank: Optional[int]) -> str:
    """#14 Carmel IN Roundabouts — the rank only if the team has one."""
    base = f"{_city(team)} {lg.nickname(team)}"
    return f"#{rank} {base}" if rank else base


def build(ledger_dir: str, date: str, out: str, top_n: int = 240) -> None:
    lg = League()
    games = pd.read_csv(Path(ledger_dir) / "games.csv")
    boxes = pd.read_csv(Path(ledger_dir) / "boxes.csv")
    sched = pd.read_csv(SCHED, parse_dates=["Date"])

    today = games[games.Date == date].copy()
    if today.empty:
        raise SystemExit(f"no games in the ledger for {date}")

    # Ranks come from CRIA through the PREVIOUS day, which is what was
    # known when the games tipped off.
    prior = games[games.Date < date]
    rank = {}
    if len(prior):
        tbl = compute(prior, verbose=False)["table"]
        rank = {r.Team: int(r.Rank) for r in tbl.itertuples()
                if int(r.Rank) <= top_n}

    # time and TV from the schedule
    meta = (sched.drop_duplicates("GameID")
                 .set_index("GameID")[["GameTime", "Timezone", "TV",
                                       "TV Window"]])
    today = today.join(meta, on="GameID")

    # series tally so far, through this game
    tally = {}
    for _, r in games[games.Date <= date].sort_values(
            ["Series", "GameNo"]).iterrows():
        k = (r.Series, r.Away, r.Home)
        t = tally.setdefault(k, {r.Away: 0, r.Home: 0})
        t[r.Winner] += 1
        if r.Date == date and r.GameNo:
            today.loc[today.GameID == r.GameID, "Tally"] = \
                f"{t[r.Away]}-{t[r.Home]}"

    # --- choose the night's TV games ---
    # The schedule marks CANDIDATES: TV is a predetermined game 1, TV* a
    # fixed game 2, ~TV a flex pick or a game 3 shortlist entry. Three or
    # four games actually air, one per window, so the report selects them
    # rather than printing every option.
    def pick_tv(df: pd.DataFrame) -> set:
        on_air = set()
        cand = df[df.TV.notna() & (df.TV.astype(str).str.strip() != "")]
        for window, grp in cand.groupby(cand["TVWindow"].fillna("")):
            fixed = grp[grp.TV.astype(str).str.strip().isin(["TV", "TV*"])]
            if len(fixed):
                # predetermined: it airs
                on_air.update(fixed.nsmallest(1, "Billing").GameID)
                continue
            # flex / game 3 shortlist: a 1-1 series first, then the best
            # billing among what is left
            live = grp[grp.Tally.isin(["1-1"])]
            pool = live if len(live) else grp
            on_air.update(pool.nsmallest(1, "Billing").GameID)
        return on_air

    today["Upset"] = today.Winner != today.Favorite
    # The winner's own pre-game chance. The biggest upset is simply the
    # lowest one — margin has nothing to do with it. Weighting by margin
    # put a 51% favorite losing above a 72% favorite losing.
    today["WinnerWP"] = today.apply(
        lambda r: (1 - DICE_WP.get(int(r.Need), 0.5)) if r.Upset
        else DICE_WP.get(int(r.Need), 0.5), axis=1)
    today["Billing"] = today.apply(
        lambda r: rank.get(r.Away, 999) + rank.get(r.Home, 999), axis=1)
    today["TVWindow"] = today["TV Window"] if "TV Window" in today else None
    on_air = pick_tv(today)

    # --- top of the report ---
    upsets = today[today.Upset].nsmallest(5, "WinnerWP")
    biggest = today.nsmallest(5, "Billing")
    day_box = boxes[boxes.Date == date]
    scorers = day_box.nlargest(5, "PTS")

    styles = getSampleStyleSheet()
    st = {
        "title": ParagraphStyle("t", parent=styles["Normal"],
                                fontName="Helvetica-Bold", fontSize=20,
                                leading=24, spaceAfter=2, textColor=INK),
        "sub": ParagraphStyle("s", parent=styles["Normal"],
                              fontName="Helvetica", fontSize=8.5,
                              leading=11, textColor=MUTED),
        "h": ParagraphStyle("h", parent=styles["Normal"],
                            fontName="Helvetica-Bold", fontSize=8,
                            textColor=MUTED, spaceBefore=8, spaceAfter=3),
        "lead": ParagraphStyle("ld", parent=styles["Normal"],
                               fontName="Helvetica-Bold", fontSize=15,
                               leading=18, textColor=INK, spaceAfter=1),
        "deck": ParagraphStyle("dk", parent=styles["Normal"],
                               fontName="Helvetica", fontSize=9,
                               leading=12, textColor=MUTED),
    }
    doc = SimpleDocTemplate(out, pagesize=letter,
                            leftMargin=0.5*inch, rightMargin=0.5*inch,
                            topMargin=0.45*inch, bottomMargin=0.45*inch,
                            title=f"SBAAA {date}")
    d = pd.Timestamp(date)
    story = [Paragraph(f"SBAAA &nbsp;·&nbsp; {d:%A, %B %-d, %Y}", st["title"]),
             Paragraph(f"{len(today)} games", st["sub"]), Spacer(1, 6)]

    def mini(rows: List[List[str]], widths, headers) -> Table:
        t = Table([headers] + rows, colWidths=widths, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.4),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    # ==================================================
    # FRONT PAGE
    # ==================================================
    # The lead is whichever mattered more: a genuine shock, or a meeting of
    # two of the best. A 19% winner beats a top-10 meeting; otherwise the
    # marquee game leads.
    lead = (upsets.iloc[0] if len(upsets) and upsets.iloc[0].WinnerWP < 0.25
            else biggest.iloc[0])
    lw = lead.Winner
    ll = lead.Home if lw == lead.Away else lead.Away
    lws, lls = max(lead.AwayPts, lead.HomePts), min(lead.AwayPts, lead.HomePts)
    lead_box = day_box[day_box.GameID == lead.GameID]
    star = lead_box.nlargest(1, "PTS").iloc[0] if len(lead_box) else None

    headline = f"{_city(lw)} {lg.nickname(lw)} {lws}, {_city(ll)} {lg.nickname(ll)} {lls}"
    story.append(Paragraph(headline, st["lead"]))
    # Built without .capitalize(), which was lowercasing state codes —
    # "Lakewood co came in with a 19% chance".
    bits = []
    if lead.Upset:
        bits.append(f"The {lg.nickname(lw)} came in with a "
                    f"{100*lead.WinnerWP:.0f}% chance")
    else:
        bits.append("The marquee game of the night")
    if rank.get(lw) and rank.get(ll):
        bits.append(f"#{rank[lw]} beat #{rank[ll]}")
    if lead.OT:
        bits.append("settled in overtime")
    if star is not None:
        bits.append(f"{star.Player} led all scorers with {star.PTS:.0f}")
    story.append(Paragraph(" &nbsp;·&nbsp; ".join(bits) + ".", st["deck"]))
    story.append(Spacer(1, 8))

    # --- the day in numbers ---
    tot = today.AwayPts + today.HomePts
    hi = today.loc[tot.idxmax()]
    blow = today.loc[today.Margin.idxmax()]
    tight = today[today.Margin == today.Margin.min()]
    strip = [[
        f"{len(today)}\nGAMES",
        f"{int(today.OT.sum())}\nOVERTIMES",
        f"{int(today.Upset.sum())}\nUPSETS",
        f"{tot.mean():.0f}\nAVG TOTAL",
        f"{int(today.Margin.min())}\nCLOSEST",
        f"{int(today.Margin.max())}\nWIDEST",
    ]]
    t = Table(strip, colWidths=[1.22*inch]*6, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica-Bold", 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F4F6")),
        ("BOX", (0, 0), (-1, -1), 0.4, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)

    def panel(title, rows, widths, headers):
        out = [Paragraph(title, st["h"])]
        t = Table([headers] + rows, colWidths=widths, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.6),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 7.6),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 2.2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        out.append(t)
        return out

    def short(team):
        r = rank.get(team)
        return (f"#{r} " if r else "") + _city(team)

    # --- two columns of panels ---
    left, right = [], []
    left += panel("BIGGEST UPSETS",
                  [[short(r.Winner), f"{max(r.AwayPts,r.HomePts)}-{min(r.AwayPts,r.HomePts)}",
                    short(r.Favorite), f"{100*r.WinnerWP:.0f}%"]
                   for r in upsets.itertuples()],
                  [1.25*inch, 0.6*inch, 1.25*inch, 0.45*inch],
                  ["WINNER", "SCORE", "BEAT", "CHANCE"])
    left += [Spacer(1, 4)]
    left += panel("BIGGEST GAMES",
                  [[short(r.Away), short(r.Home), f"{r.AwayPts}-{r.HomePts}",
                    "UP" if r.Upset else ""]
                   for r in biggest.itertuples()],
                  [1.2*inch, 1.2*inch, 0.72*inch, 0.43*inch],
                  ["AWAY", "HOME", "SCORE", ""])

    right += panel("LEADING SCORERS",
                   [[r.Player, short(r.Team), f"{r.PTS:.0f}",
                     f"{r.FGM:.0f}-{r.FGA:.0f}"]
                    for r in scorers.itertuples()],
                   [1.28*inch, 1.15*inch, 0.38*inch, 0.64*inch],
                   ["PLAYER", "TEAM", "PTS", "FG"])
    right += [Spacer(1, 4)]
    highs = []
    for col, label in (("REB", "rebounds"), ("AST", "assists"),
                       ("STL", "steals"), ("BLK", "blocks")):
        top = day_box.nlargest(1, col).iloc[0]
        highs.append([label.upper(), top.Player, short(top.Team),
                      f"{top[col]:.0f}"])
    sharp = day_box[day_box.FGA >= 8].assign(
        pc=lambda d: d.FGM / d.FGA).nlargest(1, "pc")
    if len(sharp):
        sp = sharp.iloc[0]
        highs.append(["SHOOTING", sp.Player, short(sp.Team),
                      f"{100*sp.FGM/sp.FGA:.0f}%"])
    right += panel("ALSO OF NOTE", highs,
                   [0.68*inch, 1.22*inch, 1.05*inch, 0.50*inch],
                   ["", "PLAYER", "TEAM", ""])

    cols = Table([[left, right]], colWidths=[3.75*inch, 3.75*inch],
                 hAlign="LEFT")
    cols.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (0, 0), 0),
                              ("RIGHTPADDING", (1, 0), (1, 0), 0)]))
    story.append(cols)

    # --- every game, by time ---
    # Sorted by the actual moment of tip-off, converted to Eastern. Sorting
    # the printed string instead put 10:15p PT ahead of 11:00a ET.
    OFFSET = {"ET": 0, "CT": 1, "MT": 2, "PT": 3}

    def et_minutes(t: str, zone: str) -> int:
        t = str(t).strip().lower()
        pm = t.endswith("p")
        hm = t.rstrip("apm").strip()
        hh, _, mm = hm.partition(":")
        h = int(hh) % 12 + (12 if pm else 0)
        return h * 60 + int(mm or 0) + 60 * OFFSET.get(str(zone).strip(), 0)

    story.append(Paragraph("ALL GAMES", st["h"]))
    today["Sort"] = [et_minutes(t, z)
                     for t, z in zip(today.GameTime, today.Timezone)]
    rows, styles_extra = [], []
    for i, r in enumerate(today.sort_values(["Sort", "Away"]).itertuples(), 1):
        # The schedule carries three values: TV and TV* are scheduled
        # broadcasts, ~TV is a tentative or flex pick. Flattening them all
        # to "TV" overstated the national slate.
        tv = "TV" if r.GameID in on_air else ""
        rows.append([
            f"{r.GameTime} {r.Timezone}",
            tv,
            _name(lg, r.Away, rank.get(r.Away)), str(r.AwayPts),
            _name(lg, r.Home, rank.get(r.Home)), str(r.HomePts),
            ("OT" if r.OT else ""),
            getattr(r, "Tally", "") or "",
            "UPSET" if r.Upset else "",
        ])
        if r.Upset:
            styles_extra.append(("TEXTCOLOR", (8, i), (8, i), UPSET))
            styles_extra.append(("FONT", (8, i), (8, i), "Helvetica-Bold", 7))
        # shade the winning side
        lo, hi = ((2, 3) if r.AwayPts > r.HomePts else (4, 5))
        styles_extra.append(("BACKGROUND", (lo, i), (hi, i), WIN_BG))
    t = Table([["TIME", "", "AWAY", "", "HOME", "", "", "SERIES", ""]] + rows,
              colWidths=[0.70*inch, 0.26*inch, 2.08*inch, 0.30*inch,
                         2.08*inch, 0.30*inch, 0.24*inch, 0.50*inch,
                         0.54*inch],
              hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.6),
        ("FONT", (3, 1), (3, -1), "Helvetica-Bold", 7.6),
        ("FONT", (5, 1), (5, -1), "Helvetica-Bold", 7.6),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("ALIGN", (5, 0), (5, -1), "RIGHT"),
        ("ALIGN", (7, 0), (8, -1), "CENTER"),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ] + styles_extra))
    story.append(t)
    doc.build(story)
    print("wrote", out)


if __name__ == "__main__":
    import sys
    build(sys.argv[1], sys.argv[2], sys.argv[3])
