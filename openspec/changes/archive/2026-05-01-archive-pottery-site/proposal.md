## Why

The pottery site at `www.artisanalarsonistpottery.com` runs on Square Online (Weebly), which has no built-in export and no backend access for the owner. Content is rendered client-side from a JS bundle, so a static `wget`/`requests` mirror produces an empty skeleton. We need a one-off archive that captures the rendered text, page structure, and full-resolution image assets so the content can later be hand-imported into a new CMS (likely WordPress).

## What Changes

- Add a single Python script (target ~200 lines) that crawls the live site, renders each page with Playwright, scrolls to trigger lazy-loaded galleries, downloads originals for every referenced image at the highest resolution available, and rewrites the saved HTML so links and `<img src>` point at local files.
- Prefer Square Online's public site/store JSON endpoints (e.g. the `square.online` / `editmysite.com` site-data or storefront APIs) when discoverable — fall back to DOM scraping only for whatever the API does not expose.
- Be a polite crawler: serial or low-concurrency requests, short delay between fetches, send a descriptive User-Agent, honour `robots.txt` for the live host, dedupe images by their canonical path (ignore `?width=&height=&fit=crop` variants).
- Use `uv` for dependency/run management; declare Playwright in `pyproject.toml` and document the one-shot run command. No package layout, no CLI flags beyond a single optional output directory, no resume/state files — re-running just overwrites.
- Output a flat archive directory: `pages/` (rewritten HTML), `assets/` (deduped media), and a `content.json` index with page titles, URLs, and extracted product/text records, so a later WP import is straightforward.

## Capabilities

### New Capabilities
- `site-archive`: One-off recovery of the pottery site — discover pages, render via headless browser, capture text + full-res media, and write a CMS-portable archive on disk.

### Modified Capabilities
<!-- none — greenfield -->

## Impact

- New top-level script (e.g. `archive.py`) replacing the placeholder `main.py`.
- `pyproject.toml` gains `playwright` (+ `httpx` or stdlib `urllib` for asset downloads) and a `[tool.uv]` / script entry.
- Adds a Playwright browser install step (`uv run playwright install chromium`) to the run instructions.
- New runtime output directory (gitignored) holding the captured archive; existing `scrape/` mirror stays untouched as reference data.
- No production systems are touched; the live Square Online site is read-only from our side.