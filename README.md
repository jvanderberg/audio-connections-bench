# audio-connections-bench

A single-shot LLM benchmark using the daily puzzles from
[Audio Connections](https://connections.audio/).

This is a **track-metadata benchmark**, not an audio-recognition benchmark. Each
model sees the 16 artist/title pairs and must sort them into four groups of four.
That isolates the Connections-style grouping problem from song recognition.

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

## Pilot results

Three released puzzles, July 20–22, 2026:

| model | effort | solved | average correct groups | average output tokens | average cost |
|---|---|---:|---:|---:|---:|
| Claude Fable 5 | CLI default | **3/3** | 4.00 | 2,003 | $0.048 |
| GPT-5.6 Sol | high | 1/3 | 2.67 | 1,033 | subscription |

All six attempts returned valid four-by-four JSON groupings. Sol found two
correct groups on each of its two misses.

## Test

```sh
python3 -m unittest discover -s tests -v
```
