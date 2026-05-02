## 1. Project bootstrap

- [ ] 1.1 Update `pyproject.toml` to set `requires-python = ">=3.11"` and declare runtime deps `playwright`, `httpx`, `beautifulsoup4`
- [ ] 1.2 Run `uv sync` and commit `uv.lock`
- [ ] 1.3 Run `uv run playwright install chromium` and verify the browser is available
- [ ] 1.4 Add `output/` and `*.sqlite` to `.gitignore`
- [ ] 1.5 Delete the placeholder `main.py`
- [ ] 1.6 Create empty `crawler/__init__.py` and module files (`crawl.py`, `gallery.py`, `assets.py`, `state.py`)

## 2. Reconnaissance (RECON.md gate)

- [ ] 2.1 Open `https://www.artisanalarsonistpottery.com/` in Chrome with DevTools → Network → Fetch/XHR; reload and capture all API/XHR endpoints
- [ ] 2.2 Navigate every top-level page and into at least three galleries / individual pieces; record any new endpoints
- [ ] 2.3 Determine the gallery loading pattern (data-attribute, parent `<a href>`, or click-to-open lightbox) and capture concrete CSS selectors for thumbnail, lightbox container, full-res `<img>`, and close button
- [ ] 2.4 Estimate scale (page count, gallery count, total image count)
- [ ] 2.5 Note any access gotchas: Cloudflare challenges, rate limits, geo restrictions, sitemap presence at `/sitemap.xml`
- [ ] 2.6 Write findings to `RECON.md` at the repo root with sections: Site & Stack, API Endpoints, Gallery Pattern (with selectors), Scale, Gotchas, Plan

## 3. State store (`crawler/state.py`)

- [ ] 3.1 Implement `open_state(path)` that creates / opens `output/state.sqlite` and ensures the `pages` and `images` tables exist with the schema from design D8
- [ ] 3.2 Implement `enqueue_page(url, depth)`, `claim_next_page()`, `mark_page(url, status, error=None)` with attempt-counting and atomic transitions
- [ ] 3.3 Implement equivalent `enqueue_image(url, canonical_key)`, `claim_image_batch(n)`, `mark_image(url, status, local_path=None, error=None)`
- [ ] 3.4 Implement `reset_state(path)` that deletes the SQLite file
- [ ] 3.5 Add a tiny in-memory smoke test (or `if __name__ == "__main__"`) verifying enqueue → claim → mark cycles round-trip correctly

## 4. URL handling helpers

- [ ] 4.1 Implement same-origin check against `--start-url`'s host
- [ ] 4.2 Implement non-content URL filter (`mailto:`, `tel:`, `javascript:`, fragment-only, common social/share hosts)
- [ ] 4.3 Implement slug derivation (URL path → filename) with collision suffixing
- [ ] 4.4 Implement `srcset` / `data-srcset` parser that returns the URL with the largest descriptor (handles `w` and `x` descriptors and malformed entries)
- [ ] 4.5 Implement `canonical_image_key(url)` (strip query string, lowercase host)

## 5. Image capture (`crawler/assets.py`, `crawler/gallery.py`)

- [ ] 5.1 Implement DOM image-candidate extraction following the priority order in spec `image-capture` Requirement 1 (parent `<a href>`, srcset/data-srcset, data-src/data-original/data-zoom-src, `<source>`, `src`)
- [ ] 5.2 Implement gallery thumbnail-only detection heuristic (declared `width` ≤ 400, no full-res candidates)
- [ ] 5.3 Implement `gallery_lightbox_capture(page, thumbnails)` that clicks each thumbnail, waits for the lightbox container, reads the full-res URL, and dismisses (Escape → close-button selector fallback). Selectors are constants at the top of `crawler/gallery.py`, sourced from RECON.md
- [ ] 5.4 Implement `download_images(urls, client, sem, out_dir)` using `httpx.AsyncClient` with a semaphore (max 5), saving to `output/images/<hash>.<ext>`, returning the URL → local-path mapping (None for failures)
- [ ] 5.5 Implement retry with exponential backoff (1s/2s/4s) for 5xx and timeouts; 4xx is terminal
- [ ] 5.6 Wire dedup by canonical key — choose the largest variant and persist both URL and key in the `images` table

## 6. Page rendering and discovery (`crawler/crawl.py`)

- [ ] 6.1 Set up the Playwright async context (Chromium, custom User-Agent `PotteryArchiver/1.0 (personal archive; contact: codydjango@gmail.com)`)
- [ ] 6.2 Implement `render_page(page, url)`: `goto(networkidle, 30s)` → scroll loop (scroll-by-viewport / 500ms / stop on stable scrollHeight, hard cap 50 iterations) → second `networkidle` → return `page.content()`
- [ ] 6.3 Extract same-origin `<a href>` links from the rendered DOM and feed `enqueue_page` (respect max-depth and max-pages)
- [ ] 6.4 Wire `urllib.robotparser` check before enqueueing each URL; skip with `robots_blocked` log unless `--ignore-robots`
- [ ] 6.5 Wrap per-page work in try/except so single failures are logged and isolated; implement transient retry with 1s/2s/4s backoff (3 attempts) for 5xx / timeouts; 4xx is terminal

## 7. HTML rewriting

- [ ] 7.1 Implement `rewrite_html(html_str, url_to_local)` using BeautifulSoup `html.parser` — rewrites `src`, `data-src`, `data-srcset`, `srcset`, and `<source srcset>` only when the mapping is non-None
- [ ] 7.2 Save rewritten HTML to `output/pages/<slug>.html`; the start URL writes `index.html`
- [ ] 7.3 Confirm failed-image URLs remain unchanged in the saved HTML (regression check: no rewrite when mapping is None)

## 8. Logging (`output/log.jsonl`)

- [ ] 8.1 Implement `log_event(event_type, **fields)` that appends a single JSON line with ISO-8601 UTC timestamp, type, url, status, error, duration_ms
- [ ] 8.2 Emit `run_start` (with start URL, CLI flags, robots.txt mode) on script start and `run_end` (with summary counts) on script exit, even on Ctrl-C / unhandled exception
- [ ] 8.3 Emit `page_attempt` / `page_complete` / `page_failed` and `image_attempt` / `image_complete` / `image_failed` at the right call sites
- [ ] 8.4 Emit `robots_blocked`, `page_cap_reached`, and `slug_collision` events for spec-required edge cases

## 9. CLI (`archive.py`)

- [ ] 9.1 Add argparse parser with the flags defined in spec `site-archival` Requirement 2 (defaults exactly as specified)
- [ ] 9.2 Wire `--reset` to delete `output/state.sqlite` before initializing
- [ ] 9.3 Wire `--dry-run` so pages render and candidate downloads are reported but no HTML/image files are written and no SQLite rows are persisted
- [ ] 9.4 Create `--output` (and parents) if missing; ensure `output/pages/` and `output/images/` exist before use
- [ ] 9.5 Print a concise human-readable progress line per page; on exit, print a one-line summary (pages OK / failed, images OK / failed, total bytes, elapsed)

## 10. Archive README generator

- [ ] 10.1 At `run_end`, write `output/README.md` with: capture date, start URL, total pages captured, total images captured, list of failed URLs (with error reason), instructions to view (open `output/pages/index.html`)
- [ ] 10.2 Note in the README that the archive is intentionally unstyled (no CSS) and that failed-image URLs remain as live absolute references

## 11. End-to-end run and acceptance verification

- [ ] 11.1 `uv sync && uv run playwright install chromium && uv run python archive.py --start-url https://www.artisanalarsonistpottery.com/` runs end-to-end without manual intervention
- [ ] 11.2 Spot-check 5+ random pages in `output/pages/` — content present, not empty shells
- [ ] 11.3 Spot-check 3+ random galleries — every piece present, images at full resolution (compare to live site at the same image)
- [ ] 11.4 Open `output/pages/index.html` in a browser, navigate the archive offline, confirm images load from local files
- [ ] 11.5 Confirm `output/log.jsonl` exists and is valid JSONL; review failures
- [ ] 11.6 Re-run the script (no `--reset`) and confirm it finishes quickly because state is preserved (no re-render, no re-download for already-completed work)

## 12. Repo hygiene

- [ ] 12.1 Update `README.md` at the repo root (or create `pottery-archive` section) with install / run / view / resume instructions and the CLI flag reference
- [ ] 12.2 Verify `output/` is gitignored and not committed
- [ ] 12.3 Run `openspec validate archive-site-with-playwright` and resolve any warnings before archiving the change
