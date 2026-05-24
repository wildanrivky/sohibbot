"""Hard ethical guardrails (R15 M2).

Blueprint 7.6 defines actions El Solver must NEVER take. These are
*hard-coded* and cannot be overridden by a prompt, a confidence score, or
a risk calculation — `decision_engine.decide()` calls `check_action()`
first and forces STOP_ASK on any breach.

Rules:
  G1 no money movement / transfers / payments
  G2 no impersonation of Wildan (must stay transparent it's AI)
  G3 no auto-send to external humans without a pre-approved template
  G4 no signing contracts / committing to engagements

Detection is intentionally conservative: it errs toward blocking. A false
block costs one Wildan approval; a false allow can move money or damage a
relationship.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── rule patterns ─────────────────────────────────────────────────────────────

_MONEY = re.compile(
    r"(\bwire\b|\brefund\b|\breimburse\b|\bpayout\b|\bwithdraw\b|"
    r"\btop[\s-]?up\b|\bbayar\b|\bmembayar\b|\bcairkan\b|\bsaldo\b|"
    r"kirim(?:kan)?\s+uang|transfer\s+dana|"
    r"transfer\s+(?:rp|idr|usd|\$|\d)|"
    r"(?:transfer|send|move|kirim)\s+money|"
    r"make\s+a?\s*payment|invoice\s+payment)",
    re.IGNORECASE,
)
_IMPERSONATE = re.compile(
    r"\b(pretend\s+to\s+be\s+wildan|act\s+as\s+wildan|impersonat|"
    r"pura[\s-]?pura\s+(jadi|sebagai)\s+wildan|mengaku\s+(jadi\s+)?wildan|"
    r"deny\s+(being|that\s+you\s+are)\s+an?\s+ai|claim\s+you\s+are\s+human|"
    r"jangan\s+(bilang|katakan)\s+kamu\s+ai)\b",
    re.IGNORECASE,
)
_SEND_EXTERNAL = re.compile(
    r"\b(send|kirim(?:kan)?|reply|balas|email|dm|whatsapp|wa|broadcast|post)\b",
    re.IGNORECASE,
)
_EXTERNAL_TARGET = re.compile(
    r"\b(client|klien|customer|pelanggan|peserta|audience|audiens|"
    r"follower|vendor|peer|jamaah|grup\s+tour|group\s+chat)\b",
    re.IGNORECASE,
)
_CONTRACT = re.compile(
    r"(sign\s+(?:a\s+|the\s+)?contract|tanda\s*tangan(?:i)?\s+kontrak|"
    r"\bcommit\s+to\b|\bberkomitmen\b|teken\s+(?:kontrak|mou|perjanjian)|"
    r"agree\s+to\s+the\s+terms|sepakati\s+perjanjian)",
    re.IGNORECASE,
)


@dataclass
class GuardrailVerdict:
    allowed: bool
    rule_id: str = ""
    reason: str = ""


def _ctx_truthy(context: dict | None, key: str) -> bool:
    return bool(context and context.get(key))


def check_action(action: str, context: dict | None = None) -> GuardrailVerdict:
    """Return the first hard-rule breach, or an allowed verdict.

    ``context`` flags that relax G3 only:
      - ``approved_template=True``  : message uses a Wildan-approved template
      - ``human_approved=True``     : Wildan explicitly approved this message
    No flag can relax G1/G2/G4 — those are absolute.
    """
    text = action or ""
    ctx = context or {}

    if _MONEY.search(text) or _ctx_truthy(ctx, "moves_money"):
        return GuardrailVerdict(
            False, "G1",
            "money movement is forbidden — El Solver never transfers funds",
        )

    if _IMPERSONATE.search(text) or _ctx_truthy(ctx, "impersonate"):
        return GuardrailVerdict(
            False, "G2",
            "impersonating Wildan / hiding AI identity is forbidden",
        )

    if _CONTRACT.search(text) or _ctx_truthy(ctx, "binds_commitment"):
        return GuardrailVerdict(
            False, "G4",
            "signing contracts / committing to engagements is forbidden",
        )

    if _SEND_EXTERNAL.search(text) and _EXTERNAL_TARGET.search(text):
        if _ctx_truthy(ctx, "approved_template") or _ctx_truthy(
            ctx, "human_approved"
        ):
            return GuardrailVerdict(True)
        return GuardrailVerdict(
            False, "G3",
            "auto-sending to an external human needs a Wildan-approved "
            "template or explicit approval",
        )

    return GuardrailVerdict(True)


def evaluate(action: str, context: dict | None = None) -> list[GuardrailVerdict]:
    """All breaches (not just the first) — for audits/tests."""
    text = action or ""
    ctx = context or {}
    out: list[GuardrailVerdict] = []
    if _MONEY.search(text) or _ctx_truthy(ctx, "moves_money"):
        out.append(GuardrailVerdict(False, "G1", "money movement"))
    if _IMPERSONATE.search(text) or _ctx_truthy(ctx, "impersonate"):
        out.append(GuardrailVerdict(False, "G2", "impersonation"))
    if _CONTRACT.search(text) or _ctx_truthy(ctx, "binds_commitment"):
        out.append(GuardrailVerdict(False, "G4", "contract/commitment"))
    if (
        _SEND_EXTERNAL.search(text)
        and _EXTERNAL_TARGET.search(text)
        and not (
            _ctx_truthy(ctx, "approved_template")
            or _ctx_truthy(ctx, "human_approved")
        )
    ):
        out.append(GuardrailVerdict(False, "G3", "unapproved external send"))
    return out
