"""
core/rss.py
-----------
RSS parsing + parallel fetch.

Faz 3.5 — parallel RSS fetching (rapor2.txt §3.5)
Beklenen iyileşme: 30-60s → 5-10s
"""
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from utils.http import get_session

_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"


def _parse_rss(rss_url: str, source_name: str):
    """Tek RSS feed'den ilk item'ı döndür: (title, article_url, body_text) veya None."""
    session = get_session()
    try:
        resp = session.get(rss_url, timeout=15)
        if resp.status_code != 200:
            print(f"{source_name} RSS fetch HTTP {resp.status_code}")
            return None
        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            return None
        item = channel.find("item")
        if item is None:
            return None

        title = (item.findtext("title") or "").strip()
        article_url = (item.findtext("link") or "").strip()
        raw_body = (
            item.findtext(f"{{{_CONTENT_NS}}}encoded")
            or item.findtext("description")
            or ""
        )
        body_text = ""
        if raw_body:
            body_text = BeautifulSoup(raw_body, "html.parser").get_text(separator=" ", strip=True)
        return title, article_url, body_text
    except ET.ParseError as e:
        print(f"{source_name} RSS XML parse error: {e}")
        return None
    except Exception as e:
        print(f"{source_name} RSS error: {e}")
        return None


def _parse_rss_all(rss_url: str, source_name: str, max_items: int = 20) -> list:
    """Tek RSS feed'den tüm item'ları döndür: [{"title", "url", "summary"}, ...]"""
    session = get_session()
    try:
        resp = session.get(rss_url, timeout=15)
        if resp.status_code != 200:
            print(f"{source_name} RSS fetch HTTP {resp.status_code}")
            return []
        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            return []
        items = []
        for item in channel.findall("item")[:max_items]:
            title = (item.findtext("title") or "").strip()
            url = (item.findtext("link") or "").strip()
            raw_body = (
                item.findtext(f"{{{_CONTENT_NS}}}encoded")
                or item.findtext("description")
                or ""
            )
            summary = ""
            if raw_body:
                summary = BeautifulSoup(raw_body, "html.parser").get_text(separator=" ", strip=True)
            if title:
                items.append({"title": title, "url": url, "summary": summary})
        return items
    except ET.ParseError as e:
        print(f"{source_name} RSS XML parse error: {e}")
        return []
    except Exception as e:
        print(f"{source_name} RSS error: {e}")
        return []


def fetch_all_feeds(feed_dict: dict, max_items: int = 10) -> dict:
    """Tüm RSS feed'leri paralel çek. 30-60s → 5-10s (Faz 3.5).

    feed_dict: {source_name: rss_url}
    Returns: {source_name: [article, ...]}
    """
    results = {}

    def fetch_one(source: str, url: str):
        try:
            return source, _parse_rss_all(url, source, max_items)
        except Exception:
            return source, []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(fetch_one, src, url): src
            for src, url in feed_dict.items()
        }
        for future in as_completed(futures, timeout=30):
            try:
                source, articles = future.result()
                results[source] = articles
            except Exception as e:
                print(f"fetch_all_feeds future error: {e}")

    return results
