#!/usr/bin/env python3
"""Render results/runs.jsonl into the README report (viz.html).

Screenshot with:
  npx playwright screenshot --viewport-size "1140,1220" --color-scheme light viz.html assets/results-light.png
  npx playwright screenshot --viewport-size "1140,1220" --color-scheme dark viz.html assets/results-dark.png
"""

import json
from html import escape
from pathlib import Path

from audio_bench import PROMPT_VERSION, RESULTS_FILE, parse_model_spec

ROOT = Path(__file__).resolve().parent

# Same visual language as connections-bench: ordinal blue group score and an
# aqua output-token bar, with theme-aware report chrome.
CELL = {0: "#86b6ef", 1: "#5598e7", 2: "#2a78d6", 3: "#256abf", 4: "#184f95"}
BAR = "#1baf7a"

LABELS = {
    "claude:claude-fable-5@high": "Claude Fable 5",
    "claude:claude-fable-5": "Claude Fable 5",
    "claude:claude-opus-4-8@high": "Claude Opus 4.8",
    "claude:claude-opus-4-5@high": "Claude Opus 4.5",
    "claude:claude-sonnet-5@high": "Claude Sonnet 5",
    "claude:claude-sonnet-4-5@high": "Claude Sonnet 4.5",
    "claude:claude-haiku-4-5@high": "Claude Haiku 4.5",
    "codex": "GPT-5.5 (codex)",
    "codex:gpt-5.6-sol@high": "GPT-5.6 Sol",
    "codex:gpt-5.6-terra@high": "GPT-5.6 Terra",
    "codex:gpt-5.6-luna@high": "GPT-5.6 Luna",
    "codex:gpt-5.4-mini": "GPT-5.4 mini",
    "codex-api:gpt-4.1-mini": "GPT-4.1 mini",
    "openrouter:deepseek/deepseek-v4-pro": "DeepSeek V4 Pro",
    "openrouter:deepseek/deepseek-v4-flash": "DeepSeek V4 Flash",
    "openrouter:moonshotai/kimi-k2.6": "Kimi K2.6",
    "openrouter:moonshotai/kimi-k3": "Kimi K3",
    "openrouter:z-ai/glm-5.2": "GLM-5.2",
    "openrouter:minimax/minimax-m3": "MiniMax M3",
    "openrouter:qwen/qwen3.6-35b-a3b": "Qwen3.6 35B A3B",
    "openrouter:google/gemini-3.1-pro-preview": "Gemini 3.1 Pro",
    "openrouter:google/gemini-3.5-flash": "Gemini 3.5 Flash",
    "openrouter:google/gemini-2.5-pro": "Gemini 2.5 Pro",
    "openrouter:google/gemini-3.6-flash": "Gemini 3.6 Flash",
    "openrouter:google/gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
}

IMPLICIT_EFFORT = {
    "codex-api:gpt-4.1-mini": "none",
    "openrouter:moonshotai/kimi-k3": "max",
}


def effort_label(spec: str) -> str:
    _, _, effort = parse_model_spec(spec)
    return effort or IMPLICIT_EFFORT.get(spec, "default")


def load_runs() -> list[dict]:
    if not RESULTS_FILE.exists():
        return []
    return [json.loads(line) for line in RESULTS_FILE.read_text().splitlines()
            if line.strip()]


def build() -> str:
    latest = {}
    for run in load_runs():
        if run.get("prompt_v") == PROMPT_VERSION:
            latest[(run["date"], run["model"])] = run
    if not latest:
        raise RuntimeError("no current-prompt results to visualize")

    dates = sorted({date for date, _ in latest})
    roster = {
        line.strip() for line in (ROOT / "models.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    models = sorted({model for _, model in latest if model in roster})
    stats = []
    for model in models:
        runs = [latest[(date, model)] for date in dates if (date, model) in latest]
        completed = [run for run in runs if not run.get("error")]
        solved = sum(bool(run.get("solved")) for run in completed)
        groups = sum(run.get("correct_groups", 0) for run in runs) / len(runs)
        tokens = (sum(run.get("tokens_out") or 0 for run in completed)
                  / max(len(completed), 1))
        costs = [run["cost_usd"] for run in completed
                 if run.get("cost_usd") is not None]
        cost = sum(costs) / len(costs) if costs else None
        stats.append((model, solved, len(runs), groups, tokens, cost))
    stats.sort(key=lambda item: (-item[1] / item[2], -item[3], item[4]))
    max_tokens = max(item[4] for item in stats) or 1

    day_headers = "".join(
        f'<div class="day"><span>{escape(date[5:])}</span></div>' for date in dates
    )
    rows = []
    for model, solved, count, groups, tokens, cost in stats:
        cells = []
        for date in dates:
            run = latest.get((date, model))
            if run is None:
                cells.append('<div class="cell miss" title="no attempt"></div>')
                continue
            score = 0 if run.get("error") else run.get("correct_groups", 0)
            state = "error" if run.get("error") else (
                "solved" if score == 4 else f"{score}/4 groups"
            )
            cells.append(
                f'<div class="cell" style="background:{CELL[score]}" '
                f'title="{date}: {state}"></div>'
            )
        bar_width = max(2, round(tokens / max_tokens * 190))
        cost_text = "–" if cost is None else (
            f"${cost:.3f}" if cost < 0.10 else f"${cost:.2f}"
        )
        rows.append(f"""
      <div class="row">
        <div class="name">{escape(LABELS.get(model, model))}</div>
        <div class="reason">{escape(effort_label(model))}</div>
        <div class="cells">{''.join(cells)}</div>
        <div class="solved">{solved}/{count}</div>
        <div class="groups">{groups:.2f}</div>
        <div class="tok"><span class="tokbar" style="width:{bar_width}px"></span>
          <span class="tokval">{tokens:,.0f}</span></div>
        <div class="cost">{cost_text}</div>
      </div>""")

    first = f"{dates[0][5:7]}/{dates[0][8:]}"
    last = f"{dates[-1][5:7]}/{dates[-1][8:]}"
    period = first if first == last else f"{first}–{last}/2026"
    return f"""<!doctype html>
<meta charset="utf-8">
<title>audio-connections-bench results</title>
<style>
  :root {{
    --surface: #fcfcfb; --plane: #f9f9f7; --ink: #0b0b0b; --ink2: #52514e;
    --muted: #898781; --hairline: #e1e0d9; --ring: rgba(11,11,11,0.10);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --surface: #1a1a19; --plane: #0d0d0d; --ink: #ffffff; --ink2: #c3c2b7;
      --muted: #898781; --hairline: #2c2c2a; --ring: rgba(255,255,255,0.10);
    }}
  }}
  body {{ margin: 0; background: var(--plane);
    font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--ink); }}
  .fig {{ background: var(--surface); border: 1px solid var(--ring);
    border-radius: 8px; margin: 16px; padding: 22px 24px; width: 1108px; box-sizing: border-box; }}
  h1 {{ font-size: 17px; margin: 0 0 2px; font-weight: 600; }}
  .sub {{ color: var(--ink2); font-size: 12.5px; margin-bottom: 18px; }}
  .row, .hdr {{ display: grid;
    grid-template-columns: 150px 60px 414px 46px 58px 210px 54px;
    gap: 10px; align-items: center; padding: 6px 0; }}
  .hdr {{ color: var(--muted); font-size: 11px; border-bottom: 1px solid var(--hairline);
    padding-bottom: 7px; margin-bottom: 4px; }}
  .hdr .days {{ display: flex; gap: 6px; }}
  .day {{ width: 54px; text-align: center; }}
  .name {{ font-size: 12.5px; white-space: nowrap; }}
  .reason {{ color: var(--ink2); font-size: 11.5px; white-space: nowrap; }}
  .cells {{ display: flex; gap: 6px; }}
  .cell {{ width: 54px; height: 30px; border-radius: 4px; }}
  .cell.miss {{ background: transparent; box-shadow: inset 0 0 0 1px var(--hairline); }}
  .solved, .groups {{ font-variant-numeric: tabular-nums; font-size: 12.5px; text-align: right; }}
  .tok {{ display: flex; align-items: center; gap: 7px; }}
  .tokbar {{ height: 9px; border-radius: 0 5px 5px 0; background: {BAR}; display: inline-block; }}
  .tokval, .cost {{ color: var(--ink2); font-size: 11.5px; font-variant-numeric: tabular-nums; }}
  .cost {{ text-align: right; }}
  .legend {{ display: flex; gap: 14px; margin-top: 18px; padding-top: 12px;
    border-top: 1px solid var(--hairline); color: var(--ink2); font-size: 11.5px; align-items: center; }}
  .sw {{ width: 14px; height: 14px; border-radius: 3px; display: inline-block;
    vertical-align: -2px; margin-right: 5px; }}
</style>
<div class="fig">
  <h1>Audio Connections — single-shot solve rate by model</h1>
  <div class="sub">{period} · artist/title metadata · one attempt per puzzle · answer data and tools isolated</div>
  <div class="hdr">
    <div>model</div>
    <div>reasoning</div>
    <div class="days">{day_headers}</div>
    <div style="text-align:right">solved</div>
    <div style="text-align:right">avg groups</div>
    <div>avg output tokens per puzzle</div>
    <div style="text-align:right">avg cost</div>
  </div>
  {''.join(rows)}
  <div class="legend">
    <span>cell = groups correct:</span>
    <span><span class="sw" style="background:{CELL[0]}"></span>0</span>
    <span><span class="sw" style="background:{CELL[1]}"></span>1</span>
    <span><span class="sw" style="background:{CELL[2]}"></span>2</span>
    <span><span class="sw" style="background:{CELL[4]}"></span>4 (solved)</span>
    <span style="margin-left:auto">– = subscription</span>
  </div>
</div>
"""


if __name__ == "__main__":
    output = ROOT / "viz.html"
    output.write_text(build())
    print(f"wrote {output}")
