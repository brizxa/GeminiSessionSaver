#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gemini_save.py — Download a Gemini shared conversation to a local file.

Dependencies:
    pip install requests beautifulsoup4
    # For JS-rendered pages (recommended):
    pip install playwright && playwright install chromium

Usage:
    python gemini_save.py <url>
    python gemini_save.py <url> -o output.md
    python gemini_save.py <url> --playwright          # force Playwright (more reliable)
    python gemini_save.py <url> --format text         # plain text instead of markdown
    python gemini_save.py <url> --debug-html raw.html # save raw HTML for inspection

Example:
    python gemini_save.py https://gemini.google.com/share/061733af550c
"""

import argparse
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# Ensure stdout handles Unicode on Windows consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-16"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests
import markdown as md_lib
from bs4 import BeautifulSoup, Tag
import markdownify as mdc

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Minimum character count to consider the extraction successful
MIN_CONTENT_CHARS = 100


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_with_requests(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def fetch_with_playwright(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        sys.exit(
            "Playwright not installed.\n"
            "Run:  pip install playwright && playwright install chromium"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
        page.goto(url, wait_until="load", timeout=30_000)

        # Wait for a conversation element to appear
        selectors = [
            "message-content",
            "[data-turn-index]",
            ".conversation-turn",
            ".user-query-text",
            ".model-response-text",
            "response-container",
            "model-response",
            "user-query",
        ]
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=10_000)
                break
            except PWTimeout:
                continue

        # Scroll to bottom and back to trigger lazy-loaded images, then settle
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)

        html = page.content()
        browser.close()
    return html


# ---------------------------------------------------------------------------
# Extraction strategies
# ---------------------------------------------------------------------------

class _GeminiConverter(mdc.MarkdownConverter):
    """Markdownify converter that preserves sub/sup and KaTeX spans as inline HTML."""

    def convert_sub(self, el, text, **kwargs):
        return f"<sub>{text}</sub>"

    def convert_sup(self, el, text, **kwargs):
        return f"<sup>{text}</sup>"

    def convert_span(self, el, text, **kwargs):
        cls = " ".join(el.get("class") or [])
        if "katex-display" in cls:
            inner = el.find(class_="katex")
            return f"\n\n{str(inner)}\n\n" if inner else text
        if "katex" in cls:
            return str(el)
        return text

    def convert_img(self, el, text, **kwargs):
        src = el.get("src") or ""
        alt = el.get("alt") or ""
        if not src:
            return ""
        safe_alt = alt.replace('"', "&quot;")
        return f'\n<img src="{src}" alt="{safe_alt}" style="max-width:600px;height:auto">\n'


def _element_text(el: Tag) -> str:
    return _GeminiConverter(
        heading_style="ATX", bullets="-", strip=["script", "style"]
    ).convert(str(el)).strip()


def _has_content(el: Tag) -> bool:
    """True if element has visible text OR at least one image with a src."""
    return bool(el.get_text(strip=True)) or bool(el.find("img", src=True))


def try_data_turn_role(soup: BeautifulSoup) -> list[dict]:
    """Elements with data-turn-role='user' / 'model'."""
    turns = soup.find_all(attrs={"data-turn-role": True})
    return [
        {"role": t["data-turn-role"], "content": _element_text(t)}
        for t in turns if _has_content(t)
    ]


def try_css_selectors(soup: BeautifulSoup) -> list[dict]:
    """Known Gemini/Bard CSS class and element patterns."""
    pairs = [
        ("user-query, .user-query-text, [data-message-author-role='user']", "user"),
        (
            "model-response, .model-response-text, response-container, "
            ".response-content, [data-message-author-role='model']",
            "model",
        ),
    ]

    tagged: list[tuple[int, str, Tag]] = []
    all_descendants = list(soup.descendants)

    for selector, role in pairs:
        for el in soup.select(selector):
            try:
                idx = all_descendants.index(el)
            except ValueError:
                idx = 0
            if _has_content(el):
                tagged.append((idx, role, el))

    tagged.sort(key=lambda x: x[0])
    return [{"role": role, "content": _element_text(el)} for _, role, el in tagged]


def try_af_init_data(html: str) -> list[dict]:
    """Google sometimes embeds conversation data via AF_initDataCallback JSON blobs."""
    messages: list[dict] = []

    # Capture everything inside AF_initDataCallback({...})
    for raw in re.findall(r"AF_initDataCallback\((\{.+?\})\);", html, re.DOTALL):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        found = _search_for_messages(data)
        if found:
            messages.extend(found)

    return messages


def _search_for_messages(obj, depth: int = 0) -> list[dict]:
    if depth > 12:
        return []
    if isinstance(obj, dict):
        if {"role", "content"} <= obj.keys():
            return [{"role": obj["role"], "content": str(obj["content"])}]
        results = []
        for v in obj.values():
            results.extend(_search_for_messages(v, depth + 1))
        return results
    if isinstance(obj, list):
        results = []
        for item in obj:
            results.extend(_search_for_messages(item, depth + 1))
        return results
    return []


def try_plain_text_fallback(soup: BeautifulSoup) -> list[dict]:
    """Strip boilerplate and return all remaining text as a single block."""
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return [{"role": "content", "content": text}] if text else []


def extract_conversation(html: str) -> tuple[str, list[dict]]:
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else ""

    # Try strategies in order of reliability
    for strategy in (
        lambda: try_data_turn_role(soup),
        lambda: try_css_selectors(soup),
        lambda: try_af_init_data(html),
        lambda: try_plain_text_fallback(soup),
    ):
        messages = strategy()
        total_chars = sum(len(m["content"]) for m in messages)
        if messages and total_chars >= MIN_CONTENT_CHARS:
            return title, messages

    return title, []


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

ROLE_LABELS = {"user": "You", "model": "Gemini", "assistant": "Gemini"}

_UI_PREFIXES = re.compile(r"(?m)^(You said|Gemini said)\s*$\n?", re.IGNORECASE)


def _clean(content: str) -> str:
    content = _UI_PREFIXES.sub("", content).strip()
    return re.sub(r"\n{3,}", "\n\n", content)


def format_markdown(url: str, title: str, messages: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# {title or 'Gemini Shared Conversation'}",
        "",
        f"**Source:** {url}  ",
        f"**Saved:** {now}",
        "",
        "---",
        "",
    ]
    for msg in messages:
        content = _clean(msg["content"])
        if not content:
            continue
        label = ROLE_LABELS.get(msg["role"], msg["role"].capitalize())
        lines += [f"### {label}", "", content, ""]
    return "\n".join(lines)


def format_text(url: str, title: str, messages: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = "=" * 60
    lines = [title or "Gemini Shared Conversation", sep, f"Source: {url}", f"Saved:  {now}", sep, ""]
    for msg in messages:
        content = _clean(msg["content"])
        if not content:
            continue
        label = ROLE_LABELS.get(msg["role"], msg["role"].capitalize())
        lines += [f"[{label}]", content, ""]
    return "\n".join(lines)


def format_html(url: str, title: str, messages: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_title = title or "Gemini Shared Conversation"
    converter = md_lib.Markdown(extensions=["tables", "fenced_code", "md_in_html"])

    bubbles = []
    for msg in messages:
        content = _clean(msg["content"])
        if not content:
            continue
        converter.reset()
        body = converter.convert(content)
        role_class = "user" if msg["role"] == "user" else "model"
        bubbles.append(
            f'<div class="bubble-wrap {role_class}"><div class="bubble">{body}</div></div>'
        )

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{clean_title}</title>
<style>
.katex-display{{display:block;margin:.8em 0;overflow:auto hidden}}
.katex{{display:inline-block;font:normal 1.1em "KaTeX_Main","Times New Roman",serif;white-space:nowrap;line-height:1.2;text-indent:0;vertical-align:middle}}
.katex .strut{{display:inline-block}}
.katex .base{{position:relative;display:inline-block;white-space:nowrap}}
.katex .mord,.katex .mrel,.katex .mop,.katex .mopen,.katex .mclose,.katex .mpunct,.katex .mbin,.katex .minner{{display:inline-block}}
.katex .msupsub{{text-align:left}}
.katex .vlist-t{{display:inline-table;table-layout:fixed;border-collapse:collapse}}
.katex .vlist-r{{display:table-row}}
.katex .vlist{{display:table-cell;vertical-align:bottom;position:relative}}
.katex .vlist>span{{display:block;height:0;position:relative}}
.katex .vlist>span>span{{display:inline-block}}
.katex .col-align-c{{text-align:center}}.katex .col-align-l{{text-align:left}}.katex .col-align-r{{text-align:right}}
.katex .mfrac{{text-align:center}}
.katex .frac-line{{display:inline-block;width:100%;border-bottom-style:solid}}
.katex .vlist>span>.pstrut{{overflow:hidden;width:0}}
.katex-html[aria-hidden=true]{{display:inline}}
</style>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#e5ddd5;padding:20px;min-height:100vh}}
.header{{text-align:center;margin-bottom:20px}}
.header h1{{font-size:16px;color:#555;font-weight:600}}
.header p{{font-size:12px;color:#999;margin-top:3px}}
.header a{{color:#0084ff;text-decoration:none}}
.chat{{max-width:1200px;margin:0 auto;display:flex;flex-direction:column;gap:4px}}
.bubble-wrap{{display:flex;padding:2px 0}}
.bubble-wrap.user{{justify-content:flex-end}}
.bubble-wrap.model{{justify-content:flex-start}}
.bubble{{padding:9px 13px;border-radius:18px;max-width:85%;word-break:break-word;line-height:1.55;font-size:15px}}
.bubble img{{max-width:100%;height:auto;border-radius:8px;display:block}}
.user .bubble{{background:#0084ff;color:#fff;border-radius:18px 18px 4px 18px}}
.model .bubble{{background:#fff;color:#111;border-radius:4px 18px 18px 18px;box-shadow:0 1px 1px rgba(0,0,0,.1)}}
.bubble p{{margin-bottom:.6em}}.bubble p:last-child{{margin-bottom:0}}
.bubble ul,.bubble ol{{margin:.4em 0;padding-left:1.4em}}
.bubble li{{margin-bottom:.25em}}
.bubble table{{border-collapse:collapse;margin:.6em 0;font-size:13px;width:100%}}
.bubble th,.bubble td{{border:1px solid rgba(0,0,0,.15);padding:5px 9px;text-align:left;vertical-align:top}}
.bubble th{{font-weight:600;background:rgba(0,0,0,.04)}}
.user .bubble th,.user .bubble td{{border-color:rgba(255,255,255,.3)}}
.user .bubble th{{background:rgba(255,255,255,.15)}}
.bubble code{{background:rgba(0,0,0,.07);padding:1px 5px;border-radius:4px;font-size:13px;font-family:monospace}}
.user .bubble code{{background:rgba(255,255,255,.2)}}
.bubble pre{{background:rgba(0,0,0,.06);padding:10px 12px;border-radius:8px;overflow-x:auto;margin:.5em 0}}
.user .bubble pre{{background:rgba(0,0,0,.18)}}
.bubble pre code{{background:none;padding:0}}
.bubble h1,.bubble h2,.bubble h3,.bubble h4{{margin:.7em 0 .3em;font-size:1em;font-weight:700}}
.bubble strong{{font-weight:700}}
.bubble hr{{border:none;border-top:1px solid rgba(0,0,0,.1);margin:.6em 0}}
.user .bubble hr{{border-color:rgba(255,255,255,.3)}}
.bubble sub,.bubble sup{{font-size:.75em;line-height:0;position:relative;vertical-align:baseline}}
.bubble sup{{top:-.4em}}.bubble sub{{bottom:-.2em}}
</style>
<script>
document.addEventListener('keydown',function(e){{
  var k=e.key;
  if(k==='Home')window.scrollTo(0,0);
  else if(k==='End')window.scrollTo(0,document.documentElement.scrollHeight);
  else if(k==='PageUp')window.scrollBy(0,-window.innerHeight*.9);
  else if(k==='PageDown')window.scrollBy(0,window.innerHeight*.9);
  else return;
  e.preventDefault();
}});
</script>
</head>
<body>
<div class="header">
  <h1>{clean_title}</h1>
  <p>Saved {now} &nbsp;&middot;&nbsp; <a href="{url}" target="_blank">{url}</a></p>
</div>
<div class="chat">
{"".join(bubbles)}
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def share_id_from_url(url: str) -> str:
    return urlparse(url).path.strip("/").split("/")[-1]


def _normalize_share_url(url: str) -> str:
    """Convert /share/continue/<id> to the equivalent public /share/<id> URL."""
    if "/share/continue/" in urlparse(url).path:
        sid = urlparse(url).path.strip("/").split("/")[-1]
        public_url = f"https://gemini.google.com/share/{sid}"
        print(f"Note: 'continue' link → trying public URL: {public_url}")
        return public_url
    return url


def default_filename(url: str, fmt: str) -> str:
    sid = share_id_from_url(url)
    ext = {"markdown": "md", "text": "txt", "html": "html"}.get(fmt, "md")
    return f"gemini_{sid}.{ext}"


def save(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    print(f"Saved  : {path}")


_LOGIN_PAGE_MARKERS = (
    "Sign in to start saving your chats",
    "sign-in-button",
    "meet-gemini-logo",
)


def _looks_like_login_page(title: str, messages: list[dict]) -> bool:
    if title.strip().lower() in ("google gemini", "gemini"):
        combined = " ".join(m["content"] for m in messages)
        return any(marker in combined for marker in _LOGIN_PAGE_MARKERS)
    return False


def _raw_path_for(url: str, title_override: str) -> Path:
    sid = share_id_from_url(url)
    base = re.sub(r'[\\/*?:"<>|]', "_", title_override).strip() if title_override else f"gemini_{sid}"
    return Path(f"{base}_raw.html")


def _url_from_raw(html: str, raw_path: Path) -> str:
    """Recover the share URL from HTML meta tags or the filename."""
    soup = BeautifulSoup(html[:100_000], "html.parser")
    canon = soup.find("link", rel="canonical")
    if canon and canon.get("href"):
        return canon["href"]
    og = soup.find("meta", property="og:url")
    if og and og.get("content"):
        return og["content"]
    m = re.search(r'gemini_([a-f0-9]+)[._]raw\.html$', raw_path.name)
    if m:
        return f"https://gemini.google.com/share/{m.group(1)}"
    return ""


# ---------------------------------------------------------------------------
# Stage 1 — download
# ---------------------------------------------------------------------------

def stage_download(url: str, title_override: str, args) -> "Path | None":
    """Fetch URL and save raw HTML. Returns the saved path, or None on failure."""
    print(f"Fetching: {url}")
    try:
        html = fetch_with_playwright(url) if args.playwright else fetch_with_requests(url)
    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}")
        return None
    except Exception as exc:
        print(f"Fetch failed: {exc}")
        return None

    if getattr(args, "debug_html", None):
        Path(args.debug_html).write_text(html, encoding="utf-8")
        print(f"Debug HTML: {args.debug_html}")

    _, messages = extract_conversation(html)
    sparse = (
        not messages
        or (len(messages) == 1 and messages[0]["role"] == "content" and len(messages[0]["content"]) < 500)
    )
    if not args.playwright and sparse:
        print("Content looks sparse — retrying with Playwright...")
        try:
            html = fetch_with_playwright(url)
            if getattr(args, "debug_html", None):
                Path(args.debug_html).write_text(html, encoding="utf-8")
        except SystemExit:
            pass

    raw_path = _raw_path_for(url, title_override)
    raw_path.write_text(html, encoding="utf-8")
    print(f"Raw HTML: {raw_path}")
    return raw_path


# ---------------------------------------------------------------------------
# Stage 2 — convert
# ---------------------------------------------------------------------------

def stage_convert(raw_path: Path, url: str, title_override: str, args) -> None:
    """Read raw HTML and write formatted output files."""
    html = raw_path.read_text(encoding="utf-8")

    if not url:
        url = _url_from_raw(html, raw_path)

    title, messages = extract_conversation(html)

    if not messages or _looks_like_login_page(title, messages):
        print("Warning: no conversation content extracted (got login page?).")
        return

    display_title = title_override or title
    sid = share_id_from_url(url) if url else re.sub(r'[._]raw$', '', raw_path.stem)
    base_name = re.sub(r'[\\/*?:"<>|]', "_", title_override).strip() if title_override else f"gemini_{sid}"

    if args.format == "both":
        base = Path(args.output).stem if getattr(args, "output", None) else base_name
        save(Path(f"{base}.html"), format_html(url, display_title, messages))
        save(Path(f"{base}.md"),   format_markdown(url, display_title, messages))
    else:
        formatters = {"markdown": format_markdown, "text": format_text, "html": format_html}
        out_path = Path(args.output) if getattr(args, "output", None) else Path(default_filename(url, args.format))
        save(out_path, formatters[args.format](url, display_title, messages))

    print(f"Turns  : {len(messages)}")
    print(f"Title  : {display_title or '(none found)'}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def process_one(url: str, title_override: str, args) -> None:
    """Run stage 1 then (unless --download-only) stage 2 for a single URL."""
    url = _normalize_share_url(url)

    raw_path = stage_download(url, title_override, args)
    if raw_path is None:
        return

    if not getattr(args, "download_only", False):
        stage_convert(raw_path, url, title_override, args)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Save a Gemini shared conversation to a local file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", nargs="?", help="Gemini share URL (prompted if omitted; unused with --convert)")
    parser.add_argument("-o", "--output", help="Output file path (auto-generated if omitted)")
    parser.add_argument(
        "--input-csv",
        metavar="FILE",
        help="CSV file with url[,title] columns — process multiple conversations",
    )
    parser.add_argument(
        "--format",
        choices=["both", "html", "markdown", "text"],
        default="both",
        help="Output format: both=html+md (default), html, markdown, text",
    )
    parser.add_argument(
        "--playwright",
        action="store_true",
        help="Use Playwright for JS rendering — more reliable, requires extra install",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Stage 1 only: download and save raw HTML, skip conversion",
    )
    parser.add_argument(
        "--convert",
        metavar="FILE",
        help="Stage 2 only: convert an existing _raw.html file to output formats",
    )
    parser.add_argument(
        "--debug-html",
        metavar="PATH",
        help="Save the raw fetched HTML to this path (for debugging selectors)",
    )
    args = parser.parse_args()

    # Stage 2 only
    if args.convert:
        raw_path = Path(args.convert)
        if not raw_path.exists():
            sys.exit(f"Error: file not found: {raw_path}")
        stage_convert(raw_path, args.url or "", "", args)
        return

    if args.input_csv:
        import csv
        with open(args.input_csv, newline="", encoding="utf-8-sig") as f:
            peek = f.readline()
            f.seek(0)
            first_field = peek.split(",")[0].strip().strip('"').lower()
            if first_field == "url":
                rows = [
                    {"url": (r.get("url") or "").strip(), "title": (r.get("title") or "").strip()}
                    for r in csv.DictReader(f)
                ]
            else:
                rows = [
                    {"url": cols[0].strip(), "title": cols[1].strip() if len(cols) > 1 else ""}
                    for cols in csv.reader(f)
                    if cols and cols[0].strip()
                ]
        for row in rows:
            u, t = row["url"], row["title"]
            if not u.startswith("http"):
                print(f"Skipping invalid URL: {u}")
                continue
            process_one(u, t, args)
        return

    url = args.url
    if not url:
        try:
            url = input("Gemini share URL: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nCancelled.")
    if not url.startswith("http"):
        sys.exit("Error: URL must start with http:// or https://")

    process_one(url, "", args)


if __name__ == "__main__":
    main()
