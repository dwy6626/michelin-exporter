# michelin

Scrape Michelin Guide listings and sync restaurants into Google Maps lists.

## Requirements

- [uv](https://docs.astral.sh/uv/) installed
- Python 3.14+

## Developer Setup

Create/update the virtual environment and install dependencies:

```bash
uv sync
```

Playwright browser runtime is required for Google Maps automation:

```bash
uv run playwright install chromium
```

Install developer dependencies:

```bash
uv sync --extra dev
```

## Lint Flow

Auto-fix lint issues first:

```bash
uv run --extra dev ruff check --fix .
```

Then run a clean lint check:

```bash
uv run --extra dev ruff check .
```

Run static type checks:

```bash
uv run --extra dev basedpyright
```

## Real-HTML Fixture Tests (Offline)

Run the full unittest suite offline:

```bash
uv run python -m unittest discover -s tests
```

Run Michelin fixture-only tests:

```bash
uv run python -m unittest tests.test_real_html_fixtures_michelin
```

Run Google Maps fixture-only tests:

```bash
uv run python -m unittest tests.test_real_html_fixtures_google_maps
```

Fixture files are stored under:

- `tests/fixtures/michelin/`
- `tests/fixtures/google_maps/`

Each fixture has a metadata sidecar (`*.metadata.json`) with capture scenario,
language, and `sanitized=true`.

### Importing New Real HTML Fixtures

Use the fixture import tool to convert raw snapshots into de-identified fixture
files plus metadata:

```bash
uv run python tools/import_real_html_fixture.py \
  --source /path/to/raw-snapshot.html \
  --fixture-set google_maps \
  --name maps-search-surface-2026-02 \
  --language en \
  --scenario maps-search-surface
```

The importer refuses to write output if sensitive markers remain after
redaction (email/token/cookie/user-path).

Batch import every HTML snapshot in a debug directory:

```bash
uv run python tools/import_real_html_fixture.py \
  --source-dir /path/to/debug \
  --fixture-set google_maps \
  --captured-at 2026-02-17
```

## Direct Maps Probe (No Crawl)

Use this when validating search/open/match behavior for known restaurants
without re-running Michelin scraping.

Example JSONL input (`Name` required; `City`, `Address`, `Cuisine`, `Rating`,
`LevelSlug`, `Latitude`, `Longitude` optional):

```json
{"Name":"Biriyani Osawa","City":"Tokyo, Japan","Address":"B1F, 1-15-12 Uchikanda, Chiyoda-ku, Tokyo, 101-0047, Japan","Cuisine":"Indian","Rating":"Bib Gourmand","LevelSlug":"bib-gourmand"}
```

Run direct probe:

```bash
uv run python -m michelin_scraper \
  --target "tokyo" \
  --maps-probe-rows-file /tmp/michelin-probe-rows.jsonl \
  --google-user-data-dir /tmp/michelin-codex-profile \
  --debug-sync-failures \
  --headless
```

## Run State

State files are stored in `--state-dir` (or current directory when omitted):

- `{scope}-michelin-checkpoint.json`
- `{scope}-maps-sync-journal.json`
- `{scope}-maps-sync-errors.jsonl` (only when row failures occur)
- `debug/{scope}-maps-debug-<context>-<timestamp>.html` (de-identified
  snapshots written when `--debug-sync-failures` captures page-level sync
  failures, when row-level Maps failures occur during save/search/note
  operations, and also written on runtime failure/interrupt capture paths)
- `<record-fixtures-dir>/<debug-stem>.html` and
  `<record-fixtures-dir>/<debug-stem>.metadata.json` (written only when
  `--record-fixtures-dir` is set)

Checkpoint and journal serve different purposes:

- Checkpoint controls where crawl resumes next.
- Journal stores only rows that were confirmed as successfully synced.

If a run is interrupted or partially fails, retry with the same `--state-dir`
and `--ignore-checkpoint`. This reprocesses from page 1 while safely skipping
rows already recorded in the journal.

## Notes

- `--target` is required and accepts country or predefined city values.
- If the same value exists in both lists, city target takes precedence.
- The command automatically checks login state; if no session is found, it
  prompts whether to run interactive login immediately.
- Default level buckets are `stars`, `selected`, and `bib-gourmand`. Use
  `--levels one-star,two-star,three-star,selected,bib-gourmand` to keep star
  levels split into separate lists.
- Each selected output bucket maps to one Google Maps list name generated from
  `{prefix}{scope} Michelin {level_badge}`.
- Please respect Michelin Guide and Google Maps terms of use and rate limits.

## Troubleshooting

If you see a Playwright error that Chromium executable does not exist:

```bash
uv sync
uv run playwright install chromium
```

If it still fails:

```bash
rm -rf ~/Library/Caches/ms-playwright
uv run playwright install chromium
```

If browser launch fails with `Target page, context or browser has been closed` (or SIGTRAP):

```bash
# close all Chrome windows first, then retry with a fresh profile directory
uv run python -m michelin_scraper \
  --target "tokyo" \
  --google-user-data-dir ~/.michelin-gmaps-profile-fresh
```

The tool auto-cleans stale lock/session files in `--google-user-data-dir` before launch.
If it still crashes, remove the profile directory and re-login:

```bash
rm -rf ~/.michelin-gmaps-profile
```

If startup fails with `List already exists at startup`, rerun with:

```bash
--ignore-existing-lists-check
```

Without `--ignore-existing-lists-check`, if a row is detected as already saved
in the target list, the run exits with an explicit error.

If you need to recover from a bad checkpoint after failures:

```bash
--ignore-checkpoint
```

If Google sign-in shows `This browser or app may not be secure`, login once in
regular Chrome using the same profile path, then rerun CLI:

```bash
open -na "Google Chrome" --args \
  --user-data-dir="$HOME/.michelin-gmaps-profile-fresh" \
  "https://www.google.com/maps?hl=en"
```
