#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DuckDuckGo web search CLI compatible with the web-search skill."""

import argparse
import json
import sys

try:
    from ddgs import DDGS
except Exception:
    try:
        from duckduckgo_search import DDGS
    except Exception:
        DDGS = None


def _output_stream(path):
    if path:
        return open(path, "w", encoding="utf-8")
    return sys.stdout


def _normalize(results, search_type):
    rows = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        if search_type == "web":
            rows.append({
                "title": item.get("title", ""),
                "href": item.get("href", ""),
                "body": item.get("body", ""),
            })
        elif search_type == "news":
            rows.append({
                "title": item.get("title", ""),
                "href": item.get("url", item.get("href", "")),
                "body": item.get("body", item.get("description", "")),
                "date": item.get("date", ""),
                "source": item.get("source", item.get("publisher", "")),
            })
        elif search_type == "images":
            rows.append({
                "title": item.get("title", ""),
                "href": item.get("url", ""),
                "image": item.get("image", ""),
                "thumbnail": item.get("thumbnail", ""),
                "width": item.get("width", ""),
                "height": item.get("height", ""),
                "source": item.get("source", ""),
            })
        elif search_type == "videos":
            rows.append({
                "title": item.get("title", ""),
                "href": item.get("url", item.get("href", "")),
                "body": item.get("description", item.get("content", "")),
                "publisher": item.get("publisher", ""),
                "duration": item.get("duration", ""),
                "published": item.get("published", ""),
            })
    return rows


def _format_text(rows, search_type):
    lines = []
    for idx, row in enumerate(rows, 1):
        lines.append(f"{idx}. {row.get('title', '')}")
        if search_type in ("web", "news", "videos"):
            lines.append(f"   URL: {row.get('href', '')}")
            if row.get("body"):
                lines.append(f"   {row.get('body')}")
        elif search_type == "images":
            lines.append(f"   URL: {row.get('href', '')}")
            lines.append(f"   Image: {row.get('image', '')}")
            lines.append(f"   Size: {row.get('width')}x{row.get('height')}")
        if search_type == "news" and row.get("date"):
            lines.append(f"   Published: {row.get('date')} by {row.get('source')}")
        if search_type == "videos":
            meta = " / ".join(str(row.get(k) or "") for k in ("publisher", "duration", "published"))
            if meta.strip(" /"):
                lines.append(f"   {meta}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_markdown(rows, search_type):
    blocks = []
    for idx, row in enumerate(rows, 1):
        blocks.append(f"## {idx}. {row.get('title', '')}")
        blocks.append("")
        if search_type in ("web", "news", "videos"):
            blocks.append(f"**URL:** {row.get('href', '')}")
        elif search_type == "images":
            blocks.append(f"**URL:** {row.get('href', '')}")
            blocks.append(f"**Image:** {row.get('image', '')}")
        blocks.append("")
        if row.get("body"):
            blocks.append(row.get("body"))
            blocks.append("")
    return "\n".join(blocks).strip()


def _run_search(args):
    if DDGS is None:
        raise RuntimeError(
            "Missing required dependency. Run: pip install duckduckgo-search"
        )
    kwargs = {
        "region": args.region,
        "safesearch": args.safe_search,
        "max_results": args.max_results,
    }
    if args.time_range:
        kwargs["timelimit"] = args.time_range
    with DDGS() as ddgs:
        if args.type == "web":
            raw = ddgs.text(args.query, **kwargs)
        elif args.type == "news":
            raw = ddgs.news(args.query, **kwargs)
        elif args.type == "images":
            raw = ddgs.images(
                args.query,
                region=args.region,
                safesearch=args.safe_search,
                size=args.image_size or "",
                color=args.image_color or "",
                type_image=args.image_type or "",
                layout=args.image_layout or "",
                max_results=args.max_results,
            )
        elif args.type == "videos":
            raw = ddgs.videos(
                args.query,
                region=args.region,
                safesearch=args.safe_search,
                duration=args.video_duration or "",
                resolution=args.video_resolution or "",
                max_results=args.max_results,
            )
        else:
            raise ValueError(f"Unsupported search type: {args.type}")
    return _normalize(raw, args.type)


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="DuckDuckGo web search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("-t", "--type", choices=("web", "news", "images", "videos"), default="web")
    parser.add_argument("-n", "--max-results", type=int, default=10)
    parser.add_argument("--time-range", choices=("d", "w", "m", "y"), default=None)
    parser.add_argument("-r", "--region", default="wt-wt")
    parser.add_argument("--safe-search", choices=("on", "moderate", "off"), default="moderate")
    parser.add_argument("-f", "--format", choices=("text", "markdown", "json"), default="text")
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument("--image-size", default=None)
    parser.add_argument("--image-color", default=None)
    parser.add_argument("--image-type", default=None)
    parser.add_argument("--image-layout", default=None)
    parser.add_argument("--video-duration", choices=("short", "medium", "long"), default=None)
    parser.add_argument("--video-resolution", choices=("high", "standard"), default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or sys.argv[1:])
    try:
        rows = _run_search(args)
        if args.format == "json":
            payload = json.dumps(rows, ensure_ascii=False, indent=2)
        elif args.format == "markdown":
            payload = _format_markdown(rows, args.type)
        else:
            payload = _format_text(rows, args.type)
        with _output_stream(args.output) as stream:
            print(payload, file=stream)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
