## 1. Project setup

- [x] 1.1 Add `playwright` and `httpx` to `pyproject.toml` runtime dependencies; keep `requires-python = ">=3.9"`
- [x] 1.2 Add a top-level `out/` and any temp paths to `.gitignore`
- [x] 1.3 Delete the PyCharm placeholder `main.py`
- [x] 1.4 Document the run sequence (`uv sync && uv run playwright install chromium && uv run python archive.py`) in `README.md` (create if absent, keep it short)

## 2. Discovery

- [x] 2.1 Implement `robots.txt` fetch + parse on startup; expose a `is_allowed(url)` helper
- [x] 2.2 Implement sitemap-first discovery: GET `/sitemap.xml`, parse `<loc>` entries, filter to same-host URLs
- [x] 2.3 Implement nav-fallback BFS from the rendered home page with a hard depth cap (default 3) and same-host filter
- [x] 2.4 Normalize URLs (lowercase host, strip trailing slash, drop query for navigation) and dedupe before enqueueing

## 3. Page rendering

- [x] 3.1 Launch a single Playwright chromium context with the custom User-Agent
- [x] 3.2 For each URL, navigate, wait for `networkidle`, then scroll-to-bottom in a loop until no new network activity for ≥1s
- [x] 3.3 Snapshot rendered HTML (`page.content()`) and capture a list of every request/response observed during the visit

## 4. Content extraction

- [x] 4.1 From the captured network log, identify JSON responses from Square Online / Weebly hosts (`*.editmysite.com`, `square.online`, per-tenant CDN) and store the parsed bodies per page
- [x] 4.2 Extract DOM-level fields (title, h1/h2, prose paragraphs, link list, image list) as the guaranteed fallback
- [x] 4.3 Merge JSON-derived and DOM-derived records into a single `page` entry, preferring JSON fields when both exist

## 5. Asset capture

- [x] 5.1 Collect every image URL referenced by the rendered DOM (`<img src>`, `srcset`) and observed in the network log (image content-types)
- [x] 5.2 Normalize to canonical form (strip query string) and dedupe across pages
- [x] 5.3 Download each canonical URL once via `httpx`, with a polite delay (≥0.25s), reusing one connection pool
- [x] 5.4 If the unparameterized URL fails, fall back to the largest observed `width=` variant and record the fallback in `content.json`
- [x] 5.5 Save assets as `assets/<sha1prefix>__<original-filename>`; record canonical URL → archived path mapping

## 6. HTML rewriting

- [x] 6.1 For each captured page, parse the snapshotted HTML and rewrite `<img src>` and `srcset` URLs to relative `../assets/<file>` paths when the canonical URL is in the asset map
- [x] 6.2 Rewrite `<a href>` to other archived pages as relative paths (e.g. `./shop.html`); leave external links untouched
- [x] 6.3 Write rewritten HTML to `out/pages/<slug>.html`, choosing slugs from the URL path (root → `index.html`)

## 7. Archive index

- [x] 7.1 Write `out/content.json` with `{pages: [...], assets: [...]}` covering every captured page and asset, including content hashes, observed referencing pages, and any fallback notes
- [x] 7.2 Verify on completion that every file in `out/pages/` and `out/assets/` has a matching entry in `content.json` (and vice versa); log any orphans

## 8. Politeness + abort behaviour

- [x] 8.1 Insert a ≥1s delay between page navigations and ≥0.25s between asset downloads
- [x] 8.2 On HTTP 429 or repeated 5xx (≥3 in a row from the same host), abort with a non-zero exit and a clear message naming the URL and status
- [x] 8.3 Skip URLs disallowed by `robots.txt` and log each skip

## 9. Manual verification

- [x] 9.1 Run the script end-to-end against the live site and confirm `out/` contains a non-empty `pages/`, `assets/`, and `content.json`
- [x] 9.2 Spot-check that one rewritten page's `<img>` opens locally and that an internal link resolves to a sibling page on disk
- [x] 9.3 Confirm at least one image was saved at higher resolution than any `?width=` variant present in the original markup
- [x] 9.4 Re-run the script and confirm a clean overwrite (no leftover scratch files, archive matches a fresh run)
