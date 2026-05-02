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


class MainExtractor(HTMLParser):
    """Collect (tag, text) blocks and <img src> values from inside <main>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_main = 0
        self.skip_depth = 0
        self.cur_tag: str | None = None
        self.cur_text: list[str] = []
        self.blocks: list[tuple[str, str]] = []
        self.images: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.in_main += 1
        if self.in_main and tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if self.in_main and tag == "img":
            src = dict(attrs).get("src", "")
            if src:
                self.images.append(src)
        if self.in_main and tag in BLOCK_TAGS:
            self._flush()
            self.cur_tag = tag

    def handle_endtag(self, tag):
        if self.skip_depth and tag in SKIP_TAGS:
            self.skip_depth -= 1
            return
        if self.in_main and tag in BLOCK_TAGS:
            self._flush()
        if tag == "main" and self.in_main:
            self._flush()
            self.in_main -= 1

    def handle_data(self, data):
        if self.skip_depth or not self.in_main or self.cur_tag is None:
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

    extracted: dict[str, tuple[list, list]] = {}
    for p in pages:
        html = (PAGES_DIR / f"{p['slug']}.html").read_text(encoding="utf-8")
        ex = MainExtractor()
        ex.feed(html)
        extracted[p["slug"]] = (ex.blocks, ex.images)

    block_chrome = chrome_set([blocks for blocks, _ in extracted.values()], cutoff)
    img_chrome = chrome_set([imgs for _, imgs in extracted.values()], cutoff)

    DST.mkdir(parents=True, exist_ok=True)
    for f in DST.glob("*.md"):
        f.unlink()

    for p in pages:
        slug = p["slug"]
        blocks, imgs = extracted[slug]
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

        for tag, text in blocks:
            lines += [block_to_md(tag, text), ""]

        if imgs:
            lines += ["## Images", ""]
            for src in imgs:
                lines += [f"![]({src})", ""]

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
    print(f"[done] {total} markdown files -> {DST}/")


if __name__ == "__main__":
    main()
