# artisanalarsonist

One-off archiver for `www.artisanalarsonistpottery.com`. Renders the JS-heavy
Square Online site with Playwright, downloads images at original resolution,
and writes a CMS-portable archive (`out/pages/`, `out/assets/`, `out/content.json`).

## Run

```sh
uv sync
uv run playwright install chromium
uv run python archive.py
```

Re-running overwrites `out/`. There is no resume mode.
