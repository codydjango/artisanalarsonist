"""Convert out/content.json + out/pages/*.html into per-page Markdown.

Walks the rendered <main> content area for richer text extraction than the
coarse <p>-only capture in content.json. Cross-page frequency filtering drops
nav/footer/UI chrome. For product pages, the captured Square Online product
API JSON body is the primary source for name/price/description.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

OUT = Path("out")
DST = OUT / "markdown"
PAGES_DIR = OUT / "pages"
CHROME_RATIO = 0.5
HOST_PREFIX = "https://www.artisanalarsonistpottery.com"
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "figcaption"}
SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
SKIP_CLASS_RES = (re.compile(r"\bw-slideshow\b"), re.compile(r"\binstagram\b"))


class MainExtractor(HTMLParser):
    """Collect (tag, text) blocks and <img src> values from inside <main>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_body = 0
        self.skip_depth = 0
        self.div_depth = 0
        self.skip_div_threshold: int | None = None
        self.cur_tag: str | None = None
        self.cur_text: list[str] = []
        self.blocks: list[tuple[str, str]] = []
        self.images: list[str] = []
        self.image_alts: dict[str, str] = {}

    @property
    def skipping(self) -> bool:
        return self.skip_depth > 0 or self.skip_div_threshold is not None

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body += 1
        if not self.in_body:
            return
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "div":
            self.div_depth += 1
            if self.skip_div_threshold is None:
                cls = dict(attrs).get("class", "")
                if any(p.search(cls) for p in SKIP_CLASS_RES):
                    self.skip_div_threshold = self.div_depth
        if self.skip_div_threshold is not None:
            return
        if tag == "img":
            d = dict(attrs)
            src = d.get("src", "")
            if src:
                self.images.append(src)
                alt = (d.get("alt") or "").strip()
                if alt and src not in self.image_alts:
                    self.image_alts[src] = alt
        if tag in BLOCK_TAGS:
            self._flush()
            self.cur_tag = tag

    def handle_endtag(self, tag):
        if self.skip_depth and tag in SKIP_TAGS:
            self.skip_depth -= 1
            return
        if tag == "div" and self.div_depth > 0:
            if self.skip_div_threshold is not None and self.div_depth == self.skip_div_threshold:
                self.skip_div_threshold = None
            self.div_depth -= 1
            return
        if self.skip_div_threshold is not None:
            return
        if self.in_body and tag in BLOCK_TAGS:
            self._flush()
        if tag == "body" and self.in_body:
            self._flush()
            self.in_body -= 1

    def handle_data(self, data):
        if self.skipping or not self.in_body or self.cur_tag is None:
            return
        self.cur_text.append(data)

    def _flush(self):
        if self.cur_tag is None:
            return
        text = normalize_text("".join(self.cur_text))
        if text:
            self.blocks.append((self.cur_tag, text))
        self.cur_tag = None
        self.cur_text = []


class GalleryFigures(HTMLParser):
    """Walk <figure> elements and emit (src, alt, caption) for each, in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_body = 0
        self.fig_depth = 0
        self.cap_depth = 0
        self.cur_src: str | None = None
        self.cur_alt: str = ""
        self.cur_cap: list[str] = []
        self.figures: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body += 1
        if not self.in_body:
            return
        if tag == "figure":
            self.fig_depth += 1
            self.cur_src, self.cur_alt, self.cur_cap = None, "", []
        elif self.fig_depth and tag == "img" and self.cur_src is None:
            d = dict(attrs)
            src = d.get("src", "")
            if src:
                self.cur_src = src
                self.cur_alt = (d.get("alt") or "").strip()
        elif self.fig_depth and tag == "figcaption":
            self.cap_depth += 1

    def handle_endtag(self, tag):
        if tag == "figcaption" and self.cap_depth:
            self.cap_depth -= 1
        elif tag == "figure" and self.fig_depth:
            self.fig_depth -= 1
            if self.fig_depth == 0 and self.cur_src:
                cap = normalize_text("".join(self.cur_cap))
                self.figures.append((self.cur_src, self.cur_alt, cap))
                self.cur_src, self.cur_alt, self.cur_cap = None, "", []
        elif tag == "body" and self.in_body:
            self.in_body -= 1

    def handle_data(self, data):
        if self.cap_depth:
            self.cur_cap.append(data)


def normalize_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"([a-z][.!?])([A-Z])", r"\1 \2", s)
    return s


def block_to_md(tag: str, text: str) -> str:
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return f"{'#' * int(tag[1])} {text}"
    if tag == "li":
        return f"- {text}"
    if tag == "blockquote":
        return f"> {text}"
    return text


def chrome_set(items_per_page, cutoff):
    freq = Counter()
    for items in items_per_page:
        for x in set(items):
            freq[x] += 1
    return {x for x, v in freq.items() if v >= cutoff}


def _balanced_div(html: str, start: int) -> str:
    depth, i = 0, start
    while i < len(html):
        nm = re.search(r"<(/?)div\b[^>]*>", html[i:])
        if not nm:
            break
        depth += 1 if nm.group(1) == "" else -1
        i += nm.end()
        if depth == 0:
            return html[start:i]
    return html[start:]


def extract_slides(html: str, poster_to_video: dict) -> list[dict]:
    """Walk <div class="...w-slideshow..."> and return one record per unique slide."""
    m = re.search(r'<div[^>]*class="[^"]*\bw-slideshow\b[^"]*"[^>]*>', html)
    if not m:
        return []
    region = _balanced_div(html, m.start())
    slides, seen = [], set()
    for sm in re.finditer(r'<div[^>]*class="[^"]*\bswiper-slide\b[^"]*"[^>]*>', region):
        chunk = _balanced_div(region, sm.start())
        texts: list[str] = []
        for hm in re.finditer(r"<h([1-6])[^>]*>([\s\S]*?)</h\1>", chunk):
            t = normalize_text(re.sub(r"<[^>]+>", "", hm.group(2)))
            if t:
                texts.append(t)
        srcs = re.findall(r'<img[^>]*src="([^"]+)"', chunk)
        local = [s for s in srcs if s.startswith("../assets/")]
        videos = [
            {"poster": s, "video_path": f"../{poster_to_video[s]['archived_path']}"}
            for s in local if s in poster_to_video
        ]
        plain_imgs = [s for s in local if s not in {v["poster"] for v in videos}]
        if not (texts or videos or plain_imgs):
            continue
        sig = (tuple(texts), tuple((v["poster"], v["video_path"]) for v in videos), tuple(plain_imgs))
        if sig in seen:
            continue
        seen.add(sig)
        slides.append({"texts": texts, "videos": videos, "imgs": plain_imgs})
    return slides


def write_slideshow(slides: list[dict], source_url: str, dst: Path) -> None:
    if not slides:
        return
    lines = ["# Homepage slideshow", "", f"_Source_: <{source_url}>", ""]
    for i, s in enumerate(slides, 1):
        kind = "video" if s["videos"] else ("image" if s["imgs"] else "text")
        lines += [f"## Slide {i} — {kind}", ""]
        for v in s["videos"]:
            lines += [
                f'<video src="{v["video_path"]}" poster="{v["poster"]}" controls muted loop playsinline></video>',
                "",
            ]
        for img in s["imgs"]:
            lines += [f"![]({img})", ""]
        for t in s["texts"]:
            lines += [f"> {t}", ""]
    dst.write_text("\n".join(lines), encoding="utf-8")


def render_product(d: dict) -> list[str]:
    out: list[str] = []
    name = d.get("name") or ""
    if name:
        out += [f"## {name}", ""]
    price = d.get("price") or {}
    low, high = price.get("low_formatted"), price.get("high_formatted")
    if low and high:
        out += [f"**Price:** {low}" if low == high else f"**Price:** {low} – {high}", ""]
    desc = d.get("short_description") or d.get("seo_page_description") or ""
    if desc:
        out += [desc, ""]
    if d.get("placeholder_image", {}).get("data", {}).get("placeholder"):
        out += ["_(no product image uploaded — Square placeholder shown on live site)_", ""]
    return out


def main():
    data = json.loads((OUT / "content.json").read_text(encoding="utf-8"))
    pages = data["pages"]
    total = len(pages)
    cutoff = max(2, int(total * CHROME_RATIO) + 1)

    extracted: dict[str, tuple[list, list, dict]] = {}
    for p in pages:
        html = (PAGES_DIR / f"{p['slug']}.html").read_text(encoding="utf-8")
        ex = MainExtractor()
        ex.feed(html)
        extracted[p["slug"]] = (ex.blocks, ex.images, ex.image_alts)

    block_chrome = chrome_set([blocks for blocks, _, _ in extracted.values()], cutoff)
    img_chrome = chrome_set([imgs for _, imgs, _ in extracted.values()], cutoff)

    poster_to_video: dict[str, dict] = {
        f"../{v['poster_archived_path']}": v for v in data.get("videos", [])
    }

    def render_image(src: str, alt: str = "") -> list[str]:
        v = poster_to_video.get(src)
        if v:
            return [
                f'<video src="../{v["archived_path"]}" poster="{src}" controls muted loop playsinline></video>',
            ]
        return [f"![{alt}]({src})"]

    DST.mkdir(parents=True, exist_ok=True)
    for f in DST.glob("*.md"):
        f.unlink()

    for p in pages:
        slug = p["slug"]
        blocks, imgs, alts = extracted[slug]
        blocks = [b for b in blocks if b not in block_chrome]
        imgs = [i for i in imgs if i not in img_chrome and i.startswith("../assets/")]

        lines = [f"# {p.get('title') or p['url']}", "", f"_Source_: <{p['url']}>", ""]

        if slug.startswith("product--"):
            for jb in p.get("json_bodies", []):
                u = jb.get("url", "")
                if "/products/" in u and "/skus" not in u and "store-locations/" not in u:
                    body = jb.get("body", {})
                    d = body.get("data") if isinstance(body, dict) else None
                    if isinstance(d, dict) and d.get("name"):
                        lines += render_product(d)
                        break

        if slug == "gallery":
            html = (PAGES_DIR / f"{slug}.html").read_text(encoding="utf-8")
            gp = GalleryFigures()
            gp.feed(html)
            seen: set[str] = set()
            caption_texts = {cap for _, _, cap in gp.figures if cap}
            content_blocks = [b for b in blocks if b[0] != "figcaption" and b[1] not in caption_texts]
            for tag, text in content_blocks:
                lines += [block_to_md(tag, text), ""]
            lines += ["## Image gallery", ""]
            for src, alt, cap in gp.figures:
                if src in seen or src in img_chrome or not src.startswith("../assets/"):
                    continue
                seen.add(src)
                md_alt = alt or cap
                lines += render_image(src, md_alt)
                if cap:
                    lines += ["", f"*{cap}*"]
                if alt and alt != cap:
                    lines += ["", f"<!-- alt: {alt} -->"]
                lines += [""]
        else:
            for tag, text in blocks:
                lines += [block_to_md(tag, text), ""]
            if imgs:
                lines += ["## Images", ""]
                for src in imgs:
                    lines += render_image(src, alts.get(src, ""))
                    lines += [""]

        internal = sorted({
            link for link in p.get("links", [])
            if link.startswith(HOST_PREFIX) and "#" not in link and link != p["url"]
        })
        if internal:
            lines += ["## Site links", ""]
            lines += [f"- <{link}>" for link in internal]
            lines += [""]

        (DST / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"[wrote] markdown/{slug}.md ({len(blocks)} blocks, {len(imgs)} imgs)")

    home = next((p for p in pages if p["slug"] == "index"), None)
    if home:
        home_html = (PAGES_DIR / "index.html").read_text(encoding="utf-8")
        slides = extract_slides(home_html, poster_to_video)
        write_slideshow(slides, home["url"], DST / "slideshow.md")
        print(f"[wrote] markdown/slideshow.md ({len(slides)} slides)")

    print(f"[done] {total} markdown files -> {DST}/")


if __name__ == "__main__":
    main()
