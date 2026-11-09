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
resolved concrete place URL agrees with the rendered name. Award maintenance reopens recorded, positively
owned physical list URLs and validates their IDs and exact titles. This avoids
the Saved dashboard's search-suggestion overlay without changing other tabs.
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

Omitting `--google-cdp-url` retains persistent-profile mode.

## Daily Taiwan Michelin maintenance

The HTTP-first official-source probe with admitted source-DOM fallback reuses the production Michelin listing/detail
parser, follows every next page beyond visible page estimates, and requires a
positive consistent source count, complete detail addresses, and unique rows.

```bash
TMPDIR=/home/dwhermes/.hermes/cache/scratch \
  uv run python -m michelin_scraper.sources.taiwan_michelin \
  --state-dir "$TMPDIR/michelin-source-probe"
uv run python -m michelin_scraper.application.taiwan_michelin_maintenance --help
uv run python -m michelin_scraper.application.taiwan_michelin_maintenance \
  --mode bootstrap --state-dir .michelin-state/taiwan
uv run python -m michelin_scraper.application.taiwan_michelin_maintenance \
  --mode validate-applied --state-dir .michelin-state/taiwan
```


The source session tries ordinary HTTP first. Once unavailable, the remaining collection
uses the admitted source transport. It reads the official source normally through the existing cron Chrome
at `http://127.0.0.1:9223`, using one owned 1280x900 page and original DOM or normal
same-origin fetch HTML. No cloud reader, paid quota, credential setup, cookie
extraction, profile clone or new browser service is used. The source CLI requires
the matching `44d6d0d58cbc` public external-runner lease before any CDP access;
the wrapper holds that lease throughout collection and owned-page cleanup, releases
it fully, then lets changed revisions enter normal full-runner admission.
Playwright creation, fetches and cleanup are serialized on one owner thread despite
the parser's concurrent detail workers. Only the owned page and client connection
are closed; existing tabs, browser, contexts and authentication remain intact.
Require exact requested/resolved official URLs and HTTP 200. The same strict parser
validates all pages and details; challenges, foreign redirects, partial/empty rows
and transport failures fail closed. `source-probe.json` records transport, source
status/URL, timestamps and original HTML hashes. The source budget is 850 seconds
inside the wrapper's 900-second limit, with individual source calls limited to 45
seconds and serialized source starts spaced by at least one second. Browser congestion is `not_executed`, preserving failure and applied state.
Empty HTTP 202 navigation responses receive at most two ordinary navigation retries after 3/6-second backoffs within the same source budget; persistent challenges fail closed. No applied or pending revision is consumed on a failed source.

`source-probe.json` and `source-rows.jsonl` are fresh official evidence, never applied
state. `source-failure.json` preserves failed transport/parse context; a challenge, partial pagination or empty source cannot authorize removal.
The fingerprint tracks name, rating, address, city and guide year, ignoring row
ordering and transient links. Michelin updates between annual award editions, so
production checks daily at 09:00 Asia/Taipei. A healthy unchanged check is silent and
invokes no Google Maps, authentication, model or full runner. Public source browsing may be required.

`bootstrap` derives `applied.json` only from the actual complete accepted source
rows and SHA-bound signed-in/anonymous acceptance. `validate-applied` is read-only:
it emits verified applied metadata or JSON `null` when absent, and rejects any
inconsistent evidence. Neither command imports restaurants or invents acceptance.
Only changed revisions invoke the existing scoped production writer and full
source-to-membership reconciliation under the external runner's browser lease:

```bash
uv run python -m michelin_scraper.application.taiwan_michelin_maintenance \
  --mode sync --state-dir .michelin-state/taiwan \
  --probe-dir "$MICHELIN_OFFICIAL_PROBE_DIR" --google-cdp-url "$BROWSER_CDP_URL"
```

`--probe-dir` reuses the complete fresh official probe supplied by the normal job;
if omitted, `sync` writes a fresh probe in the runtime `TMPDIR`. It preserves the
three existing list names, URLs, sharing and accepted corrections, verifies all
required replacements before removing exact positively owned stale memberships,
and protects physical places still required in the same list. It records pending
work separately and advances applied only after complete signed-in and independent
anonymous inventories reconcile every source row. Source and actual accepted note
bindings are retained separately when descriptions change without a business change.
The first actual source failure stays quiet; every later consecutive source check
failure notifies, valid source resets that streak, and required sync failure reports
immediately through the owning external transport. No test reports are sent.

## Sync Google My Maps KML/KMZ

The current official Taiwan 500 Bowls / 500 Dishes source inventory can be
inspected without opening a browser or changing Saved Lists:

```bash
uv run python -m michelin_scraper.sources.taiwan_500_awards --help
uv run python -m michelin_scraper.sources.taiwan_500_awards \
  --state-dir .michelin-state/500-awards
```

This source probe discovers the current homepage script and story feed, follows
the semantic current map/winner link, and downloads the official public KML.
It retains only the exact current Taiwan award folder, validates every selected
point, preserves the document's default list name and distinct branch addresses,
and records an order-independent fingerprint of names, addresses and award
fields. The KML subsets and `source-probe.json` are evidence, not applied sync
state. A missing, empty, invalid or partially importable roster fails the probe.
The Dishes Asia and sponsor folders are excluded.

The executable maintenance entrypoint discovers both latest editions and uses
the normal Saved List matcher and note writer:

```bash
uv run python -m michelin_scraper.application.taiwan_500_maintenance --help
TMPDIR=/home/dwhermes/.hermes/cache/scratch \
  uv run python -m michelin_scraper.application.taiwan_500_maintenance \
  --google-cdp-url http://127.0.0.1:9222 \
  --state-dir .michelin-state/500-awards --mode sync
```

`sync` resumes all pending rows without a row cap. `single-write` saves one
pending row per list for an initial staged check; it never marks the source
applied. `verify` independently reads both complete lists without importing
pending rows. Verification requires existing viewer sharing and stops before
enabling it. It resolves the recorded viewer short link anonymously to the same
owned list, then reads the complete public inventory without entering the sharing
menu. Each successful normal save is recorded in the award's
`memberships.json`. Failures preserve these checkpoints for the next invocation.
Award-specific note failures remain retry work even if Google confirms the
membership; they cannot consume the preceding row's confirmation or mark an
unverified note complete.
Exact-row identity corrections live in `verified-place-evidence.json`; they
require matching original name/address and public evidence URLs. Notes preserve
the official address and disclose the correction and evidence.
Dishes public notes omit `區域:`, `官方:` and `管理:`. Their official source URL,
source keys and confirmed physical place identities stay in internal provenance.
Bowls notes retain their existing format. Dishes inventory audits, retry work and
stale cleanup use the internal physical identity ledger; signed DOM cards join
uniquely to the complete anonymous inventory by exact name and note. Unknown,
duplicate or conflicting memberships still stop reconciliation.

For an already accepted Dishes list, the leased 9223 runner can use
`--mode clean-dishes-notes --note-evidence-dir "$TMPDIR/dishes-note-cleanup"`
with the existing state directory. This uses accepted local KML without source
rediscovery or membership imports. It edits only the three rejected note segments
through the existing list textareas, preserving all other text and order. The
first edit must survive reopening and complete physical membership readback
before further edits. Use `--note-limit 1` for a single staged edit. The external
runner must hold its public browser lease for the whole migration/readback and
pass its admission token; a busy lane must defer before browser access.
The CLI enforces a fifteen-minute run budget. `dishes/note-cleanup.json` checkpoints
each exact source key/place ID and its verified old-to-new note transformation.
Continuation reads real notes before writing remaining mismatches. The evidence
directory contains de-identified DOM, complete signed/anonymous readbacks and
`receipt.json` with verified/pending counts. `failure.html` and `failure.json`
retain actionable page context and write count on failure; resume using the same
state and evidence directories after resolving the reported failure.

Recovery defaults to at most 25 edits per invocation; larger `--note-limit` values
are capped at 25. The shared typed editor enforces a minimum three-second interval
and waits for the recorded `POST /maps/preview/entitylist/updateitem` acknowledgment.
Complete signed/anonymous inventories run at batch boundaries rather than after
every note. Uncertain writes remain checkpointed and must be read on restart.
No live request is needed to test the owner's cooldown entrypoint.

Pass `--note-baseline PATH` with the original physical-note and five-list metadata
evidence for final completion. `receipt.json` reports `partial` or `completed`,
verified/pending/write counts and the final completion evidence path. A contended
maintenance lock reports `busy` with `browser_opened: false`. `failure.json`
classifies `challenge`, `auth_blocked` or `failed` and retains `retry_after_seconds`
where supplied. Challenge/429 and auth failures never retry in the same occurrence.
The external dedicated recovery owner persists its hourly-or-longer cooldown,
resumes bounded batches, and disables only itself after complete physical notes,
Bowls, Michelin notes and list metadata are verified. Missing completion evidence
is an explicit local blocker. Pacing reduces request pressure without guaranteeing
that Google will not issue another challenge.

If Google shows an unusual-traffic challenge, cleanup stops before further edits
and identifies that surface explicitly. Clear the challenge manually in the
existing cron browser before resuming; cleanup does not retry or bypass it.

An evidenced restaurant inside a hotel can require its own venue name, so the
containing hotel cannot satisfy the source row or the final inventory audit.
Award matching also requires restaurant-name evidence; generic cuisine and
house-number overlap alone cannot accept a different restaurant.
Award writes additionally require a rendered address and concrete physical
place URL. A query URL cannot be checkpointed as a confirmed place.
If a result click changes the URL but leaves its search feed visible, the
driver can reload that observed restaurant link and require its same physical
place ID, rendered title and Save control before normal matching continues.
Street queries retain official lane/alley numbers for branch selection.
Award recovery opens a fresh owned tab in the same admitted CDP browser when
returning to Maps home. It closes its preceding owned tab and leaves all
pre-existing tabs untouched; it never creates a browser context or native window.

Explicit user exclusions live in `user-skips.json` under the same state directory.
Each decision pins the award, edition, official semantic fingerprint, exact source
key and complete original row, with the user's reason, quote and evidence provenance.
The approved 2026 COAST and nabo ulv entries are skipped before matching, syncing
and readback reconciliation; SAAKA and Restaurant Bon appétit receive no replacement
membership or transferred award. These are user decisions, not independently proved
permanent closures. A changed source revision or identity requires renewed approval;
a later edition does not inherit the skips. Unknown rows still fail normally.
Official KML, source counts and source fingerprints retain all rows. Acceptance
separately records eligible physical counts and `user_skipped_source_rows`.
Policy changes invalidate the maintenance no-change check. Confirmed memberships
and exact recorded notes are reused on restart.

Acceptance requires every eligible source row, distinct physical identity, complete
note, and membership to reconcile against signed-in rendered cards and a fresh
anonymous public Maps renderer response sent without cookies or authentication.
The latter is an HTTP inventory, not an anonymous browser DOM snapshot. A
server-declared count larger than the response, an unidentified row, an
unexpected physical ID, or a duplicate fails acceptance. Sharing uses only
"Send link to view". `final-signed-in.json`, its redacted HTML,
`final-anonymous.json`, and the viewer-link dialog are retained per award.
`acceptance-manifest.json` and `applied.json` are written only after both lists
pass. `inventory-before-cleanup.json` retains the complete rendered card values
even when the first audit fails; textarea values are not serialized in HTML.
Debug HTML is redacted under `debug/`; `last-failure.json` and
`last-verification-failure.json` preserve the failing stage. A failed or partial
run does not consume the source update.
Award snapshots remove script state, reviewer account names and profile links
before writing to disk, while retaining restaurant and list DOM controls.

Each edition reuses the positively task-owned list and the latest document's
default name. Only task-marked obsolete memberships may be removed, after both
complete replacement inventories have passed. Unidentified entries stop
reconciliation. Other Saved Lists are outside this command's scope. Meaningful
source fingerprints include award fields and coordinates, independent of row
order or fetch time. Healthy unchanged checks verify current browser DOM auth,
update `last-check.json`, and print nothing.

The parent-owned daily detector `7f9de040f528` invokes the same command for changed sources from this
repository after its external runner acquires the existing cron browser lease:

```bash
TMPDIR=/home/dwhermes/.hermes/cache/scratch \
  uv run python -m michelin_scraper.application.taiwan_500_maintenance \
  --google-cdp-url "$BROWSER_CDP_URL" \
  --state-dir .michelin-state/500-awards --mode sync
```

The runner must supply `BROWSER_CDP_URL=http://127.0.0.1:9223` and the existing
`HERMES_CRON_BROWSER_LANE_TOKEN` while holding
`~/.hermes/cron/locks/browser-cdp.lock` through process cleanup. The command
validates the matching lease token and live mutex; cron use of 9222 is rejected.
See the external deployment's `requirements/browser-cron-admission.md` and
`requirements/hermes-external-runner.md`. This repository does not acquire or
replace the outer lease, create jobs, or send notifications. The parent owns
daily scheduling (09:00 Asia/Taipei), verified activation, and delivery. Its lightweight
official-source gate runs before browser admission: unchanged accepted sources invoke
no browser, authentication, model or full maintenance runner. A required changed-source
sync checks current signed-in DOM evidence under the existing full-job browser lease;
historical authentication failures do not establish the current browser state.

For the captured 2026 edition, Bowls has 472 official map locations. Dishes has
319 locations in `500盤2026`; its 89 Asia and 105 sponsor points are excluded.
The Dishes announcement reports 314 restaurants. Retained article/image-to-map
comparison evidence is in `source-reconciliation.json`; counts alone do not
establish a branch explanation. The authorized latest Taiwan map controls the
import inventory, and its individual locations and award dishes remain intact.
The two user-authorized skips leave 317 eligible Dishes memberships while its
official source count remains 319. See [the scoped contract](requirements/taiwan-500-awards.md).

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
