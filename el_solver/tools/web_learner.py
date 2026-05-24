"""
Web Learner — cari di web, fetch, extract text, simpan ke memory.

Flow:
  1. search_web(topic) → hasil DDG
  2. fetch_page(url) → clean text via BeautifulSoup
  3. save ke memory/web/{slug}/index.md
  4. update memory/web/INDEX.md
"""
from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from el_solver.config import PROJECT_ROOT
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

WEB_MEMORY_DIR = PROJECT_ROOT / "memory" / "web"
MAX_PAGES = 3
MAX_CHARS_PER_PAGE = 4000
FETCH_TIMEOUT = 10.0

# Sandbox + throttle (R14 M5): web_learner fetches attacker-influenceable
# URLs (search results), so guard SSRF and rate-limit.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB hard cap before parsing
THROTTLE_SECONDS = 1.0  # min delay between outbound fetches
_throttle_lock = threading.Lock()
_last_fetch_ts = 0.0


def _is_safe_url(url: str) -> bool:
    """Reject non-http(s) schemes and hosts that resolve to private/reserved
    IPs (SSRF guard: blocks localhost, link-local, 169.254.169.254, RFC1918)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(str(addr).split("%")[0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _throttle() -> None:
    """Block until THROTTLE_SECONDS has elapsed since the last fetch."""
    global _last_fetch_ts
    with _throttle_lock:
        wait = THROTTLE_SECONDS - (time.monotonic() - _last_fetch_ts)
        if wait > 0:
            time.sleep(wait)
        _last_fetch_ts = time.monotonic()

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:50].strip("-")


def _search_web(query: str, max_results: int = MAX_PAGES + 2) -> list[dict]:
    """Cari di DuckDuckGo via ddgs. Return list of {title, href, body}."""
    try:
        from ddgs import DDGS
        with DDGS() as ddg:
            results = list(ddg.text(query, max_results=max_results))
        return results
    except ImportError:
        # fallback: coba package lama (duckduckgo_search)
        try:
            from duckduckgo_search import DDGS  # type: ignore[no-redef]
            with DDGS() as ddg:
                results = list(ddg.text(query, max_results=max_results))
            return results
        except Exception as exc:
            logger.warning(f"web_learner: DDG search gagal: {exc}")
            return []
    except Exception as exc:
        logger.warning(f"web_learner: DDG search gagal: {exc}")
        return []


def _fetch_page(url: str) -> Optional[str]:
    """Fetch URL, return clean plain text. None kalau gagal atau bukan HTML."""
    if not _is_safe_url(url):
        logger.warning(f"web_learner: blocked unsafe URL {url}")
        return None
    _throttle()
    try:
        with httpx.Client(
            headers=_HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            if "html" not in resp.headers.get("content-type", ""):
                return None
            raw = resp.content[:MAX_RESPONSE_BYTES]
            soup = BeautifulSoup(raw, "lxml")
    except Exception as exc:
        logger.debug(f"web_learner: fetch gagal {url}: {exc}")
        return None

    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "button"]):
        tag.decompose()

    text = ""
    for selector in ["main", "article", "[role=main]", ".content", "#content", "body"]:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator="\n", strip=True)
            break
    if not text:
        text = soup.get_text(separator="\n", strip=True)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    clean = "\n".join(lines)
    return clean[:MAX_CHARS_PER_PAGE] if len(clean) > 200 else None


def _save_to_memory(topic: str, slug: str, pages: list[dict]) -> Path:
    """Simpan ke memory/web/{slug}/index.md. Return path."""
    dest_dir = WEB_MEMORY_DIR / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts = [
        f"---",
        f"name: web-{slug}",
        f"description: Knowledge tentang '{topic}' dari web ({now})",
        f"type: note",
        f"topic: {topic}",
        f"learned_at: {now}",
        f"sources: {len(pages)}",
        f"---",
        f"",
        f"# {topic}",
        f"",
        f"Dipelajari dari {len(pages)} sumber pada {now}.",
        f"",
    ]

    for i, page in enumerate(pages, 1):
        short_url = page["url"][:80] + ("..." if len(page["url"]) > 80 else "")
        parts += [
            f"---",
            f"",
            f"## Sumber {i}: {page['title']}",
            f"URL: {short_url}",
            f"",
            page["text"],
            f"",
        ]

    dest = dest_dir / "index.md"
    dest.write_text("\n".join(parts), encoding="utf-8")
    return dest


def _update_memory_index(topic: str, slug: str) -> None:
    """Tambah atau perbarui entry di memory/web/INDEX.md."""
    index_file = WEB_MEMORY_DIR / "INDEX.md"
    entry = f"- [{topic}]({slug}/index.md) — web knowledge\n"

    if index_file.exists():
        content = index_file.read_text(encoding="utf-8")
        lines = [ln for ln in content.splitlines() if slug not in ln]
        content = "\n".join(lines).rstrip() + "\n" + entry
    else:
        WEB_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        content = f"# Web Knowledge Index\n\n{entry}"

    index_file.write_text(content, encoding="utf-8")


def search_and_learn(topic: str, max_pages: int = MAX_PAGES) -> str:
    """
    Cari topik di web, fetch, simpan ke memory.
    Return ringkasan untuk ditampilkan ke user.
    """
    slug = _slugify(topic)
    logger.info(f"web_learner: mulai belajar '{topic}' slug={slug}")

    results = _search_web(topic, max_results=max_pages + 2)
    if not results:
        return (
            f"Tidak bisa cari sumber tentang '{topic}'. "
            "Periksa koneksi internet atau coba topik yang lebih spesifik."
        )

    pages: list[dict] = []
    for r in results:
        if len(pages) >= max_pages:
            break
        url = r.get("href", "")
        title = r.get("title", url)
        if not url:
            continue
        text = _fetch_page(url)
        if text:
            pages.append({"url": url, "title": title, "text": text})
            logger.debug(f"web_learner: ok {url} ({len(text)} chars)")

    if not pages:
        return (
            f"Dapat hasil pencarian untuk '{topic}', tapi gagal fetch isinya. "
            "Mungkin situs diblokir atau koneksi terganggu."
        )

    dest = _save_to_memory(topic, slug, pages)
    _update_memory_index(topic, slug)
    logger.info(f"web_learner: disimpan {dest} ({len(pages)} sumber)")

    lines = []
    for p in pages:
        url_short = p["url"][:60] + "..." if len(p["url"]) > 60 else p["url"]
        lines.append(f"- {p['title']} ({url_short})")

    return (
        f"Sudah belajar tentang '{topic}' dari {len(pages)} sumber:\n"
        + "\n".join(lines)
        + "\n\nTersimpan di knowledge base. Tanya aja kalau butuh info ini."
    )
