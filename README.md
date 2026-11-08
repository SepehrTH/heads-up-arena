# Heads-Up Arena

Play **any two bots** from the TAMU Datathon 2026 Heads-Up challenge (Short Deck 5-card PLO)
against each other in a 1v1 match, scored by the **official judge** with the exact tournament
rules. Bring two repo links, get a verdict in about a minute.

```bash
python arena.py https://github.com/alice/bot https://github.com/bob/bot
```

## Setup (once)

You need git and Python 3.10+.

```bash
git clone <this repo> && cd <this repo>
python -m venv .venv && source .venv/bin/activate     # or: conda create -n arena python=3.12
pip install fastapi uvicorn numpy
```

If a bot needs more packages, add `--install` to install both bots' `requirements.txt`.

## Usage

```bash
python arena.py <bot A> <bot B> [options]
```

Each bot is a **public GitHub URL** or a **local folder** with `agent.py` at its root.

| option | default | meaning |
| --- | --- | --- |
| `--seeds N` | 3 | number of matches |
| `--quick` | off | just one match (~30-60 s) |
| `--deals N` | 1000 | deals per match (1000 is tournament length) |
| `--ref-a X` / `--ref-b X` | latest | branch, tag or commit of bot A / B |
| `--seed S` | random | base seed, printed so anyone can replay the same cards |
| `--judge X` | official bundle | judge repo or folder with `judge_engine.py` |
| `--install` | off | `pip install -r requirements.txt` for both bots |
| `--keep-logs` | off | keep the bots' server logs |
| `--baselines` | off | also play each bot against the official practice bots |
| `--baseline-matches N` | 1 | matches per practice bot |

Examples:

```bash
python arena.py https://github.com/alice/bot https://github.com/bob/bot --quick
python arena.py ../my-bot https://github.com/bob/bot --seeds 5
python arena.py https://github.com/alice/bot https://github.com/bob/bot --ref-a 88c3d4e --seed 931
```

## Practice bots

Add `--baselines` to also play each bot against the 4 practice bots that ship with the official
judge (random, always-half, always-pot, calling station). Only those official bots are used, never
other teams' repos. Both bots get the same cards, and you get one table at the end:

```
                      AliceBot                BobBot
  AlwaysHalf            +14.10  WIN             +9.80  WIN
  ...
  points              4 / 4                   4 / 4
  avg bb/deal         +10.61                  +7.38
```

Give just one bot to test only that bot against the practice bots:

```bash
python arena.py https://github.com/you/your-bot --baselines
```

## What you see

After **every** match (so nobody has to wait for the whole run):

```
match 1/3 (seed 4821-0) ...
  RESULT: alice / AliceBot +0.412 bb/deal  95% CI [+0.11, +0.71]  ->  alice / AliceBot WINS   (41s)
    A: time 2.1s/2.3s, errors 0   |   B: time 0.4s/0.4s, errors 0
  SCORE so far: alice / AliceBot 1 wins, 0 chops, bob / BobBot 0 wins (points 1-0); ...
```

Every finished match is also appended right away to `arena_runs/results.md`.

## Watch the hands

```bash
python viewer.py              # latest run
python viewer.py arena_runs/<run folder>
```

Opens a page in your browser with every deal as a **duplicate pair**: half 1 next to half 2,
where the bots swap cards. You get both hands, the board, every action per street and the
result. Sort by biggest swings or filter to showdowns to see exactly where one bot outplayed
the other with the same cards.

## Fair by construction

- **Neutral referee**: the judge is cloned from the organizers' repo
  (`tamu-datathon-org/heads-up-bundle`), not supplied by either player.
- **Duplicate format**: each match plays the same cards twice with seats swapped, so luck cancels.
- **Same rules as the tournament**: 8 s time bank per half, 1 s per decision, 5 errors =
  forfeit; win only if the 95% CI is entirely above 0, otherwise chop.
- **Fresh processes**: each half starts both bots from scratch, like the real event.
- **Nothing kept**: repos cloned from URLs go to a temporary folder and are deleted when the
  run ends (even on Ctrl-C). Local folders are only read.

## Run it on Linux for tournament-accurate results

The tournament runs bots on Linux. Some bots ship compiled Linux libraries (`.so` files) and
quietly fall back to a weaker mode on a Mac or on native Windows, so a Mac result can be wrong.
Use Linux, WSL on Windows, or a Linux container.

## Warning

This runs both bots' code on your computer. Only run repos you trust, or run it inside a
container or throwaway VM.
