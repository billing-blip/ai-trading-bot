"""Free headlines from Google News RSS (used by snapshots and check-ins)."""
from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET

import requests


def headlines(query: str, limit: int = 5) -> list[dict]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for item in root.iter("item"):
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip(),
        })
        if len(out) >= limit:
            break
    return out
