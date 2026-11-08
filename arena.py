#!/usr/bin/env python3
"""
arena.py - 1v1 duplicate matches between any two heads-up Short Deck PLO5 bots
(TAMU Datathon 2026 "Heads-Up" bundle format: a repo with agent.py at its root).

    python arena.py https://github.com/alice/bot https://github.com/bob/bot
    python arena.py https://github.com/alice/bot ../my-local-bot --seeds 5
    python arena.py A B --quick                      # one match, ~30-60 s
    python arena.py A B --baselines                  # + both bots vs the official practice bots
    python arena.py A --baselines                    # just test one bot against the practice bots

What it does:
  1. Fetches both bots (GitHub URL or local folder) and the organizers' OFFICIAL judge
     (judge_engine.py from tamu-datathon-org/heads-up-bundle), so neither side supplies the referee.
  2. For every match and every half, starts each bot as a fresh server on a free port,
     exactly like the tournament (each half runs in a brand-new process).
  3. Runs the official judge: full duplicate format, same time bank, same verdict rule
     (95% CI of bb/deal entirely above 0 = win, below 0 = loss, otherwise chop).
  4. Prints each verdict plus a running score as soon as a match finishes, saves it to
     arena_runs/results.md, and keeps hand histories for viewer.py.

Cloned repos are temporary and deleted when the run ends; local folders are never modified.
Needs: git, Python 3.10+, fastapi + uvicorn (+ numpy if a bot uses it).
WARNING: this runs both bots' code on your machine. Only run repos you trust, or use a
container / throwaway VM.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "arena_runs")
OFFICIAL_JUDGE = "https://github.com/tamu-datathon-org/heads-up-bundle"
PY = sys.executable
LIVE: list[subprocess.Popen] = []
TEMP_DIRS: list[str] = []


# ----------------------------------------------------------------------------- helpers
def say(msg=""):
    print(msg, flush=True)


def slug(src: str) -> str:
    s = src.rstrip("/").removesuffix(".git")
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+)$", s)
    name = f"{m.group(1)}__{m.group(2)}" if m else os.path.basename(os.path.abspath(s))
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def git_clone(src: str, dest: str, ref: str | None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")      # never ask for a username/password
    depth = [] if ref else ["--depth", "1"]
    r = subprocess.run(["git", "clone", "-q", *depth, src, dest], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        cleanup()
        sys.exit(f"ERROR: could not clone {src}\n"
                 "  -> the repo is private or the URL is misspelled. Use the exact public link.\n"
                 f"  (git said: {r.stderr.strip()[:200]})")
    if ref:
        r = subprocess.run(["git", "-C", dest, "checkout", "-q", ref], capture_output=True, text=True)
        if r.returncode != 0:
            cleanup()
            sys.exit(f"ERROR: {src} has no branch/commit '{ref}'")


def fetch_bot(src: str, ref: str | None, label: str) -> tuple[str, str]:
    """Local folder as-is, or a temporary clone of a git URL (deleted at exit)."""
    if os.path.isdir(src):
        path = os.path.abspath(src)
        say(f"  {label}: local folder {path}")
    else:
        tmp = tempfile.mkdtemp(prefix="arena_bot_")
        TEMP_DIRS.append(tmp)
        path = os.path.join(tmp, slug(src))
        say(f"  {label}: cloning {src}" + (f" @ {ref}" if ref else ""))
        git_clone(src, path, ref)
    if not os.path.isfile(os.path.join(path, "agent.py")):
        cleanup()
        sys.exit(f"ERROR: {label} has no agent.py at its root ({src})")
    commit = subprocess.run(["git", "-C", path, "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "local"
    return path, commit


def fetch_judge(src: str) -> str:
    """The official judge, cached in arena_runs/.judge (a URL) or used in place (a folder)."""
    if os.path.isdir(src):
        path = os.path.abspath(src)
    else:
        path = os.path.join(RUNS, ".judge", slug(src))
        if os.path.isdir(os.path.join(path, ".git")):
            subprocess.run(["git", "-C", path, "pull", "-q", "--ff-only"],
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0"), capture_output=True)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            git_clone(src, path, None)
    if not os.path.isfile(os.path.join(path, "judge_engine.py")):
        cleanup()
        sys.exit(f"ERROR: no judge_engine.py in {src}")
    return path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_bot(spec: tuple[str, str], port: int, log: str) -> subprocess.Popen:
    """spec = (folder to run in, script) - a bot is (repo, "agent.py")."""
    cwd, script = spec
    env = dict(os.environ, PORT=str(port))
    for k in ("BOT_PARAMS", "BOT_DEBUG", "AGENT_TOKEN"):
        env.pop(k, None)
    p = subprocess.Popen([PY, script], cwd=cwd, env=env, stdout=open(log, "w"),
                         stderr=subprocess.STDOUT, start_new_session=True)
    LIVE.append(p)
    return p


def wait_ready(port: int, proc: subprocess.Popen, timeout=20.0) -> dict | None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return None
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
                return json.loads(r.read().decode() or "{}")
        except Exception:
            time.sleep(0.3)
    return None


def stop(proc: subprocess.Popen):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
    if proc in LIVE:
        LIVE.remove(proc)


def cleanup(*_):
    for p in list(LIVE):
        stop(p)
    for d in list(TEMP_DIRS):            # cloned bots never stay on this machine
        shutil.rmtree(d, ignore_errors=True)
        TEMP_DIRS.remove(d)


def tail(path, n=12):
    try:
        return "".join(open(path).readlines()[-n:])
    except Exception:
        return ""


def name_of(info: dict | None, fallback: str) -> str:
    if not info:
        return fallback
    p, a = info.get("participant", ""), info.get("agent_name", "")
    return f"{p} / {a}" if p and a else (p or a or fallback)


# ----------------------------------------------------------------------------- one match
def play(spec_a, spec_b, judge_dir: str, seed: str, deals: int, logdir: str,
         logs=True, tags=("A", "B")):
    """One duplicate match. Fresh servers for each half, like the tournament."""
    halves, info = [], (None, None)
    for half in (1, 2):
        p1, p2 = free_port(), free_port()
        la = os.path.join(logdir, f"{tags[0]}_{seed}_h{half}.log")
        lb = os.path.join(logdir, f"{tags[1]}_{seed}_h{half}.log")
        a, b = start_bot(spec_a, p1, la), start_bot(spec_b, p2, lb)
        try:
            ia, ib = wait_ready(p1, a), wait_ready(p2, b)
            for i, l, t in ((ia, la, tags[0]), (ib, lb, tags[1])):
                if i is None:
                    say(f"  bot {t} failed to start (missing packages? try --install):\n" + tail(l))
                    return None
            info = (ia, ib)
            out = os.path.join(logdir, f"match_{seed}_h{half}.json")
            r = subprocess.run([PY, "judge_engine.py", "--agent1", str(p1), "--agent2", str(p2),
                                "--deals", str(deals), "--seed", seed, "--half", str(half),
                                "--json", out] + (["--logs"] if logs else []),
                               cwd=judge_dir, capture_output=True, text=True, timeout=1800)
            if not os.path.isfile(out):
                say("  judge failed:\n" + r.stdout[-800:] + r.stderr[-800:])
                return None
            halves.append(out)
        finally:
            stop(a)
            stop(b)
    combined = os.path.join(logdir, f"match_{seed}.json")
    r = subprocess.run([PY, "judge_engine.py", "combine", *halves, "--json", combined],
                       cwd=judge_dir, capture_output=True, text=True)
    if not os.path.isfile(combined):
        say("  could not combine halves:\n" + r.stdout[-800:] + r.stderr[-800:])
        return None
    d = json.load(open(combined))
    d.pop("logs", None)                    # hand logs live in the half files
    json.dump(d, open(combined, "w"))
    d["_info"] = info
    return d


# ----------------------------------------------------------------------------- practice-bot gauntlet
def run_baselines(bots, judge_dir, base, n, deals, logdir, md):
    """Every bot vs every official practice bot in the judge bundle (never other teams' repos).
    bots = [(tag, path)]. Both bots get the same cards against each practice bot."""
    files = sorted(glob.glob(os.path.join(judge_dir, "baseline-bots", "*.py")))
    if not files:
        say("  (no baseline-bots/ in the judge bundle - skipping practice bots)")
        return
    sub = os.path.join(logdir, "baselines")
    os.makedirs(sub, exist_ok=True)
    bl_names = [os.path.basename(f)[:-3].removeprefix("baseline_") for f in files]
    names = {tag: f"bot {tag}" for tag, _ in bots}
    cells: dict = {}                                   # (tag, j) -> list of match dicts
    say("")
    say(f"  PRACTICE BOTS  ({len(files)} official baselines x {n} match(es) x {deals} deals, same cards for each bot)")
    say("-" * 70)
    for j, f in enumerate(files):
        for tag, path in bots:
            for k in range(n):
                seed = f"{base}-b{j}-{k}"
                t0 = time.time()
                d = play((path, "agent.py"), (judge_dir, os.path.relpath(f, judge_dir)), judge_dir,
                         seed, deals, sub, logs=False, tags=(tag, "base"))
                if d is None:
                    say("  aborted.")
                    return
                names[tag] = name_of(d["_info"][0], names[tag])
                bl_names[j] = (d["_info"][1] or {}).get("agent_name") or bl_names[j]
                cells.setdefault((tag, j), []).append(d)
                say(f"  {short(names[tag]):<22} vs {bl_names[j]:<18} {d['mean_bb_per_deal']:+8.2f} bb/deal  "
                    f"{verdict_word(d):<7} ({time.time() - t0:.0f}s)")

    # ---- the clean summary table
    tags = [tag for tag, _ in bots]
    w = max(24, *(len(short(names[t])) + 2 for t in tags))

    def cell(ms):
        mean = sum(m["mean_bb_per_deal"] for m in ms) / len(ms)
        words = [verdict_word(m) for m in ms]
        rec = words[0] if len(ms) == 1 else " ".join(f"{words.count(x)}{x[0]}" for x in ("WIN", "CHOP", "LOSS") if x in words)
        return f"{mean:+8.2f}  {rec}"
    say("")
    say("  " + "".ljust(20) + "".join(short(names[t]).ljust(w) for t in tags))
    for j, bl in enumerate(bl_names):
        say("  " + bl.ljust(20) + "".join(cell(cells.get((t, j), [])).ljust(w) if cells.get((t, j)) else "-".ljust(w)
                                          for t in tags))
    say("  " + "-" * (20 + w * len(tags)))
    tot_rows = []
    for t in tags:
        ms = [m for j in range(len(files)) for m in cells.get((t, j), [])]
        pts = sum(m.get("points", {}).get("agent1", 0) for m in ms)
        avg = sum(m["mean_bb_per_deal"] for m in ms) / len(ms)
        errs = sum(x.get("errors", 0) for m in ms for x in m.get("agent_stats", {}).get("agent1", []))
        fts = sum(any(x.get("forfeited") for x in m.get("agent_stats", {}).get("agent1", [])) for m in ms)
        tot_rows.append((t, pts, len(ms), avg, errs, fts))
    say("  " + "points".ljust(20) + "".join(f"{p:g} / {c}".ljust(w) for _, p, c, _, _, _ in tot_rows))
    say("  " + "avg bb/deal".ljust(20) + "".join(f"{a:+.2f}".ljust(w) for _, _, _, a, _, _ in tot_rows))
    say("  " + "errors / forfeits".ljust(20) + "".join(f"{e} / {f}".ljust(w) for _, _, _, _, e, f in tot_rows))
    say("  (bb/deal is each bot's own result; a solid bot should win every row)")

    with open(md, "a") as fh:
        fh.write("\n**Practice bots** (bb/deal, verdict)\n\n| practice bot | " +
                 " | ".join(short(names[t]) for t in tags) + " |\n|" + " --- |" * (len(tags) + 1) + "\n")
        for j, bl in enumerate(bl_names):
            fh.write(f"| {bl} | " + " | ".join(" ".join(cell(cells[(t, j)]).split()) if cells.get((t, j)) else "-" for t in tags) + " |\n")
        fh.write("| **points** | " + " | ".join(f"{p:g} / {c}" for _, p, c, _, _, _ in tot_rows) + " |\n")


def short(name: str) -> str:
    """'team / BotName' -> 'BotName' for tight tables."""
    return name.split(" / ")[-1] if " / " in name else name


def verdict_word(d) -> str:
    if any(x.get("forfeited") for x in d.get("agent_stats", {}).get("agent1", [])):
        return "FORFEIT"
    return {"AGENT1_WIN": "WIN", "AGENT2_WIN": "LOSS"}.get(d.get("verdict"), "CHOP")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="1v1 duplicate matches between two heads-up PLO5 bots")
    ap.add_argument("bot_a", help="bot A: GitHub repo URL or local folder")
    ap.add_argument("bot_b", nargs="?", default=None,
                    help="bot B: GitHub repo URL or local folder (optional with --baselines)")
    ap.add_argument("--ref-a", default=None, help="branch / tag / commit of bot A")
    ap.add_argument("--ref-b", default=None, help="branch / tag / commit of bot B")
    ap.add_argument("--seeds", type=int, default=3, help="number of matches (default 3)")
    ap.add_argument("--quick", action="store_true", help="a single match")
    ap.add_argument("--deals", type=int, default=1000, help="deals per match (default 1000, tournament length)")
    ap.add_argument("--seed", default=None, help="base seed (default random; printed so anyone can replay)")
    ap.add_argument("--judge", default=OFFICIAL_JUDGE, help="judge repo/folder (default: the official bundle)")
    ap.add_argument("--install", action="store_true", help="pip install both bots' requirements.txt first")
    ap.add_argument("--keep-logs", action="store_true", help="keep the bots' server logs")
    ap.add_argument("--baselines", action="store_true",
                    help="also play each bot vs the official practice bots in the judge bundle")
    ap.add_argument("--baseline-matches", type=int, default=1, help="matches per practice bot (default 1)")
    a = ap.parse_args()
    if not a.bot_b and not a.baselines:
        ap.error("give two bots, or one bot with --baselines")
    if a.quick:
        a.seeds = 1

    signal.signal(signal.SIGINT, lambda *_: (cleanup(), sys.exit(130)))
    signal.signal(signal.SIGTERM, lambda *_: (cleanup(), sys.exit(143)))
    os.makedirs(RUNS, exist_ok=True)
    base = a.seed or str(random.randint(1000, 99999))

    say("=" * 70)
    say("  HEADS-UP ARENA  -  Short Deck PLO5, official judge, duplicate format")
    say("=" * 70)
    results, paired, names = [], [], ("bot A", "bot B")
    md = os.path.join(RUNS, "results.md")
    logdir = None

    def scoreboard(final=False):
        pa = sum(r.get("points", {}).get("agent1", 0) for r in results)
        pb = sum(r.get("points", {}).get("agent2", 0) for r in results)
        wa = sum(r["verdict"] == "AGENT1_WIN" for r in results)
        wb = sum(r["verdict"] == "AGENT2_WIN" for r in results)
        ch = len(results) - wa - wb
        n = len(paired)
        mean = sum(paired) / n if n else float("nan")
        sd = math.sqrt(sum((x - mean) ** 2 for x in paired) / (n - 1)) if n > 1 else float("nan")
        ci = 1.96 * sd / math.sqrt(n) if n > 1 else float("nan")
        lead = (f"{names[0]} AHEAD" if mean - ci > 0 else
                f"{names[1]} AHEAD" if mean + ci < 0 else "TOO CLOSE TO CALL")
        line = (f"{names[0]} {wa} wins, {ch} chops, {names[1]} {wb} wins  (points {pa:g}-{pb:g}); "
                f"pooled {names[0]} {mean:+.3f} +/- {ci:.3f} bb/deal -> {lead}")
        if final:
            say("=" * 70)
            say(f"  FINAL  {names[0]}  vs  {names[1]}")
            say(f"  {line}")
            say("=" * 70)
        else:
            say(f"    SCORE so far: {line}")
        return line

    try:
        judge_dir = fetch_judge(a.judge)
        bot_a, ca = fetch_bot(a.bot_a, a.ref_a, "bot A")
        bot_b, cb = fetch_bot(a.bot_b, a.ref_b, "bot B") if a.bot_b else (None, None)
        if bot_b and os.path.samefile(bot_a, bot_b):
            sys.exit("ERROR: both bots point at the same folder")
        if a.install:
            for path in filter(None, (bot_a, bot_b)):
                req = os.path.join(path, "requirements.txt")
                if os.path.isfile(req):
                    say(f"  installing {req} ...")
                    subprocess.run([PY, "-m", "pip", "install", "-q", "-r", req], check=False)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        tail_name = f"vs_{slug(a.bot_b)}" if a.bot_b else "baselines"
        logdir = os.path.join(RUNS, f"{stamp}_{slug(a.bot_a)}_{tail_name}")
        os.makedirs(logdir, exist_ok=True)
        say(f"  base seed {base}")
        if a.bot_b:
            say(f"  HEAD TO HEAD  ({a.seeds} match(es) x {a.deals} deals)")
        say("-" * 70)
        header_written = False

        for i in range(a.seeds if a.bot_b else 0):
            seed = f"{base}-{i}"
            say(f"  match {i + 1}/{a.seeds} (seed {seed}) ...")
            t0 = time.time()
            d = play((bot_a, "agent.py"), (bot_b, "agent.py"), judge_dir, seed, a.deals, logdir)
            if d is None:
                say("  match aborted.")
                break
            names = (name_of(d["_info"][0], "bot A"), name_of(d["_info"][1], "bot B"))
            results.append(d)
            paired += d.get("paired_bb", [])
            st = d.get("agent_stats", {})

            def summ(side):
                h = st.get(side, [])
                t = "/".join(f"{x.get('time_seconds', 0):.1f}s" for x in h)
                e = sum(x.get("errors", 0) for x in h)
                f = any(x.get("forfeited") for x in h)
                return f"time {t}, errors {e}" + (", FORFEITED" if f else "")
            v = {"AGENT1_WIN": f"{names[0]} WINS", "AGENT2_WIN": f"{names[1]} WINS"}.get(d.get("verdict"), "CHOP")
            lo, hi = d.get("ci", [float("nan")] * 2)
            say(f"    RESULT: {names[0]} {d['mean_bb_per_deal']:+.3f} bb/deal  95% CI [{lo:+.2f}, {hi:+.2f}]"
                f"  ->  {v}   ({time.time() - t0:.0f}s)")
            say(f"      A: {summ('agent1')}   |   B: {summ('agent2')}")
            scoreboard()
            with open(md, "a") as fh:        # saved immediately: Ctrl-C never loses a finished match
                if not header_written:
                    fh.write(f"\n## {stamp}  {names[0]} ({a.bot_a} @ {ca})  vs  {names[1]} ({a.bot_b} @ {cb})\n\n")
                    fh.write(f"| seed | {names[0]} bb/deal | 95% CI | verdict |\n| --- | --- | --- | --- |\n")
                    header_written = True
                fh.write(f"| {seed} | {d['mean_bb_per_deal']:+.3f} | [{lo:+.2f}, {hi:+.2f}] | {v} |\n")

        if a.bot_b and results and len(results) == a.seeds:
            line = scoreboard(final=True)
            with open(md, "a") as fh:
                fh.write(f"\n**Overall:** {line}\n")
        if a.baselines and (results or not a.bot_b):
            if not a.bot_b:
                with open(md, "a") as fh:
                    fh.write(f"\n## {stamp}  {a.bot_a} @ {ca}  vs practice bots\n")
            run_baselines([("A", bot_a)] + ([("B", bot_b)] if bot_b else []), judge_dir, base,
                          a.baseline_matches, a.deals, logdir, md)
    finally:
        cleanup()
        if not a.keep_logs and logdir and os.path.isdir(logdir):
            for f in os.listdir(logdir):
                if f.endswith(".log"):
                    os.remove(os.path.join(logdir, f))

    if a.bot_b and not results:
        sys.exit(1)
    say("=" * 70)
    say(f"  saved to {md}")
    if results:
        say(f"  watch the hands:  python viewer.py \"{logdir}\"")


if __name__ == "__main__":
    main()
