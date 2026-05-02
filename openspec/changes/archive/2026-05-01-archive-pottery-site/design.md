## Context

`www.artisanalarsonistpottery.com` is a Square Online (Weebly) site. The page HTML at the top level is a thin loader for a `cdn3.editmysite.com` JS bundle; products, gallery items, and prose are injected client-side. The owner has no admin access, so we cannot use Square's authenticated export — we are reverse-engineering from the public surface only.

The captured `scrape/www.artisanalarsonistpottery.com/index.html` confirms two useful properties:
- The site has a per-tenant CDN host (`64cf9085515bf81e6c45.cdn6.editmysite.com`) which is where rendered/site-data fetches typically originate.
- Image URLs are served at `/uploads/<id>/<file>?width=&height=&fit=crop`. The unparameterized path returns the original file.

The work is a single-shot recovery, not a recurring pipeline. The user has been explicit: keep it small (~200 lines), one script, no resumability, polite crawling.

## Goals / Non-Goals

**Goals:**
- Capture every page's rendered text and structural HTML.
- Capture every referenced image at the highest resolution (one per canonical path, not every responsive variant).
- Produce CMS-portable output: rewritten HTML pointing at local relative paths, plus a `content.json` index summarizing pages and (if discoverable) products.
- Prefer the public Square Online site/store JSON endpoints when they expose the same content with less work; fall back to DOM scraping otherwise.
- Stay polite: low concurrency, brief inter-request delay, descriptive User-Agent, honour `robots.txt`.

**Non-Goals:**
- Re-creating an interactive replica of the site. We need content, not behaviour.
- Resumable crawls, partial-restart support, on-disk state machines.
- A reusable framework or package layout. One file, one purpose.
- Authenticated Square admin access, theme/CSS preservation, JS bundle archival.
- Migration into WordPress itself — that is a separate, later step.

## Decisions

### 1. Playwright over `requests` + parser
Square Online renders client-side, so a static HTML fetch returns a skeleton. Playwright (chromium) gives us the post-render DOM and the network-request log we use to discover image originals and any JSON endpoints the page itself calls. Alternative considered: `requests-html` / `pyppeteer` — Playwright has the better maintained Python bindings and a first-class network-event API.

### 2. API-first with DOM fallback
On each page render, capture the network log and look for JSON responses from `*.editmysite.com`, `square.online`, or the per-tenant CDN host. If the body parses as JSON and contains recognizable site/page/product fields, prefer it as the source of truth for `content.json`. The DOM extraction stays as a guaranteed fallback so we are not blocked by undocumented API shapes. We do not separately probe undocumented endpoints — we only use what the site itself fetches, which keeps us within the site's public surface.

### 3. Image dedupe by canonical path; download originals
Every image URL is normalized to its path-without-query before being added to the asset set. We download once, from the URL with all `?width=&height=&fit=crop` query parameters stripped, which Square's CDN serves as the original upload. Alternatives considered: pick the largest variant we observe. Stripping the query is simpler, smaller, and produces the same or better result.

### 4. Page discovery via sitemap → nav → BFS
Try `/sitemap.xml` first (Square Online publishes one). If absent, parse the rendered nav links from the home page, then breadth-first follow internal links up to a small depth cap. We keep the discovery surface bounded to same-host URLs and skip query-only differences.

### 5. Polite crawling defaults
- Single browser context, one page at a time (concurrency = 1).
- 1.0–1.5s delay between page loads, 0.25s between asset downloads.
- Custom User-Agent: `artisanal-arsonist-archiver/0.1 (one-off content recovery; contact: codydjango@gmail.com)`.
- Read `robots.txt` once at startup; skip any disallowed path.
- Image downloads via `httpx.AsyncClient` (or stdlib `urllib.request`) reusing a single connection pool.

### 6. Output layout
```
out/
  pages/<slug>.html        # rewritten HTML (links + img src → local)
  assets/<hash>__<name>    # deduped originals, content-hash prefix avoids collisions
  content.json             # {pages: [...], products: [...], assets: [...]}
```
Re-running overwrites `out/` wholesale. No locks, no partial-state files.

### 7. `uv` for dependency + run
`pyproject.toml` declares `playwright` and `httpx`. Running is a two-step the README documents: `uv sync && uv run playwright install chromium`, then `uv run python archive.py`. No console-script entry point — keep it a plain module run.

### 8. Replace `main.py` with `archive.py`
The placeholder serves no purpose. New script lives at the repo root as `archive.py`. We do not preserve `main.py`.

## Risks / Trade-offs

- **Square's bundle changes shape** → Mitigation: rely on the rendered DOM (stable selectors like `<h1>`, `<p>`, `<img>`, `<a>`) rather than internal class names; treat any API-derived data as a bonus, not the contract.
- **Lazy-load galleries miss images if we don't scroll far enough** → Mitigation: scroll to bottom in a loop until network is idle and no new image requests fire for 1s.
- **Non-image media (PDFs, audio) on the site** → Out of scope unless trivially captured by the same asset-URL collector. We log skipped URLs in `content.json` so a human can decide.
- **CDN rate-limits or blocks us** → Concurrency = 1 plus a polite delay should keep us well under any threshold. If we are throttled we abort with a clear error rather than retry-loop.
- **Originals are unexpectedly missing for some uploads** → Fall back to the largest observed variant (highest `width=` value seen) for that canonical path.
- **Sitemap is incomplete or absent** → BFS from the homepage with a small depth cap (e.g. 3) catches the rest. Worst case the user adds a few seed URLs by hand and re-runs.

## Migration Plan

This is a one-off recovery script with no production deployment. Re-running on the same machine reproduces the archive. The user manually inspects `out/` and uses it as the source for the eventual WordPress import.

## Open Questions

- Does the rendered site expose a stable `/sitemap.xml`? (Verify on first run; if absent, log and fall through to nav crawl.)
- Are there any password-protected or "members only" pages that would need separate handling? Assumed no until observed.