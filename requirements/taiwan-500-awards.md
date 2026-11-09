# Taiwan 500 awards maintenance contract

Scope: the latest complete official Taiwan edition of Bowls and Dishes, one
positively owned Saved List per award using the official document name. Preserve
Asia/sponsor exclusions, normal matcher vetoes, all other lists and Michelin state.
The public source probe and `--help` remain the source-discovery and CLI references.

## Approved 2026 user exclusions (2026-11-08)

The user said `看起來都歇業 就跳過吧` and explicitly approved skipping only:

| Award / edition | Original source name | Exact source key |
| --- | --- | --- |
| Dishes / 2026 | COAST | b65194b3b69ae24029a2134a19d8ea4e08514bf90fcdf39f7f3aa63b9821811c |
| Dishes / 2026 | nabo ulv | 58323509c234b70052a97902573341438bcc75cc063b2ae7a5b7ac85250a9ddc |

Persist decisions in `.michelin-state/500-awards/user-skips.json`, including full
original rows, reasons, authorization quote and original identity-evidence paths
(`dishes/coast-source-identity-blocker.json`, `dishes/nabo-source-identity-blocker.json`).
These exclusions express user policy; they do not independently prove permanent
closure, particularly for nabo ulv. Never substitute SAAKA or Restaurant Bon appétit
or transfer award claims to them.

Match decisions by award, edition, exact source key, full original row and official
semantic fingerprint. Changed current-edition identity/revision fails pending renewed
approval; a later edition does not inherit exclusions. Apply decisions before
matching, syncing, retries and verification. No substring/cuisine/closed-status rule
or implicit skip of unknown rows is permitted.

Keep all official source rows, KML and semantic fingerprints unchanged. Separate
source counts from eligible physical counts. For the unchanged recorded sources,
retain 472 Bowls and 319 Dishes source points (791 total), with 472 and 317 eligible
memberships (789 total) and exactly two explicit skips. Preserve original
`source_fingerprints`; changed live sources must be reported truthfully.

## Acceptance and replay

Use the existing state directory and confirmed membership/note checkpoints.
Replay must not reimport confirmed rows. Policy and identity-resolution fingerprints
participate in the maintenance no-change decision; the daily detector compares
official editions and semantic fingerprints through its existing acceptance reader.

Only real complete signed-in DOM and independent anonymous cookie-free renderer
readbacks with source-to-membership reconciliation may produce identical complete
`acceptance-manifest.json` and `applied.json`. Include original source counts,
eligible physical counts, explicit skips, zero unresolved rows and failures,
`source_version_applied=true` and `full_pair_acceptance=true`. Failed/partial runs
must not consume the source version. Unknown memberships, missing eligible rows,
incorrect notes, duplicate places and identity conflicts still fail acceptance.

The previous skip-only stage required verify-only execution and paused activation.
The user's 2026-11-09 continuation supersedes those stage restrictions: finish,
install and activate the promised daily production jobs after real acceptance.
Preserve all exact exclusions, accepted lists and normal source/sync failure policy.

Use the genuine public external job-scoped browser lease for `7f9de040f528` on CDP
9223 for changed-source sync and full verification. Create and close only owned
pages; preserve existing tabs/profile/auth. Current auth failure retains actionable
origin/DOM evidence without reusing an old diagnosis. Healthy unchanged official
sources return before browser admission and invoke no auth, model or full runner.

The owning automation runs daily 02:25 Asia/Taipei, with original manifest/job pins,
installer provenance and verified transport preserved. Activate only after initial
complete pair acceptance plus a fresh normal-entrypoint official-source invocation;
read back native enabled/next run and matching manifest enabled via public Hermes
CLI. No native JSON edits, Hermes core changes, supervisor network notices, test
report sends, shared transport changes or unrelated workflow changes are authorized.

## Dishes public notes (2026-11-09)

Remove only the `區域:`, `官方:` and `管理:` segments from all 317 existing 2026
Dishes notes. Preserve every other segment verbatim and in order, including any
identity-check explanation. Bowls notes, list membership, names, icons and sharing
remain unchanged. The official article URL and exact source-row reconciliation
identifier remain internal provenance; never delete source rows, fingerprints,
source keys or ownership evidence to remove their public display.

Use the accepted local KML and confirmed physical place identities without source
rediscovery or place search/import. Dishes audits, retries and stale cleanup must
associate physical renderer identities with the internal ownership ledger, with
unknown, duplicate and conflicting associations rejected. Signed DOM cards must
join uniquely to the complete anonymous physical inventory by exact name and note.

`--mode clean-dishes-notes --note-evidence-dir PATH` edits existing Saved List note
textareas using normal input/blur events. Require the public external cron lease
on 9223 throughout writes/readback. Reopen and independently read the first saved
note and unchanged complete membership set before scaling. Persist exact source
key/place ID, original/target note and real readback evidence in
`dishes/note-cleanup.json`; continuation rereads actual notes and writes only
remaining mismatches. `--note-limit 1` bounds the first stage. Save de-identified
DOM and complete signed/anonymous inventories beneath the runtime scratch path.
The migration has a fifteen-minute CLI budget and never forces pair reacceptance
or changes the source-version acceptance manifests.

## Finite low-frequency Dishes recovery (2026-11-09)

The user authorized durable monitoring and automatic continuation after Google's
restriction clears. A dedicated external native no-agent/local recovery job named
`五百盤附註低頻續跑` wakes at `40 * * * *` Asia/Taipei. Its durable cooldown and
installation receipts belong to the owning hermes-automation repository; the daily
02:25 awards and 02:45 Michelin detector schedules and source acceptance stay intact.
Known challenges do not authorize immediate live probes, login retries, model
invocations or remote test/supervisor notifications. No CAPTCHA solver, stealth,
human-mimicry, evasion jitter, proxy/IP/account/profile/browser-lane rotation, or
cookie/credential manipulation is allowed. Request pacing reduces pressure; it
cannot guarantee Google will never challenge the browser.

The shared existing-note writer enforces at least three seconds between writes,
serially, and waits for the recorded normal `POST entitylist/updateitem` HTTP 200
acknowledgment. An acknowledgment is not a verified note. No per-note anonymous
inventory polling is permitted. Each invocation edits at most 25 mismatches and
uses complete signed plus cookie-free anonymous inventories at the beginning and
end of its batch. The first real saved-row reopen barrier remains mandatory; its
existing verified checkpoint is reusable. Fresh complete readback alone advances
verified counts. Persist uncertain exact source/physical rows before edits and
retain them on exceptions; restarts read actual notes before writing.

The public lease must be for exact job `7f9de040f528`, CDP 9223, with its real
admission token. The CLI holds `maintenance.lock` through the full batch/readback;
a busy lock produces a local busy receipt before browser access. Reuse one owned
page, close only owned pages, and never reopen the 317-row list per note.
A challenge or HTTP 429 ends this occurrence without retries and preserves
Retry-After for the external cooldown owner. Authentication failure is blocked
without login retry. Unexpected failures preserve local actionable evidence.

`--note-baseline PATH` supplies immutable original Dishes/Bowls notes by physical
ID plus recorded icon/sharing metadata for both awards and all three accepted
Michelin lists. Where older icon evidence predates a permitted icon change, pin
current metadata once before the first recovery write; retain its provenance and
compare final metadata. Completion requires all original 317 Dishes IDs clean in
fresh signed/anonymous inventories, all original 472 Bowls IDs/notes unchanged in
fresh signed/anonymous inventories, unchanged Michelin notes, and unchanged icons
and sharing of these five lists. Missing comparable evidence blocks completion.
Only that full evidence permits the owner to disable just the recovery job and
its matching manifest; pending notes keep the durable owner enabled. No cosmetic
cleanup consumes a source version or rewrites pair acceptance.
