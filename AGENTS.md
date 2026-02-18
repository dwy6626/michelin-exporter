# Repository Rules

- Use English as the primary language across the repository.
- Use English for code comments and developer-facing docs by default.
- Multilingual string literals are allowed in source/tests when they are real data, user-facing content, or matching fixtures.
- Do not replace multilingual literals with Unicode escapes unless explicitly required.
- CLI usage documentation must be complete in `--help` output.
- All help option values and choices must reference `constants` definitions to keep a single source of truth.
- `README.md` is developer-facing only and must not be treated as the canonical user usage reference.
- Any newly added debug behavior (flags, logs, troubleshooting flows, probe modes, or failure artifacts) must be documented in `README.md`.
- Refactors are allowed to be breaking by default. Do not add compatibility wrappers, aliases, shim modules, re-export facades, or deprecated code paths unless the user explicitly requests backward compatibility.
- When replacing architecture, remove superseded entrypoints/modules instead of forwarding old APIs to new implementations.
- Target only the project's declared Python version (`pyproject.toml` `requires-python`). Do not add legacy compatibility code for older Python versions.
- Agents must run their own targeted validation (unit/integration/smoke as applicable) before asking users to execute manual verification commands.
- Agents must always fix lint violations in files they change before finalizing work.
- For routing/list-selection bugs, validation must include at least one non-unit integration path exercised by de-identified real HTML fixtures; synthetic-only unit tests are not sufficient.
- For Google Maps place-matching/search regressions, validation must include representative probe rows encoded in de-identified real HTML fixtures.
- For Google Maps selector work, selectors must be derived from real DOM evidence (captured runtime snapshots committed as fixtures), not guessed from static assumptions.
- Google Maps selector changes must be validated by fixture-based unittest coverage built from recorded, de-identified real page snapshots.
- Do not classify selector failures as generic "selector changed" without runtime evidence. Failure reasons must preserve actionable runtime context from the failing page/surface.
- In `--debug-sync-failures` runs, every page-level Maps sync failure must persist an HTML snapshot under `state_dir/debug/` for postmortem.
- Persisted debug HTML snapshots must be de-identified before storage (redact account identifiers, email, cookie/session/token values, and local user-path data).
- Unit tests for Maps selector/note-save behavior must be based on recorded, de-identified HTML snapshots captured from real runs; synthetic-only DOM guesses are not acceptable coverage.
- Never use a user's real account profile data for tests. Use isolated temporary profiles and non-destructive modes for browser automation validation.

## Rule Enforcement

- Treat every rule as a release gate, not a suggestion. If a rule cannot be satisfied, stop and report the blocker instead of claiming completion.
- Completion is invalid without verification evidence. Final handoff must include: changed files, commands run, and pass/fail status.
- Prefer machine-verifiable rules over prose-only rules. When adding or updating rules, also add or update tests/checks so violations fail fast.
