"""Browser automation utilities untuk EL SOLVER."""
from __future__ import annotations

import subprocess
import urllib.parse

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


def open_url(url: str) -> str:
    """Buka URL di Chrome. Chrome tetap terbuka setelah fungsi selesai."""
    subprocess.Popen(["open", "-a", "Google Chrome", url])
    logger.info(f"Opened: {url}")
    return url


def youtube_play(query: str) -> dict:
    """
    Cari video di YouTube dan putar di Chrome.

    Flow: Playwright headless cari URL → Chrome buka langsung → video autoplay.
    Chrome tetap terbuka setelah selesai.

    Returns {"title": str, "url": str}
    """
    from playwright.sync_api import sync_playwright

    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    logger.info(f"YouTube search: {query!r}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(search_url, wait_until="domcontentloaded", timeout=20000)

        page.wait_for_selector("ytd-video-renderer a#video-title", timeout=10000)
        href = page.get_attribute("ytd-video-renderer a#video-title", "href")
        title = page.get_attribute("ytd-video-renderer a#video-title", "title") or query
        browser.close()

    video_url = f"https://www.youtube.com{href}"
    subprocess.Popen(["open", "-a", "Google Chrome", video_url])
    logger.info(f"Playing: {title!r} → {video_url}")
    return {"title": title, "url": video_url}


def browser_task(url: str, steps: list[str]) -> str:
    """
    Buka URL di Chrome yang bisa dilihat dan jalankan langkah-langkah otomatis.
    Chrome tetap terbuka setelah semua langkah selesai.

    steps: list instruksi string, mis. ["klik tombol Login", "isi form nama dengan 'Wildan'"]
    Returns: status string.

    Gunakan fungsi ini kalau user minta task web yang butuh interaksi (klik, isi form, scroll, dll).
    Untuk task kompleks, tulis Playwright script Python langsung ke /tmp/ lalu jalankan.
    """
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=False)

        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        results.append(f"Buka: {url}")

        for step in steps:
            try:
                _execute_step(page, step)
                results.append(f"✓ {step}")
            except Exception as e:
                results.append(f"✗ {step}: {e}")

        # Detach dari Chrome tanpa menutupnya
        context.close()
        browser.disconnect()

    return "\n".join(results)


def _execute_step(page: object, instruction: str) -> None:
    """Eksekusi satu langkah instruksi sederhana."""
    low = instruction.lower()
    if low.startswith("klik "):
        text = instruction[5:].strip("'\"")
        page.get_by_text(text, exact=False).first.click()
    elif low.startswith("isi ") or low.startswith("ketik "):
        # "isi [field] dengan [value]" atau "ketik [value] di [field]"
        page.keyboard.type(instruction.split(" dengan ")[-1].strip("'\""))
    elif low.startswith("scroll"):
        page.evaluate("window.scrollBy(0, 500)")
    elif low.startswith("tunggu"):
        page.wait_for_timeout(2000)
    else:
        # Fallback: coba klik elemen yang namanya sesuai
        page.get_by_text(instruction, exact=False).first.click()
