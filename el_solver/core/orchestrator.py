"""
Orchestrator — intent router untuk EL SOLVER.

Menerima pesan dari channel manapun (Telegram/CLI), mengklasifikasi
intent ke salah satu dari 4 mode, lalu merutekan ke handler yang tepat.

Mode:
  CONVERSATION  — pesan biasa, tanya-jawab, catat, ingat
  CREATE_AGENT  — "buatkan agent ...", "saya butuh sistem ..."
  INVOKE_AGENT  — "panggil/jalankan agent X untuk ..."
  MAINTAIN_AGENT — "perbaiki/update/hapus agent X"

Klasifikasi: keyword matching dulu (cepat, gratis).
Kalau confidence < 0.75 → fallback ke LLM classifier.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

# ── Enum Mode ─────────────────────────────────────────────────────────────────

class Mode(str, Enum):
    CONVERSATION    = "conversation"
    CREATE_PROJECT  = "create_project"
    CREATE_AGENT    = "create_agent"
    INVOKE_AGENT    = "invoke_agent"
    MAINTAIN_AGENT  = "maintain_agent"
    CREATE_CAROUSEL = "create_carousel"
    BROWSER         = "browser"
    WEB_LEARN       = "web_learn"


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class IntentResult:
    mode: Mode
    confidence: float          # 0.0 – 1.0
    raw_message: str
    agent_name: Optional[str] = None   # ekstrak dari INVOKE / MAINTAIN
    method: str = "keyword"            # "keyword" | "llm"
    extras: dict = field(default_factory=dict)
    skip_brain: bool = False           # True kalau keyword conf >= 0.85 → skip Brain LLM call
    strategy: str = "reply_direct"     # brain decision: reply_direct|clarify|invoke_single|decompose_chain|proactive_followup
    reasoning: str = ""                # one-line brain reasoning for this strategy choice

    def __repr__(self) -> str:
        return (
            f"IntentResult(mode={self.mode.value}, conf={self.confidence:.2f}, "
            f"method={self.method}, strategy={self.strategy!r}, agent={self.agent_name!r})"
        )


# ── Keyword patterns ──────────────────────────────────────────────────────────
# Setiap entry: (compiled regex, confidence, mode)
# Ordered dari paling spesifik ke paling umum.

_PATTERNS: list[tuple[re.Pattern, float, Mode]] = []

def _p(pattern: str, confidence: float, mode: Mode) -> None:
    _PATTERNS.append((re.compile(pattern, re.IGNORECASE), confidence, mode))


# WEB_LEARN — "el belajar tentang X", "el pelajari X", "pelajari tentang X", dst
# Harus sebelum BROWSER dan CREATE_AGENT agar tidak salah route
_p(r"/learn\b", 0.98, Mode.WEB_LEARN)
_p(r"\bel\s+(belajar|pelajari)\b", 0.96, Mode.WEB_LEARN)
_p(r"\bbelajar\b.{0,30}\b(dari\s+)?(web|internet|online)\b", 0.94, Mode.WEB_LEARN)
_p(r"\bpelajari\b.{0,60}\b(dari\s+)?(web|internet|online)\b", 0.93, Mode.WEB_LEARN)
_p(r"\bpelajari\s+(tentang|soal|mengenai)\b", 0.93, Mode.WEB_LEARN)
_p(r"\bcari\s+tahu\b.{0,30}\b(tentang|soal|mengenai)\b", 0.90, Mode.WEB_LEARN)
_p(r"\bcari\s+(?:referensi|materi|sumber)\b.{0,40}\b(dari\s+)?(web|internet|online)\b", 0.92, Mode.WEB_LEARN)

# BROWSER — harus dicek LEBIH DULU dari CREATE_AGENT agar "buka youtube cari X"
# tidak salah diklasifikasi jadi create_agent
_p(r"\bbuka\b.{0,30}\b(youtube|netflix|chrome|google|web|url|link|site|website)\b", 0.93, Mode.BROWSER)
_p(r"\b(youtube|netflix)\b.{0,20}\b(buka|cari|search|play|putar|tonton|lihat)\b", 0.92, Mode.BROWSER)
_p(r"\bcari\b.{0,20}\bdi\s+youtube\b", 0.93, Mode.BROWSER)
_p(r"\bcari\b.{0,10}\byoutube\b", 0.91, Mode.BROWSER)
_p(r"\byoutube\b.{0,20}\bcari\b", 0.91, Mode.BROWSER)
_p(r"\b(putar|play)\b.{0,50}\b(lagu|video|musik|song|audio)\b", 0.90, Mode.BROWSER)
_p(r"\bel\s+buka\b", 0.92, Mode.BROWSER)
_p(r"\bbuka\s+(?:url|link|https?://)\b", 0.93, Mode.BROWSER)

# CREATE_PROJECT — "buat/bikin project X" — dicek SEBELUM CREATE_AGENT agar tidak salah route
_p(r"\b(buat(?:kan)?|bikin(?:in)?|buatin|create|tambah(?:kan)?)\b.{0,15}\bpro(?:ject|jek)\b", 0.95, Mode.CREATE_PROJECT)
_p(r"\bpro(?:ject|jek)\s+baru\b", 0.92, Mode.CREATE_PROJECT)
_p(r"\bbikin\b.{0,10}\bprojek\b", 0.92, Mode.CREATE_PROJECT)

# CREATE_AGENT — paling sering punya kata "buat", "bikin", "create", "bangun"
# diikuti "agent"/"agen", "sistem", "bot", "asisten", "tool"
_p(r"\b(buat(?:kan)?|bikin|create|build|bangun|develop)\b.{0,30}\b(agen|agent|sistem|bot|asisten|automation|tool|workflow)\b", 0.93, Mode.CREATE_AGENT)
_p(r"\b(saya\s+)?(mau|ingin|pengen|pingin|perlu|butuh|need)\b.{0,30}\b(agen|agent|bot|asisten|sistem)\b.{0,30}\b(yang|untuk|buat|bisa|jadi)\b", 0.88, Mode.CREATE_AGENT)
_p(r"\b(tolong\s+)?(buatkan|bikinin|generate|spawn)\b.{0,20}\b(agen|agent|bot)\b", 0.90, Mode.CREATE_AGENT)
_p(r"\b(agen|agent)\s+baru\b", 0.85, Mode.CREATE_AGENT)
_p(r"\b(pengen|pingin|mau|ingin)\s+(punya|bikin|buat)\b.{0,40}\b(agen|agent|bot|asisten|sistem)\b", 0.88, Mode.CREATE_AGENT)
_p(r"\b(automat(?:e|ikan)|otomatis(?:kan)?)\b.{0,40}\b(untuk|supaya|agar|biar)\b", 0.75, Mode.CREATE_AGENT)

# INVOKE_AGENT — "panggil", "jalankan", "run", "pakai", "gunakan" + nama agent
_p(r"\b(panggil|jalankan|run|invoke|execute|aktif(?:kan)?|start|trigger)\b.{0,20}\bagent\b", 0.93, Mode.INVOKE_AGENT)
_p(r"\bgunakan\s+agent\b", 0.90, Mode.INVOKE_AGENT)
_p(r"(?:^|\s)/agents?\s+[\w]", 0.95, Mode.INVOKE_AGENT)
_p(r"\bpakai\s+agent\b.{0,30}\buntuk\b", 0.85, Mode.INVOKE_AGENT)
_p(r"\bagent\b.{0,20}\b(jalankan|run|panggil|aktif(?:kan)?)\b", 0.88, Mode.INVOKE_AGENT)

# MAINTAIN_AGENT — "perbaiki", "update", "edit", "ubah", "hapus", "stop" + agent
_p(r"\b(perbaiki|fix|repair|debug)\b.{0,20}\bagent\b", 0.93, Mode.MAINTAIN_AGENT)
_p(r"\b(update|upgrade|perbarui|revisi)\b.{0,20}\bagent\b", 0.92, Mode.MAINTAIN_AGENT)
_p(r"\b(edit|ubah|ganti|modify|change)\b.{0,20}\bagent\b", 0.90, Mode.MAINTAIN_AGENT)
_p(r"\b(hapus|delete|remove|uninstall|nonaktif(?:kan)?|disable)\b.{0,20}\bagent\b", 0.92, Mode.MAINTAIN_AGENT)
_p(r"\b(stop|hentikan|matikan|restart|reload)\b.{0,20}\bagent\b", 0.88, Mode.MAINTAIN_AGENT)
_p(r"\bagent\b.{0,40}\b(rusak|broken|error|gagal|tidak\s+jalan|tidak\s+bisa)\b", 0.85, Mode.MAINTAIN_AGENT)
_p(r"\bagent\b.{0,40}\b(kayaknya|sepertinya|kok)\b.{0,30}\b(rusak|error|gagal|broken)\b", 0.82, Mode.MAINTAIN_AGENT)

# CREATE_CAROUSEL — "buat/bikin carousel [untuk @akun] [tentang/soal topik]"
_p(r"\b(buat(?:kan)?|bikin(?:in)?|create|generate|buatin)\b.{0,40}\bcarousel\b", 0.95, Mode.CREATE_CAROUSEL)
_p(r"\bcarousel\b.{0,30}\b(untuk|buat|bikin|tentang|soal)\b", 0.90, Mode.CREATE_CAROUSEL)
_p(r"\bcarousel\b.{0,20}\b(@?your_account|@?your_other_account)\b", 0.95, Mode.CREATE_CAROUSEL)
_p(r"\b(@?your_account|@?your_other_account)\b.{0,30}\bcarousel\b", 0.95, Mode.CREATE_CAROUSEL)

# THUMBNAIL — "buat/bikin thumbnail [tentang/soal/untuk topik]"
_p(r"\b(buat(?:kan)?|bikin(?:in)?|buatin|create|generate)\b.{0,40}\bthumbnail\b", 0.95, Mode.INVOKE_AGENT)
_p(r"\bthumbnail\b.{0,30}\b(buat|bikin|tentang|soal|untuk|reels)\b", 0.90, Mode.INVOKE_AGENT)

# IG-TRANSCRIBER — "ambil/transkripsi/transcript/script video [URL/reel/instagram]"
_p(r"\b(ambil|dapatkan|get|transkripsi|transcript|transcribe)\b.{0,30}\b(script|transkrip|teks|narasi|isi)\b.{0,30}\b(video|reel|instagram|ig)\b", 0.96, Mode.INVOKE_AGENT)
_p(r"\b(script|transkrip|teks|narasi)\b.{0,20}\b(video|reel)\b.{0,50}\binstagram\b", 0.94, Mode.INVOKE_AGENT)
_p(r"\btranskripsi\b.{0,50}\b(video|reel|ig)\b", 0.95, Mode.INVOKE_AGENT)
_p(r"\bscript\b.{0,20}\b(video|reel)\b.{0,30}\b(ini|nya|tadi|tsb)\b", 0.90, Mode.INVOKE_AGENT)
_p(r"\big-transcriber\b", 0.99, Mode.INVOKE_AGENT)


# ── Project extractor ─────────────────────────────────────────────────────────

_PROJECT_STRIP_RE = re.compile(
    r"\b(buat(?:kan)?|bikin(?:in)?|buatin|create|tambah(?:kan)?|pro(?:ject|jek)|baru|dong|deh|nih|yuk|ayok)\b",
    re.IGNORECASE,
)


def _extract_project_info(text: str) -> dict:
    name = _PROJECT_STRIP_RE.sub(" ", text).strip()
    name = re.sub(r"\s{2,}", " ", name).strip(" ,-")
    return {"project_name": name}


# ── Browser extractor ────────────────────────────────────────────────────────

_BROWSER_URL_RE = re.compile(r"https?://\S+")
_BROWSER_SITE_RE = re.compile(
    r"\b(youtube|netflix|google|instagram|twitter|facebook|tiktok|spotify)\b",
    re.IGNORECASE,
)
_BROWSER_SEARCH_STRIP_RE = re.compile(
    r"\b(el\s+)?(buka|cari|search|putar|play|lihat|tonton|di\s+|youtube|netflix|chrome|browser|kamu|bisa|dong|deh)\b",
    re.IGNORECASE,
)


def _extract_browser_info(text: str) -> dict:
    """
    Return dict dengan keys: action, query, url.
    action: "youtube_search" | "youtube_play" | "open_url" | "open_site"
    """
    # URL eksplisit
    url_m = _BROWSER_URL_RE.search(text)
    if url_m:
        return {"action": "open_url", "query": url_m.group(), "url": url_m.group()}

    # Cari yang bahas / search topic → youtube_search
    site_m = _BROWSER_SITE_RE.search(text)
    site = site_m.group(1).lower() if site_m else None

    # Strip kata-kata perintah, ambil sisa sebagai query
    query = _BROWSER_SEARCH_STRIP_RE.sub(" ", text).strip()
    query = re.sub(r"\s{2,}", " ", query).strip(" ,-")

    if "putar" in text.lower() or "play" in text.lower():
        return {"action": "youtube_play", "query": query or text, "url": None}

    if site == "youtube":
        # Query kosong/noise → buka homepage YouTube saja
        clean_query = re.sub(r"^[\s?!.,]+$", "", query)
        if not clean_query or len(clean_query) < 3:
            return {"action": "open_url", "query": "https://www.youtube.com", "url": "https://www.youtube.com"}
        return {"action": "youtube_search", "query": clean_query, "url": None}

    if site is None:
        # Tidak ada site/URL spesifik → jangan asumsi YouTube, biarkan conversation handler yg jawab
        return {"action": "clarify", "query": query or text, "url": None}

    return {"action": "open_site", "query": site or text, "url": None}


# ── Carousel extractor ────────────────────────────────────────────────────────

_CAROUSEL_ACCOUNT_RE = re.compile(
    r"@?(your_account1|your_account2)",
    re.IGNORECASE,
)

_THUMBNAIL_STRIP_RE = re.compile(
    r"\b(buat(?:kan)?|bikin(?:in)?|buatin|create|generate|thumbnail|reels|untuk|tentang|soal|mengenai|konten)\b",
    re.IGNORECASE,
)

def _extract_thumbnail_topic(text: str) -> str:
    """Ekstrak topik dari pesan thumbnail, strip kata-kata perintah."""
    topic = _THUMBNAIL_STRIP_RE.sub("", text).strip()
    topic = re.sub(r"\s+", " ", topic).strip(" .,!?-")
    topic = topic or text
    # Trim ke 500 karakter — cukup untuk konteks hook, hindari timeout Claude CLI
    return topic[:500] if len(topic) > 500 else topic

_CAROUSEL_STRIP_RE = re.compile(
    r"\b(buat(?:kan)?|bikin(?:in)?|buatin|create|generate|carousel|untuk|tentang|soal|mengenai|konten|akun|di|@?your_account|@?your_other_account)\b",
    re.IGNORECASE,
)


def _extract_carousel_info(text: str) -> tuple[Optional[str], str]:
    """
    Return (account, idea) dari pesan carousel.
    account: 'account1' | 'account2' | None (perlu konfirmasi)
    idea   : sisa teks setelah strip kata-kata carousel/akun
    """
    m = _CAROUSEL_ACCOUNT_RE.search(text)
    account = m.group(1).lower() if m else None

    idea = _CAROUSEL_STRIP_RE.sub(" ", text).strip()
    idea = re.sub(r"@\w*", "", idea)          # hapus sisa @mention
    idea = re.sub(r"\s{2,}", " ", idea).strip(" ,-@")
    return account, idea


# ── Agent name extractor ──────────────────────────────────────────────────────

_AGENT_NAME_RE = re.compile(
    r"""
    \b(?:agent|bot)\s+                       # kata "agent" atau "bot" (whole word)
    (?:bernama|namanya|called|named)?\s*    # opsional deskriptor
    ["\']?                                   # opsional quote
    ([\w][\w\-]*)                           # nama: word chars + dash
    ["\']?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Kata yang muncul sebelum "agent" tapi BUKAN nama agent
_VERB_BEFORE_AGENT = {
    "panggil", "jalankan", "run", "invoke", "gunakan", "pakai",
    "aktifkan", "start", "trigger", "minta", "coba", "si", "sang",
    "sebuah", "satu", "suatu",
}

def _extract_agent_name(text: str) -> Optional[str]:
    # Pola 1: "agent [nama]" atau "bot [nama]"
    m = _AGENT_NAME_RE.search(text)
    if m:
        return m.group(1).lower()
    # Pola 2: /agents <nama>
    m2 = re.search(r"/agents?\s+([\w\-]+)", text, re.IGNORECASE)
    if m2:
        return m2.group(1).lower()
    # Pola 3: "[nama] agent" — nama sebelum kata "agent" (contoh: "panggil dalil agent")
    m3 = re.search(r"([\w][\w\-]*)\s+agent\b", text, re.IGNORECASE)
    if m3:
        word = m3.group(1).lower()
        if word not in _VERB_BEFORE_AGENT:
            return word
    return None


# ── Classifier ────────────────────────────────────────────────────────────────

_LLM_CONFIDENCE_THRESHOLD = 0.75  # di bawah ini → fallback ke LLM

_INVOKE_VERBS_RE = re.compile(
    r"\b(panggil|jalankan|run|invoke|execute|aktif(?:kan)?|start|trigger|pakai|gunakan|coba)\b",
    re.IGNORECASE,
)


def _registry_invoke_check(message: str) -> tuple[bool, Optional[str]]:
    """Cek apakah pesan mengandung invoke verb + nama agent yang terdaftar di registry.

    Menangani kasus seperti "jalankan finance manager" di mana tidak ada kata 'agent'.
    Return (matched, agent_name).
    """
    if not _INVOKE_VERBS_RE.search(message):
        return False, None
    try:
        from el_solver.utils.db import get_connection
        conn = get_connection()
        names = [r[0] for r in conn.execute("SELECT name FROM agents_registry").fetchall()]
        conn.close()
    except Exception:
        return False, None

    msg_lower = message.lower()
    for name in names:
        # Coba exact match, space-for-hyphen, dan underscore-for-hyphen
        for variant in (name, name.replace("-", " "), name.replace("-", "_")):
            if variant in msg_lower:
                return True, name
    return False, None


_WEB_LEARN_STRIP_RE = re.compile(
    r"\bel\b\s*"
    r"|\bbelajar\b\s*"
    r"|\bpelajari\b\s*"
    r"|\bcari\s+tahu\b\s*"
    r"|\bcari\s+(?:referensi|materi|sumber)\b\s*"
    r"|\b(tentang|soal|mengenai|dari)\b\s*"
    r"|\b(web|internet|online)\b"
    r"|/learn\s*",
    re.IGNORECASE,
)


def _extract_web_learn_info(text: str) -> dict:
    topic = _WEB_LEARN_STRIP_RE.sub(" ", text).strip()
    topic = re.sub(r"\s{2,}", " ", topic).strip(" ,-")
    return {"topic": topic or text.strip()}


_MODE_TO_STRATEGY = {
    Mode.CONVERSATION: "reply_direct",
    Mode.BROWSER: "reply_direct",
    Mode.CREATE_AGENT: "invoke_single",
    Mode.INVOKE_AGENT: "invoke_single",
    Mode.MAINTAIN_AGENT: "invoke_single",
    Mode.CREATE_CAROUSEL: "invoke_single",
    Mode.CREATE_PROJECT: "invoke_single",
    Mode.WEB_LEARN: "invoke_single",
}


def _strategy_for_mode(mode: Mode) -> str:
    return _MODE_TO_STRATEGY.get(mode, "reply_direct")


_PLAIN_CONVERSATION_RE = re.compile(
    r"""
    \b(
        saya\s+ngomong\s+apa|
        aku\s+ngomong\s+apa|
        apa\s+yang\s+(?:tadi|barusan)\s+saya\s+bilang|
        apa\s+yang\s+(?:tadi|barusan)\s+aku\s+bilang|
        tadi\s+saya\s+bilang\s+apa|
        tadi\s+aku\s+bilang\s+apa|
        barusan\s+saya\s+bilang\s+apa|
        barusan\s+aku\s+bilang\s+apa|
        apa\s+yang\s+saya\s+omongin|
        apa\s+yang\s+aku\s+omongin|
        bisa\s+belajar\s+sendiri|
        belajar\s+sendiri|
        kamu\s+(?:bisa|nggak\s+bisa|gak\s+bisa)\s+belajar(?!\s+tentang|\s+soal|\s+mengenai|\s+dari)
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CAPABILITY_QUESTION_RE = re.compile(
    r"""
    \b(
        bisa\s+(?:nggak|ga|gak|tidak|kah)?\s+.*\b(belajar\s+sendiri)\b|
        gak\s+bisa\s+.*\bbelajar\s+sendiri\b|
        nggak\s+bisa\s+.*\bbelajar\s+sendiri\b|
        kamu\s+bisa\s+.*\bbelajar\s+sendiri\b|
        belajar\s+sendiri\b
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TASK_VERB_RE = re.compile(
    r"\b(buat(?:kan)?|bikin(?:in)?|buatin|create|generate|spawn|panggil|jalankan|run|invoke|perbaiki|fix|update|edit|ubah|hapus|remove|stop|restart|buka|cari|carikan|lihat|tonton|putar|play|search|open|lanjutkan|lanjutin)\b",
    re.IGNORECASE,
)


def _parse_brain_json(response: str) -> dict | None:
    """Parse JSON object dari output Brain LLM yang bisa berupa plain text atau fenced code."""
    import json as _json

    if not isinstance(response, str):
        return None

    candidates: list[str] = []
    stripped = response.strip()
    if stripped:
        candidates.append(stripped)

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL | re.IGNORECASE)
    if fenced_match:
        candidates.insert(0, fenced_match.group(1).strip())

    # Scan balanced braces supaya JSON yang diapit teks tambahan tetap bisa dipakai.
    starts = [m.start() for m in re.finditer(r"\{", response)]
    for start in starts:
        depth = 0
        for idx in range(start, len(response)):
            ch = response[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(response[start:idx + 1].strip())
                    break

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = _json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _should_force_conversation(message: str, keyword_result: Optional["IntentResult"] = None) -> bool:
    """Kunci pesan conversational murni supaya tidak ditarik konteks jadi task."""
    if keyword_result and keyword_result.mode != Mode.CONVERSATION:
        return False
    msg = (message or "").strip()
    if not msg:
        return True
    if _PLAIN_CONVERSATION_RE.search(msg):
        return True
    if _CAPABILITY_QUESTION_RE.search(msg):
        return True
    return False


_SAVE_NOTE_RE = re.compile(
    r"^(simpan|catat|ingat|jadikan|jadikan ini|taruh|masukin|tambahkan ke)\b.{0,60}"
    r"(memory|catatan|note|notes|ingatan|sebagai|ke memory|jadi memory)",
    re.IGNORECASE | re.DOTALL,
)


def _keyword_classify(message: str) -> IntentResult:
    """
    Coba match semua pattern. Return mode dengan confidence tertinggi.
    Kalau tidak ada match → CONVERSATION dengan confidence 0.6.
    """
    # Early-exit: pesan yang DIMULAI dengan intent simpan/catat → paksa CONVERSATION.
    # Ini mencegah isi panjang yang mengandung kata "carousel" dsb memicu mode lain.
    head = (message or "")[:200]
    if _SAVE_NOTE_RE.search(head):
        return IntentResult(
            mode=Mode.CONVERSATION,
            confidence=0.98,
            strategy="save_note",
            raw_message=message,
            extras={"save_note": True},
        )

    best_conf = 0.0
    best_mode = Mode.CONVERSATION

    for pattern, conf, mode in _PATTERNS:
        if pattern.search(message):
            if conf > best_conf:
                best_conf = conf
                best_mode = mode

    agent_name = None
    extras: dict = {}

    if best_mode in (Mode.INVOKE_AGENT, Mode.MAINTAIN_AGENT):
        agent_name = _extract_agent_name(message)

    # Fallback: invoke verb + nama agent di registry (tanpa butuh kata 'agent')
    if best_mode == Mode.CONVERSATION or (best_mode == Mode.INVOKE_AGENT and not agent_name):
        matched, reg_name = _registry_invoke_check(message)
        if matched and reg_name:
            best_mode = Mode.INVOKE_AGENT
            best_conf = max(best_conf, 0.87)
            agent_name = reg_name

    if best_mode == Mode.CREATE_PROJECT:
        extras = _extract_project_info(message)

    if best_mode == Mode.CREATE_CAROUSEL:
        account, idea = _extract_carousel_info(message)
        extras = {"carousel_account": account, "carousel_idea": idea}

    # Thumbnail shortcut — auto-route ke thumbnail-agent
    _THUMBNAIL_TRIGGER_RE = re.compile(r"\bthumbnail\b", re.IGNORECASE)
    if best_mode == Mode.INVOKE_AGENT and _THUMBNAIL_TRIGGER_RE.search(message) and not agent_name:
        agent_name = "thumbnail-agent"
        topic = _extract_thumbnail_topic(message)
        extras = {"thumbnail_topic": topic}

    # IG-Transcriber shortcut — auto-route ke ig-transcriber
    _IG_TRANS_TRIGGER_RE = re.compile(
        r"\b(transkripsi|transcribe|transkrip|script\s+video|script\s+reel|"
        r"ambil\s+script|ambil\s+transkrip|ig-transcriber)\b",
        re.IGNORECASE,
    )
    if best_mode == Mode.INVOKE_AGENT and _IG_TRANS_TRIGGER_RE.search(message) and not agent_name:
        agent_name = "ig-transcriber"

    if best_mode == Mode.BROWSER:
        extras = _extract_browser_info(message)

    if best_mode == Mode.WEB_LEARN:
        extras = _extract_web_learn_info(message)

    if best_conf == 0.0:
        # Tidak ada pattern match → conversation
        return IntentResult(
            mode=Mode.CONVERSATION,
            confidence=0.60,
            raw_message=message,
            method="keyword",
        )

    return IntentResult(
        mode=best_mode,
        confidence=best_conf,
        raw_message=message,
        agent_name=agent_name,
        extras=extras,
        method="keyword",
        skip_brain=(best_conf >= 0.85),  # R8.5: high-confidence → skip Brain LLM
    )


def _llm_classify(
    message: str,
    keyword_result: Optional["IntentResult"] = None,
    conversation_context: str = "",
) -> "IntentResult":
    """
    Brain Decision: panggil Claude CLI untuk klasifikasi + strategy selection.
    Gunakan prompts/jarvis-brain-v1.md sebagai template.
    Return CONVERSATION/reply_direct jika LLM call gagal (fail-safe).
    Emits brain.thinking dan brain.decided events.
    """
    import json as _json
    from pathlib import Path as _Path
    from el_solver.llm import call_claude_cli

    _kw_mode = keyword_result.mode.value if keyword_result else "conversation"
    _kw_conf = f"{keyword_result.confidence:.2f}" if keyword_result else "0.50"
    _ctx = conversation_context or "(belum ada konteks percakapan)"

    # Load brain prompt template
    _brain_tpl_path = _Path(__file__).parent.parent.parent / "prompts" / "jarvis-brain-v1.md"
    try:
        _brain_tpl = _brain_tpl_path.read_text(encoding="utf-8")
        prompt = (
            _brain_tpl
            .replace("{message}", message)
            .replace("{conversation_context}", _ctx)
            .replace("{keyword_mode}", _kw_mode)
            .replace("{keyword_confidence}", _kw_conf)
        )
    except Exception:
        # Fallback ke simple prompt kalau template tidak ada
        prompt = f"""Klasifikasikan pesan: "{message}"
Mode: conversation|create_agent|invoke_agent|maintain_agent|create_carousel|browser
Strategy: reply_direct|clarify|invoke_single|decompose_chain|proactive_followup
JSON: {{"mode":"...","confidence":0.0,"agent_name":null,"strategy":"reply_direct","reasoning":"..."}}"""

    # Emit brain.thinking event (best-effort)
    try:
        from el_solver.core.events import emit_event as _emit
        _emit("brain.thinking", {"message_preview": message[:100], "keyword_mode": _kw_mode})
    except Exception:
        pass

    _VALID_STRATEGIES = {"reply_direct", "clarify", "invoke_single", "decompose_chain", "proactive_followup"}

    try:
        if _should_force_conversation(message, keyword_result):
            logger.info("Brain skipped for plain conversation message")
            return IntentResult(
                mode=Mode.CONVERSATION,
                confidence=keyword_result.confidence if keyword_result else 0.60,
                raw_message=message,
                agent_name=keyword_result.agent_name if keyword_result else None,
                method="keyword-fallback",
                extras=keyword_result.extras if keyword_result else {},
                skip_brain=True if keyword_result else False,
                strategy="reply_direct",
                reasoning="plain conversation message",
            )

        response, *_ = call_claude_cli(
            prompt,
            model="claude-haiku-4-5-20251001",
            timeout=30
        )
        data = _parse_brain_json(response)
        if data is None:
            if keyword_result is not None:
                logger.warning(
                    "Brain classify non-JSON, fallback ke keyword mode=%s",
                    keyword_result.mode.value,
                )
                return IntentResult(
                    mode=keyword_result.mode,
                    confidence=keyword_result.confidence,
                    raw_message=message,
                    agent_name=keyword_result.agent_name,
                    method="keyword-fallback",
                    extras=keyword_result.extras,
                    skip_brain=keyword_result.skip_brain,
                    strategy=_strategy_for_mode(keyword_result.mode),
                    reasoning=keyword_result.reasoning,
                )
            raise ValueError("No JSON in LLM response")

        mode_str = data.get("mode", "conversation").lower()
        mode = Mode(mode_str) if mode_str in Mode._value2member_map_ else Mode.CONVERSATION
        confidence = float(data.get("confidence", 0.7))
        agent_name = data.get("agent_name") or None
        strategy = data.get("strategy", "reply_direct")
        if strategy not in _VALID_STRATEGIES:
            strategy = "reply_direct"
        reasoning = data.get("reasoning", "")

        # Guardrail: LLM tidak boleh classify ke CREATE_CAROUSEL kecuali kata "carousel"
        # ada di pesan — mencegah false positive untuk instruksi yang tidak dikenal.
        if mode == Mode.CREATE_CAROUSEL and not re.search(r"\bcarousel\b", message, re.IGNORECASE):
            logger.info("Brain guardrail: CREATE_CAROUSEL overridden ke CONVERSATION (kata 'carousel' tidak ada di pesan)")
            mode = Mode.CONVERSATION
            strategy = "reply_direct"

        logger.debug(f"Brain classify: {mode.value} ({confidence:.2f}) strategy={strategy}")

        # Emit brain.decided event
        try:
            from el_solver.core.events import emit_event as _emit
            _emit("brain.decided", {
                "mode": mode.value, "strategy": strategy,
                "confidence": confidence, "reasoning": reasoning,
            })
        except Exception:
            pass

        return IntentResult(
            mode=mode,
            confidence=confidence,
            raw_message=message,
            agent_name=agent_name,
            method="llm",
            strategy=strategy,
            reasoning=reasoning,
        )

    except Exception as e:
        logger.warning(f"Brain classify gagal ({e}), fallback ke CONVERSATION")
        try:
            from el_solver.core.events import emit_event as _emit
            _emit("brain.decided", {"mode": "conversation", "strategy": "reply_direct", "error": str(e)})
        except Exception:
            pass
        return IntentResult(
            mode=Mode.CONVERSATION,
            confidence=0.50,
            raw_message=message,
            method="llm-fallback",
            strategy=_strategy_for_mode(Mode.CONVERSATION),
        )


# ── Public API ────────────────────────────────────────────────────────────────

class Orchestrator:
    """
    Entry point utama untuk routing intent.

    Pemakaian:
        orch = Orchestrator()
        result = orch.classify(message)
        if result.mode == Mode.CREATE_AGENT:
            ...
    """

    def __init__(self, llm_fallback: bool = True) -> None:
        self._llm_fallback = llm_fallback

    def classify(self, message: str, conversation_context: str = "") -> IntentResult:
        """Klasifikasi pesan, return IntentResult dengan strategy dari Brain."""
        msg = message.strip()
        if not msg:
            return IntentResult(
                mode=Mode.CONVERSATION,
                confidence=1.0,
                raw_message=message,
                method="empty",
            )

        result = _keyword_classify(msg)
        logger.debug(f"keyword classify: {result}")

        if result.confidence < _LLM_CONFIDENCE_THRESHOLD and self._llm_fallback:
            logger.info(f"confidence rendah ({result.confidence:.2f}), Brain LLM dipanggil")
            result = _llm_classify(msg, keyword_result=result, conversation_context=conversation_context)
        else:
            # High-confidence keyword match: derive strategy from mode, skip Brain LLM
            result.strategy = _strategy_for_mode(result.mode)

        return result


# Singleton default — bisa di-override di tests
_default_orchestrator: Optional[Orchestrator] = None


def get_orchestrator(llm_fallback: bool = True) -> Orchestrator:
    global _default_orchestrator
    if _default_orchestrator is None:
        _default_orchestrator = Orchestrator(llm_fallback=llm_fallback)
    return _default_orchestrator


def classify(message: str, llm_fallback: bool = False) -> IntentResult:
    """Shorthand: classify tanpa instantiate Orchestrator manual."""
    return Orchestrator(llm_fallback=llm_fallback).classify(message)
