# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Recover the content from a friend's pottery site at `www.artisanalarsonistpottery.com` (built on Square Online — no export feature, no backend access) so it can be migrated to a new CMS later, likely WordPress. The goal is content recovery, not a faithful site clone.

## Current state

Project is an empty scaffold:
- `main.py` is the PyCharm-generated placeholder — safe to replace.
- `pyproject.toml` has no dependencies declared yet (`requires-python = ">=3.9"`).
- `scrape/www.artisanalarsonistpottery.com/` holds a manual mirror of the live site — `index.html`, `robots.txt`, `manifest.webmanifest`, favicon, and a handful of `uploads/.../*.jpg|png` assets at various `?width=&height=&fit=crop` query-string variants. Treat this directory as captured input data, not source.

## Working notes for content recovery

- Square Online sites render content client-side from a JS bundle (see the `cdn3.editmysite.com` CSS preload and `loading-view` skeleton in `scrape/.../index.html`). A plain `requests`/static fetch will not produce the rendered DOM — pages, products, and text live behind the JS app. Plan for a headless browser (Playwright is the natural choice) if you need real page content.
- The CDN serves the same image at many `?width=&height=&fit=crop` variants. When pulling assets, dedupe by the path before the `?` and grab one high-res original per asset rather than every responsive variant.
- Output should be CMS-portable: prefer Markdown or structured JSON for text content and a flat `assets/` directory for media, so a later WordPress import (e.g. via WP-CLI or the WXR importer) is straightforward.

## OpenSpec workflow

This repo uses [OpenSpec](https://github.com/Fission-AI/OpenSpec) for spec-driven development. Specs live in `openspec/specs/`, in-flight change proposals live in `openspec/changes/`. Project-local Claude commands `/opsx:propose`, `/opsx:explore`, `/opsx:apply`, and `/opsx:archive` (backed by skills under `.claude/skills/openspec-*/`) drive the propose → apply → archive flow. The `openspec` CLI (`openspec list`, `openspec show`, `openspec validate`, etc.) is also available directly.

Prefer routing non-trivial work through a change proposal rather than editing specs directly.