# The scheduled task prompt

Paste this as the task instructions. It has to be fully self-contained — a scheduled task runs on its own and can't stop to ask a question.

Replace the Drive path on the first line if you put the folder elsewhere.

---

```
Run the next day of the SBAAA 2027 season.

The engine lives in Google Drive at:
My Drive / SBAAA / engine2027 / SBAAA_2027_Daily_Bundle
(Ignore the sibling folder SBAAA_2027_FINAL_REALIGNED. It is source material only.)

1. Download the whole SBAAA_2027_Daily_Bundle folder into your working
   directory, including the data/ and season/ subfolders, then cd into it.
   All commands below run from inside that folder. season/ holds the
   ledger from previous runs. If it is missing, this is the first run
   and the ledger will be created.

2. Check this conversation for anything I entered before the run: dice I
   rolled myself, or final scores from games we played for real. They
   will be given as a GameID plus either six dice (White1 White2 White3
   Red Green Blue) or a final score. Pass them through, e.g.

     python3 run_day.py next --dice 305276001008=554632 --score 305278002025=61-44

   If I gave you nothing, just run:

     python3 run_day.py next

3. Upload the updated season/ folder back to Drive at
   My Drive / SBAAA / engine2027 / SBAAA_2027_Daily_Bundle / season,
   overwriting the old one. This step must not be skipped. The ledger
   is the only thing that cannot be rebuilt.

4. Give me the daily PDF from reports/, and in the chat write a short
   summary: the lead game, the biggest upset, anything notable (an
   overtime run, an injury, a ranked team losing badly), and how the
   series in progress stand.

5. If today is a Monday, also produce an updated Top 25 from CRIA:

     python3 sbaaa_cria.py season/games.csv

   Write it as a poll: rank, team, record, movement from last week, and
   a line on anyone who moved sharply. The week of Memorial Day this
   moves to Tuesday.

Notes:
- Team naming: lead with the city in bold, nickname after.
- Recaps may cite box score stats.
- Never invent a result. Everything comes from the ledger the run wrote.
- If run_day.py reports a 'score-forced' game, mention it: that means a
  score I gave could not be produced by any dice roll, so it stands but
  the dice behind it are approximate.

```

---

## For the dry run

Keep the task **paused** and use **Run on demand** to advance one day at a time. That way you control the pace while you tune the output.

When it's ready for the real season, set the cadence to daily and unpause it.