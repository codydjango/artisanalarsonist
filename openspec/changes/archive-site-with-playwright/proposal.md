## Why

The target site (`www.artisanalarsonistpottery.com`, hosted on Square Online) renders all content client-side from a JS bundle, so static fetchers like `wget` and `requests` produce empty HTML shells. To recover the friend's pottery content for migration to a new CMS we need a headless-browser crawler that captures fully-rendered pages, follows lazy-loaded gallery flows, and preserves images at the highest available resolution. The current repo is an empty Python scaffold — there is no crawler yet.

## What Changes

- Add a Playwright-based crawler that discovers same-origin pages from a seed URL, renders each one to fully-loaded HTML, and writes a self-contained offline archive.
- Add gallery-aware image capture that resolves full-resolution variants (via `srcset`, `data-*` attributes, parent `<a href>`, or — as a fallback — clicking a thumbnail and reading the lightbox `src`) and downloads them concurrently with `httpx`.
- Add HTML rewriting (BeautifulSoup) that points `src` / `data-src` / `srcset` / `<source>` at the locally captured files so the archive is browseable offline; leave the original URL in place when a download fails.
- Add resumable crawl state (SQLite) so re-runs skip already-completed pages and images, plus a JSONL event log under `output/log.jsonl`.
- Add an `archive.py` CLI accepting `--start-url`, `--output`, `--max-pages`, `--max-depth`, `--delay`, `--reset`, `--ignore-robots`, and `--dry-run`, with default politeness (≤2 page renders, 3–5 image downloads concurrently, 1s page delay, robots.txt honored, custom User-Agent, retry-with-backoff for 5xx/timeouts).
- Add a `RECON.md` capturing reconnaissance findings (tech stack, API endpoints, gallery loading pattern, scale, gotchas) before the crawler is finalized.
- Manage dependencies with `uv` (`playwright`, `httpx`, `beautifulsoup4`); pin Python to ≥3.11 in `pyproject.toml`; add `output/` to `.gitignore`.

## Capabilities

### New Capabilities
- `site-archival`: reconnaissance, discovery, rendering, HTML capture & rewriting, crawl-state resumability, logging, politeness, and the CLI surface for producing a browseable offline archive.
- `image-capture`: gallery detection, full-resolution image-URL resolution (including lightbox interaction), concurrent downloading, deduplication, and the URL→local-path mapping consumed by HTML rewriting.

### Modified Capabilities
<!-- None — this repo has no existing specs. -->

## Impact

- New runtime dependencies: `playwright` (with the Chromium browser installed via `uv run playwright install chromium`), `httpx`, `beautifulsoup4`.
- New tooling dependency: `uv` for environment and lockfile management; `uv.lock` is committed.
- New top-level files / dirs: `archive.py`, `crawler/` package (`crawl.py`, `gallery.py`, `assets.py`, `state.py`), `RECON.md`, `README.md` updates, `output/` (gitignored).
- `pyproject.toml` is rewritten to declare dependencies and pin `requires-python = ">=3.11"` (currently `>=3.9` with no deps).
- `main.py` (PyCharm placeholder) is removed; `archive.py` becomes the entry point.
- Operationally: WSL2-friendly; archive output written to the Linux filesystem, not `/mnt/c/...`. One-time run, not scheduled.
- Existing manual mirror under `scrape/www.artisanalarsonistpottery.com/` is unaffected — it remains as captured input data.