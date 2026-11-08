"""
SBAAA series report: cover, then a page per game with recap, both box
scores, the game flow and the win probability.
"""

import io
import pickle
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, PageBreak)

from sbaaa_data import League
from sbaaa_pbp import GAME_SECONDS
from sbaaa_winprob import curve_by_possession
from make_report import _city, INK, MUTED, RULE, AWAY_C, HOME_C, _style

HEAD = ["", "POS", "MIN", "PTS", "FG", "3PT", "FT", "REB", "AST",
        "STL", "BLK", "TO", "PF"]


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
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (0, -1), 5),
    ]))
    return t


def flow_png(g, A, H, T) -> io.BytesIO:
    run = {A: 0, H: 0}
    xs, ya, yh = [], [], []
    for p in g["poss"]:
        run[p.team] += p.points
        xs.append((T - (p.clock_start - p.duration)) / 60)
        ya.append(run[A]); yh.append(run[H])
    fig, ax = plt.subplots(figsize=(6.9, 1.9), dpi=200)
    ax.plot(xs, ya, color=AWAY_C, lw=1.6, label=_city(A))
    ax.plot(xs, yh, color=HOME_C, lw=1.6, label=_city(H))
    ax.set_xlim(0, T/60); ax.set_ylim(0, None)
    ax.set_xlabel("Minutes elapsed", fontsize=7.5, color="#4B5563")
    ax.set_ylabel("Points", fontsize=7.5, color="#4B5563")
    ax.legend(frameon=False, fontsize=7.5, loc="upper left")
    _style(ax)
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png")
    plt.close(fig); buf.seek(0); return buf


def wp_png(g, A, H, T, need, fav) -> io.BytesIO:
    c = curve_by_possession(g["poss"], need, fav, A, H, T)
    xs = [x for x, _ in c]; ys = [y for _, y in c]
    col = HOME_C if fav == H else AWAY_C
    fig, ax = plt.subplots(figsize=(6.9, 1.9), dpi=200)
    ax.axhline(50, color="#9CA3AF", lw=0.9, ls="--")
    ax.plot(xs, ys, color=col, lw=1.2)
    ax.fill_between(xs, 50, ys, where=[v >= 50 for v in ys],
                    color=col, alpha=0.16, interpolate=True)
    ax.fill_between(xs, 50, ys, where=[v <= 50 for v in ys],
                    color="#4B5563", alpha=0.12, interpolate=True)
    ax.set_xlim(0, T/60); ax.set_ylim(0, 100); ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Minutes elapsed", fontsize=7.5, color="#4B5563")
    ax.set_ylabel(f"{_city(fav)} win %", fontsize=7.5, color="#4B5563")
    _style(ax)
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png")
    plt.close(fig); buf.seek(0); return buf


def build(meta: Dict[str, Any], recaps: List[str], summary: str,
          out: str) -> None:
    lg = League()
    s, A, H = meta["s"], meta["A"], meta["H"]
    styles = getSampleStyleSheet()
    st = {
        "title": ParagraphStyle("t", parent=styles["Normal"],
                                fontName="Helvetica-Bold", fontSize=21,
                                leading=25, spaceAfter=3, textColor=INK),
        "h": ParagraphStyle("h", parent=styles["Normal"],
                            fontName="Helvetica-Bold", fontSize=17,
                            leading=20, textColor=INK, spaceAfter=2),
        "sub": ParagraphStyle("sub", parent=styles["Normal"],
                              fontName="Helvetica", fontSize=8.5,
                              leading=11.5, textColor=MUTED),
        "body": ParagraphStyle("b", parent=styles["Normal"],
                               fontName="Helvetica", fontSize=9.2,
                               leading=13, textColor=INK, spaceAfter=5),
        "lbl": ParagraphStyle("l", parent=styles["Normal"],
                              fontName="Helvetica-Bold", fontSize=7.8,
                              textColor=MUTED, spaceAfter=3),
    }
    doc = SimpleDocTemplate(out, pagesize=letter,
                            leftMargin=0.55*inch, rightMargin=0.55*inch,
                            topMargin=0.5*inch, bottomMargin=0.5*inch,
                            title="SBAAA Series Report")
    story = [
        Paragraph(f"<b>{_city(H)}</b> wins the series {s['score']}", st["title"]),
        Paragraph(f"{_city(A)} {lg.nickname(A)} (#{lg.rank(A)}) at "
                  f"{_city(H)} {lg.nickname(H)} (#{lg.rank(H)}) &nbsp;·&nbsp; "
                  f"Series {meta['S']} &nbsp;·&nbsp; week of {meta['date']}",
                  st["sub"]),
        Spacer(1, 10),
        Paragraph(summary, st["body"]),
        Spacer(1, 6),
    ]

    for i, g in enumerate(s["games"]):
        story.append(PageBreak())
        win, lose = ((A, H) if g["away_pts"] > g["home_pts"] else (H, A))
        ws, ls = max(g["away_pts"], g["home_pts"]), min(g["away_pts"], g["home_pts"])
        tag = "  (OT)" if g["ot"] else ""
        story.append(Paragraph(
            f'<b>{_city(win)}</b> <font color="#6B7280" size="12">'
            f'{lg.nickname(win)}</font> <b>{ws}</b>, '
            f'<b>{_city(lose)}</b> <font color="#6B7280" size="12">'
            f'{lg.nickname(lose)}</font> <b>{ls}</b>{tag}', st["h"]))
        note = []
        if g["rested"]:
            note.append("series already decided, starters rested")
        if g["fatigue"][A] or g["fatigue"][H]:
            note.append(f"travel {g['fatigue'][A]}/{g['fatigue'][H]}")
        if g["momentum"][A] or g["momentum"][H]:
            note.append(f"momentum {g['momentum'][A]:+d}/{g['momentum'][H]:+d}")
        story.append(Paragraph(
            f"Game {g['game']} of 3" + ("  ·  " + "  ·  ".join(note) if note else ""),
            st["sub"]))
        story.append(Spacer(1, 8))
        story.append(Paragraph(recaps[i], st["body"]))
        story.append(Spacer(1, 4))
        story.append(box_table(A, g["away"], g["away_box"], lg))
        story.append(Spacer(1, 6))
        story.append(box_table(H, g["home"], g["home_box"], lg))
        story.append(Spacer(1, 9))
        T = GAME_SECONDS + (g.get("ot_seconds") or 0.0)
        story.append(Paragraph("GAME FLOW", st["lbl"]))
        story.append(Image(flow_png(g, A, H, T), width=6.9*inch, height=1.9*inch))
        story.append(Spacer(1, 5))
        story.append(Paragraph("WIN PROBABILITY", st["lbl"]))
        story.append(Image(wp_png(g, A, H, T, g["need"], g["fav"]),
                           width=6.9*inch, height=1.9*inch))
    doc.build(story)
    print("wrote", out)
