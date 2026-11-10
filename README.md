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

For local browser launches, install the bundled Playwright browser runtime:

```bash
uv run playwright install chromium
```

Google Maps can also use installed Google Chrome or attach to a running browser
with `--google-cdp-url`. Michelin's browser-rendered fetch fallback prefers
bundled Chromium; if its executable is missing, it retries with installed Google
Chrome through Playwright's `channel="chrome"`. This supports environments where
the bundled browser cannot be installed. The Michelin fallback uses a separate
temporary browser session, independent of the attached Google session.

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

## Attach to an Existing Google Session

Both sync commands accept optional `--google-cdp-url` with an existing Chromium
CDP HTTP or WebSocket endpoint. For a browser already exposing CDP at port 9222,
run a small read-only Taiwan probe:

```bash
uv run python -m michelin_scraper sync-michelin \
  --target taiwan --language zh_TW \
  --google-cdp-url http://127.0.0.1:9222 \
  --maps-probe-only --max-pages 1 --max-rows-per-page 1 \
  --on-missing-list stop --debug-sync-failures \
  --state-dir .michelin-state/cdp-probe
```

CDP mode uses the browser's default context and opens one new tab. It bypasses
profile preparation and browser launch; `--google-user-data-dir`, browser channel,
and `--headed/--headless` do not configure the attached browser. Normal shutdown,
forced shutdown, and interrupts close only the new tab and disconnect Playwright.
The new tab receives a 1280 × 720 viewport so Maps search remains usable when
the external browser reports zero window dimensions; this applies only to that tab.
Its own CDP target also bypasses the Maps service worker during navigation.
Home navigation waits for the actual search control after the response commits;
a streamed document can remain in `loading` while the application is usable.
The bypass ends with the owned tab; browser storage, service workers and other
tabs are not changed.
Search readiness requires changed rendered result content. A changed query URL
can still show the preceding restaurant panel. Opening a result also waits for
its title to replace that panel. A repeated query may retain its title when the
resolved concrete place URL agrees with the rendered name.
The external browser, context, and preexisting tabs remain open. Recovery and
debug capture never adopt or inspect another tab. If the owned tab closes, retry
the command. Connection errors fail without launching another Google browser.

Authentication in CDP mode uses the Maps UI without reading session cookies. If
the account controls are still loading, it waits up to 10 seconds. The visible
Google Account control confirms sign-in; its `accounts.google.com/SignOutOptions`
link is an account-menu control, not a sign-in prompt. If
authentication is required, sign in within the existing browser and retry; the
CLI does not prompt to launch a persistent profile. Probe-only mode skips
authentication and list preparation and performs search/match checks without
creating lists, saving places, or changing sharing. Its state and de-identified
failure HTML are stored under `--state-dir`, including `debug/` snapshots when
`--debug-sync-failures` is set. Default list names and buckets still apply.

If a result click changes the URL but leaves the search feed visible, the driver
can reload that observed restaurant link. It requires the same physical place ID,
rendered title and Save control before matching continues. Street queries retain
official lane and alley numbers for branch selection.

Omitting `--google-cdp-url` retains persistent-profile mode.

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
  suffix retained for existing state files; includes the source cursor, row
  counters, and confirmed saved-row keys in `synced_row_keys`)
- `{scope}-maps-sync-errors.jsonl` (only when row failures occur)
- `debug/{scope}-maps-debug-<context>-<timestamp>.html` (de-identified
  snapshots written when `--debug-sync-failures` captures page-level sync
  failures, when row-level Maps failures occur during save/search/note
  operations, and also written on runtime failure/interrupt capture paths)
- `<record-fixtures-dir>/<debug-stem>.html` and
  `<record-fixtures-dir>/<debug-stem>.metadata.json` (written only when
  `--record-fixtures-dir` is set)

The checkpoint stores both source progress and confirmed saved-row keys. There
is no separate sync journal file.
When resuming an existing checkpoint, each confirmed save also atomically updates
`synced_row_keys` without advancing the source cursor. A failure partway through
a page therefore retains confirmed saves and retries that page on the next run.
Successful completion also retains the checkpoint and saved-row keys, with the
cursor on the terminal page. Rerunning checks that page and skips confirmed
rows; completion no longer deletes the deduplication ledger.

Fail-fast shuts down the producer queue, cancels detail jobs that have not
started, and waits for active requests to finish their bounded timeouts. Every
browser-rendered source fetch closes its own browser and Playwright runtime;
these source browsers are independent of the attached Google browser. No shared
Chrome service needs to be stopped during cleanup.

When debug logging is enabled, Maps sync logs every accepted and rejected
row-to-candidate decision with the source row name, query string, candidate
name/address/category, and match signals. Taiwan rows with district-level
address text also try a narrowed `name + district` query before broader
city-level queries, for example `首烏 板橋區` before `首烏 New Taipei, 臺灣`.

Normal resume retains the source cursor and saved-row keys. A row-capped write
can advance the source cursor beyond rows that were collected but not saved.
To process the full source after a capped run, restart from page 1 with the same
`--state-dir`, `--ignore-checkpoint`, and `--ignore-existing-lists-check`, and
remove the page/row limits. Ignoring the checkpoint clears its saved-row keys;
list reuse checks skip places already present in the exact generated target lists.

If Maps search fails, the error includes visible search controls; use the
de-identified HTML under `state_dir/debug/` to identify the failing surface.
If source queries unexpectedly lack an address or district, check the source
fetch messages first. Restaurant detail fetches require a nonempty address;
incomplete HTTP content is retried in a browser that waits for the recorded
address element within the navigation timeout. Persistent failures report the
source URL and required selector instead of treating challenge HTML as a valid
detail page. A detail fetch that still has no address stops the listing producer
before that incomplete identity can reach Maps or advance the page checkpoint.
Matching thresholds and address-conflict checks are unchanged.

An address-like Maps title can contain a restaurant's street-number branding
(for example, `No.10 Nanmen南門壹食`). The matcher recognizes this only when the
title contains the exact source restaurant name independently of its address,
the category is food service, and both city and house number corroborate the
source without a house-number conflict. Bare address pins, coordinate titles,
and businesses located inside another venue retain their safety checks.
Browser-rendered source navigation retries Chromium's transient
`net::ERR_NETWORK_CHANGED` on the same owned page, up to the configured HTTP
attempt limit and within the original navigation deadline. Other browser
navigation errors retain their existing failure behavior.
For restaurant names whose only distinctive token is a number, matching also
requires a shared street anchor. A shared city and house number alone can
identify an unrelated restaurant. Coordinate and cross-script fallbacks respect
hard vetoes from the matcher.
When a saved list and a place are visible together, candidate extraction prefers
the place-detail heading over the list heading, keeping the name with the
address and category from the opened place.
Direct place results are assessed in place. Article cards for neighboring
businesses or other tenants below that panel are not treated as search results.
The non-food category veto also recognizes the recorded Traditional Chinese
tourist-attraction category.
An unrecognized category needs strong name or alias identity. A fuzzy name and
shared city cannot establish that a campground or another unfamiliar category
is the source restaurant. Exact food-business names with generic shop categories
retain their existing handling.
Pagination follows the source's next-page link even when it exceeds the highest
visible numbered button. That number is a progress estimate, not a limit; the
visited-URL guard still prevents pagination loops. Taiwan's page 9 can link to
page 10 while showing numbered buttons only through 9.

## Notes

- `sync-michelin --target` is required and accepts country or predefined city values.
- If the same value exists in both lists, city target takes precedence.
- Localized country listing URLs are resolved from the verified target matrix in
  `michelin_scraper/catalog/target_url_matrix.json`. If a localized country has
  no verified listing URL, the command fails early instead of scraping a guessed
  Michelin URL.
- The Traditional Chinese Taiwan country listing uses
  `/tw/zh_TW/selection/taiwan-region/restaurants`. Nearby and new-restaurant
  recommendation sections are excluded from results, including on empty searches.
- The command automatically checks login state; persistent-profile mode prompts
  whether to run interactive login immediately when no session is found. CDP
  mode requires signing in within the attached browser before retrying.
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

If bundled Chromium is missing, Michelin's browser-rendered fallback retries
installed Google Chrome automatically. If neither browser is available:

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

In persistent-profile mode, the tool auto-cleans stale lock/session files in
`--google-user-data-dir` before launch. CDP mode does not touch profile files.
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
