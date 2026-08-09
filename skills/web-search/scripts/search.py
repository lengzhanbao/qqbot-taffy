#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DuckDuckGo web search CLI compatible with the web-search skill."""

import argparse
import concurrent.futures
import json
import re
import sys
import time
from urllib.parse import parse_qs, unquote, urlparse

try:
    import requests
except Exception:
    requests = None

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

try:
    from ddgs import DDGS
except Exception:
    try:
        from duckduckgo_search import DDGS
    except Exception:
        DDGS = None


DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _output_stream(path):
    if path:
        return open(path, "w", encoding="utf-8")
    return sys.stdout


def _decode_ddg_href(href):
    """Decode DuckDuckGo /l/?uddg= redirect links to the real URL."""
    try:
        query = parse_qs(urlparse(href or "").query)
        raw = query.get("uddg")
        if raw:
            return unquote(raw[0])
    except Exception:
        pass
    return href or ""


def _parse_ddg_html(html_text):
    soup = BeautifulSoup(html_text or "", "html.parser")
    rows = soup.select(".result")
    out = []
    for row in rows:
        link = row.select_one("a.result__a")
        if not link:
            continue
        title = link.get_text(" ", strip=True)
        snippet_el = row.select_one(".result__snippet")
        body = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        if title:
            out.append({
                "title": title,
                "href": _decode_ddg_href(link.get("href") or ""),
                "body": body,
            })
    if out:
        return out
    for link in soup.select("a[href*='uddg=']"):
        title = link.get_text(" ", strip=True)
        if not title:
            continue
        snippet_el = link.find_next(
            attrs={"class": ["result__snippet", "snippet"]}
        )
        out.append({
            "title": title,
            "href": _decode_ddg_href(link.get("href") or ""),
            "body": snippet_el.get_text(" ", strip=True) if snippet_el else "",
        })
    return out


def _parse_ddg_lite(html_text):
    soup = BeautifulSoup(html_text or "", "html.parser")
    out = []
    for row in soup.select("table.result, .result, .web-result"):
        link = row.select_one("a.result-link") or row.select_one("a")
        if not link:
            continue
        title = link.get_text(" ", strip=True)
        snippet_el = row.select_one(".result-snippet") or row.select_one(
            "td.result-snippet"
        )
        if title:
            out.append({
                "title": title,
                "href": _decode_ddg_href(link.get("href") or ""),
                "body": snippet_el.get_text(" ", strip=True)
                if snippet_el else "",
            })
    return out


def _fetch_ddg_serp(query, region, max_results, timeout, kind):
    if requests is None or BeautifulSoup is None:
        return []
    per_request_timeout = max(1.0, min(float(timeout), 8.0))
    if kind == "lite":
        resp = requests.get(
            DDG_LITE_URL,
            params={"q": query, "kl": region},
            headers=DDG_HEADERS,
            timeout=per_request_timeout,
        )
    else:
        resp = requests.post(
            DDG_HTML_URL,
            data={"q": query, "kl": region},
            headers=DDG_HEADERS,
            timeout=per_request_timeout,
        )
    if resp.status_code in (202, 403, 429):
        return []
    if not resp.ok:
        return []
    rows = _parse_ddg_lite(resp.text) if kind == "lite" else _parse_ddg_html(
        resp.text
    )
    return rows[:max_results]


def _search_ddg_stable(
    query, region="wt-wt", max_results=10, timeout=10, retries=1
):
    """DuckDuckGo without token: try HTML and Lite concurrently, then fall back."""
    if requests is None or BeautifulSoup is None:
        return []
    seen = set()
    out = []
    regions = [region] if region in ("", None) else [region, "wt-wt"]
    if region != "wt-wt":
        regions = list(dict.fromkeys([region, "wt-wt"]))
    deadline = time.monotonic() + max(1.0, min(float(timeout), 12.0))
    attempts = max(1, int(retries or 1))
    for _ in range(attempts):
        if time.monotonic() >= deadline:
            break
        for region_try in regions:
            if time.monotonic() >= deadline:
                break
            remaining = max(1.0, deadline - time.monotonic())
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(
                        _fetch_ddg_serp,
                        query,
                        region_try,
                        max_results,
                        remaining,
                        kind,
                    )
                    for kind in ("html", "lite")
                ]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        rows = future.result() or []
                    except Exception:
                        rows = []
                    for row in rows:
                        key = re.sub(r"\W+", "", (row.get("title") or "").lower())
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        out.append(row)
                    if len(out) >= max_results:
                        return out[:max_results]
            if out:
                return out[:max_results]
        if out:
            return out[:max_results]
    return out[:max_results]


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
    if args.type == "web":
        raw = _search_ddg_stable(
            args.query,
            region=args.region,
            max_results=args.max_results,
            timeout=args.timeout,
            retries=args.retries,
        )
        return _normalize(raw, "web")
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
    with DDGS(timeout=args.timeout) as ddgs:
        if args.type == "news":
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
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--retries", type=int, default=2)
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
