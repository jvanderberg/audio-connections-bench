# audio-connections-bench

A single-shot LLM benchmark using the daily puzzles from
[Audio Connections](https://connections.audio/).

This is a **track-metadata benchmark**, not an audio-recognition benchmark. Each
model sees the 16 artist/title pairs and must sort them into four groups of four.
That isolates the Connections-style grouping problem from song recognition.

![Audio Connections solve-rate grid for 25 models across seven daily puzzles](assets/results-light-v3.png#gh-light-mode-only)
![Audio Connections solve-rate grid for 25 models across seven daily puzzles](assets/results-dark-v3.png#gh-dark-mode-only)

## Spoiler isolation

Audio Connections publishes a JSON catalogue containing both clues and answers.
The benchmark never puts that structure into the prompt:

1. It fetches `https://connections.audio/api/v0/puzzle.json` into a gitignored
   local cache.
2. It extracts only each track's artist and title; theme labels, notes, iTunes
   IDs, author, and schedule metadata stay out of the prompt.
3. It deterministically shuffles all 16 tracks, because the source JSON stores
   them in four already-solved theme arrays.
4. It assigns opaque track numbers and grades those number sets against the
   answer key after the model returns.
5. Codex runs from an empty temporary working directory, so its read-only tools
   cannot inspect the answer-bearing catalogue cache or prior results. Claude
   tools are disabled, and OpenRouter uses a raw chat-completions request.

Puzzle-wide constraints are included as `Puzzle note:` because players receive
those notes in the game.

## Usage

```sh
./audio_bench.py run --date 2026-07-20 --models codex
./audio_bench.py run --day 1 --models claude:claude-sonnet-5@high --no-record
./audio_bench.py run --start 2026-07-01 --end 2026-07-20 --jobs 4
./audio_bench.py summary
```

Runner specs are `runner[:model][@effort]`:

- `claude:<model>[@effort]` uses Claude Code with tools disabled. `@effort`
  requires a Claude Code release whose CLI exposes `--effort`; omit it on older
  releases such as 2.0.9.
- `codex[:<model>][@effort]` uses the Codex CLI and ChatGPT account.
- `codex-api:<model>[@effort]` uses an isolated API-key Codex home.
- `openrouter:<model-id>[@effort]` uses OpenRouter chat completions.

With no `--models`, the roster comes from `models.txt`. Set
`OPENROUTER_API_KEY` or `OPENAI_API_KEY` in the environment or a gitignored
`.env` when the selected runner needs it.

The first run caches the current catalogue. Pass `--refresh` to fetch it again.
Recorded attempts are skipped when the date, model, prompt version, and puzzle
fingerprint match. If an upstream puzzle is corrected, its fingerprint changes
and it becomes eligible to run again.

Future scheduled puzzles are rejected by default even though the upstream API
contains them. `--allow-unreleased` is an explicit spoiler override.

Results append to `results/runs.jsonl`. Each record includes puzzle identity and
fingerprint, model/runner, parse validity, exact and partial scores, token/cost/
time data, the parsed guess, and raw model response.

## Results (July 16–22, 2026)

| model | reasoning | solved | avg groups | avg out tokens | avg cost |
|---|---|---:|---:|---:|---:|
| Claude Opus 5 | high | **6/7** | 3.71 | 2,051 | $0.11 |
| Gemini 3.6 Flash | default | **6/7** | 3.71 | 4,861 | $0.037 |
| Gemini 3.5 Flash | default | **6/7** | 3.71 | 5,450 | $0.049 |
| Gemini 3.1 Pro | default | **6/7** | 3.71 | 9,764 | $0.12 |
| Gemini 2.5 Pro | default | **6/7** | 3.57 | 6,649 | $0.067 |
| GPT-5.5 (codex) | default | **5/7** | 3.43 | 1,483 | – |
| Claude Fable 5 | high | **5/7** | 3.43 | 2,920 | $0.28 |
| Claude Opus 4.8 | high | **5/7** | 3.43 | 3,797 | $0.15 |
| GPT-5.6 Terra | high | **5/7** | 3.29 | 6,699 | – |
| Kimi K3 | max | **5/7** | 3.14 | 32,161 | $0.48 |
| GPT-5.6 Sol | high | **4/7** | 3.14 | 1,828 | – |
| Claude Sonnet 5 | high | **4/7** | 3.14 | 6,294 | $0.16 |
| GPT-5.6 Luna | high | **4/7** | 3.14 | 11,564 | – |
| Claude Opus 4.5 | high | **4/7** | 3.14 | 13,186 | $0.41 |
| Claude Sonnet 4.5 | high | **3/7** | 2.86 | 4,060 | $0.11 |
| GLM-5.2 | default | **3/7** | 2.43 | 35,124 | $0.13 |
| DeepSeek V4 Pro | default | **2/7** | 1.86 | 11,976 | $0.022 |
| Kimi K2.6 | default | **2/7** | 1.14 | 41,703 | $0.13 |
| GPT-5.4 mini | default | **1/7** | 2.00 | 10,722 | – |
| DeepSeek V4 Flash | default | **1/7** | 1.86 | 12,459 | $0.003 |
| Gemini 3.5 Flash Lite | default | **1/7** | 1.14 | 128 | $0.000 |
| MiniMax M3 | default | **1/7** | 1.14 | 36,366 | $0.052 |
| Qwen3.6 35B A3B | default | **1/7** | 0.86 | 44,402 | $0.044 |
| Claude Haiku 4.5 | high | **0/7** | 1.29 | 9,278 | $0.061 |
| GPT-4.1 mini | none | **0/7** | 0.86 | 109 | – |

No model swept the week. Claude Opus 5 and four Gemini models tied at 6/7;
Claude Opus 5 used the fewest output tokens of them, while Gemini 3.6 Flash was
the cheapest. Puzzle difficulty varied sharply: July 19 was solved by 19/25
models, while July 21 was solved by only 5/25.

## Test

```sh
python3 -m unittest discover -s tests -v
```

Regenerate the graphical report with `python3 viz.py`, then capture light and
dark screenshots using the commands in `viz.py`'s module docstring.
