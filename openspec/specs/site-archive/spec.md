# site-archive

## Purpose

One-off recovery of `www.artisanalarsonistpottery.com` (a Square Online site with no export). Discovers pages, renders them via headless browser, captures full-resolution media, and writes a CMS-portable archive on disk for later WordPress import.

## Requirements

### Requirement: Single-script entrypoint

The archiver SHALL be implemented as a single Python script (~200 lines) at the repo root, runnable end-to-end with `uv run python archive.py`. It SHALL NOT introduce package directories, CLI subcommands, configuration files, or on-disk resume/state beyond the output archive itself.

#### Scenario: One-shot run produces the archive
- **WHEN** a user runs `uv run python archive.py` with the live site reachable
- **THEN** the script exits 0 and writes a populated `out/` directory containing `pages/`, `assets/`, and `content.json` with no other side effects on the repo

#### Scenario: Re-run overwrites cleanly
- **WHEN** a user runs the script a second time over an existing `out/` directory
- **THEN** the script overwrites prior output and produces an archive equivalent to a fresh run, without requiring manual cleanup

### Requirement: JS-rendered page capture

The script SHALL render every discovered page using a headless browser (Playwright chromium) so that client-side-injected text, navigation, and gallery markup are present in the captured HTML. It SHALL scroll each rendered page to trigger lazy-loaded content before snapshotting the DOM.

#### Scenario: Lazy gallery is fully expanded
- **WHEN** a page contains a gallery whose images load on scroll
- **THEN** the captured HTML and asset list include every gallery image, not only those visible above the fold

#### Scenario: Render waits for content
- **WHEN** the script captures a page
- **THEN** it waits for the network to become idle (no in-flight requests for ≥1s) before reading the DOM, so the snapshot reflects the fully rendered state

### Requirement: Page discovery

The script SHALL discover pages starting from `https://www.artisanalarsonistpottery.com/`, preferring `/sitemap.xml` when available, and otherwise breadth-first following same-host links from the rendered home page up to a bounded depth. It SHALL deduplicate URLs by their normalized form (lowercased host, path without trailing slash, query removed for navigation) and SHALL stay within the live host.

#### Scenario: Sitemap is used when present
- **WHEN** `https://www.artisanalarsonistpottery.com/sitemap.xml` returns a parseable sitemap
- **THEN** the script seeds its crawl queue from the sitemap entries

#### Scenario: Falls back to nav crawl
- **WHEN** the sitemap is missing or empty
- **THEN** the script falls back to BFS from the rendered home page, bounded by a hard depth cap, and logs the discovered URL set

#### Scenario: External links are not followed
- **WHEN** a page links to a URL whose host is not `www.artisanalarsonistpottery.com`
- **THEN** the script does not enqueue or fetch that URL

### Requirement: API-first content extraction

For each rendered page, the script SHALL inspect the network requests it issued and SHALL prefer JSON responses from Square Online / Weebly hosts (e.g. `*.editmysite.com`, `square.online`, the per-tenant `*.cdn*.editmysite.com`) as the source of structured content (page metadata, product records) when those responses contain recognizable fields. It SHALL fall back to extracting content from the rendered DOM when no usable JSON is observed.

#### Scenario: Site-data JSON is captured when emitted
- **WHEN** a rendered page triggers a JSON response from a Square Online / Weebly host that includes page or product fields
- **THEN** the parsed JSON is stored alongside the page record in `content.json` and used in preference to DOM-only extraction for those fields

#### Scenario: Pure-DOM page still produces content
- **WHEN** no usable JSON response is observed for a page
- **THEN** the script extracts title, headings, body text, image references, and outbound links from the rendered DOM and writes them to `content.json`

### Requirement: Full-resolution image capture with dedupe

The script SHALL collect every image URL referenced by rendered pages (DOM `<img>` tags and CSS background images observed via the network log), normalize each to its canonical form by stripping the query string, and download each unique canonical URL exactly once at the original (un-resized) resolution. It SHALL store assets in a flat `assets/` directory using a content-hash-prefixed filename to avoid collisions.

#### Scenario: Responsive variants collapse to one download
- **WHEN** the same upload is referenced as `…/foo.jpg?width=300…`, `…/foo.jpg?width=900…`, and `…/foo.jpg`
- **THEN** the script downloads `…/foo.jpg` (no query) exactly once and records all three variants as resolving to that asset

#### Scenario: Original is preferred over largest variant
- **WHEN** a canonical image URL is reachable without the resize query parameters
- **THEN** the script saves the response from the unparameterized URL rather than any resized variant

#### Scenario: Fallback to largest observed variant
- **WHEN** the unparameterized URL returns a non-success status
- **THEN** the script downloads the variant with the largest observed `width=` instead and records the fallback in `content.json`

### Requirement: Local-rewriting of saved HTML

For each captured page, the script SHALL save a rewritten HTML file in which `<a href>` values pointing to other archived pages and `<img src>` / `srcset` values pointing to archived assets are replaced with relative paths into the local archive. References to non-archived URLs SHALL be left intact.

#### Scenario: Image src points at local asset
- **WHEN** a saved page contains an `<img>` that originally pointed at `https://www.artisanalarsonistpottery.com/uploads/.../foo.jpg?width=900&fit=crop`
- **THEN** the saved HTML's `src` attribute points at `../assets/<hash>__foo.jpg` and the file exists on disk

#### Scenario: Internal page link is rewritten
- **WHEN** a saved page links to another archived page
- **THEN** the saved HTML's `href` is the relative path to that page's archived HTML file (e.g. `./shop.html`)

#### Scenario: External link is preserved
- **WHEN** a saved page links to a URL outside the live host
- **THEN** the saved HTML's `href` remains the original absolute URL

### Requirement: Polite crawling

The script SHALL crawl the live site with a single browser context and concurrency 1, insert a brief delay between page loads (≥1s) and between asset downloads (≥0.25s), send a descriptive `User-Agent` identifying the archiver and the maintainer, and SHALL respect `https://www.artisanalarsonistpottery.com/robots.txt`. On clear rate-limit or block signals (HTTP 429 or repeated 5xx) the script SHALL abort with a non-zero exit and a clear message rather than retry-loop.

#### Scenario: robots.txt disallows a path
- **WHEN** `robots.txt` disallows a path the discovery step would otherwise visit
- **THEN** the script skips that URL and logs the skip

#### Scenario: 429 from the host
- **WHEN** the live host returns HTTP 429 on a page or asset request
- **THEN** the script aborts with a non-zero exit code and a message naming the URL and status, without retrying in a tight loop

### Requirement: CMS-portable archive layout

The script SHALL produce its output under a single root directory (default `out/`) with the layout `pages/`, `assets/`, and `content.json`. `content.json` SHALL be a UTF-8 JSON document indexing every captured page (URL, title, archived HTML path, extracted text/structure, referenced asset paths) and every captured asset (canonical URL, archived path, content hash, observed referencing pages).

#### Scenario: Archive root contains the expected three children
- **WHEN** the script completes successfully
- **THEN** the archive root contains exactly `pages/`, `assets/`, and `content.json`, with no scratch or lock files

#### Scenario: content.json indexes all pages and assets
- **WHEN** `content.json` is loaded
- **THEN** every file in `pages/` corresponds to a page entry and every file in `assets/` corresponds to an asset entry, with no orphans in either direction

### Requirement: Dependency management via uv

The project SHALL declare runtime dependencies (Playwright, an HTTP client for asset downloads) in `pyproject.toml` and SHALL be runnable via `uv sync && uv run playwright install chromium && uv run python archive.py`. The placeholder `main.py` SHALL be removed in favour of `archive.py` at the repo root.

#### Scenario: Fresh checkout runs end-to-end
- **WHEN** a contributor clones the repo and runs the documented command sequence
- **THEN** the archiver runs to completion without additional manual dependency installation steps

#### Scenario: No leftover placeholder
- **WHEN** the change is applied
- **THEN** the repo contains `archive.py` and does not contain the original PyCharm `main.py` placeholder
