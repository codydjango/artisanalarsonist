## Context

The repo currently holds an empty Python scaffold and a manually-pulled mirror of `www.artisanalarsonistpottery.com` under `scrape/`. That mirror is the rendered HTML shell only — Square Online injects content via a JS bundle (`cdn3.editmysite.com`), so static fetchers cannot recover product, page, or gallery content. We need a one-shot crawler that drives a real browser, then writes a self-contained offline archive that can later feed a WordPress import.

Constraints:
- **WSL2-friendly**: Playwright + Chromium runs under WSL2 fine; output must live on the Linux filesystem (e.g. `~/...`), not `/mnt/c/...`, for I/O performance.
- **Politeness**: this is a friend's live site; the crawler must be gentle (low concurrency, delays, robots.txt by default, retry-with-backoff).
- **Resumability**: an archival run can take a long time and may fail mid-flight; re-running must not re-render or re-download captured work.
- **Image fidelity is the priority**. Pottery sites are visual; thumbnails are useless for migration.
- **One-time use**: no scheduled crawling, no re-hosting. Optimize for clarity and robustness over throughput.

Stakeholders: the repo owner (running the crawler), the site owner (the friend whose site is being archived), and a future engineer running a WordPress import against the output.

## Goals / Non-Goals

**Goals:**
- Produce a browseable offline archive: open `output/pages/index.html` in a browser, navigate the site, see images render from local disk.
- Capture every page reachable from the start URL (within depth/page caps) with fully-rendered DOM.
- Capture full-resolution images for every gallery, resolving lazy-load and lightbox patterns.
- Resumable: a re-run skips work already done; `--reset` is the only way to start over.
- Per-page failures are logged and isolated — they don't abort the run.
- The archive is CMS-portable in spirit: HTML files plus a flat `images/` directory, with clear URL→local-path mapping retained for downstream tooling.

**Non-Goals:**
- Pixel-perfect site clone (CSS, JS, fonts beyond what's needed to view content).
- Re-hosting the archive as a live service.
- Crawling authenticated content, third-party widgets, or off-origin pages.
- A reusable, configurable framework. This is a single-purpose script.
- Markdown / WXR conversion of content. (That belongs to a follow-up change for the actual CMS migration.)
- Scheduled or incremental re-crawls.

## Decisions

### D1: Single-repo project, not a sibling `pottery-archive/`

The original brief sketched a sibling project (`uv init pottery-archive`). We instead build the crawler **in this repo** because it is already the empty scaffold described in `CLAUDE.md` and there is no second project to merge with. The directory layout from the brief (`archive.py`, `crawler/`, `output/`, `RECON.md`) is adopted at the repo root.

Alternatives considered: keep a sibling project for cleanliness. Rejected — adds a directory hop with no upside; this repo *is* the pottery-archive project.

### D2: `uv` for environment & dependency management

Use `uv` as the brief specifies. `pyproject.toml` declares `requires-python = ">=3.11"` and the runtime deps (`playwright`, `httpx`, `beautifulsoup4`); `uv.lock` is committed. No `requirements.txt`.

Alternatives: `pip` + `requirements.txt` (less reproducible), Poetry (heavier, slower, no compelling reason here).

### D3: Playwright Chromium, async API, mostly sequential pages

One browser context shared across pages. Page rendering runs **sequentially** (one page at a time) for simplicity and to keep the load on the friend's site low. Image downloads, by contrast, run **concurrently** under `httpx.AsyncClient` with a small semaphore (3–5 in flight). This pairs naturally because Playwright's async API and `httpx` share an event loop.

Why not headless Firefox / WebKit: Chromium is the default and most common target; SPA bundles are routinely tested against it.

Why not multiple Playwright workers: marginal speedup against a small site and adds politeness concerns. If runtime becomes painful we can raise concurrency to 2; we will not exceed that without explicit reason.

### D4: Page-render recipe

For each page:
1. `page.goto(url, wait_until="networkidle", timeout=30_000)`.
2. **Lazy-load scroll loop**: `page.evaluate("window.scrollBy(0, window.innerHeight)")` then `page.wait_for_timeout(500)`. Repeat until `document.body.scrollHeight` stops growing across two consecutive iterations or a hard cap (e.g. 50 iterations) is hit.
3. Wait for `networkidle` again post-scroll.
4. Capture `page.content()` as the HTML snapshot.
5. Extract candidate links (same-origin `<a href>`) and image URLs (DOM walk, see D5).

This is a simple, defensible recipe; if a specific page needs custom waits we add a small per-URL hook rather than over-engineering the generic path.

### D5: Image-URL resolution priority

For every `<img>` and `<picture>` we collect candidates in this priority order, picking the highest-resolution available:
1. Parent `<a href>` if it points at an image (common lightbox pattern).
2. `data-srcset` / `srcset` — parse and pick the largest descriptor (`w` or `x`).
3. `data-src` / `data-original` / `data-zoom-src` (Square Online and other CMSes have a few flavors).
4. `<source srcset>` inside `<picture>`.
5. `src`.

We also collect direct `<a href>` targets pointing at image URLs (lightbox triggers).

`srcset` parsing is a small in-house helper (comma-split URL+descriptor pairs) — no extra dependency.

When the **same logical image** is served at many `?width=&height=&fit=crop` query variants (which the existing scraped mirror confirms is the case for this CDN), we keep one full-resolution copy. The dedup key is the URL path with the query string stripped; we keep the candidate whose `width` query param (or `srcset` descriptor) is largest.

### D6: Lightbox fallback (gallery interaction)

If a gallery page yields image URLs that look like thumbnails (heuristics: small declared `width`, query params force a small size, or no full-res candidate found), we fall back to clicking each thumbnail:
- Click, wait for the lightbox container (network-idle + a short fixed delay), read the largest-resolution `<img>` inside the modal, then close (Escape, then a known close-button selector if Escape doesn't dismiss).
- The selectors used are documented in `RECON.md` after Step 1; the gallery module exposes them as constants so a future site needs only one edit.

This fallback is slow and brittle by nature, so we use it only when DOM/`srcset` resolution fails.

### D7: HTML rewriting with BeautifulSoup

After a page renders and its referenced images have been resolved & downloaded, we parse the captured HTML with `bs4` (`html.parser`) and rewrite `src`, `data-src`, `srcset`, `data-srcset`, and `<source srcset>` to point at relative paths under `../images/`. We **only rewrite URLs whose download succeeded**; failed ones keep their original absolute URL so an online viewer still falls back gracefully.

We do not attempt to rewrite CSS `url(...)` references in this pass — the goal is content recovery, not visual fidelity. CSS / fonts / JS are not downloaded; the archive intentionally renders unstyled or minimally styled.

The rewritten HTML is saved as `output/pages/<slug>.html`. The slug is derived from the URL path (`/about-us` → `about-us.html`); the start URL becomes `index.html`. Slug collisions are resolved by appending a short hash.

### D8: State store — SQLite

`output/state.sqlite` (single file, stdlib `sqlite3`). Two tables:
- `pages(url TEXT PRIMARY KEY, depth INT, status TEXT, attempts INT, error TEXT, completed_at TEXT)`
- `images(url TEXT PRIMARY KEY, canonical_key TEXT, local_path TEXT, status TEXT, attempts INT, error TEXT, completed_at TEXT)`

Status values: `queued | in_progress | completed | failed`. A re-run loads pending+failed-with-attempts-remaining rows back into the queue. `--reset` deletes the DB.

Alternatives considered: JSONL append-only log (simple, but harder to query for resume; would need an in-memory index on every run). SQLite wins on quick lookups and atomic updates without adding a dependency.

### D9: Logging

`output/log.jsonl` — one JSON object per significant event (`page_attempt`, `page_complete`, `page_failed`, `image_attempt`, `image_complete`, `image_failed`, `run_start`, `run_end`). Fields: ISO timestamp, event type, URL, status, error, duration_ms. JSONL because it's trivial to grep, `jq`, or summarize with a small Python script after the fact.

Console output stays human-readable and concise (one line per page, periodic image-progress summaries).

### D10: Politeness defaults

- Concurrency: 1 page at a time, ≤5 concurrent image downloads.
- 1.0s delay between page navigations (CLI-tunable via `--delay`).
- `urllib.robotparser` checks every URL before queueing; `--ignore-robots` overrides (the site owner has consented, so this is "in your back pocket" rather than an everyday flag).
- User-Agent: `PotteryArchiver/1.0 (personal archive; contact: codydjango@gmail.com)`.
- Retry policy: 3 attempts for 5xx / network timeout / `playwright` `TimeoutError`, with exponential backoff (1s, 2s, 4s). 4xx is terminal — log and move on.
- Per-page exceptions are caught and logged; they do not propagate up and abort the crawl.

### D11: CLI surface (argparse)

`uv run python archive.py --start-url <URL> [...]`. Flags exactly as the brief lists: `--start-url` (required), `--output` (default `./output`), `--max-pages` (default 1000), `--max-depth` (default 10), `--delay` (default 1.0), `--reset`, `--ignore-robots`, `--dry-run`. `--dry-run` renders pages and reports candidate downloads without writing HTML or images (state is also not persisted in dry-run mode).

### D12: Reconnaissance happens first, in `RECON.md`

Before locking selectors and gallery logic, we spend a focused session in DevTools (Network → Fetch/XHR) to:
- Identify the platform (we already strongly suspect Square Online from the existing mirror's `cdn3.editmysite.com` references).
- Look for a JSON API that enumerates pages / products / gallery items — even if we still render with Playwright, knowing the API simplifies image-URL discovery.
- Confirm gallery loading pattern (data-attr full-res vs. lightbox-on-click) and capture the exact selectors.
- Note Cloudflare / bot-detection / rate-limit signals.

Findings land in `RECON.md`; the gallery module's selector constants are derived from it. This gate is **not optional** — we don't guess at selectors.

## Risks / Trade-offs

- **[Square Online bundle changes]** → the JS bundle could re-render with different selectors mid-archive, breaking gallery extraction. **Mitigation**: capture is a one-shot operation; we run it within a single session, and we keep the gallery selectors centralized so a re-run after a vendor change needs one edit.
- **[Bot detection / Cloudflare interstitials]** → the friend's Square Online site may serve a challenge. **Mitigation**: identify in `RECON.md`. If encountered, switch to `playwright`'s persistent context, optionally use `playwright-stealth` (added if needed, not preemptively), and fall back to manual `storage_state` capture from a real browser session.
- **[Lightbox-only sites are slow]** → click-wait-extract per thumbnail multiplies runtime. **Mitigation**: only invoke lightbox fallback when DOM resolution fails; record per-thumbnail status so a re-run skips already-resolved ones.
- **[Image dedup is heuristic]** → stripping query strings assumes the same path = same logical image, which is true for this CDN but could be wrong elsewhere. **Mitigation**: store both the canonical key and the original URL in the `images` table so a future audit can spot collisions.
- **[Network idle is unreliable]** → SPAs sometimes hold connections open (analytics, websockets) and `wait_until="networkidle"` either over-waits or never resolves. **Mitigation**: 30s timeout; `networkidle` is paired with the scroll loop so we don't strictly depend on it for content readiness.
- **[Archive is unstyled]** → opening pages locally will look bare. **Mitigation**: explicitly called out in the archive `README.md` and in this design's Non-Goals — content recovery is the goal, not visual fidelity. The downstream WordPress import doesn't need the source CSS.
- **[Disk usage]** → image-heavy sites can produce hundreds of MB. **Mitigation**: `output/` is gitignored; the archive `README.md` reports total size after a run.
- **[Robots.txt disagrees with site-owner consent]** → if `robots.txt` disallows what the owner has explicitly permitted, defaulting to "respect" creates a contradiction. **Mitigation**: `--ignore-robots` exists and is documented; the run log records whether it was used.

## Migration Plan

This is a greenfield, reversible change:
1. Replace `main.py` with `archive.py`; add the `crawler/` package.
2. Update `pyproject.toml` (deps + `requires-python`); commit `uv.lock`.
3. Add `output/` to `.gitignore`.
4. Run reconnaissance against the live site; commit `RECON.md`.
5. Run the crawler end-to-end against the live site; commit any selector tweaks discovered during that run.
6. Verify acceptance criteria in `proposal.md` — spot-check pages, galleries, full-res images, offline navigation, resume behavior.

Rollback: `git revert` the implementation commit. Nothing leaves this repo; nothing is published. The `output/` directory is local-only.

## Open Questions

- **Target start URL** — assumed `https://www.artisanalarsonistpottery.com/`; confirm there isn't a preferred entry point (e.g. a `/shop` or `/gallery` root).
- **Scope preference** — capture everything reachable, or only the work galleries? Default plan: capture everything within `--max-pages`, with galleries/products prioritized in queue ordering if the friend asks for that later.
- **Sitemap** — does Square Online expose a `/sitemap.xml`? If yes, seed the queue from it instead of pure link-following. Defer answer to the recon step.
- **Scale** — dozens or hundreds of pieces? Drives `--max-pages` defaults and runtime expectations. Defer to recon.
- **Delivery** — local archive only, or also pushed to cloud storage for the friend? Out of scope for this change unless asked; the deliverable is `output/` on disk.
- **Priority galleries** — if the full crawl turns out to be impractical, which gallery URLs are non-negotiable? Capture in `RECON.md` once known.
