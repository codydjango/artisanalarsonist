"""Download Cloudflare Stream videos referenced by the archived poster thumbnails.

Square Online's slideshow blocks render videos through Cloudflare Stream
(`customer-<id>.cloudflarestream.com/<video_id>/...`). The original archive
captured the poster JPEGs as image assets, but the actual video stream is
served behind a `blob:` URL on the player and is therefore missing from the
mirror. This script reads `out/content.json`, finds every Cloudflare Stream
video ID via the captured posters, and downloads the video by parsing its
public HLS manifest and concatenating the fMP4 init segment with the media
segments — which produces a directly-playable fragmented MP4 with no muxer
required. Updates `content.json` in place with a `videos` array.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import httpx

OUT = Path("out")
ASSETS = OUT / "assets"
CONTENT = OUT / "content.json"
UA = "artisanal-arsonist-archiver/0.1 (one-off content recovery; contact codydjango@gmail.com)"


def collect_videos(data: dict) -> dict[tuple[str, str], dict]:
    pat = re.compile(r"https?://(customer-[a-z0-9]+)\.cloudflarestream\.com/([a-f0-9]{32})/")
    found: dict[tuple[str, str], dict] = {}
    for asset in data.get("assets", []):
        m = pat.search(asset["canonical_url"])
        if not m:
            continue
        key = (m.group(1), m.group(2))
        rec = found.setdefault(key, {
            "customer": m.group(1),
            "video_id": m.group(2),
            "poster_archived_path": asset["archived_path"],
            "referenced_by": [],
        })
        rec["referenced_by"] = sorted(set(rec["referenced_by"]) | set(asset.get("referenced_by", [])))
    return found


def parse_playlist(text: str):
    """Return ('master', [(bandwidth, uri), ...]) or ('media', (init_uri, [seg_uris])).

    Master playlists list bitrate variants; media playlists list segments and an
    optional init segment (#EXT-X-MAP) for fragmented MP4 / CMAF content.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines):
        out = []
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
                m = re.search(r"BANDWIDTH=(\d+)", line)
                out.append((int(m.group(1)) if m else 0, lines[i + 1]))
        return "master", out
    init, segs = None, []
    for line in lines:
        if line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                init = m.group(1)
        elif not line.startswith("#"):
            segs.append(line)
    return "media", (init, segs)


def fetch_text(http: httpx.Client, url: str) -> str:
    r = http.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.text


def fetch_bytes(http: httpx.Client, url: str) -> bytes:
    r = http.get(url, timeout=120, follow_redirects=True)
    r.raise_for_status()
    return r.content


def download_video(http: httpx.Client, customer: str, video_id: str, dest: Path) -> bool:
    base = f"https://{customer}.cloudflarestream.com/{video_id}"
    master_url = f"{base}/manifest/video.m3u8"
    try:
        master_text = fetch_text(http, master_url)
    except httpx.HTTPError as e:
        print(f"  [error] master fetch: {e}", file=sys.stderr)
        return False
    kind, items = parse_playlist(master_text)
    if kind == "master":
        if not items:
            print("  [error] master playlist has no renditions", file=sys.stderr)
            return False
        items.sort(key=lambda t: t[0], reverse=True)
        media_url = urljoin(master_url, items[0][1])
        print(f"  picked rendition {items[0][0] // 1000} kbps")
        try:
            media_text = fetch_text(http, media_url)
        except httpx.HTTPError as e:
            print(f"  [error] media fetch: {e}", file=sys.stderr)
            return False
        kind2, items2 = parse_playlist(media_text)
        if kind2 != "media":
            print("  [error] expected media playlist after master", file=sys.stderr)
            return False
        init_uri, seg_uris = items2
        playlist_url = media_url
    else:
        init_uri, seg_uris = items
        playlist_url = master_url
    if not seg_uris:
        print("  [error] no segments in playlist", file=sys.stderr)
        return False
    print(f"  segments: {len(seg_uris)} (init: {'yes' if init_uri else 'no'})")
    with dest.open("wb") as f:
        if init_uri:
            try:
                f.write(fetch_bytes(http, urljoin(playlist_url, init_uri)))
            except httpx.HTTPError as e:
                print(f"  [error] init segment: {e}", file=sys.stderr)
                return False
        for i, seg in enumerate(seg_uris, 1):
            try:
                f.write(fetch_bytes(http, urljoin(playlist_url, seg)))
            except httpx.HTTPError as e:
                print(f"  [error] segment {i}/{len(seg_uris)}: {e}", file=sys.stderr)
                return False
    return dest.exists() and dest.stat().st_size > 0


def main() -> None:
    data = json.loads(CONTENT.read_text(encoding="utf-8"))
    videos = collect_videos(data)
    if not videos:
        print("[done] no Cloudflare Stream videos referenced; nothing to do")
        return
    records: list[dict] = []
    with httpx.Client(headers={"User-Agent": UA}) as http:
        for (customer, vid), meta in videos.items():
            print(f"[video] {vid}")
            fname = f"{hashlib.sha1(vid.encode()).hexdigest()[:10]}__video_{vid[:8]}.mp4"
            dest = ASSETS / fname
            if not download_video(http, customer, vid, dest):
                if dest.exists():
                    dest.unlink()
                print(f"  [fail] {vid}", file=sys.stderr)
                continue
            size = dest.stat().st_size
            records.append({
                "video_id": vid,
                "customer": customer,
                "manifest_url": f"https://{customer}.cloudflarestream.com/{vid}/manifest/video.m3u8",
                "archived_path": f"assets/{fname}",
                "poster_archived_path": meta["poster_archived_path"],
                "referenced_by": meta["referenced_by"],
                "sha1": hashlib.sha1(dest.read_bytes()).hexdigest(),
                "size_bytes": size,
            })
            print(f"  -> assets/{fname} ({size // 1024} KiB)")
    data["videos"] = records
    CONTENT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[done] {len(records)} video(s) saved; content.json updated")


if __name__ == "__main__":
    main()
