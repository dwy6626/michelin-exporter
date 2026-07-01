# Place Matcher Corpus Report

- Total corpus cases: 207
- Known-good Japan Michelin seed cases: 2
- Japan live probe attempts: `tokyo`, `japan`, and `tokyo --language zh-tw`; all returned 0 listing cards in this session, so no fabricated Japan probe cases were added.
- Known-good Taiwan observed probe cases: 123 selected from accepted probe candidates plus three reclassified safe weak-probe variants.
- Known rejects: 60 observed rejected candidates after replacing safe reclassified matches with clearly wrong candidates from JSONL diagnostics.
- My Maps unresolved: 22 retained from `.michelin-state/my-maps-500-2026-maps-sync-errors.jsonl`; 5 are safe auto-match cases, 16 remain manual, and 1 is an observed wrong-place reject.
- Accepted/rejected Taiwan cases were joined to the local KML source rows so row address/city evidence matches the actual probe input.

The committed fixture intentionally favors observed row/candidate pairs over synthetic mutations. Japan coverage remains small because no local or live de-identified Japan accepted probe corpus was available during implementation.

Expectation labels:

- `match`: 130 cases.
- `reject`: 61 cases.
- `manual`: 16 cases. Manual cases must remain `weak` because the available evidence is insufficient for an automatic save.
