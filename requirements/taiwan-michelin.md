# Taiwan Michelin production maintenance

Maintain the three existing accepted Taiwan Michelin lists only: stars, selected,
and Bib Gourmand, preserving their names, viewer URLs, sharing and physical branch
identity. Reuse the existing source/detail parser, production matcher and writer.
The 2026-11-09 continuation authorizes completing daily deployment and activation
after genuine acceptance; prior skip-only stage restrictions do not apply.

Official source is
https://guide.michelin.com/tw/zh_TW/selection/taiwan-region/restaurants.
Michelin's official 2026 Taiwan new-restaurants and live-updates articles establish
both annual awards and updates between editions. Check every day 09:00 Asia/Taipei.
Probe over HTTP without Google, browser, authentication, model or full runner.
Follow and validate every next link, require positive stable total counts and
unique complete name/rating/address rows. Compare sorted semantic identities,
ignoring row ordering and transient URLs. Partial, challenged, failed or empty
sources fail closed and cannot remove memberships or consume a revision.

Bootstrap only from the actual complete 434-row acceptance/source rows, checking
all bound hashes, zero missing/duplicate/unidentified memberships and equal source,
signed-in and anonymous counts. Never hand-write success. A healthy unchanged
revision is silent. Pending remains separate from applied.

Changed revisions invoke the existing scoped production writer under the existing
full-job 9223 browser mutex and matching lease; retain credentialed browser tools
for genuine login needs. Preserve all existing contexts, tabs and authentication;
close only owned pages, never clone profiles/read cookies/restart/bypass CAPTCHA.
Verify every replacement across all three lists before exact positively owned stale
membership removal. Retain physical places still required in that list. Complete
independent signed-in and anonymous source reconciliation is required before applied
advances. Preserve source rows separately from actual accepted note bindings.

First actual source failure is quiet; each later consecutive failure counts and
notifies, valid source resets the streak, and changed-required sync failure reports
immediately. Ordinary unchanged produces no report. True updated reports contain
only real concise list changes and links via the existing verified transport. Never
send infrastructure tests or supervisor network notices. Production cadence/model
pins belong to the owning automation; engineering/debug remains gpt-6.1-sol.

Focused gates: complete/partial/duplicate/pagination source checks using recorded
real rows, HTTP-only challenge never launching browser, actual acceptance hash/count
rejection, recorded note reconciliation and owned browser lifecycle. Fresh production
entrypoint evidence is required before activation; an upstream challenge remains
pending acceptance and cannot be relabeled unchanged or complete.

The source session tries ordinary HTTP for each listing and detail first. When
unavailable it uses the existing Firecrawl vendor's official `/v2/scrape` API with
`formats=["rawHtml"]` and `maxAge=0`, without credentials, cookies or browser use.
The original source and resolved URL must match the requested official URL, with
vendor HTTP 200, success true and original-source status 200. The same production
parser consumes that HTML; vendor errors remain failures. `source-probe.json`
records per-fetch transport, original status/URL, timestamps and HTML hashes.
The source budget is 850 seconds, with at most four detail requests in flight,
vendor starts spaced by 250 ms and each vendor request limited to 75 seconds.
No applied or pending revision is consumed on a failed source.
