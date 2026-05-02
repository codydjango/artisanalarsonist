"""One-off archiver for www.artisanalarsonistpottery.com (Square Online).

Renders pages with Playwright, captures full-resolution images from the public
CDN, and writes a CMS-portable archive (pages/, assets/, content.json).
"""
from __future__ import annotations

import asyncio, hashlib, json, re, shutil, sys, urllib.parse, urllib.robotparser
from collections import defaultdict
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

ROOT = "https://www.artisanalarsonistpottery.com"
HOST = urllib.parse.urlparse(ROOT).netloc
UA = "artisanal-arsonist-archiver/0.1 (one-off content recovery; contact codydjango@gmail.com)"
OUT = Path("out")
PAGE_DELAY, ASSET_DELAY, SCROLL_QUIET_S = 1.0, 0.25, 1.0
DEPTH_CAP, MAX_PAGES = 3, 200
JSON_HOST_RE = re.compile(r"(\.editmysite\.com|\.square\.online|^square\.online$)", re.I)


def norm_nav(url):
    p = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", "", "", ""))


def canon(url):
    p = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def slug(url):
    return urllib.parse.urlparse(url).path.strip("/").replace("/", "--").lower() or "index"


def asset_name(canon_url):
    h = hashlib.sha1(canon_url.encode()).hexdigest()[:10]
    base = Path(urllib.parse.urlparse(canon_url).path).name or "asset"
    return f"{h}__{re.sub(r'[^A-Za-z0-9._-]', '_', base)}"


def width_of(url):
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        return int(qs.get("width", ["0"])[0])
    except (TypeError, ValueError):
        return 0


async def fetch_robots(http):
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = await http.get(f"{ROOT}/robots.txt", timeout=10)
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except httpx.HTTPError:
        rp.parse([])
    return rp


async def fetch_sitemap(http):
    try:
        r = await http.get(f"{ROOT}/sitemap.xml", timeout=10)
        if r.status_code != 200:
            return []
        return [u for u in re.findall(r"<loc>([^<]+)</loc>", r.text)
                if urllib.parse.urlparse(u).netloc == HOST]
    except httpx.HTTPError:
        return []


async def render(page, url):
    """Navigate, scroll lazy-load, return DOM facts + raw HTML + log + JSON bodies from Square hosts."""
    responses = []
    page.on("response", lambda r: responses.append(r))
    await page.goto(url, wait_until="networkidle", timeout=45000)
    last = -1
    for _ in range(20):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(SCROLL_QUIET_S)
        if len(responses) == last:
            break
        last = len(responses)
    await page.wait_for_load_state("networkidle", timeout=10000)
    raw = await page.content()
    dom = await page.evaluate("""() => ({
        title: document.title,
        h1: [...document.querySelectorAll('h1')].map(e => e.textContent.trim()).filter(Boolean),
        h2: [...document.querySelectorAll('h2')].map(e => e.textContent.trim()).filter(Boolean),
        paragraphs: [...document.querySelectorAll('p')].map(e => e.textContent.trim()).filter(Boolean),
        links: [...document.querySelectorAll('a[href]')].map(e => e.href),
        images: [...document.querySelectorAll('img')].map(e => ({src: e.src || '', srcset: e.srcset || ''})),
    })""")
    log, json_bodies = [], []
    for r in responses:
        try:
            ct = (r.headers.get("content-type") or "").lower()
            log.append((r.url, r.status, ct))
            if "json" in ct and JSON_HOST_RE.search(urllib.parse.urlparse(r.url).netloc or ""):
                try:
                    json_bodies.append({"url": r.url, "body": await r.json()})
                except Exception:
                    pass
        except Exception:
            continue
    return {"html": raw, "dom": dom, "log": log, "json_bodies": json_bodies}


def collect_image_urls(dom, log):
    urls = set()
    for img in dom.get("images", []):
        if img.get("src"):
            urls.add(img["src"])
        for entry in (img.get("srcset") or "").split(","):
            bits = entry.strip().split()
            if bits:
                urls.add(bits[0])
    for url, status, ct in log:
        if "image" in ct and status < 400:
            urls.add(url)
    return {u for u in urls if u.startswith(("http://", "https://"))}


def merge_with_json(dom, json_bodies):
    """Prefer JSON-derived title/description when a Square API body exposes them."""
    out = dict(dom)
    for entry in json_bodies:
        b = entry.get("body")
        if not isinstance(b, dict):
            continue
        for src_key, target in (("title", "title"), ("name", "title"), ("description", "description")):
            v = b.get(src_key)
            if isinstance(v, str) and v.strip() and not out.get(target):
                out[target] = v.strip()
    return out


async def download_one(http, canon_url, observed_variants):
    """Try the un-parameterized URL; on failure fall back to the largest observed width variant."""
    try:
        r = await http.get(canon_url, timeout=30)
        if r.status_code < 400:
            return r.content, None
    except httpx.HTTPError:
        pass
    best, best_w = None, -1
    for raw in observed_variants:
        w = width_of(raw)
        if w > best_w and raw != canon_url:
            best, best_w = raw, w
    if not best:
        return None, None
    try:
        r = await http.get(best, timeout=30)
        if r.status_code < 400:
            return r.content, best
    except httpx.HTTPError:
        pass
    return None, None


async def download_assets(http, observed, out_dir):
    canon_to_local, records = {}, []
    for canon_url in sorted(observed):
        variants = [raw for _, raw in observed[canon_url]]
        body, fallback = await download_one(http, canon_url, variants)
        if body is None:
            print(f"[asset-fail] {canon_url}", file=sys.stderr)
            await asyncio.sleep(ASSET_DELAY)
            continue
        fname = asset_name(canon_url)
        (out_dir / fname).write_bytes(body)
        records.append({
            "canonical_url": canon_url,
            "archived_path": f"assets/{fname}",
            "sha1": hashlib.sha1(body).hexdigest(),
            "fallback_url": fallback,
            "referenced_by": sorted({u for u, _ in observed[canon_url]}),
        })
        canon_to_local[canon_url] = f"assets/{fname}"
        await asyncio.sleep(ASSET_DELAY)
    return canon_to_local, records


def rewrite_html(raw, canon_to_local, url_to_slug):
    def sub_src(m):
        attr, q, val = m.group(1), m.group(2), m.group(3)
        local = canon_to_local.get(canon(val))
        return f"{attr}={q}../{local}{q}" if local else m.group(0)

    def sub_srcset(m):
        attr, q, val = m.group(1), m.group(2), m.group(3)
        parts = []
        for entry in val.split(","):
            bits = entry.strip().split()
            if not bits:
                continue
            local = canon_to_local.get(canon(bits[0]))
            url = f"../{local}" if local else bits[0]
            parts.append(" ".join([url, *bits[1:]]).strip())
        return f"{attr}={q}{', '.join(parts)}{q}"

    def sub_href(m):
        attr, q, val = m.group(1), m.group(2), m.group(3)
        s = url_to_slug.get(norm_nav(val))
        return f"{attr}={q}./{s}.html{q}" if s else m.group(0)

    out = re.sub(r'(\bsrc)=(["\'])(https?://[^"\']+)\2', sub_src, raw)
    out = re.sub(r'(\bsrcset)=(["\'])([^"\']+)\2', sub_srcset, out)
    out = re.sub(r'(\bhref)=(["\'])(https?://[^"\']+)\2', sub_href, out)
    return out


async def crawl(http, ctx, rp):
    seeds = await fetch_sitemap(http)
    sitemap_used = bool(seeds)
    if not seeds:
        seeds = [ROOT]
    seen, queue = set(), []
    for s in seeds:
        n = norm_nav(s)
        if urllib.parse.urlparse(n).netloc == HOST and n not in seen:
            seen.add(n)
            queue.append((n, 0))

    observed, page_records, json_endpoints = defaultdict(set), [], []
    consec_5xx, i = 0, 0
    while i < len(queue) and i < MAX_PAGES:
        url, depth = queue[i]
        i += 1
        if not rp.can_fetch(UA, url):
            print(f"[skip] robots disallow: {url}", file=sys.stderr)
            continue
        page = await ctx.new_page()
        print(f"[render] {url} (depth {depth})", file=sys.stderr)
        try:
            result = await render(page, url)
        except Exception as e:
            print(f"[error] render failed for {url}: {e}", file=sys.stderr)
            await page.close()
            continue
        for ru, rs, _ in result["log"]:
            if urllib.parse.urlparse(ru).netloc != HOST:
                continue
            if rs == 429:
                sys.exit(f"[abort] 429 from {ru}")
            if 500 <= rs < 600:
                consec_5xx += 1
                if consec_5xx >= 3:
                    sys.exit(f"[abort] {consec_5xx} consecutive 5xx (last: {ru} {rs})")
            elif 200 <= rs < 400:
                consec_5xx = 0
        imgs = collect_image_urls(result["dom"], result["log"])
        for raw in imgs:
            observed[canon(raw)].add((url, raw))
        for jb in result["json_bodies"]:
            json_endpoints.append({"page": url, "endpoint": jb["url"]})
        merged = merge_with_json(result["dom"], result["json_bodies"])
        page_records.append({
            "url": url, "slug": slug(url), "depth": depth,
            "title": merged.get("title", ""), "description": merged.get("description", ""),
            "h1": merged.get("h1", []), "h2": merged.get("h2", []),
            "paragraphs": merged.get("paragraphs", []),
            "links": sorted(set(merged.get("links", []))),
            "raw_html": result["html"],
            "image_canonical_urls": sorted({canon(u) for u in imgs}),
            "json_bodies": result["json_bodies"],
        })
        if not sitemap_used and depth < DEPTH_CAP:
            for href in result["dom"].get("links", []):
                if not href.startswith(("http://", "https://")):
                    continue
                if urllib.parse.urlparse(href).netloc != HOST:
                    continue
                n = norm_nav(href)
                if n not in seen:
                    seen.add(n)
                    queue.append((n, depth + 1))
        await page.close()
        await asyncio.sleep(PAGE_DELAY)
    return sitemap_used, page_records, observed, json_endpoints


async def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "pages").mkdir(parents=True)
    (OUT / "assets").mkdir(parents=True)

    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True) as http:
        rp = await fetch_robots(http)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            ctx = await browser.new_context(user_agent=UA)
            sitemap_used, page_records, observed, json_endpoints = await crawl(http, ctx, rp)
            await browser.close()
        canon_to_local, asset_records = await download_assets(http, observed, OUT / "assets")

    url_to_slug = {p["url"]: p["slug"] for p in page_records}
    page_index = []
    for rec in page_records:
        rewritten = rewrite_html(rec["raw_html"], canon_to_local, url_to_slug)
        (OUT / "pages" / f"{rec['slug']}.html").write_text(rewritten, encoding="utf-8")
        page_index.append({
            "url": rec["url"], "slug": rec["slug"], "depth": rec["depth"],
            "archived_path": f"pages/{rec['slug']}.html",
            "title": rec["title"], "description": rec["description"],
            "h1": rec["h1"], "h2": rec["h2"], "paragraphs": rec["paragraphs"],
            "links": rec["links"],
            "image_canonical_urls": rec["image_canonical_urls"],
            "image_archived_paths": sorted({canon_to_local[c] for c in rec["image_canonical_urls"] if c in canon_to_local}),
            "json_bodies": rec["json_bodies"],
        })
    (OUT / "content.json").write_text(json.dumps({
        "site": ROOT, "discovery_mode": "sitemap" if sitemap_used else "nav-bfs",
        "pages": page_index, "assets": asset_records,
        "json_endpoints_observed": json_endpoints,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    page_files = {p.name for p in (OUT / "pages").iterdir()}
    asset_files = {a.name for a in (OUT / "assets").iterdir()}
    page_indexed = {Path(p["archived_path"]).name for p in page_index}
    asset_indexed = {Path(a["archived_path"]).name for a in asset_records}
    for name in page_files ^ page_indexed:
        print(f"[orphan-page] {name}", file=sys.stderr)
    for name in asset_files ^ asset_indexed:
        print(f"[orphan-asset] {name}", file=sys.stderr)
    print(f"[done] {len(page_index)} pages, {len(asset_records)} assets -> {OUT}/", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
