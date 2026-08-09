# Web Search

## Overview

Search the web using DuckDuckGo's API to find information across web pages, news articles, images, and videos. Returns results in multiple formats (text, markdown, JSON) with filtering options for time range, region, and safe search.

## When to Use This Skill

Use this skill when users request:

- Web searches for information or resources
- Finding current or recent information online
- Looking up news articles about specific topics
- Searching for images by description or topic
- Finding videos on specific subjects
- Research requiring current web data
- Fact-checking or verification using web sources
- Gathering URLs and resources on a topic

## Prerequisites

Install the required dependency:

```bash
pip install duckduckgo-search
```

This library provides a simple Python interface to DuckDuckGo's search API without requiring API keys or authentication.

## Core Capabilities

### 1. Basic Web Search

```bash
python scripts/search.py "<query>"
```

Returns the top 10 web results with titles, URLs, and descriptions in a clean text format.

### 2. Limiting Results

```bash
python scripts/search.py "<query>" --max-results <N>
```

### 3. Time Range Filtering

```bash
python scripts/search.py "<query>" --time-range <d|w|m|y>
```

### 4. News Search

```bash
python scripts/search.py "<query>" --type news
```

### 5. Image Search

```bash
python scripts/search.py "<query>" --type images
```

Image filters: `--image-size`, `--image-color`, `--image-type`, `--image-layout`.

### 6. Video Search

```bash
python scripts/search.py "<query>" --type videos
```

Video filters: `--video-duration`, `--video-resolution`.

### 7. Region-Specific Search

```bash
python scripts/search.py "<query>" --region us-en
```

Common region codes: `us-en`, `uk-en`, `ca-en`, `au-en`, `de-de`, `fr-fr`, `wt-wt`.

### 8. Safe Search Control

```bash
python scripts/search.py "<query>" --safe-search on
```

Options: `on`, `moderate`, `off`.

### 9. Output Formats

```bash
python scripts/search.py "<query>" --format text
python scripts/search.py "<query>" --format markdown
python scripts/search.py "<query>" --format json
```

### 10. Saving Results to File

```bash
python scripts/search.py "<query>" --output results.txt
```

## Quick Reference

```bash
python scripts/search.py "<query>" [options]
```

Essential options:

- `-t, --type` - Search type (web, news, images, videos)
- `-n, --max-results` - Maximum results (default: 10)
- `--time-range` - Time filter (d, w, m, y)
- `-r, --region` - Region code
- `--safe-search` - Safe search level (on, moderate, off)
- `-f, --format` - Output format (text, markdown, json)
- `-o, --output` - Save to file

## Best Practices

- Be specific; use clear, specific search queries.
- Use time filters for current information.
- Start with 10-20 results, increase if needed.
- Use JSON for automation.
- Do not hammer the API with rapid repeated searches.

## Troubleshooting

- Missing dependency: run `pip install duckduckgo-search`
- No results: broaden query or remove time filters
- Timeout: retry after a moment
- Rate limiting: space out searches
- For unstable connections, `scripts/search.py` now uses the no-token
  `html.duckduckgo.com/html/` and `lite.duckduckgo.com/lite/` endpoints
  concurrently, decodes `uddg=` redirect URLs, and falls back from `cn-zh`
  to `wt-wt`. Use `--timeout` and `--retries` to tune slow networks.

## Resources

### scripts/search.py

The main search tool implementing DuckDuckGo search functionality. It supports web, news, images, and videos; flexible filtering; multiple output formats; file output; and graceful error handling.
