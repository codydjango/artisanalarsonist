## ADDED Requirements

### Requirement: Reconnaissance gate

A `RECON.md` document SHALL exist at the repo root before the crawler is run against the live site for production capture. It MUST identify the site URL, the apparent tech stack and hosting platform, any backend / JSON API endpoints discovered (with example responses), the gallery loading pattern (data-attribute full-res, parent `<a href>`, or click-to-open lightbox), an approximate scale (page and gallery counts), and any gotchas (Cloudflare, bot detection, geographic restrictions).

#### Scenario: RECON.md is present and complete
- **WHEN** an engineer prepares to run the production capture
- **THEN** `RECON.md` exists at the repo root and documents at minimum: site URL, tech stack, API endpoints (or "none found"), gallery loading pattern with concrete CSS selectors, approximate scale, and any access gotchas

#### Scenario: Selectors live in code, derived from recon
- **WHEN** the gallery module is implemented
- **THEN** the lightbox / thumbnail / close-button selectors used by the crawler match the patterns documented in `RECON.md` and are defined as named constants at the top of `crawler/gallery.py`

### Requirement: CLI entry point

The crawler SHALL be invoked via `uv run python archive.py` and accept the following flags using stdlib `argparse`: `--start-url URL` (required), `--output DIR` (default `./output`), `--max-pages N` (default 1000), `--max-depth N` (default 10), `--delay SECONDS` (default 1.0), `--reset`, `--ignore-robots`, and `--dry-run`. Unknown flags MUST cause the script to exit non-zero with a usage message.

#### Scenario: Required flag missing
- **WHEN** `uv run python archive.py` is invoked without `--start-url`
- **THEN** the script exits with a non-zero status and prints argparse's usage message naming `--start-url`

#### Scenario: Defaults applied
- **WHEN** the user supplies only `--start-url`
- **THEN** the run uses output dir `./output`, max-pages 1000, max-depth 10, delay 1.0s, robots.txt enforcement on, dry-run off, and does not reset existing state

#### Scenario: Reset wipes state
- **WHEN** the user passes `--reset`
- **THEN** any existing `output/state.sqlite` is deleted before the queue is initialized, and the run starts fresh from `--start-url`

#### Scenario: Dry-run captures nothing
- **WHEN** the user passes `--dry-run`
- **THEN** pages are rendered and candidate image URLs are reported to stdout/log, but no HTML files are written to `output/pages/`, no images are downloaded to `output/images/`, and no rows are written to `output/state.sqlite`

### Requirement: Page discovery

The crawler SHALL discover URLs by extracting same-origin `<a href>` links from each rendered page and enqueueing newly-seen URLs. It MUST respect `--max-depth` (depth from `--start-url`) and `--max-pages` (total pages enqueued) as hard caps. URLs matching non-content patterns — `mailto:`, `tel:`, `javascript:`, fragment-only (`#...`), and well-known social/share URLs — MUST be skipped.

#### Scenario: Same-origin only
- **WHEN** a rendered page contains an `<a href>` whose host differs from `--start-url`'s host
- **THEN** that URL is not enqueued

#### Scenario: Depth cap enforced
- **WHEN** a page at depth `--max-depth` is processed and yields child links
- **THEN** those children are not enqueued

#### Scenario: Page cap enforced
- **WHEN** the queue + completed count would exceed `--max-pages`
- **THEN** further URLs are not enqueued and a single `page_cap_reached` event is logged

#### Scenario: Non-content URLs skipped
- **WHEN** a page references `mailto:owner@example.com`, `tel:+15551234`, `#about`, or `https://twitter.com/intent/tweet?...`
- **THEN** none of those URLs are enqueued

### Requirement: Page rendering

For each queued URL the crawler SHALL: (1) navigate via Playwright `page.goto(url, wait_until="networkidle", timeout=30000)`, (2) execute a lazy-load scroll loop that scrolls by viewport height and waits 500 ms per iteration until `document.body.scrollHeight` is stable across two consecutive iterations or a hard iteration cap is reached, (3) wait for `networkidle` again post-scroll, and (4) capture `page.content()` as the HTML snapshot.

#### Scenario: Lazy-loaded content is materialized
- **WHEN** a page lazily loads images on scroll
- **THEN** after the scroll loop the captured HTML contains the lazy-loaded `<img>` tags (or their `data-src` populated) such that subsequent image extraction sees them

#### Scenario: Scroll loop has a safety cap
- **WHEN** a page's `scrollHeight` keeps growing indefinitely (e.g. an infinite-scroll feed)
- **THEN** the scroll loop terminates after the hard iteration cap and the run continues

#### Scenario: Navigation timeout is handled
- **WHEN** `page.goto` does not reach `networkidle` within 30 seconds
- **THEN** the failure is caught, logged as a `page_failed` event with the timeout reason, and the run continues with the next URL

### Requirement: HTML rewriting

After a page renders and its referenced images have been resolved and downloaded, the crawler SHALL parse the captured HTML with BeautifulSoup (`html.parser`) and rewrite `src`, `data-src`, `data-srcset`, `srcset`, and `<source srcset>` attributes to point at the locally-saved image files. Rewriting MUST only replace URLs whose download succeeded; URLs whose download failed MUST remain unchanged so an online viewer falls back to the live URL. The rewritten HTML SHALL be written to `output/pages/<slug>.html`, where the start URL becomes `index.html` and other URLs derive a slug from their path; slug collisions are resolved by appending a short hash.

#### Scenario: Successful image is rewritten to local path
- **WHEN** a page references `https://cdn.../foo.jpg` and that image was downloaded to `output/images/abc123.jpg`
- **THEN** the saved HTML's corresponding `<img src>` (or `data-src`, etc.) points at `../images/abc123.jpg`

#### Scenario: Failed image is preserved
- **WHEN** a page references an image whose download failed
- **THEN** the saved HTML retains the original absolute URL for that image, and the failure is recorded in `output/log.jsonl`

#### Scenario: Start URL becomes index.html
- **WHEN** the crawl completes for `--start-url`
- **THEN** the rewritten HTML for that URL is saved as `output/pages/index.html`

#### Scenario: Slug collisions are disambiguated
- **WHEN** two distinct URLs derive the same slug
- **THEN** the second one is saved with a short hash suffix and both files are listed in `output/log.jsonl`

### Requirement: Resumable state

The crawler SHALL persist crawl progress in `output/state.sqlite` so that re-running the script (without `--reset`) skips work that has already completed and resumes work that was queued or failed-with-attempts-remaining. The schema MUST track per-URL status (`queued`, `in_progress`, `completed`, `failed`), attempt count, last error, and completion timestamp for both pages and images.

#### Scenario: Re-run skips completed pages
- **WHEN** a page completed in a prior run and the user re-runs without `--reset`
- **THEN** the page is not re-rendered and its already-saved HTML is preserved

#### Scenario: Re-run retries failed-with-attempts-remaining
- **WHEN** a page failed once in a prior run (attempts < 3) and the user re-runs without `--reset`
- **THEN** the page is re-attempted

#### Scenario: Reset wipes state
- **WHEN** the user passes `--reset`
- **THEN** `output/state.sqlite` is deleted before the run begins and prior queue/completed/failed records do not influence the new run

### Requirement: Per-page failure isolation

Exceptions raised while processing one page MUST NOT abort the crawl. The crawler SHALL catch unexpected exceptions per-page, log them as `page_failed` events with full error detail, and proceed to the next URL. Transient failures (HTTP 5xx, network timeouts, Playwright `TimeoutError`) MUST be retried up to 3 times with exponential backoff (1s, 2s, 4s); 4xx responses are terminal and MUST NOT be retried.

#### Scenario: Single page exception does not abort run
- **WHEN** rendering one URL raises an unexpected exception
- **THEN** the exception is logged with traceback to `output/log.jsonl`, the page is marked `failed`, and the next URL in the queue is processed

#### Scenario: Transient failures are retried
- **WHEN** `page.goto` returns HTTP 503 or raises a network timeout
- **THEN** the URL is retried up to 3 total attempts with 1s/2s/4s backoff before being marked `failed`

#### Scenario: 4xx is terminal
- **WHEN** `page.goto` returns HTTP 404
- **THEN** the URL is marked `failed` immediately without retry

### Requirement: Politeness defaults

The crawler SHALL render at most one page at a time (Playwright concurrency = 1 by default) and download at most 5 images concurrently. It SHALL pause `--delay` seconds (default 1.0) between page navigations. It SHALL check `urllib.robotparser` against `robots.txt` before queueing each URL and skip disallowed URLs unless `--ignore-robots` is passed. It SHALL set the User-Agent string `PotteryArchiver/1.0 (personal archive; contact: codydjango@gmail.com)` for both Playwright navigations and `httpx` image fetches.

#### Scenario: Robots.txt is honored by default
- **WHEN** `robots.txt` disallows a discovered URL and `--ignore-robots` is not passed
- **THEN** the URL is not enqueued and a `robots_blocked` event is logged

#### Scenario: --ignore-robots overrides
- **WHEN** the user passes `--ignore-robots` and `robots.txt` disallows a discovered URL
- **THEN** the URL is enqueued and the run-start log records that robots.txt was ignored

#### Scenario: Delay is enforced
- **WHEN** the previous page navigation completes
- **THEN** the next navigation begins no sooner than `--delay` seconds later

#### Scenario: User-Agent is set on requests
- **WHEN** Playwright navigates a URL or `httpx` fetches an image
- **THEN** the request `User-Agent` header is `PotteryArchiver/1.0 (personal archive; contact: codydjango@gmail.com)`

### Requirement: Logging

The crawler SHALL append one JSON object per significant event to `output/log.jsonl`. Event types MUST include at minimum: `run_start`, `run_end`, `page_attempt`, `page_complete`, `page_failed`, `image_attempt`, `image_complete`, `image_failed`. Each event MUST carry an ISO-8601 UTC timestamp, the event type, the URL (where applicable), a status field, an `error` field (null on success), and a `duration_ms` field for terminal events.

#### Scenario: Each event is one JSON line
- **WHEN** any tracked event occurs
- **THEN** exactly one JSON object is appended to `output/log.jsonl` followed by a newline, and the file remains valid JSONL (parseable line-by-line) at all times

#### Scenario: Run boundaries are bracketed
- **WHEN** the script starts and exits normally
- **THEN** the first non-empty line of `log.jsonl` for that run is a `run_start` event and the final line for that run is a `run_end` event with summary counts

### Requirement: Output layout

Successful runs SHALL produce the following structure under `--output` (default `./output`): `pages/` containing rewritten HTML files, `images/` containing downloaded full-resolution images, `state.sqlite` for resumable crawl state, `log.jsonl` for the event log, and `README.md` describing the capture (date, start URL, total pages, total images, known gaps). The `--output` directory and its parents MUST be created if they do not exist.

#### Scenario: Output directory is created
- **WHEN** the user passes `--output ./fresh-dir` and that path does not exist
- **THEN** the path is created (with parents as needed) before any output is written

#### Scenario: Archive README is generated at run end
- **WHEN** a non-dry-run completes
- **THEN** `output/README.md` exists and contains the capture date, the `--start-url`, total pages captured, total images captured, and a list of failed URLs (if any)
