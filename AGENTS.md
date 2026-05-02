# Repository Rules

- Use English for code comments and developer-facing docs. Multilingual literals are allowed only for real data, user-facing content, or fixtures; do not replace them with Unicode escapes unless explicitly required.
- `--help` is the canonical CLI usage reference. Keep option values/choices sourced from `constants`; document any new debug flags, logs, probe modes, troubleshooting flows, or failure artifacts in `README.md`.
- Breaking refactors are allowed. Do not add compatibility wrappers, aliases, shim modules, re-export facades, deprecated paths, or legacy Python-version support unless explicitly requested.
- Agents must run targeted validation before handoff and fix lint violations in files they change.
- Tests and fixtures must never contain real personal account names or account labels. Redact Google account display names as `<redacted-account-name>` and use isolated temporary browser profiles only.
- Persisted debug HTML snapshots must be de-identified before storage: redact account identifiers, account names, email, cookie/session/token values, and local user-path data.
- Google Maps selector and note-save work must use recorded, de-identified real DOM snapshots committed as fixtures; synthetic-only DOM guesses are not acceptable coverage.
- Routing, list-selection, place-matching, and Google Maps search regressions need fixture-backed integration validation with representative de-identified real HTML/probe rows.
- Do not label selector failures as generic "selector changed"; preserve actionable runtime context from the failing page/surface. `--debug-sync-failures` must persist every page-level Maps sync failure HTML snapshot under `state_dir/debug/`.
- Treat these rules as release gates. If a rule cannot be satisfied, stop and report the blocker. Final handoff must include changed files, commands run, and pass/fail status.
