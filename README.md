# michelin

Sync place sources into Google Maps Saved Lists. Current sources are Michelin
Guide listings and local Google My Maps `.kml` / `.kmz` exports.

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

Enable local git hooks for this checkout:

```bash
git config core.hooksPath .githooks
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

Run the local fixture safety gate before committing fixture changes:

```bash
make fixture-safety
```

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
redaction. The local scanner blocks emails, token/cookie values, local user
paths, Google account names or labels, and Google account avatar URLs. Public
Google Maps reviewer names, contribution IDs, and public reviewer avatar URLs
are allowed.

Batch import every HTML snapshot in a debug directory:

```bash
uv run python tools/import_real_html_fixture.py \
  --source-dir /path/to/debug \
  --fixture-set google_maps \
  --captured-at 2026-02-17
```

Clean and verify existing fixture files:

```bash
uv run python -m michelin_scraper.devtools.redact_fixture_files
uv run python tools/scan_sensitive_fixtures.py --all
uv run python tools/scan_sensitive_git_history.py
```

See `docs/security/fixture-data-safety.md` for the full local gate and history
cleanup procedure.

## Direct Maps Probe (No Crawl)

Use this when validating search/open/match behavior for known places without
re-running source collection. Direct probe rows currently live under
`sync-michelin` because they use the Michelin bucket router.

Example JSONL input (`Name` required; `City`, `Address`, `Cuisine`, `Rating`,
`LevelSlug`, `Latitude`, `Longitude` optional):

```json
{"Name":"Biriyani Osawa","City":"Tokyo, Japan","Address":"B1F, 1-15-12 Uchikanda, Chiyoda-ku, Tokyo, 101-0047, Japan","Cuisine":"Indian","Rating":"Bib Gourmand","LevelSlug":"bib-gourmand"}
```

Run direct probe:

```bash
uv run python -m michelin_scraper \
  sync-michelin \
  --target "tokyo" \
  --maps-probe-rows-file /tmp/michelin-probe-rows.jsonl \
  --google-user-data-dir /tmp/michelin-codex-profile \
  --debug-sync-failures \
  --headless
```

## Sync Michelin

Michelin is a source adapter under the `sync-michelin` command:

```bash
uv run python -m michelin_scraper sync-michelin --target "tokyo"
```

Use `--help` as the canonical CLI reference:

```bash
uv run python -m michelin_scraper sync-michelin --help
```

Michelin-only options include `--target`, `--levels`, `--language`,
`--crawl-delay`, `--max-pages`, `--ca-bundle`, and `--insecure`.

## Sync Google My Maps KML/KMZ

Export your Google My Maps data as `.kml` or `.kmz`, then import the local file
into one Google Maps Saved List:

```bash
uv run python -m michelin_scraper sync-my-maps \
  --my-maps-file /path/to/my-map.kmz
```

Override the destination list name when needed:

```bash
uv run python -m michelin_scraper sync-my-maps \
  --my-maps-file /path/to/my-map.kml \
  --list-name "Taipei saved places"
```

If `--list-name` is omitted, the importer uses the KML document name, then the
file stem. My Maps imports create a single `imported` bucket and use
`{prefix}{scope}` as the default list naming template.

Supported KML rows are named `Placemark` entries with either an address or
valid `Point/coordinates`. Coordinates are parsed in KML order:
`longitude,latitude[,altitude]`; rows without coordinates use their address for
Google Maps search/match. The importer recognizes KML `<address>` plus common
ExtendedData address/city keys, including `address`, `formatted_address`,
`地址`, `city`, and `地區`. Unnamed placemarks and placemarks without address or
coordinates are skipped and reported in run warnings.

### My Maps note formatting

`sync-my-maps` does not add Michelin sync metadata such as `updated <date>` to
saved-place notes. By default it writes parsed KML placemark description fields
as a single `key: value | key: value` note. If a description has no parseable
fields, it falls back to the plain description text. My Maps notes are formatted
as a single line because Google Maps saved-place notes are easier to scan that
way:

```bash
uv run python -m michelin_scraper sync-my-maps \
  --my-maps-file /path/to/my-map.kml \
  --note-format raw
```

For 500 Bowls-style maps whose descriptions contain fields such as `總得碗數`,
`得獎菜色`, `地區`, `菜系`, `推薦評審`, `地址`, `營業時間`, and `電話`, use:

```bash
uv run python -m michelin_scraper sync-my-maps \
  --my-maps-file /path/to/500-bowls.kml \
  --note-format 500bowls
```

For custom maps, use a template. Template placeholders refer to parsed
description field names and output values only. Add labels directly in the
template when needed:

```bash
uv run python -m michelin_scraper sync-my-maps \
  --my-maps-file /path/to/my-map.kml \
  --note-format template \
  --note-template "{總得碗數}碗 | {得獎菜色} | 菜系: {菜系} | 地址: {地址} | 電話: {電話}"
```

Missing values are removed and common extra separators are cleaned up.

## Run State

State files are stored in `--state-dir` (or current directory when omitted):

- `{checkpoint-scope}-michelin-checkpoint.json` (legacy checkpoint filename
  suffix retained for existing state files)
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

- Checkpoint controls where source collection resumes next.
- Journal stores only rows that were confirmed as successfully synced.

When debug logging is enabled, Maps sync logs every accepted and rejected
row-to-candidate decision with the source row name, query string, candidate
name/address/category, and match signals. Taiwan rows with district-level
address text also try a narrowed `name + district` query before broader
city-level queries, for example `首烏 板橋區` before `首烏 New Taipei, 臺灣`.

If a run is interrupted or partially fails, retry with the same `--state-dir`
and `--ignore-checkpoint`. This reprocesses from page 1 while safely skipping
rows already recorded in the journal.

## Notes

- `sync-michelin --target` is required and accepts country or predefined city values.
- If the same value exists in both lists, city target takes precedence.
- Localized country listing URLs are resolved from the verified target matrix in
  `michelin_scraper/catalog/target_url_matrix.json`. If a localized country has
  no verified listing URL, the command fails early instead of scraping a guessed
  Michelin URL.
- The command automatically checks login state; if no session is found, it
  prompts whether to run interactive login immediately.
- Default level buckets are `stars`, `selected`, and `bib-gourmand`. Use
  `--levels one-star,two-star,three-star,selected,bib-gourmand` to keep star
  levels split into separate lists.
- Each selected output bucket maps to one Google Maps list name generated from
  `{prefix}{scope} Michelin {level_badge}`.
- `sync-my-maps --my-maps-file` requires a local `.kml` or `.kmz` export; My
  Maps viewer URLs are not fetched directly.
- Please respect Michelin Guide and Google Maps terms of use and rate limits.

## Architecture Note

The sync workflow is organized as source adapters feeding Google Maps Saved
Lists. Michelin resolves Michelin targets, crawls listing pages, and routes
restaurant rows into output buckets such as `stars`, `selected`, and
`bib-gourmand`. My Maps parses local KML/KMZ exports into a single `imported`
bucket. The shared sync core only receives bucketed place rows and writes them
to Google Maps lists.

Future sources should implement the same adapter boundary and produce rows with
at least `Name`; `Address`, `City`, `Latitude`, `Longitude`, and `Description`
should be included when available to improve Maps search and matching.

## Target Matrix Maintenance

Use the live matrix updater when Michelin changes localized country listing
URLs or when adding country targets:

```bash
uv run python tools/update_target_url_matrix.py --language zh-tw
```

To validate or update one country:

```bash
uv run python tools/update_target_url_matrix.py --language zh-tw --country greece
```

The updater writes supported entries only after a live Michelin listing page
passes HTTP/content checks. Countries without a verified single country listing
remain marked `unsupported-or-undiscovered`.

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
  sync-michelin \
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
