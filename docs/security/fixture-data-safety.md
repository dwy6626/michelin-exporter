# Fixture Data Safety

Fixture files must not contain real personal account names, account labels,
account avatar URLs, email addresses, session/cookie/token values, or local user
paths.

Reviewer names, public contribution IDs, and public reviewer avatar URLs are
allowed because they are public Google Maps content. Google account surfaces are
not public reviewer content and must be redacted.

## Local Gates

Enable the repository hooks once per checkout:

```bash
git config core.hooksPath .githooks
```

The pre-commit hook runs these local checks before a commit is created:

```bash
uv run python tools/scan_sensitive_fixtures.py --staged
uv run python tools/scan_sensitive_fixtures.py --all
uv run python -m unittest \
  tests.test_real_html_fixtures_google_maps \
  tests.test_import_real_html_fixture_tool \
  tests.test_sensitive_fixture_scanner
```

`--staged` reads fixture blobs from the git index with `git show :<path>`. It
does not trust the working-tree copy, so a staged leak cannot be hidden by
rewriting the file after `git add`.

Run the same gate manually:

```bash
make fixture-safety
```

## Import And Cleanup

Import raw snapshots through the fixture importer so shared redaction and marker
checks run before fixture files are written:

```bash
uv run python tools/import_real_html_fixture.py \
  --source-dir /path/to/debug \
  --fixture-set google_maps \
  --captured-at YYYY-MM-DD
```

Clean existing fixture files with:

```bash
uv run python -m michelin_scraper.devtools.redact_fixture_files
uv run python tools/scan_sensitive_fixtures.py --all
```

## Git History

Scan committed fixture blobs locally before any remote cleanup:

```bash
uv run python tools/scan_sensitive_git_history.py
```

If history contains leaked fixture data, rewrite history only after explicit
operator approval. After a rewrite, rerun the history scanner locally before any
force push.
