# SBAAA 2027 — setting up the daily routine

Everything here runs standalone. No absolute paths, no external files, no
network. Unzip it anywhere and it works.

---

## What's in the bundle

```
run_day.py          the one command you need
data/               roster, R*, coaches, team identity, full schedule
season/             the ledger — created on first run, grows daily
reports/            daily PDFs — created on first run
*.py                the engine (see README.md for how it works)
```

The data is the 2027 drop converted to CSV: 2,400 players, 240 teams, the
locked 35,280-row schedule. About 4.5 MB in total.

---

## Step 1 — put the bundle in Google Drive

Upload the unzipped folder to Drive. Somewhere stable, e.g.

```
My Drive / SBAAA / engine2027 /
```

This matters because **a scheduled task runs in a fresh session with no
memory of any chat**. It can reach your connectors and the files in your
Claude account, but nothing that lived in a previous conversation's
workspace. Drive is what makes the setup survive.

---

## Step 2 — try it by hand once

In a Cowork session with Drive connected, ask Claude to pull the folder
down and run:

```
python3 run_day.py 2027-03-05
```

That writes 120 games to `season/games.csv`, 1,200 rows to
`season/boxes.csv`, and a PDF to `reports/`. Check the PDF looks right
before automating anything.

---

## Step 3 — create the scheduled task

On desktop: **Scheduled** in the left sidebar, then **New task**. Either
"Create with Claude" and describe it, or "Set up manually" and paste the
prompt from `TASK_PROMPT.md`.

Set the cadence to daily. For the dry run, leave it paused and use **Run
on demand** whenever you want to advance a day.

Scheduled tasks run remotely, so they fire whether or not your machine is
awake. You can trigger an on-demand run from mobile; Cowork on mobile is in
beta, so setting the task up is easier on desktop.

---

## Step 4 — the daily rhythm

**When you have nothing to add**, the task runs the next unplayed day and
hands you the paper. Nothing needed from you.

**When you rolled dice yourself**, or played a game in the driveway and
have a final score, say so *before* the run. Give Claude the GameID and
either the six dice or the score:

```
Dice for 305276001008: 5 5 4 6 3 2
Final for 305278002025: San Luis Obispo 61, Portland 44
```

Anything you don't name is simulated. The `Source` column in the ledger
records which was which — `sim`, `dice`, `score`, or `score-forced` when a
score you gave can't be produced by any roll (it stands anyway).

**Mondays**: update the Top 25 off the CRIA table. The week of Memorial
Day, that moves to Tuesday.

---

## Step 5 — keep the ledger in Drive

After each run, the updated `season/` folder has to go back to Drive, or
the next day starts from nothing. The task prompt covers this, but it is
the single most important step — everything else is reproducible, the
ledger is not.

---

## Running a day by hand

```
python3 run_day.py 2027-03-05                  # a specific day
python3 run_day.py next                        # the next unplayed day
python3 run_day.py next --no-report            # ledger only, no PDF
python3 run_day.py 2027-03-05 \
    --dice 305276001008=554632 \
    --score 305278002025=61-44
```

Dice are six digits in order: White1 White2 White3 Red Green Blue.

Re-running a day is safe — games already in the ledger are skipped, so
nothing double-counts.

---

## Other things it can produce

```
python3 sbaaa_cria.py season/games.csv         # current CRIA table
python3 sbaaa_validate.py                      # the invariant suite
python3 audit.py                               # the wider logic audit
```

`make_series_report.py` builds the three-game series PDF; it needs recaps
passed in, so it's a Claude job rather than a command.

---

## A caution for the dry run

The season runs March 5 to Memorial Day one day at a time, which is about
60 game days. Two things worth watching early:

- **Does the right kind of team win?** Averages and invariants are all
  verified; whether the league *feels* right is not something either can
  tell you.
- **Does CRIA settle?** It is volatile for the first few series by design,
  and should steady out by about series 10.
