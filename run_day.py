"""
SBAAA — run one day.

    python3 run_day.py 2027-03-05
    python3 run_day.py next                     # the next unplayed day
    python3 run_day.py 2027-03-05 --no-report   # ledger only

Hand-entered results, before the day is run:
    python3 run_day.py 2027-03-05 \
        --dice 305276001008=554632 \
        --score 305278002025=61-44

Everything not named is simulated. The ledger lives in ./season and is
appended to, so re-running a day adds nothing.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

from sbaaa_season import Season
import make_daily

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "season"
REPORTS = HERE / "reports"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", help="YYYY-MM-DD, or 'next'")
    ap.add_argument("--dice", action="append", default=[],
                    help="GAMEID=WWWRGB, six digits")
    ap.add_argument("--score", action="append", default=[],
                    help="GAMEID=AWAY-HOME")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--folder", default=str(LEDGER))
    a = ap.parse_args()

    season = Season(folder=a.folder)

    if a.date == "next":
        done = season.ledger.played()
        played_days = set()
        if season.ledger.games.exists():
            df = pd.read_csv(season.ledger.games)
            played_days = set(df.Date.astype(str))
        days = sorted(season.games.Date.unique())
        nxt = next((pd.Timestamp(d).strftime("%Y-%m-%d") for d in days
                    if pd.Timestamp(d).strftime("%Y-%m-%d") not in played_days),
                   None)
        if not nxt:
            print("season complete")
            return
        a.date = nxt

    for item in a.dice:
        gid, _, val = item.partition("=")
        season.set_dice(gid, [int(c) for c in val.strip()])
        print(f"  dice entered for {gid}: {val}")
    for item in a.score:
        gid, _, val = item.partition("=")
        away, _, home = val.partition("-")
        season.set_score(gid, int(away), int(home))
        print(f"  score entered for {gid}: {away}-{home}")

    res = season.run_day(a.date)
    print(f"{a.date}: {res['played']} games written to {a.folder}/games.csv")

    if not a.no_report and res["played"]:
        REPORTS.mkdir(exist_ok=True)
        out = REPORTS / f"SBAAA_{a.date}.pdf"
        make_daily.build(a.folder, a.date, str(out))
        print(f"report: {out}")


if __name__ == "__main__":
    main()
