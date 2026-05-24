"""Crisis handling flow (R15 M4, blueprint 8.7).

    Trigger → Severity → Containment → Wildan Notify → Recovery → Post-mortem

A "crisis" is any of: anomaly, missed deadline, repeated agent failure,
client complaint, system error. The handler is deterministic and
side-effect-light: it classifies, decides whether autonomous actions must
pause, builds a terse structured notify, ranks recovery options, and
writes a lesson to ``memory/lessons/`` so the same failure teaches the
fleet (blueprint 9.4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from el_solver.config import settings
from el_solver.core.proposal import Option, rank_options
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

# severity floor per trigger kind (1 cosmetic … 5 business-threatening)
_BASE_SEVERITY = {
    "system_error": 3,
    "anomaly": 2,
    "deadline_missed": 4,
    "agent_failure_repeated": 3,
    "client_complaint": 4,
}
CONTAINMENT_THRESHOLD = 4  # severity ≥ this → pause autonomous actions


@dataclass
class Crisis:
    kind: str
    severity: int
    what: str
    impact: str
    source: str = ""
    when: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class CrisisReport:
    crisis: Crisis
    contained: bool
    notify: str
    recovery: list[Option]
    lesson_path: str


def detect_crisis(signal: dict) -> Crisis | None:
    """Classify a raw signal dict into a Crisis (or None if benign).

    signal keys: kind, detail, impact?, source?, severity? (override),
    failure_count? (for agent_failure_repeated).
    """
    kind = str(signal.get("kind") or "").strip()
    if kind not in _BASE_SEVERITY:
        return None
    detail = str(signal.get("detail") or "").strip()
    if not detail:
        return None

    severity = _BASE_SEVERITY[kind]
    if kind == "agent_failure_repeated":
        # escalate with repetition
        count = int(signal.get("failure_count") or 0)
        if count >= 5:
            severity = 5
        elif count >= 3:
            severity = 4
    override = signal.get("severity")
    if override is not None:
        severity = max(1, min(5, int(override)))

    return Crisis(
        kind=kind,
        severity=severity,
        what=detail,
        impact=str(signal.get("impact") or "belum dinilai"),
        source=str(signal.get("source") or ""),
    )


def contain(crisis: Crisis) -> bool:
    """Return True if autonomous actions should pause (containment first)."""
    return crisis.severity >= CONTAINMENT_THRESHOLD


def notify_message(crisis: Crisis, contained: bool) -> str:
    """Terse structured Wildan notify: WHAT / WHEN / IMPACT / PROPOSED."""
    pause = (
        "Aksi otonom DIPAUSE sampai kamu putuskan."
        if contained
        else "Aksi otonom tetap jalan (severity rendah)."
    )
    return "\n".join(
        [
            f"⚠️ CRISIS [{crisis.kind} • sev {crisis.severity}/5]",
            f"WHAT: {crisis.what}",
            f"WHEN: {crisis.when}",
            f"IMPACT: {crisis.impact}",
            f"PROPOSED: {pause} Lihat opsi recovery di bawah.",
        ]
    )


def recovery_options(crisis: Crisis) -> list[Option]:
    """Recovery options ranked by speed × cost (cheap+fast scored highest)."""
    opts = [
        Option(
            "Mitigasi cepat",
            "Tindakan paling cepat untuk hentikan kerugian lebih lanjut.",
            score=0.9,
            risk_note="mungkin tidak menyelesaikan akar masalah",
        ),
        Option(
            "Perbaikan akar masalah",
            "Investigasi 5-whys lalu fix penyebab.",
            score=0.6,
            risk_note="lebih lambat, butuh waktu Wildan",
        ),
        Option(
            "Eskalasi penuh ke Wildan",
            "Serahkan keputusan + konteks lengkap ke Wildan.",
            score=0.4,
        ),
    ]
    if crisis.severity >= 5:
        # at max severity, full escalation should lead
        for o in opts:
            if o.label.startswith("Eskalasi"):
                o.score = 0.95
    return rank_options(opts)


def _slug(text: str) -> str:
    return (
        re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", text.lower())).strip("-")[:40]
        or "crisis"
    )


def post_mortem(crisis: Crisis, memory_root: Path | None = None) -> Path:
    """Write a lesson to memory/lessons/{date}-{kind}-{slug}.md."""
    base = (memory_root or settings.memory_path) / "lessons"
    base.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    path = base / f"{date}-{crisis.kind}-{_slug(crisis.what)}.md"
    body = [
        "---",
        f"name: lesson-{crisis.kind}",
        f"description: Post-mortem {crisis.kind} sev{crisis.severity}",
        "type: lesson",
        "---",
        "",
        f"# Post-mortem: {crisis.what}",
        "",
        f"- kind: {crisis.kind}",
        f"- severity: {crisis.severity}",
        f"- when: {crisis.when}",
        f"- impact: {crisis.impact}",
        f"- source: {crisis.source or '-'}",
        "",
        "## Lesson",
        "_(diisi otomatis oleh self-eval R16; seed dari crisis handler)_",
        "",
    ]
    path.write_text("\n".join(body), encoding="utf-8")
    logger.info(f"crisis: post-mortem written {path.name}")
    return path


def handle_crisis(
    signal: dict, memory_root: Path | None = None
) -> CrisisReport | None:
    """Full flow. Returns None if the signal is not a crisis."""
    crisis = detect_crisis(signal)
    if crisis is None:
        return None
    contained = contain(crisis)
    notify = notify_message(crisis, contained)
    recovery = recovery_options(crisis)
    lesson = post_mortem(crisis, memory_root)
    try:
        from el_solver.core.events import emit_event

        emit_event(
            "crisis.detected",
            {
                "kind": crisis.kind,
                "severity": crisis.severity,
                "contained": contained,
            },
        )
    except Exception as exc:  # noqa: BLE001 — telemetry must not break crisis flow
        logger.debug(f"crisis: emit event failed ({exc})")
    return CrisisReport(
        crisis=crisis,
        contained=contained,
        notify=notify,
        recovery=recovery,
        lesson_path=str(lesson),
    )
