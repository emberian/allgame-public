"""Reading the web: fetch a URL as text."""

import re

from resident.tools.registry import tool

MAX_CHARS = 40_000


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


@tool("web", "fetch_url", "Fetch a web page or text file and return its text.",
      required=["url"], url={"type": "string"})
def fetch_url(args, ctx):
    import requests
    url = args["url"].strip()
    if not url.startswith(("http://", "https://")):
        return {"content": "Only http(s) URLs.", "is_error": True}
    r = requests.get(url, timeout=30, headers={"User-Agent": "resident/0.1"})
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if not any(t in ctype for t in ("text", "json", "xml")):
        return {"content": f"{url} is {ctype or 'binary'}, not text.", "is_error": True}
    text = _html_to_text(r.text) if "html" in ctype else r.text
    more = f"\n\n[truncated: {len(text)} chars]" if len(text) > MAX_CHARS else ""
    return {"content": text[:MAX_CHARS] + more}
