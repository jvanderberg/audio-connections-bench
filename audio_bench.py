#!/usr/bin/env python3
"""Single-shot LLM benchmark for Audio Connections puzzles.

The published Audio Connections catalogue contains both tracks and answers.
This program flattens and deterministically shuffles each board before making
the prompt, then grades the model's opaque track-number groups separately.

Usage:
  ./audio_bench.py run --date 2026-07-20 --models codex
  ./audio_bench.py run --start 2026-07-01 --end 2026-07-20 --jobs 4
  ./audio_bench.py run --day 1 --no-record
  ./audio_bench.py summary
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
CATALOG_FILE = ROOT / "catalog" / "puzzle.json"
RESULTS_FILE = ROOT / "results" / "runs.jsonl"

CATALOG_URL = "https://connections.audio/api/v0/puzzle.json"
PROMPT_VERSION = 1
RATE_LIMIT_RETRIES = 5
RATE_LIMIT_BACKOFF_S = 15

PROMPT_TEMPLATE = """\
Solve the Audio Connections puzzle. Group the 16 tracks into four groups of four.
Use each track number exactly once.
{constraint}
{tracks}

Respond with ONLY a JSON object, no other text:
{{"groups": [{{"theme": "...", "tracks": [1, 2, 3, 4]}}, ...]}}
"""


# ---------------------------------------------------------------- catalogue

def fetch_catalog(refresh: bool = False) -> dict:
    """Load the published catalogue, caching a local spoiler-bearing copy."""
    if CATALOG_FILE.exists() and not refresh:
        data = json.loads(CATALOG_FILE.read_text())
    else:
        req = urllib.request.Request(
            CATALOG_URL, headers={"User-Agent": "audio-connections-bench"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        CATALOG_FILE.parent.mkdir(exist_ok=True)
        CATALOG_FILE.write_text(json.dumps(data, indent=2) + "\n")
    validate_catalog(data)
    return data


def validate_catalog(data: object) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("puzzles"), list):
        raise ValueError("catalogue has no puzzles array")
    for puzzle in data["puzzles"]:
        if not isinstance(puzzle, dict):
            raise ValueError("catalogue puzzle is not an object")
        themes = puzzle.get("themes")
        if not isinstance(themes, list) or len(themes) != 4:
            raise ValueError(f"day {puzzle.get('day')} does not have four themes")
        tracks = [track for theme in themes for track in theme.get("tracks", [])]
        if len(tracks) != 16:
            raise ValueError(f"day {puzzle.get('day')} does not have 16 tracks")
        if any(not isinstance(t.get("artist"), str) or
               not isinstance(t.get("title"), str) for t in tracks):
            raise ValueError(f"day {puzzle.get('day')} has an invalid track")


def puzzle_fingerprint(puzzle: dict) -> str:
    raw = json.dumps(puzzle, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def board(puzzle: dict) -> tuple[list[dict], dict[frozenset[int], str]]:
    """Return a shuffled answer-free board and the separately held answer key.

    Public catalogue order is grouped by theme, so flattening alone would leak
    every answer. The stable per-puzzle seed makes comparisons reproducible.
    """
    tagged: list[tuple[int, dict, str]] = []
    source_index = 0
    for theme in puzzle["themes"]:
        for track in theme["tracks"]:
            tagged.append((source_index, {
                "artist": track["artist"],
                "title": track["title"],
            }, theme["theme"]))
            source_index += 1

    seed_material = f"audio-connections-bench:{puzzle.get('id')}:{puzzle.get('date')}"
    seed = int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:8], "big")
    random.Random(seed).shuffle(tagged)

    clues: list[dict] = []
    group_numbers: dict[str, list[int]] = {}
    for number, (_, clue, theme) in enumerate(tagged, start=1):
        clues.append({"number": number, **clue})
        group_numbers.setdefault(theme, []).append(number)
    answers = {frozenset(numbers): theme for theme, numbers in group_numbers.items()}
    return clues, answers


def make_prompt(puzzle: dict) -> str:
    clues, _ = board(puzzle)
    tracks = "\n".join(
        f"{c['number']:>2}. {c['artist']} — {c['title']}" for c in clues
    )
    constraint = puzzle.get("constraint")
    constraint_line = f"Puzzle note: {constraint}\n" if constraint else ""
    return PROMPT_TEMPLATE.format(constraint=constraint_line, tracks=tracks)


def select_puzzles(catalog: dict, args: argparse.Namespace) -> list[dict]:
    puzzles = catalog["puzzles"]
    if args.day is not None:
        selected = [p for p in puzzles if p.get("day") == args.day]
    elif args.date:
        selected = [p for p in puzzles if p.get("date") == args.date]
    elif args.start and args.end:
        start = dt.date.fromisoformat(args.start)
        end = dt.date.fromisoformat(args.end)
        if end < start:
            raise ValueError("--end must be on or after --start")
        selected = [p for p in puzzles
                    if start <= dt.date.fromisoformat(p["date"]) <= end]
    else:
        raise ValueError("run requires --day, --date, or --start and --end")
    if not selected:
        raise ValueError("no scheduled puzzle matches that selection")

    if not args.allow_unreleased:
        now = dt.datetime.now(dt.timezone.utc)
        future = [p for p in selected
                  if dt.datetime.fromisoformat(p["releaseAt"].replace("Z", "+00:00")) > now]
        if future:
            dates = ", ".join(p["date"] for p in future[:3])
            raise ValueError(
                f"selection includes unreleased puzzle(s): {dates}; "
                "use --allow-unreleased only if intentional"
            )
    return selected


# ---------------------------------------------------------------- runners

def parse_model_spec(spec: str) -> tuple[str, str | None, str | None]:
    """Parse runner[:model][@effort]."""
    runner, sep, rest = spec.partition(":")
    if runner not in RUNNERS:
        raise ValueError(f"unknown runner {runner!r} in model spec {spec!r}")
    if not sep:
        return runner, None, None
    model, effort_sep, effort = rest.partition("@")
    return runner, model or None, effort if effort_sep and effort else None


def secret(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                key, _, candidate = line.strip().partition("=")
                if key == name and candidate:
                    value = candidate.strip().strip('"')
    if not value:
        raise RuntimeError(f"{name} not set (export it or put it in .env)")
    return value


def codex_api_home() -> Path:
    home = ROOT / ".codex-api"
    home.mkdir(exist_ok=True)
    (home / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": secret("OPENAI_API_KEY")})
    )
    (home / "config.toml").write_text('preferred_auth_method = "apikey"\n')
    return home


def read_with_deadline(req: urllib.request.Request, timeout: int) -> bytes:
    start = time.monotonic()
    chunks = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                raise TimeoutError(
                    f"request exceeded {timeout}s ({sum(map(len, chunks))} bytes received)"
                )
            chunk = resp.read1(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def run_openrouter(prompt: str, model: str | None, effort: str | None,
                   timeout: int) -> dict:
    if not model:
        raise ValueError("openrouter spec needs a model id")
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "usage": {"include": True},
    }
    if effort:
        body["reasoning"] = {"effort": effort}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {secret('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
        },
    )
    raw = b""
    for retry in range(RATE_LIMIT_RETRIES):
        try:
            raw = read_with_deadline(req, timeout)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or retry == RATE_LIMIT_RETRIES - 1:
                raise
            time.sleep(RATE_LIMIT_BACKOFF_S * 2 ** retry)
    if not raw.strip():
        raise RuntimeError("openrouter returned no completion payload")
    data = json.loads(raw)
    if data.get("error"):
        raise RuntimeError(f"openrouter error: {data['error']}")
    usage = data.get("usage", {})
    details = usage.get("completion_tokens_details") or {}
    return {
        "text": data["choices"][0]["message"].get("content") or "",
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_in_cached": (usage.get("prompt_tokens_details") or {}).get(
            "cached_tokens", 0
        ),
        "tokens_out": usage.get("completion_tokens", 0),
        "tokens_reasoning": details.get("reasoning_tokens"),
        "cost_usd": usage.get("cost"),
        "model_used": data.get("model", model),
    }


def run_claude(prompt: str, model: str | None, effort: str | None,
               timeout: int) -> dict:
    cmd = ["claude", "-p", "--tools", "", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
    data = json.loads(proc.stdout)
    usage = data.get("usage", {})
    model_usage = data.get("modelUsage", {})
    model_used = max(
        model_usage.items(),
        key=lambda pair: pair[1].get("outputTokens", 0),
        default=(None, None),
    )[0]
    return {
        "text": data.get("result", ""),
        "tokens_in": usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0),
        "tokens_in_cached": usage.get("cache_read_input_tokens", 0),
        "tokens_out": usage.get("output_tokens", 0),
        "tokens_reasoning": None,
        "cost_usd": data.get("total_cost_usd"),
        "model_used": model_used,
    }


def run_codex(prompt: str, model: str | None, effort: str | None,
              timeout: int, api: bool = False) -> dict:
    env = os.environ.copy()
    if api:
        env["CODEX_HOME"] = str(codex_api_home())
    cmd = [
        "codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
        "-c", "tools.web_search=false", "--ephemeral", "--color", "never", "--json",
    ]
    if model:
        cmd += ["-m", model]
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    cmd.append(prompt)

    # Codex can read its working directory even in the read-only sandbox. Run
    # it in an empty temporary directory so the answer-bearing cache and prior
    # results are not available to the model.
    with tempfile.TemporaryDirectory(prefix="audio-connections-attempt-") as cwd:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout, env=env, cwd=cwd,
        )
    if proc.returncode != 0:
        detail = ""
        for line in proc.stdout.splitlines():
            if '"error"' in line or line.startswith("ERROR"):
                detail = line.strip()[:300]
        raise RuntimeError(
            f"codex exited {proc.returncode}: {detail or proc.stderr[:300]}"
        )
    response_text, usage = "", {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            response_text = item.get("text", "")
        elif event.get("type") == "turn.completed":
            usage = event.get("usage", {})
    return {
        "text": response_text,
        "tokens_in": usage.get("input_tokens", 0) - usage.get("cached_input_tokens", 0),
        "tokens_in_cached": usage.get("cached_input_tokens", 0),
        "tokens_out": usage.get("output_tokens", 0),
        "tokens_reasoning": usage.get("reasoning_output_tokens"),
        "cost_usd": None,
        "model_used": model,
    }


def run_codex_api(prompt: str, model: str | None, effort: str | None,
                  timeout: int) -> dict:
    return run_codex(prompt, model, effort, timeout, api=True)


Runner = Callable[[str, str | None, str | None, int], dict]
RUNNERS: dict[str, Runner] = {
    "claude": run_claude,
    "codex": run_codex,
    "codex-api": run_codex_api,
    "openrouter": run_openrouter,
}


# ---------------------------------------------------------------- grading

def extract_json(text: str) -> dict | None:
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:index + 1])
                    break
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def grade(text: str, answers: dict[frozenset[int], str]) -> dict:
    result = {
        "parsed": False, "valid": False, "correct_groups": 0,
        "solved": False, "guess": None,
    }
    parsed = extract_json(text)
    if not parsed or not isinstance(parsed.get("groups"), list):
        return result
    result["parsed"] = True
    guess = []
    for group in parsed["groups"]:
        tracks = group.get("tracks") if isinstance(group, dict) else None
        if not isinstance(tracks, list):
            return result
        try:
            numbers = [int(number) for number in tracks]
        except (TypeError, ValueError):
            return result
        guess.append({"theme": group.get("theme", ""), "tracks": numbers})
    result["guess"] = guess
    sets = [frozenset(group["tracks"]) for group in guess]
    used = [number for group in guess for number in group["tracks"]]
    result["valid"] = (
        len(sets) == 4 and all(len(group) == 4 for group in sets)
        and len(used) == 16 and set(used) == set(range(1, 17))
    )
    result["correct_groups"] = sum(group in answers for group in sets)
    result["solved"] = result["valid"] and result["correct_groups"] == 4
    return result


# ---------------------------------------------------------------- results and commands

def load_runs() -> list[dict]:
    if not RESULTS_FILE.exists():
        return []
    return [json.loads(line) for line in RESULTS_FILE.read_text().splitlines()
            if line.strip()]


def append_run(run: dict) -> None:
    RESULTS_FILE.parent.mkdir(exist_ok=True)
    with RESULTS_FILE.open("a") as output:
        output.write(json.dumps(run) + "\n")


def attempt(puzzle: dict, spec: str, timeout: int) -> dict:
    runner, model, effort = parse_model_spec(spec)
    _, answers = board(puzzle)
    run = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "date": puzzle["date"],
        "day": puzzle["day"],
        "puzzle_id": puzzle["id"],
        "puzzle_fingerprint": puzzle_fingerprint(puzzle),
        "model": spec,
        "runner": runner,
        "prompt_v": PROMPT_VERSION,
    }
    start = time.monotonic()
    try:
        output = RUNNERS[runner](make_prompt(puzzle), model, effort, timeout)
    except Exception as exc:  # record transport/runner failures for auditability
        run.update({
            "error": f"{type(exc).__name__}: {exc}",
            "solved": False,
            "correct_groups": 0,
            "duration_s": round(time.monotonic() - start, 1),
        })
        return run
    run["duration_s"] = round(time.monotonic() - start, 1)
    run.update(grade(output["text"], answers))
    run.update({key: output[key] for key in (
        "tokens_in", "tokens_in_cached", "tokens_out", "tokens_reasoning",
        "cost_usd", "model_used",
    )})
    run["raw"] = output["text"]
    return run


def model_specs(arg: str | None) -> list[str]:
    if arg:
        return [spec.strip() for spec in arg.split(",") if spec.strip()]
    roster = ROOT / "models.txt"
    if not roster.exists():
        raise ValueError("no --models given and no models.txt found")
    return [line.strip() for line in roster.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def cmd_run(args: argparse.Namespace) -> None:
    catalog = fetch_catalog(args.refresh)
    puzzles = select_puzzles(catalog, args)
    specs = model_specs(args.models)
    done = {
        (run["date"], run["model"], run.get("puzzle_fingerprint"))
        for run in load_runs()
        if not run.get("error") and run.get("prompt_v") == PROMPT_VERSION
    }
    tasks = [
        (puzzle, spec) for puzzle in puzzles for spec in specs
        if args.no_record or args.rerun or
        (puzzle["date"], spec, puzzle_fingerprint(puzzle)) not in done
    ]
    skipped = len(puzzles) * len(specs) - len(tasks)
    if skipped:
        print(f"skipping {skipped} attempt(s) already recorded (use --rerun to redo)")
    if not tasks:
        return

    print(f"running {len(tasks)} attempt(s) with {args.jobs} worker(s)")
    session_runs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(attempt, puzzle, spec, args.timeout): (puzzle, spec)
            for puzzle, spec in tasks
        }
        for future in concurrent.futures.as_completed(futures):
            run = future.result()
            session_runs.append(run)
            if not args.no_record:
                append_run(run)
            if run.get("error"):
                status = f"ERROR {run['error'][:90]}"
            else:
                status = "SOLVED" if run["solved"] else f"failed ({run['correct_groups']}/4)"
                status += (
                    f" in={run['tokens_in'] + run['tokens_in_cached']}"
                    f" out={run['tokens_out']} tok, {run['duration_s']}s"
                )
            print(f"  day {run['day']:>2}  {run['date']}  {run['model']:<28} {status}")
    if args.no_record:
        print("\nlocal-only run: results were not recorded")
        print_summary(session_runs)
    else:
        print_summary(load_runs())


def print_summary(runs: list[dict]) -> None:
    runs = [run for run in runs if run.get("prompt_v") == PROMPT_VERSION]
    if not runs:
        print("no runs recorded for the current prompt version")
        return
    latest = {}
    for run in runs:
        latest[(run["date"], run["model"])] = run
    by_model: dict[str, list[dict]] = {}
    for run in latest.values():
        by_model.setdefault(run["model"], []).append(run)

    print(f"\n{'model':<30} {'puzzles':>7} {'solved':>6} {'rate':>6} "
          f"{'avg grp':>7} {'avg out':>9} {'avg cost':>9} {'avg time':>8}")
    for model in sorted(by_model):
        model_runs = by_model[model]
        completed = [run for run in model_runs if not run.get("error")]
        solved = sum(bool(run.get("solved")) for run in completed)
        avg_groups = sum(run.get("correct_groups", 0) for run in model_runs) / len(model_runs)
        avg_out = (sum(run.get("tokens_out") or 0 for run in completed) /
                   len(completed)) if completed else 0
        costs = [run["cost_usd"] for run in completed if run.get("cost_usd") is not None]
        cost = f"${sum(costs) / len(costs):.3f}" if costs else "-"
        avg_time = (sum(run.get("duration_s", 0) for run in completed) /
                    len(completed)) if completed else 0
        print(f"{model:<30} {len(model_runs):>7} {solved:>6} "
              f"{solved / len(model_runs) * 100:>5.0f}% {avg_groups:>7.2f} "
              f"{avg_out:>9.0f} {cost:>9} {avg_time:>7.0f}s")


def cmd_summary(_: argparse.Namespace) -> None:
    print_summary(load_runs())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run models against scheduled puzzle(s)")
    selection = run.add_mutually_exclusive_group(required=True)
    selection.add_argument("--day", type=int, help="Audio Connections day number")
    selection.add_argument("--date", help="single puzzle date YYYY-MM-DD")
    selection.add_argument("--start", help="range start YYYY-MM-DD (requires --end)")
    run.add_argument("--end", help="range end YYYY-MM-DD (with --start)")
    run.add_argument("--models", help="comma-separated runner[:model][@effort] specs")
    run.add_argument("--jobs", type=int, default=4, help="parallel attempts")
    run.add_argument("--timeout", type=int, default=600, help="per-attempt seconds")
    run.add_argument("--rerun", action="store_true", help="rerun recorded attempts")
    run.add_argument("--no-record", action="store_true", help="do not append results")
    run.add_argument("--refresh", action="store_true", help="refresh catalogue cache")
    run.add_argument("--allow-unreleased", action="store_true",
                     help="permit future scheduled puzzles (contains spoilers)")
    run.set_defaults(func=cmd_run)

    summary = commands.add_parser("summary", help="print leaderboard table")
    summary.set_defaults(func=cmd_summary)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "run" and args.start and not args.end:
        parser.error("--start requires --end")
    if args.command == "run" and args.end and not args.start:
        parser.error("--end requires --start")
    try:
        args.func(args)
    except (ValueError, urllib.error.URLError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
