## ADDED Requirements

### Requirement: Image URL resolution priority

For each `<img>` and `<picture>` element discovered on a rendered page, the crawler SHALL resolve a single best image URL by considering candidates in the following priority order, choosing the first one that yields a usable image URL: (1) parent `<a href>` if it points at an image (lightbox pattern), (2) `data-srcset` then `srcset`, picking the largest descriptor, (3) `data-src` / `data-original` / `data-zoom-src`, (4) `<source srcset>` inside `<picture>`, (5) `src`. The crawler SHALL also collect direct `<a href>` URLs that point at images (where there is no surrounding `<img>`).

#### Scenario: Lightbox parent anchor wins
- **WHEN** an `<img>` is wrapped in `<a href="https://cdn/full.jpg">` and also has `src="https://cdn/thumb.jpg"`
- **THEN** the resolved candidate is `https://cdn/full.jpg`

#### Scenario: srcset chosen over src
- **WHEN** an `<img>` has `src="thumb.jpg"` and `srcset="small.jpg 400w, large.jpg 1600w"` and no enclosing `<a>` to an image
- **THEN** the resolved candidate is `large.jpg`

#### Scenario: data-src for lazy-loaded images
- **WHEN** an `<img>` has `src=""` (or a placeholder) and `data-src="https://cdn/real.jpg"`
- **THEN** the resolved candidate is `https://cdn/real.jpg`

#### Scenario: Direct anchor-to-image is captured
- **WHEN** a page contains `<a href="https://cdn/standalone.jpg">Download</a>` with no `<img>` child
- **THEN** `https://cdn/standalone.jpg` is added to the image queue

### Requirement: Highest-resolution selection from srcset

The crawler SHALL parse `srcset` and `data-srcset` attributes (comma-separated `URL descriptor` pairs, where descriptors are width like `1600w` or pixel-density like `2x`) and select the URL with the largest descriptor. Parsing MUST be implemented in-house without adding a dependency.

#### Scenario: Largest width wins
- **WHEN** `srcset` is `a.jpg 400w, b.jpg 800w, c.jpg 1600w`
- **THEN** `c.jpg` is selected

#### Scenario: Largest pixel density wins
- **WHEN** `srcset` is `a.jpg 1x, b.jpg 2x, c.jpg 3x`
- **THEN** `c.jpg` is selected

#### Scenario: Mixed and malformed entries
- **WHEN** `srcset` contains entries with extra whitespace, missing descriptors, or trailing commas
- **THEN** the parser tolerates them, descriptor-less entries are treated as `1x`, and a sensible best candidate is still returned without raising

### Requirement: Lightbox fallback for galleries

When DOM-based resolution yields only thumbnail-grade URLs for a gallery page (heuristics: declared `width` query param ≤ a small threshold such as 400, all candidates resolve to identical small dimensions, or no full-res candidate is found), the crawler SHALL fall back to clicking each thumbnail, waiting for the lightbox container to render (network-idle plus a short fixed delay), reading the largest-resolution `<img>` inside the modal, and then closing the modal (Escape key first, falling back to a documented close-button selector). Selectors MUST be defined as constants in `crawler/gallery.py` and match those documented in `RECON.md`.

#### Scenario: Fallback is invoked when DOM yields only thumbnails
- **WHEN** every image candidate on a gallery page has a query `width` ≤ 400 and there is no enclosing `<a href>` to a larger image
- **THEN** the crawler clicks each thumbnail, captures the modal's full-res image URL, and adds those URLs to the download queue instead of the thumbnails

#### Scenario: Fallback is skipped when DOM already resolved full-res
- **WHEN** the gallery's images are already resolved to full-resolution URLs via `srcset` or parent `<a href>`
- **THEN** the crawler does not click any thumbnails

#### Scenario: Modal close uses Escape, then selector fallback
- **WHEN** a captured modal does not dismiss after pressing Escape
- **THEN** the crawler clicks the documented close-button selector to dismiss the modal before moving to the next thumbnail

### Requirement: Concurrent image downloading

Image downloads SHALL be performed with `httpx.AsyncClient` under a semaphore that limits concurrency to at most 5 in-flight requests at a time. Successful downloads MUST be saved under `output/images/` with a deterministic filename (URL-derived hash plus the original extension). Failed downloads MUST be retried up to 3 times with exponential backoff (1s, 2s, 4s) for 5xx and network/timeout errors; 4xx errors MUST be terminal.

#### Scenario: Concurrency cap is enforced
- **WHEN** 20 image URLs are queued for download
- **THEN** at no point do more than 5 `httpx` image requests run concurrently

#### Scenario: Successful download is saved
- **WHEN** an image download returns HTTP 200
- **THEN** the bytes are written to `output/images/<url-hash>.<ext>` and the `images` row is updated to status `completed` with the local path

#### Scenario: 5xx is retried with backoff
- **WHEN** an image fetch returns HTTP 503
- **THEN** it is retried up to 3 total attempts with 1s/2s/4s backoff; if all fail it is marked `failed`

#### Scenario: 4xx is terminal
- **WHEN** an image fetch returns HTTP 404
- **THEN** the URL is marked `failed` immediately without retry

### Requirement: Image deduplication

The crawler SHALL deduplicate images by a canonical key consisting of the URL's scheme + host + path with the query string stripped. When multiple variants of the same canonical key are encountered (e.g. `?width=400` vs. `?width=1600`), exactly one variant SHALL be downloaded — the variant whose `width` query parameter (or `srcset` descriptor that selected it) is largest. The chosen URL and its canonical key MUST both be persisted in the `images` table so future audits can verify the choice.

#### Scenario: Same path, multiple sizes
- **WHEN** the page references `cdn/foo.jpg?width=400` and `cdn/foo.jpg?width=1600`
- **THEN** only `cdn/foo.jpg?width=1600` is downloaded and both URLs map to the same local file via the canonical key

#### Scenario: Different paths are not collapsed
- **WHEN** `cdn/foo.jpg?width=1600` and `cdn/bar.jpg?width=1600` both appear
- **THEN** both are downloaded as distinct images

#### Scenario: Canonical key persists in state
- **WHEN** an image is downloaded
- **THEN** the `images` row contains both the original URL and the canonical key (path-without-query) and the local path

### Requirement: URL → local-path mapping for HTML rewriting

The image-capture subsystem SHALL expose, after each page's images are downloaded, a mapping from every original image URL referenced by that page to either the local relative path (on success) or `None` (on failure). HTML rewriting (in the site-archival capability) consumes this mapping to decide which attributes to rewrite and which to leave untouched.

#### Scenario: Successful image yields a local path
- **WHEN** an image is downloaded successfully
- **THEN** the mapping for its original URL is the relative path under `output/images/`

#### Scenario: Failed image yields None
- **WHEN** an image download fails terminally
- **THEN** the mapping for its original URL is `None`, signaling HTML rewriting to leave the original URL in place

#### Scenario: Aliased URLs share a target
- **WHEN** two URLs collapse to the same canonical key (e.g. different `?width=` variants)
- **THEN** both URLs map to the same local relative path
