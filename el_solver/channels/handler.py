"""
Channel Handler — dispatcher utama untuk IntentResult ke handler konkret.

Mapping:
  CONVERSATION  → _conversation_handler (Agent.run existing)
  CREATE_AGENT  → _create_agent_handler (planner → risk → factory/approval)
  INVOKE_AGENT  → _invoke_agent_handler (registry lookup → run.py subprocess)
  MAINTAIN_AGENT → _maintain_agent_handler (stub)

Setiap handler return HandlerResponse. Channel (Telegram/CLI) yang render.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable, Optional

from el_solver.agent import Agent
from el_solver.config import PROJECT_ROOT
from el_solver.core import approval, clarifier, factory, planner, registry, risk
from el_solver.core.orchestrator import IntentResult, Mode, get_orchestrator
from el_solver.core.planner import PlanError
from el_solver.core.approval import ApprovalStatus
from el_solver.core.registry import AgentRegistry
from el_solver.utils.db import get_connection
from el_solver.utils.db import migrate
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

AGENTS_DIR = PROJECT_ROOT / "agents"


# ── Response dataclass ─────────────────────────────────────────────────────────

@dataclass
class HandlerResponse:
    text: str
    needs_approval: bool = False
    approval_request_id: Optional[str] = None
    # plan yang menunggu approval — handler simpan agar bisa di-materialize setelah approve
    pending_plan: Optional[planner.PlanV1] = None
    metadata: dict = field(default_factory=dict)


# ── Telegram send callback type ────────────────────────────────────────────────

SendTelegramFn = Optional[Callable[[str, list], Awaitable[None]]]


def _extract_task_keywords(text: str) -> list[str]:
    stopwords = {
        "dan", "lalu", "kemudian", "untuk", "yang", "dengan", "di", "ke", "dari",
        "buat", "bikin", "buatkan", "tolong", "coba", "sekarang", "agar", "biar",
        "topik", "topiknya", "masing", "masing-masing", "riset", "carikan",
    }
    tokens = [tok.lower() for tok in re.findall(r"[a-zA-Z0-9\-]+", text or "")]
    return [tok for tok in tokens if tok not in stopwords and len(tok) > 1]


def _should_orchestrate(intent: IntentResult) -> bool:
    raw = intent.raw_message or ""
    word_count = len(raw.split())
    if intent.confidence < 0.7 and word_count > 30:
        return True

    try:
        from el_solver.core.capability_graph import load_default_graph

        graph = load_default_graph()
        gaps = graph.gap_for_task(_extract_task_keywords(raw))
        if len(gaps) > 1 and intent.mode == Mode.CONVERSATION:
            lowered = raw.lower()
            if any(marker in lowered for marker in (" lalu ", " kemudian ", " masing-masing")):
                return True
    except Exception as exc:
        logger.warning(f"handler: orchestrate gate skipped: {exc}")

    return False


# ── Decision-engine pre-gate (R15 wiring) ─────────────────────────────────────
#
# Runs decision_engine.decide() *before* the mode dispatch. With the
# conservative mode→risk table below every normal intent lands in the
# low-risk band → ACT_LOG / ACT_NOTIFY, so dispatch proceeds exactly as
# before (no behaviour change). Callers escalate explicitly by putting
# decision_* keys in intent.extras. The gate is fail-open: any error logs
# and returns (None, None) so the handler can never be crashed by it.

# mode → (severity, probability, irreversibility, stakes_is_high, reversible)
_MODE_RISK: dict[Mode, tuple[int, float, int, bool, bool]] = {
    Mode.CONVERSATION:    (1, 0.0, 1, False, True),
    Mode.WEB_LEARN:       (1, 0.0, 1, False, True),
    Mode.CREATE_CAROUSEL: (1, 0.0, 1, False, True),
    Mode.INVOKE_AGENT:    (2, 0.1, 2, False, True),
    Mode.MAINTAIN_AGENT:  (2, 0.1, 2, False, True),
    Mode.BROWSER:         (2, 0.1, 2, False, True),
    Mode.CREATE_AGENT:    (2, 0.1, 2, False, True),
    Mode.CREATE_PROJECT:  (2, 0.1, 2, False, True),
}


def _build_decision_input(intent: IntentResult):
    """Map an IntentResult to a DecisionInput. extras override the defaults."""
    from el_solver.core.decision_engine import DecisionInput, Stakes

    sev, prob, irr, high_stakes, reversible = _MODE_RISK.get(
        intent.mode, (2, 0.1, 2, False, True)
    )
    ex = intent.extras or {}
    sev = int(ex.get("decision_severity", sev))
    prob = float(ex.get("decision_probability", prob))
    irr = int(ex.get("decision_irreversibility", irr))
    if "decision_reversible" in ex:
        reversible = bool(ex["decision_reversible"])
    if "decision_stakes" in ex:
        high_stakes = str(ex["decision_stakes"]).lower() == "high"
    raw = (intent.raw_message or "").strip()
    return DecisionInput(
        action=f"{intent.mode.value}: {raw[:80]}",
        severity=sev,
        probability=prob,
        irreversibility=irr,
        confidence=float(intent.confidence),
        stakes=Stakes.HIGH if high_stakes else Stakes.LOW,
        reversible=reversible,
        context={"channel_mode": intent.mode.value},
    )


def _decision_pre_gate(intent: IntentResult):
    """Route the intent through decision_engine before dispatch.

    Returns (outcome, short_circuit_response). When the second element is
    not None the handler must skip dispatch and return it. ACT_LOG /
    ACT_NOTIFY return (outcome, None) so dispatch proceeds.
    """
    try:
        from el_solver.core import proposal as _proposal
        from el_solver.core.decision_engine import Policy, decide

        outcome = decide(_build_decision_input(intent))

        if outcome.policy in (Policy.ACT_LOG, Policy.ACT_NOTIFY):
            logger.info(
                f"decision pre-gate: {outcome.policy.value} "
                f"({outcome.rationale}) — proceeding"
            )
            return outcome, None

        if outcome.policy is Policy.PROPOSE_OPTIONS:
            ex = intent.extras or {}
            raw_opts = ex.get("decision_options") or [
                ("Lanjutkan sesuai rencana El Solver", 0.7),
                ("Tahan dulu, tunggu arahan Wildan", 0.3),
            ]
            options = [
                _proposal.Option(label=str(lbl), score=float(sc))
                for lbl, sc in raw_opts
            ]
            text = _proposal.render_proposal(
                outcome.action, options, outcome.rationale
            )
            try:
                _proposal.write_decision_record(
                    outcome.action,
                    outcome.policy.value,
                    outcome.rationale,
                    options,
                    decision_id=outcome.decision_id,
                )
            except Exception as exc:  # noqa: BLE001 — record is best-effort
                logger.warning(f"decision pre-gate: record write failed ({exc})")
            return outcome, HandlerResponse(
                text=text,
                metadata={
                    "decision_policy": outcome.policy.value,
                    "decision_id": outcome.decision_id,
                    "decision_rationale": outcome.rationale,
                },
            )

        # STOP_ASK → explicit approval
        risk_result = risk.RiskResult(
            level="L3" if outcome.guardrail_block else "L2",
            reasons=[outcome.rationale],
        )
        req = approval.create_request(
            context=outcome.action, risk_result=risk_result
        )
        return outcome, HandlerResponse(
            text=_proposal.render_stop_ask(outcome.action, outcome.rationale),
            needs_approval=True,
            approval_request_id=req.request_id,
            metadata={
                "decision_policy": outcome.policy.value,
                "decision_id": outcome.decision_id,
                "decision_rationale": outcome.rationale,
            },
        )
    except Exception as exc:  # noqa: BLE001 — gate must never crash dispatch
        logger.warning(f"decision pre-gate skipped: {exc}")
        return None, None


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def handle(
    intent: IntentResult,
    channel: str,
    user_id: str,
    send_telegram_fn: SendTelegramFn = None,
    conversation_history: list[tuple[str, str]] | None = None,
    claude_cli_conv_id: str | None = None,
) -> HandlerResponse:
    """Dispatcher utama. Route IntentResult ke handler per mode.

    conversation_history: list of (user_message, bot_response) tuples, newest last.
    claude_cli_conv_id: Claude CLI session ID untuk --resume (dari ConversationManager).
    """
    import time, uuid as _uuid
    logger.info(f"handler.handle: mode={intent.mode.value} channel={channel} user={user_id}")

    run_id = str(_uuid.uuid4())
    t0 = time.monotonic()
    agent_name = getattr(intent, "agent_name", None)
    trigger = "telegram" if channel == "telegram" else ("auto" if channel == "scheduler" else "manual")
    clarification_resolved = False
    route_mode = intent.mode.value

    # Init per-message LLM budget counter (R8.5)
    try:
        from el_solver.core.budget import get_max_llm_calls_per_message as _get_max_llm
        from el_solver.llm import set_message_llm_budget as _set_llm_budget
        _set_llm_budget(run_id, _get_max_llm())
    except Exception:
        pass

    # task.started event
    _ev_task_id: str | None = None  # akan diisi setelah DecisionCard dibuat
    try:
        from el_solver.core.events import emit_event as _emit
        _emit("task.started", {"channel": channel, "mode": intent.mode.value, "input": (intent.raw_message or "")[:200]},
              agent=agent_name, run_id=run_id)
    except Exception:
        pass

    # Pending clarification takes precedence over pending project answers.
    try:
        pending_clarification = clarifier.load_pending(user_id, channel)
        if pending_clarification and not (intent.raw_message or "").lstrip().startswith("/"):
            resolved = clarifier.resolve_pending(user_id, channel, intent.raw_message or "")
            # Re-klasifikasi merged text, tapi jangan buang mode original kalau hasil re-klasifikasi
            # downgrade ke CONVERSATION (ini menyebabkan CREATE_AGENT hilang setelah klarifikasi).
            reclassified = get_orchestrator(llm_fallback=False).classify(resolved.raw_message)
            if reclassified.mode == Mode.CONVERSATION and resolved.mode != Mode.CONVERSATION:
                intent = resolved
            else:
                intent = reclassified
                intent.agent_name = intent.agent_name or resolved.agent_name
                intent.extras = {**resolved.extras, **intent.extras}
            route_mode = intent.mode.value
            clarification_resolved = True
    except Exception as exc:
        logger.warning(f"handler: pending clarification resolve skipped: {exc}")

    response: HandlerResponse
    status = "success"
    error_msg = None
    decision_outcome = None
    gate_response: HandlerResponse | None = None
    try:
        # Cek apakah user punya pending project yang menunggu jawaban
        # Kalau iya dan pesannya bukan perintah baru → anggap jawaban, langsung eksekusi
        pending = _get_pending_project(user_id, channel)
        if pending and intent.mode == Mode.CONVERSATION and not clarification_resolved:
            _delete_pending_project(user_id, channel)
            import json as _json
            task_records = _json.loads(pending["tasks_json"])
            import asyncio as _aio
            _aio.create_task(
                _execute_project_tasks(
                    pending["project_id"],
                    pending["project_name"],
                    task_records,
                    send_telegram_fn,
                    user_context=intent.raw_message,
                )
            )
            return HandlerResponse(
                text=(
                    f"Oke, noted. Mulai mengerjakan project '{pending['project_name']}' sekarang.\n"
                    f"Saya akan update tiap task selesai di sini."
                )
            )

        # Decision-engine pre-gate (R15): runs before mode dispatch.
        decision_outcome, gate_response = _decision_pre_gate(intent)

        if gate_response is not None:
            route_mode = "decision_gate"
            response = gate_response
        elif _should_orchestrate(intent):
            route_mode = "orchestrate"
            from el_solver.core.decomposer import decompose as _decompose
            from el_solver.core.orchestrator_chain import execute_chain as _execute_chain
            from el_solver.core.planner import PlanError as _PlanError

            try:
                plan = _decompose(intent, max_steps=5)
            except _PlanError as _plan_err:
                logger.warning(f"handler: decompose ditolak untuk mode {intent.mode.value} ({_plan_err}), fallback ke conversation handler")
                response = await _conversation_handler(
                    intent, channel, user_id,
                    conversation_history=conversation_history,
                    claude_cli_conv_id=claude_cli_conv_id,
                    run_id=run_id,
                )
                response.metadata["route_mode"] = "conversation-fallback"
                return response
            try:
                from el_solver.core.events import emit_event as _emit
                _emit(
                    "orchestrate.decomposed",
                    {
                        "parent_task_id": run_id,
                        "step_count": len(plan.steps),
                        "summary": plan.request_summary,
                        "agents": [agent.name for agent in plan.agents],
                    },
                    agent=agent_name,
                    task_id=run_id,
                    run_id=run_id,
                )
            except Exception:
                pass

            if len(plan.steps) == 1:
                step = plan.steps[0]
                orchestrated_intent = IntentResult(
                    mode=Mode.INVOKE_AGENT,
                    confidence=1.0,
                    raw_message=step.description,
                    agent_name=step.agent_assignee or step.tool_or_agent,
                    method="orchestrate",
                    extras={"parent_task_id": run_id, "plan_mode": plan.mode},
                )
                response = await _invoke_agent_handler(
                    orchestrated_intent,
                    channel=channel,
                    user_id=user_id,
                    run_id=run_id,
                    send_telegram_fn=send_telegram_fn,
                )
                if not agent_name:
                    agent_name = response.metadata.get("agent") or orchestrated_intent.agent_name
                response.metadata.update({
                    "route_mode": route_mode,
                    "delegation_mode": "single-step",
                    "plan_summary": plan.request_summary,
                })
            else:
                chain_result = await _execute_chain(plan, parent_task_id=run_id, channel=channel, user_id=user_id)
                response = HandlerResponse(
                    text=chain_result.summary,
                    metadata={
                        "route_mode": route_mode,
                        "parent_task_id": run_id,
                        "delegations": [step.__dict__ for step in chain_result.step_results],
                        "plan_summary": plan.request_summary,
                    },
                )
                if not agent_name and plan.agents:
                    agent_name = plan.agents[0].name
            if agent_name is None and plan.agents:
                agent_name = plan.agents[0].name
        elif intent.mode == Mode.CONVERSATION:
            from el_solver.core.clarifier import should_clarify as _should_clarify, store_pending as _store_pending

            clarify_needed, question_text = _should_clarify(intent, None, None)
            if clarify_needed:
                clarification_id = _store_pending(user_id, channel, intent, question_text)
                try:
                    from el_solver.core.events import emit_event as _emit
                    _emit(
                        "clarification.requested",
                        {"clarification_id": clarification_id, "question": question_text},
                        agent=agent_name,
                        task_id=_ev_task_id or clarification_id,
                        run_id=run_id,
                    )
                except Exception:
                    pass
                if send_telegram_fn is not None and channel == "telegram":
                    try:
                        await send_telegram_fn(question_text, [])
                    except Exception:
                        pass
                response = HandlerResponse(
                    text=f"{question_text}\n\n⏸ Tunggu jawaban Wildan untuk lanjutkan task."
                    if send_telegram_fn is None
                    else "⏸ Tunggu jawaban Wildan untuk lanjutkan task.",
                    metadata={"clarification_id": clarification_id, "question": question_text},
                )
            else:
                response = await _conversation_handler(
                    intent, channel, user_id, conversation_history, claude_cli_conv_id,
                    run_id=run_id,
                )
        elif intent.mode == Mode.CREATE_PROJECT:
            response = await _create_project_handler(intent, channel, user_id, send_telegram_fn)
        elif intent.mode == Mode.CREATE_AGENT:
            response = await _create_agent_handler(intent, channel, user_id, send_telegram_fn)
        elif intent.mode == Mode.INVOKE_AGENT:
            response = await _invoke_agent_handler(intent, channel, user_id, run_id=run_id, send_telegram_fn=send_telegram_fn)
            if not agent_name:
                agent_name = response.metadata.get("agent")
        elif intent.mode == Mode.MAINTAIN_AGENT:
            response = _maintain_agent_handler(intent)
        elif intent.mode == Mode.CREATE_CAROUSEL:
            response = await _carousel_handler(intent, channel, user_id)
        elif intent.mode == Mode.BROWSER:
            if (intent.extras or {}).get("action") == "clarify":
                response = await _conversation_handler(
                    intent, channel, user_id,
                    conversation_history=conversation_history,
                    claude_cli_conv_id=claude_cli_conv_id,
                    run_id=run_id,
                )
            else:
                response = await _browser_handler(intent, channel, user_id)
        elif intent.mode == Mode.WEB_LEARN:
            response = await _web_learn_handler(intent, channel, user_id)
        else:
            response = await _conversation_handler(
                intent, channel, user_id, claude_cli_conv_id=claude_cli_conv_id, run_id=run_id
            )
    except Exception as exc:
        status = "error"
        error_msg = str(exc)
        response = HandlerResponse(text=f"Maaf, ada error: {exc}")
        # Real-time skill gap detection saat agent invocation gagal
        if agent_name and intent.mode == Mode.INVOKE_AGENT:
            try:
                from el_solver.core.gap_detector import detect_single_gap
                detect_single_gap(agent_name, str(exc), source_type="handler_failure")
            except Exception:
                pass

    # Stamp decision metadata for the autonomous (ACT_*) path so the
    # decision is visible to the channel/audit without changing behaviour.
    if decision_outcome is not None and gate_response is None:
        response.metadata.setdefault(
            "decision_policy", decision_outcome.policy.value
        )
        response.metadata.setdefault("decision_id", decision_outcome.decision_id)
        if decision_outcome.policy.value == "act_notify":
            response.metadata.setdefault("decision_notify", True)

    if agent_name is None and intent.mode in (Mode.CONVERSATION, Mode.BROWSER):
        agent_name = "el-solver"

    duration_ms = int((time.monotonic() - t0) * 1000)
    raw_in = intent.raw_message or ""
    raw_out = response.text or ""
    _record_run(
        run_id=run_id,
        channel=channel,
        user_id=user_id,
        agent_name=agent_name,
        mode=route_mode,
        trigger=trigger,
        input_preview=raw_in[:200],
        output_preview=raw_out[:200],
        status=status,
        error_message=error_msg,
        duration_ms=duration_ms,
        input_chars=len(raw_in),
        output_chars=len(raw_out),
    )
    # Record per-message LLM call count (R8.5)
    try:
        from el_solver.llm import clear_message_llm_budget as _clear_llm
        from el_solver.core.budget import record_llm_call_count as _rec_llm
        llm_count = _clear_llm(run_id)
        if llm_count:
            _rec_llm(run_id, llm_count)
    except Exception:
        pass
    return response


# ── CONVERSATION ───────────────────────────────────────────────────────────────

async def _conversation_handler(
    intent: IntentResult,
    channel: str,
    user_id: str,
    conversation_history: list[tuple[str, str]] | None = None,
    claude_cli_conv_id: str | None = None,
    run_id: str | None = None,
) -> HandlerResponse:
    """Panggil Agent.run() existing, persist conversation state ke SQLite.

    conversation_history: list of (user_message, bot_response) tuples untuk inject context.
    claude_cli_conv_id: session ID untuk --resume Claude CLI (persistent conversation).
    run_id: dipakai untuk per-message LLM budget counter.
    """
    try:
        import asyncio
        _ensure_conversation_record(channel, user_id, "conversation")

        # Kalau conv_id tidak di-pass, coba ambil dari ConversationManager
        effective_conv_id = claude_cli_conv_id
        if not effective_conv_id:
            try:
                from el_solver.core.conversation import get_manager as _get_cm
                ctx = _get_cm().get_context(user_id, channel, max_recent=0)
                effective_conv_id = ctx.claude_cli_conv_id
            except Exception:
                pass

        message = _inject_history(intent.raw_message, conversation_history)
        agent = Agent()
        # Agent.run() adalah blocking — jalankan di thread pool agar tidak block event loop
        result = await asyncio.to_thread(
            agent.run, message, 180, effective_conv_id, run_id
        )
        # Return session_id di metadata agar telegram_bot bisa persist ke ConversationManager
        return HandlerResponse(
            text=result.text or "",
            metadata={"claude_cli_conv_id": result.session_id},
        )
    except Exception as e:
        logger.exception("conversation handler error")
        return HandlerResponse(text=f"Maaf, ada error: {e}")


def _inject_history(
    message: str,
    history: list[tuple[str, str]] | None,
    max_turns: int = 3,
    max_bot_response_chars: int = 300,
) -> str:
    """Inject conversation history ke message agar LLM punya context multi-turn.

    Ambil max_turns terakhir. Bot response di-truncate ke max_bot_response_chars
    biar total prompt tidak membengkak dan Claude tidak lambat.
    """
    if not history:
        return message

    recent = history[-max_turns:]
    lines = ["[Riwayat percakapan sebelumnya (ringkasan):]"]
    for user_msg, bot_msg in recent:
        # Truncate user msg panjang juga (unlikely tapi safety)
        u = user_msg[:200] + ("…" if len(user_msg) > 200 else "")
        b = bot_msg[:max_bot_response_chars] + ("…" if len(bot_msg) > max_bot_response_chars else "")
        lines.append(f"Wildan: {u}")
        lines.append(f"El Solver: {b}")
    lines.append("")
    lines.append(f"Wildan: {message}")

    return "\n".join(lines)


# ── CREATE_AGENT ───────────────────────────────────────────────────────────────

async def _create_agent_handler(
    intent: IntentResult,
    channel: str,
    user_id: str,
    send_telegram_fn: SendTelegramFn = None,
) -> HandlerResponse:
    """
    Flow:
    1. generate_plan (Claude CLI)
    2. assess_plan (risk)
    3. L0/L1 → langsung materialize + register
    4. L2/L3 → buat approval request, return needs_approval=True
    """
    _ensure_conversation_record(channel, user_id, "create_agent")

    # 1. Generate plan
    try:
        plan = planner.generate_plan(intent.raw_message, mode="create_agent")
    except PlanError as e:
        logger.error(f"planner gagal: {e}")
        return HandlerResponse(text=f"Gagal membuat plan: {e}")
    except Exception as e:
        logger.exception("create_agent handler: planner error")
        return HandlerResponse(text=f"Error saat planning: {e}")

    # 1b. Handle clarification needed
    from el_solver.core.clarifier import should_clarify as _should_clarify, store_pending as _store_pending
    clarify_needed, question_text = _should_clarify(intent, plan, None)
    if clarify_needed:
        clarification_id = _store_pending(
            user_id,
            channel,
            intent,
            question_text,
            context=plan.request_summary,
        )
        try:
            from el_solver.core.events import emit_event as _emit
            _emit(
                "clarification.requested",
                {"clarification_id": clarification_id, "question": question_text},
                agent=None,
                task_id=clarification_id,
                run_id=None,
            )
        except Exception:
            pass
        if send_telegram_fn is not None and channel == "telegram":
            try:
                await send_telegram_fn(question_text, [])
            except Exception:
                pass
        return HandlerResponse(
            text=f"{question_text}\n\n⏸ Tunggu jawaban Wildan untuk lanjutkan task."
            if send_telegram_fn is None
            else "⏸ Tunggu jawaban Wildan untuk lanjutkan task.",
            metadata={"clarification_id": clarification_id, "question": question_text},
        )

    # 1c. Guard empty agents
    if not plan.agents:
        logger.warning(f"planner tidak menghasilkan agent untuk: {intent.raw_message[:80]!r}")
        return HandlerResponse(
            text=(
                "AI planner tidak menghasilkan definisi agent dari request ini. "
                "Coba ulangi dengan deskripsi lebih spesifik — misalnya: "
                "\"buat agent yang tiap hari jam 08.00 kirim reminder sholat subuh ke Telegram\"."
            )
        )

    # 2. Assess risk
    risk_result = risk.assess_plan(plan)

    # 3. Low risk — langsung materialize
    if risk.gate(risk_result, auto_approve_up_to="L1"):
        try:
            created_paths = factory.materialize(plan)
        except Exception as e:
            logger.exception("factory.materialize error")
            return HandlerResponse(text=f"Gagal membuat agent: {e}")

        # Register ke DB
        reg = AgentRegistry()
        for spec in plan.agents:
            try:
                reg.register(
                    name=spec.name,
                    archetype=spec.archetype.value,
                    role_description=spec.role_description,
                    manifest={"tools": spec.tools_required, "memory_scopes": spec.memory_scopes},
                    overwrite=True,
                )
            except Exception as e:
                logger.warning(f"registry.register gagal untuk {spec.name}: {e}")

        names = ", ".join(f"`{p.name}`" for p in created_paths)
        return HandlerResponse(
            text=f"Agent {names} berhasil dibuat. Jalankan dengan: `el-solver agent {created_paths[0].name} run \"input\"`",
            metadata={"created": [p.name for p in created_paths]},
        )

    # 4. High risk — butuh approval
    approval_req = approval.create_request(
        context=f"Buat agent: {plan.request_summary}",
        risk_result=risk_result,
    )

    plan_summary = _format_plan_summary(plan, risk_result)
    return HandlerResponse(
        text=plan_summary,
        needs_approval=True,
        approval_request_id=approval_req.request_id,
        pending_plan=plan,
        metadata={"risk_level": risk_result.level, "plan_summary": plan.request_summary},
    )


def _format_plan_summary(plan: planner.PlanV1, risk_result: risk.RiskResult) -> str:
    agents_text = "\n".join(
        f"  - `{spec.name}` ({spec.archetype.value}): {spec.role_description}"
        for spec in plan.agents
    )
    reasons_text = "\n".join(f"  • {r}" for r in risk_result.reasons) or "  • (tidak ada)"
    return (
        f"Rencana pembuatan agent:\n\n"
        f"{agents_text}\n\n"
        f"Risk level: {risk_result.level}\n"
        f"Alasan:\n{reasons_text}\n\n"
        f"Setuju dibuat?"
    )


async def materialize_after_approval(
    plan: planner.PlanV1,
    channel: str,
    user_id: str,
) -> HandlerResponse:
    """
    Dipanggil setelah Wildan klik Approve di Telegram (atau tekan Y di CLI).
    Materialize plan dan register agent ke DB.
    """
    try:
        created_paths = factory.materialize(plan)
    except Exception as e:
        logger.exception("materialize_after_approval: factory error")
        return HandlerResponse(text=f"Gagal membuat agent setelah approval: {e}")

    reg = AgentRegistry()
    for spec in plan.agents:
        try:
            reg.register(
                name=spec.name,
                archetype=spec.archetype.value,
                role_description=spec.role_description,
                manifest={"tools": spec.tools_required, "memory_scopes": spec.memory_scopes},
                overwrite=True,
            )
        except Exception as e:
            logger.warning(f"registry.register gagal untuk {spec.name}: {e}")

    names = ", ".join(f"`{p.name}`" for p in created_paths)
    return HandlerResponse(
        text=f"Agent {names} berhasil dibuat. Jalankan: `el-solver agent {created_paths[0].name} run \"input\"`",
        metadata={"created": [p.name for p in created_paths]},
    )


# ── INVOKE_AGENT ───────────────────────────────────────────────────────────────

async def _invoke_agent_handler(
    intent: IntentResult,
    channel: str,
    user_id: str,
    run_id: str | None = None,
    send_telegram_fn: SendTelegramFn = None,
) -> HandlerResponse:
    """
    Lookup agent di registry atau disk, spawn run.py via subprocess.
    PRE-execution: generate DecisionCard, save DB, evaluate decide().
    Kalau decide='approval_required': insert ke approvals table (Telegram di R2).
    """
    _ensure_conversation_record(channel, user_id, "invoke_agent")

    agent_name = intent.agent_name
    if not agent_name:
        return HandlerResponse(
            text="Nama agent tidak ditemukan di pesan. Contoh: 'panggil agent news-summarizer untuk rangkum berita hari ini'"
        )

    reg = AgentRegistry()

    # Resolve agent_name ke nama folder aktual (registry + disk fallback)
    from el_solver.core.agent_io import list_available_agents, invoke_subagent as _invoke_sub
    resolved_name = _resolve_agent_name(agent_name, reg)
    if resolved_name is None:
        available_disk = list_available_agents(AGENTS_DIR)
        available_text = ", ".join(f"`{n}`" for n in available_disk) if available_disk else "(belum ada)"
        return HandlerResponse(
            text=f"Agent `{agent_name}` tidak ditemukan. Agent tersedia: {available_text}"
        )

    agent_name = resolved_name
    agent_dir = AGENTS_DIR / agent_name

    # ── PRE-EXECUTION: Decision Card ──────────────────────────────────────────
    decision_card = None
    try:
        from el_solver.core import confidence as _conf_mod
        from el_solver.core import budget as _budget_mod
        from el_solver.core.decision import build_decision_card, decide, save_decision
        import datetime as _dt

        msg = intent.raw_message or ""
        est_in, est_out = _budget_mod.estimate_tokens_from_message(msg)
        confidence, conf_signals = _conf_mod.compute_confidence(agent_name, f"invoke.{agent_name}")

        risk_tier, side_effects, reversibility = _get_agent_risk_profile(agent_name, reg)
        candidate_count = _count_agent_candidates(msg, reg)
        daily_remaining = _budget_mod.get_agent_daily_remaining(agent_name)
        daily_budget = daily_remaining + _budget_mod.estimate_cost(
            (reg.get(agent_name) or type('x', (), {'manifest': {}})()).manifest.get('token_limit_daily', 50000) // 2,
            (reg.get(agent_name) or type('x', (), {'manifest': {}})()).manifest.get('token_limit_daily', 50000) // 4,
        ) if daily_remaining < 999 else 2.0

        decision_card = build_decision_card(
            agent=agent_name,
            task_message=msg,
            confidence=confidence,
            confidence_signals=conf_signals,
            risk_tier=risk_tier,
            side_effects=side_effects,
            reversibility=reversibility,
            estimated_input_tokens=est_in,
            estimated_output_tokens=est_out,
            budget_remaining_usd=daily_remaining,
            daily_budget_usd=max(daily_remaining, 0.01),
            run_id=run_id,
            intent_mode=intent.mode.value,
            candidate_count=candidate_count,
        )

        from el_solver.core.clarifier import should_clarify as _should_clarify, store_pending as _store_pending
        clarify_needed, question_text = _should_clarify(intent, None, decision_card)
        if clarify_needed:
            clarification_id = _store_pending(
                user_id,
                channel,
                intent,
                question_text,
                context=msg,
            )
            try:
                from el_solver.core.events import emit_event as _emit
                _emit(
                    "clarification.requested",
                    {"clarification_id": clarification_id, "question": question_text},
                    agent=agent_name,
                    task_id=decision_card.task_id,
                    run_id=run_id,
                )
            except Exception:
                pass
            if send_telegram_fn is not None and channel == "telegram":
                try:
                    await send_telegram_fn(question_text, [])
                except Exception:
                    pass
            return HandlerResponse(
                text=f"{question_text}\n\n⏸ Tunggu jawaban Wildan untuk lanjutkan task."
                if send_telegram_fn is None
                else "⏸ Tunggu jawaban Wildan untuk lanjutkan task.",
                metadata={
                    "agent": agent_name,
                    "task_id": decision_card.task_id,
                    "clarification_id": clarification_id,
                    "question": question_text,
                },
            )

        # Get agent approval_rules from DB column with backward-compatible fallback.
        approval_rules = _get_agent_approval_rules(agent_name, reg)

        decision, reasons = decide(decision_card, daily_remaining, approval_rules)
        decision_card.decision = decision
        decision_card.decision_reasons = reasons
        if decision == "approval_required":
            import datetime as _dt2
            from datetime import timezone as _tz
            decision_card.approval_expires_at = _dt2.datetime.now(_tz.utc) + _dt2.timedelta(hours=24)

        save_decision(decision_card, run_id=run_id)

        # Emit decision.created event
        try:
            from el_solver.core.events import emit_event as _emit
            _emit("decision.created", {
                "decision": decision, "confidence": confidence,
                "risk_tier": risk_tier, "reasons": reasons,
            }, agent=agent_name, task_id=decision_card.task_id, run_id=run_id)
        except Exception:
            pass

        # Budget enforcement check (alert mode — tidak kill)
        budget_result = _budget_mod.enforce(agent_name, est_in, est_out)
        if budget_result.alerts:
            logger.warning(f"budget alerts for {agent_name}: {budget_result.alerts}")

        # Handle approval_required → insert ke approvals table + Telegram notif
        if decision == "approval_required":
            _insert_approval_request(decision_card, reasons)
            # Emit approval.requested event
            try:
                from el_solver.core.events import emit_event as _emit
                _emit("approval.requested", {"task_id": decision_card.task_id, "reasons": reasons},
                      agent=agent_name, task_id=decision_card.task_id, run_id=run_id)
            except Exception:
                pass

            approval_text = _build_approval_card_text(decision_card)
            return HandlerResponse(
                text=approval_text,
                needs_approval=True,
                approval_request_id=decision_card.task_id,
                metadata={"agent": agent_name, "task_id": decision_card.task_id, "is_decision_approval": True},
            )

        if decision == "reject":
            reason_text = ", ".join(reasons)
            return HandlerResponse(
                text=f"Agent `{agent_name}` ditolak: {reason_text}",
                metadata={"agent": agent_name, "decision": "reject"},
            )

        if decision == "notify":
            logger.info(f"decision=notify untuk {agent_name}: {reasons}")

    except ImportError:
        logger.debug("decision modules belum tersedia, skip pre-execution check")
    except Exception as exc:
        logger.warning(f"decision card error (non-critical, lanjut eksekusi): {exc}")

    # ── EKSEKUSI via agent_io.invoke_subagent ─────────────────────────────────
    task_id_for_event = decision_card.task_id if decision_card else None
    try:
        from el_solver.core.events import emit_event as _emit
        _emit("llm.called", {"agent": agent_name, "mode": "subprocess"},
              agent=agent_name, task_id=task_id_for_event, run_id=run_id)
    except Exception:
        pass

    invoke_prompt = intent.raw_message or ""
    extra_invoke_args: list[str] | None = None
    if agent_name == "thumbnail-agent":
        invoke_prompt = (intent.extras or {}).get("thumbnail_topic") or invoke_prompt
        _thumb_preview_root = PROJECT_ROOT / "el_solver" / "web" / "static" / "thumbnail_preview"
        _thumb_preview_dir = _thumb_preview_root / (run_id or "latest")
        _thumb_preview_dir.mkdir(parents=True, exist_ok=True)
        extra_invoke_args = ["--preview-dir", str(_thumb_preview_dir)]
    elif agent_name == "ig-transcriber":
        # Ekstrak URL Instagram dari pesan jika ada
        _ig_url_m = re.search(r"https://(?:www\.)?instagram\.com/\S+", invoke_prompt)
        if _ig_url_m:
            invoke_prompt = _ig_url_m.group(0).rstrip(".,;)")
        # Kalau tidak ada URL, pass full message (mungkin media_id atau shortcode)

    result = _invoke_sub(agent_dir, invoke_prompt, timeout=180, extra_args=extra_invoke_args)
    try:
        from el_solver.core.events import emit_event as _emit
        _emit("task.completed",
              {"exit_code": result.exit_code, "output_preview": result.summary},
              agent=agent_name, task_id=task_id_for_event, run_id=run_id,
              duration_ms=result.duration_ms)
    except Exception:
        pass

    if result.error and result.exit_code == -1 and "Timeout" in result.error:
        return HandlerResponse(text=f"Agent `{agent_name}` timeout (>120s).")
    if not result.ok and not result.text.strip():
        return HandlerResponse(text=f"Error saat menjalankan agent `{agent_name}`: {result.error}")

    # Clean response text untuk thumbnail-agent
    response_text = result.text
    if agent_name == "thumbnail-agent":
        try:
            _td = json.loads(result.text)
            if _td.get("status") == "ok":
                response_text = "Thumbnail sudah bisa di preview"
            else:
                response_text = f"Thumbnail gagal: {_td.get('error', 'unknown error')}"
        except Exception:
            response_text = result.text

    return HandlerResponse(
        text=response_text,
        metadata={
            "agent": agent_name,
            "exit_code": result.exit_code,
            "task_id": task_id_for_event,
            "run_id": run_id,
            "agent_result_summary": result.summary,
        },
    )


def _get_agent_risk_profile(agent_name: str, reg: "AgentRegistry") -> tuple[int, list[str], bool]:
    """
    Return (risk_tier, side_effects, reversibility) untuk agent.
    Mapping default berdasarkan tipe agent. Override via manifest.risk_tier.
    """
    _DEFAULTS: dict[str, tuple[int, list[str], bool]] = {
        "carousel-wildan":        (3, ["send_telegram", "write_file"], True),
        "carousel-account2":  (3, ["send_telegram", "write_file"], True),
        "dalil-agent":            (2, ["save_draft"], True),
        "tour-helper-agent":      (2, ["save_draft"], True),
    }

    agent_rec = reg.get(agent_name)
    if agent_rec:
        manifest = agent_rec.manifest or {}
        if "risk_tier" in manifest:
            tier = int(manifest["risk_tier"])
            side_effects = manifest.get("side_effects", [])
            reversible = bool(manifest.get("reversible", True))
            return tier, side_effects, reversible

    if agent_name in _DEFAULTS:
        return _DEFAULTS[agent_name]

    return 2, ["save_draft"], True


def _count_agent_candidates(message: str, reg: AgentRegistry) -> int:
    names: list[str] = []
    try:
        names = [agent.name for agent in reg.list_all()]
    except Exception:
        names = []

    candidates = 0
    for name in names:
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", message, re.IGNORECASE):
            candidates += 1
    return candidates


def _get_agent_approval_rules(agent_name: str, reg: "AgentRegistry") -> list[str]:
    """Return approval rules from DB column with manifest fallback."""
    raw_value: object | None = None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT approval_rules, manifest FROM agents_registry WHERE name=?",
            (agent_name,),
        ).fetchone()
        if row:
            raw_value = row["approval_rules"] or json.loads(row["manifest"] or "{}").get("approval_rules")
    except Exception:
        raw_value = None
    finally:
        conn.close()

    if raw_value is None:
        agent_rec = reg.get(agent_name)
        if agent_rec:
            raw_value = agent_rec.manifest.get("approval_rules")

    if raw_value is None:
        return []

    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]

    if not isinstance(raw_value, str):
        raw_value = str(raw_value)

    raw_text = raw_value.strip()
    if not raw_text:
        return []

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return [rule.strip() for rule in raw_text.split(",") if rule.strip()]

    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str):
        return [parsed.strip()] if parsed.strip() else []
    return []


def _insert_approval_request(card: "DecisionCard", reasons: list[str]) -> None:
    """Insert approval request ke tabel approvals untuk DecisionCard yang butuh approval."""
    import json as _json
    import uuid as _uuid
    from el_solver.utils.db import get_connection as _get_conn

    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO approvals
               (request_id, context, risk_level, status, reasons, task_id, agent, decision_card)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                card.task_id,
                f"Agent '{card.agent}' diminta invoke: {card.signature}",
                f"L{card.risk_tier}",
                "pending",
                _json.dumps(reasons),
                card.task_id,
                card.agent,
                _json.dumps(card.to_dict()),
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"_insert_approval_request failed (non-critical): {e}")
    finally:
        conn.close()


_RISK_LABELS = {
    1: "L1 — Read-only",
    2: "L2 — Write internal",
    3: "L3 — Write internal, hard-undo",
    4: "L4 — External, reversible",
    5: "L5 — External, irreversible",
}


def _build_approval_card_text(card: "DecisionCard") -> str:
    """Format approval card sesuai section 7.5 BLUEPRINT."""
    top_signal = max(card.confidence_signals.items(), key=lambda x: x[1], default=("—", 0))
    top_warning = card.uncertainty_signals[0] if card.uncertainty_signals else None
    risk_label = _RISK_LABELS.get(card.risk_tier, f"L{card.risk_tier}")
    budget_total = card.budget_remaining_usd + card.estimated_cost_usd
    expires_h = 24

    lines = [
        f"🤖 {card.agent} minta approval",
        "",
        f"📋 TASK: \"{card.signature}\"",
        "",
        f"🎯 CONFIDENCE: {card.confidence:.2f}",
        f"   ✓ {top_signal[0]}: {top_signal[1]:.2f}",
    ]
    if top_warning:
        lines.append(f"   ⚠ {top_warning}")
    lines += [
        "",
        f"💰 COST (estimasi): ${card.estimated_cost_usd:.5f}",
        f"   (sisa ${card.budget_remaining_usd:.4f} / ${budget_total:.4f})",
        f"   ⚠ Subscription mode — bukan tagihan real",
        "",
        f"⚠️ RISK: {risk_label}",
        "",
        f"⏰ Auto-reject jika tidak respond {expires_h}j",
        f"🔑 Task ID: {card.task_id[:12]}",
    ]
    return "\n".join(lines)


# ── CREATE_CAROUSEL ────────────────────────────────────────────────────────────

_CAROUSEL_BOTS: dict[str, Path] = {
    "account1": Path(os.getenv("CAROUSEL_ACCOUNT1_DIR", "")),
    "account2": Path(os.getenv("CAROUSEL_ACCOUNT2_DIR", "")),
}

_CAROUSEL_PREVIEW_ROOT = Path(__file__).parent.parent / "web" / "static" / "carousel_preview"
_CAROUSEL_PREVIEW_MAX = 10  # max folder preview yang disimpan, oldest dihapus


def _cleanup_carousel_previews() -> None:
    """Hapus folder carousel preview terlama kalau sudah melebihi batas."""
    try:
        if not _CAROUSEL_PREVIEW_ROOT.exists():
            return
        folders = sorted(_CAROUSEL_PREVIEW_ROOT.iterdir(), key=lambda p: p.stat().st_mtime)
        for old in folders[:-_CAROUSEL_PREVIEW_MAX]:
            import shutil as _sh
            _sh.rmtree(old, ignore_errors=True)
    except Exception:
        pass


async def _carousel_handler(
    intent: IntentResult,
    channel: str,
    user_id: str,
) -> HandlerResponse:
    """
    Buat carousel Instagram langsung via subprocess ke create_carousel.py.
    Tidak lewat Claude — panggil script Python secara langsung agar reliable.
    """
    import asyncio
    import json

    # Guardrail: jangan jalankan carousel kalau kata "carousel" tidak ada di pesan asli.
    # Ini mencegah false positive saat LLM classifier salah classify pesan yang tidak dikenal.
    if not re.search(r"\bcarousel\b", intent.raw_message or "", re.IGNORECASE):
        logger.warning(
            "carousel_handler: guardrail triggered — kata 'carousel' tidak ada di pesan: %r",
            (intent.raw_message or "")[:80],
        )
        return HandlerResponse(
            text="Hmm, tidak ngerti maksudnya. Coba ulangi dengan lebih jelas?"
        )

    account: str | None = intent.extras.get("carousel_account")
    idea: str = intent.extras.get("carousel_idea", "").strip()

    # Akun tidak diketahui → minta konfirmasi
    if not account:
        return HandlerResponse(
            text=(
                "Mau buat carousel untuk akun mana?\n"
                "- @akun_kamu\n"
                "- @akun_lainnya\n\n"
                "Kirim ulang dengan sebutkan akunnya, contoh: "
                "\"buat carousel @akun_kamu tentang tips packing""
            )
        )

    bot_dir = _CAROUSEL_BOTS.get(account)
    if not bot_dir or not bot_dir.exists():
        return HandlerResponse(text=f"Folder carousel bot untuk @{account} tidak ditemukan.")

    # Idea kosong → pakai raw message sebagai fallback
    if not idea:
        idea = intent.raw_message

    # Cari Python interpreter: venv dulu, fallback ke sistem
    import shutil
    python_bin_str: str | None = None
    for candidate in [
        str(bot_dir / ".venv" / "bin" / "python"),
        str(bot_dir / ".venv" / "bin" / "python3"),
    ]:
        if Path(candidate).exists():
            python_bin_str = candidate
            break
    if python_bin_str is None:
        python_bin_str = shutil.which("python3") or shutil.which("python")
    if python_bin_str is None:
        return HandlerResponse(text=f"Python tidak ditemukan untuk carousel bot @{account}.")

    import uuid
    run_id = str(uuid.uuid4())
    preview_dir = _CAROUSEL_PREVIEW_ROOT / run_id
    _cleanup_carousel_previews()

    logger.info(f"carousel: account={account} idea={idea[:60]!r} run_id={run_id}")

    def _run_carousel() -> tuple[str, str, int]:
        result = subprocess.run(
            [python_bin_str, "create_carousel.py", "--idea", idea, "--preview-dir", str(preview_dir)],
            cwd=str(bot_dir),
            capture_output=True,
            text=True,
            timeout=600,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode

    try:
        stdout, stderr, rc = await asyncio.to_thread(_run_carousel)
    except subprocess.TimeoutExpired:
        return HandlerResponse(text="Carousel timeout (>10 menit). Coba lagi atau cek koneksi.")
    except Exception as e:
        logger.exception("carousel_handler error")
        return HandlerResponse(text=f"Error saat buat carousel: {e}")

    if rc != 0:
        err_preview = stderr[:300] if stderr else "(tidak ada stderr)"
        logger.error(f"carousel exit {rc}: {err_preview}")
        return HandlerResponse(text=f"Carousel gagal (exit {rc}):\n{err_preview}")

    # Parse JSON output dari carousel bot
    # Output bisa multiline JSON atau mixed dengan teks lain
    data = _parse_json_from_output(stdout)
    if data is not None:
        status = data.get("status", "unknown")
        if status != "success":
            return HandlerResponse(text=f"Carousel selesai tapi status: {status}\n{stdout[:300]}")
        theme = data.get("theme", idea[:40])
        slides = data.get("slide_count", "?")
        images: list[str] = data.get("images", [])
        return HandlerResponse(
            text=f"Carousel @{account} berhasil!\nTema: {theme} ({slides} slides)\nSudah dikirim ke Telegram.",
            metadata={"account": account, "theme": theme, "slide_count": slides, "run_id": run_id, "images": images},
        )

    # Fallback: tidak bisa parse JSON tapi exit 0 → anggap berhasil
    return HandlerResponse(
        text=f"Carousel @{account} selesai diproses. Cek Telegram untuk hasilnya.",
        metadata={"account": account, "run_id": run_id},
    )


# ── BROWSER ────────────────────────────────────────────────────────────────────

_BROWSER_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "browser.py"
_VENV_PYTHON = Path(__file__).parent.parent.parent / ".venv" / "bin" / "python"

_SITE_URLS = {
    "netflix": "https://www.netflix.com",
    "google": "https://www.google.com",
    "instagram": "https://www.instagram.com",
    "twitter": "https://twitter.com",
    "facebook": "https://www.facebook.com",
    "tiktok": "https://www.tiktok.com",
    "spotify": "https://open.spotify.com",
}


async def _browser_handler(
    intent: IntentResult,
    channel: str,
    user_id: str,
) -> HandlerResponse:
    import asyncio
    import json as _json

    action: str = intent.extras.get("action", "youtube_search")
    query: str = intent.extras.get("query", intent.raw_message)
    url: str | None = intent.extras.get("url")

    python_bin = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable
    script = str(_BROWSER_SCRIPT)

    def _run(cmd: str, arg: str) -> tuple[str, int]:
        result = subprocess.run(
            [python_bin, script, cmd, arg],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip(), result.returncode

    try:
        if action == "open_url" and url:
            _, rc = await asyncio.to_thread(_run, "open", url)
            return HandlerResponse(text=f"Sudah dibuka di Chrome: {url}")

        elif action == "open_site":
            site_url = _SITE_URLS.get(query.lower(), f"https://www.{query}.com")
            _, rc = await asyncio.to_thread(_run, "open", site_url)
            return HandlerResponse(text=f"Sudah dibuka di Chrome: {site_url}")

        elif action in ("youtube_play", "youtube_search"):
            stdout, rc = await asyncio.to_thread(_run, "youtube", query)
            if rc != 0 or not stdout:
                return HandlerResponse(text=f"Gagal membuka YouTube untuk '{query}'. Coba lagi.")
            try:
                data = _json.loads(stdout)
                title = data.get("title", query)
                video_url = data.get("url", "")
                return HandlerResponse(
                    text=f"Sudah dibuka di Chrome:\n{title}\n{video_url}",
                    metadata={"browser_action": action, "query": query},
                )
            except Exception:
                return HandlerResponse(text=f"YouTube dibuka untuk '{query}'.")

        else:
            return HandlerResponse(text=f"Browser action tidak dikenali: {action}")

    except subprocess.TimeoutExpired:
        return HandlerResponse(text="Browser timeout. Pastikan Mac tidak sleep.")
    except Exception as e:
        logger.exception("browser_handler error")
        return HandlerResponse(text=f"Gagal kontrol browser: {e}")


# ── WEB_LEARN ─────────────────────────────────────────────────────────────────

async def _web_learn_handler(
    intent: IntentResult,
    channel: str,
    user_id: str,
) -> HandlerResponse:
    import asyncio

    topic: str = intent.extras.get("topic", "").strip() or intent.raw_message.strip()
    if not topic or len(topic) < 2:
        return HandlerResponse(
            text="Topik apa yang mau dipelajari dari web? Contoh: 'el belajar tentang marketing digital'"
        )

    logger.info(f"web_learn: topic={topic!r}")
    try:
        from el_solver.tools.web_learner import search_and_learn
        result = await asyncio.to_thread(search_and_learn, topic)
        return HandlerResponse(text=result, metadata={"topic": topic})
    except Exception as exc:
        logger.exception("web_learn_handler error")
        return HandlerResponse(text=f"Gagal belajar dari web: {exc}")


# ── CREATE_PROJECT ─────────────────────────────────────────────────────────────

async def _create_project_handler(
    intent: IntentResult,
    channel: str,
    user_id: str,
    send_telegram_fn: SendTelegramFn = None,
) -> HandlerResponse:
    import asyncio
    import json as _json
    import re

    project_name = intent.extras.get("project_name", "").strip()
    if not project_name or len(project_name) < 2:
        return HandlerResponse(
            text=(
                "Nama project-nya apa?\n"
                "Contoh: 'buat project konten ramadhan' atau 'bikin project campaign produk baru'"
            )
        )

    slug = re.sub(r"[^\w\s-]", "", project_name.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")[:40]

    emoji = "📦"
    name_lower = project_name.lower()
    if any(w in name_lower for w in ["tour", "wisata", "travel", "jalan", "trip"]):
        emoji = "✈️"
    elif any(w in name_lower for w in ["konten", "instagram", "carousel", "ig", "video", "reels"]):
        emoji = "📸"
    elif any(w in name_lower for w in ["dalil", "quran", "kajian", "religi", "islami"]):
        emoji = "📿"
    elif any(w in name_lower for w in ["kelas", "kursus", "belajar", "training"]):
        emoji = "📚"
    elif any(w in name_lower for w in ["bisnis", "jualan", "produk", "sales"]):
        emoji = "💼"

    try:
        conn = _get_db_connection()
        base_slug = slug
        counter = 1
        while conn.execute("SELECT 1 FROM projects WHERE id=?", (slug,)).fetchone():
            slug = f"{base_slug}-{counter}"
            counter += 1

        conn.execute(
            "INSERT INTO projects (id, name, emoji, status) VALUES (?, ?, ?, 'active')",
            (slug, project_name, emoji),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.exception("create_project_handler DB error")
        return HandlerResponse(text=f"Gagal simpan project ke database: {e}")

    # Generate tasks via LLM
    tasks = await asyncio.to_thread(_generate_project_tasks, project_name, intent.raw_message)
    task_records = _insert_project_tasks(slug, tasks)

    # Generate clarifying questions sebelum eksekusi
    question = await asyncio.to_thread(_generate_clarifying_question, project_name, tasks)

    # Simpan pending state — eksekusi menunggu jawaban user
    _save_pending_project(
        user_id=user_id,
        channel=channel,
        project_id=slug,
        project_name=project_name,
        task_records=task_records,
        question=question,
    )

    task_lines = "\n".join(f"  {i+1}. {t['title']}" for i, t in enumerate(tasks))
    return HandlerResponse(
        text=(
            f"{emoji} Project *{project_name}* dibuat!\n\n"
            f"Tasks ({len(tasks)}):\n{task_lines}\n\n"
            f"{question}"
        ),
        metadata={"project_id": slug, "project_name": project_name, "tasks_count": len(tasks)},
    )


def _generate_project_tasks(project_name: str, raw_message: str) -> list[dict]:
    """Panggil Claude CLI untuk generate daftar tasks. Return list of dicts."""
    import json as _json
    from el_solver.llm import call_claude_cli

    prompt = f"""Kamu adalah project manager. Buatkan daftar langkah/tasks untuk project berikut.

Project: {project_name}
Konteks tambahan dari user: {raw_message}

Buat 5–8 tasks konkret yang harus dikerjakan untuk project ini, urut dari awal sampai selesai.
Setiap task punya: title (singkat, max 60 karakter), priority (high/medium/low), status selalu "backlog".

PENTING — aturan penulisan task:
- Tasks harus berupa PEKERJAAN KONKRET: riset, menulis draft, membuat outline, menganalisis, menjadwalkan
- JANGAN sebut nama agent, tool, atau sistem di dalam title task (jangan: "via sub-agent X", "gunakan tool Y", "lewat carousel bot")
- JANGAN task meta seperti "review sistem" atau "panggil agent Z"
- Contoh BENAR: "Tulis hook & copy untuk konten hari 1"
- Contoh SALAH: "Buat carousel hari 1 via sub-agent carousel-wildan"

Jawab HANYA dengan JSON array, tanpa teks lain:
[
  {{"title": "...", "priority": "high", "status": "backlog"}},
  ...
]"""

    try:
        response, *_ = call_claude_cli(prompt, timeout=60)
        # Ekstrak JSON array dari response
        start = response.find("[")
        end = response.rfind("]") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON array in response")
        tasks = _json.loads(response[start:end])
        # Validasi: harus list of dicts dengan key 'title'
        return [
            {
                "title": str(t.get("title", "Task"))[:60],
                "priority": t.get("priority", "medium") if t.get("priority") in ("high", "medium", "low") else "medium",
                "status": "backlog",
            }
            for t in tasks if isinstance(t, dict) and t.get("title")
        ]
    except Exception as e:
        logger.warning(f"_generate_project_tasks gagal ({e}), pakai default tasks")
        return [
            {"title": "Riset dan perencanaan awal", "priority": "high", "status": "backlog"},
            {"title": "Eksekusi tahap pertama", "priority": "high", "status": "backlog"},
            {"title": "Review dan iterasi", "priority": "medium", "status": "backlog"},
            {"title": "Finalisasi dan publish", "priority": "medium", "status": "backlog"},
        ]


def _insert_project_tasks(project_id: str, tasks: list[dict]) -> list[dict]:
    """Insert tasks sebagai issues ke DB. Return list dengan issue_id ditambahkan."""
    records: list[dict] = []
    try:
        conn = _get_db_connection()
        row = conn.execute("SELECT COUNT(*) as n FROM issues WHERE project_id=?", (project_id,)).fetchone()
        seq_start = (row["n"] if row else 0) + 1
        slug = project_id[:8].rstrip("-")

        for i, task in enumerate(tasks):
            issue_id = f"{slug}-{seq_start + i}"
            conn.execute(
                "INSERT INTO issues (id, project_id, title, priority, status) VALUES (?,?,?,?,?)",
                (issue_id, project_id, task["title"], task["priority"], task["status"]),
            )
            records.append({"issue_id": issue_id, "title": task["title"], "priority": task["priority"]})
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"_insert_project_tasks gagal (non-critical): {e}")
    return records


# ── PENDING PROJECT STATE ─────────────────────────────────────────────────────

def _generate_clarifying_question(project_name: str, tasks: list[dict]) -> str:
    """Minta Claude generate pertanyaan klarifikasi sebelum eksekusi."""
    from el_solver.llm import call_claude_cli

    task_list = "\n".join(f"- {t['title']}" for t in tasks)
    prompt = (
        f"Kamu adalah project manager AI. Project '{project_name}' baru dibuat dengan tasks:\n"
        f"{task_list}\n\n"
        f"Sebelum mengerjakan, kamu perlu info konkret dari user.\n"
        f"Tulis 1 pesan singkat (maks 4 baris) yang menanyakan info paling penting yang dibutuhkan "
        f"agar semua tasks bisa dikerjakan dengan hasil yang relevan dan spesifik.\n"
        f"Jangan bertele-tele. Langsung ke pertanyaan."
    )
    try:
        response, *_ = call_claude_cli(prompt, timeout=30)
        return response.strip()
    except Exception:
        return f"Sebelum saya mulai mengerjakan, info apa yang perlu saya tahu tentang project '{project_name}' ini?"


def _save_pending_project(
    user_id: str,
    channel: str,
    project_id: str,
    project_name: str,
    task_records: list[dict],
    question: str,
) -> None:
    """Simpan pending execution state ke DB."""
    import json as _json, uuid as _uuid
    try:
        conn = _get_db_connection()
        # Hapus pending lama untuk user ini (satu pending per user)
        conn.execute("DELETE FROM pending_project_executions WHERE user_id=? AND channel=?", (user_id, channel))
        conn.execute(
            """INSERT INTO pending_project_executions
               (id, user_id, channel, project_id, project_name, tasks_json, question)
               VALUES (?,?,?,?,?,?,?)""",
            (str(_uuid.uuid4()), user_id, channel, project_id, project_name,
             _json.dumps(task_records), question),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"_save_pending_project gagal: {e}")


def _get_pending_project(user_id: str, channel: str) -> dict | None:
    """Ambil pending execution untuk user ini, kalau ada (max 30 menit)."""
    try:
        conn = _get_db_connection()
        row = conn.execute(
            """SELECT * FROM pending_project_executions
               WHERE user_id=? AND channel=?
               AND created_at >= datetime('now', '-30 minutes')
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, channel),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _delete_pending_project(user_id: str, channel: str) -> None:
    try:
        conn = _get_db_connection()
        conn.execute("DELETE FROM pending_project_executions WHERE user_id=? AND channel=?", (user_id, channel))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── PROJECT EXECUTOR ──────────────────────────────────────────────────────────

async def _execute_project_tasks(
    project_id: str,
    project_name: str,
    task_records: list[dict],
    send_fn: SendTelegramFn,
    user_context: str = "",
) -> None:
    """Background executor: kerjakan setiap task menggunakan agent yang tepat."""
    import asyncio as _aio

    total = len(task_records)
    done_count = 0
    completed_context: list[str] = []  # akumulasi output task sebelumnya

    for rec in task_records:
        issue_id = rec["issue_id"]
        title = rec["title"]

        _update_issue_status(issue_id, "in_progress")

        agent_name, _ = _match_agent_for_task(title, project_name)
        output: str | None = None
        used: str = "el-solver-direct"
        agent_error: str | None = None
        prompt_chars = 0
        output_chars = 0
        t_task = time.monotonic()

        # Bangun konteks: user context + output task sebelumnya
        ctx_parts = []
        if user_context:
            ctx_parts.append(f"Info dari user: {user_context}")
        if completed_context:
            summary = "\n".join(f"- {c}" for c in completed_context[-3:])  # max 3 task terakhir
            ctx_parts.append(f"Hasil task sebelumnya:\n{summary}")
        full_context = "\n\n".join(ctx_parts)

        # Step 1: coba agent yang cocok
        if agent_name:
            try:
                output, used = await _run_task_with_agent(agent_name, title, project_name, full_context)
                output_chars = len(output)
            except Exception as e:
                agent_error = str(e)
                logger.warning(f"Agent '{agent_name}' gagal untuk '{title}': {e} → fallback Claude")
                output = None

        # Step 2: fallback ke Claude kalau agent gagal atau tidak ada
        if output is None:
            try:
                output, used, prompt_chars, output_chars = await _aio.to_thread(
                    _run_task_with_claude, title, project_name, full_context
                )
                if agent_name:
                    _aio.create_task(_spawn_agent_for_task(title, project_name))
            except Exception as e:
                logger.error(f"Claude juga gagal untuk '{title}': {e}")
                output = None

        task_ms = int((time.monotonic() - t_task) * 1000)

        done_count += 1

        if output is None:
            _update_issue_status(issue_id, "error", notes=f"Gagal: {agent_error or 'unknown'}", agent_used="error")
            _record_task_run(project_id, title, "error", prompt_chars, 0, task_ms, status="error")
            if send_fn:
                try:
                    await send_fn(
                        f"❌ [{done_count}/{total}] {title}\n"
                        f"Tidak bisa dikerjakan otomatis. Apa info tambahan yang bisa kamu berikan?",
                        []
                    )
                except Exception as ex:
                    logger.warning(f"send_fn gagal: {ex}")
        else:
            _update_issue_status(issue_id, "done", notes=output, agent_used=used)
            _record_task_run(project_id, title, used, prompt_chars, output_chars, task_ms)
            # Akumulasi konteks untuk task berikutnya — simpan ringkasan singkat
            completed_context.append(f"{title}: {output[:300].strip()}")
            if send_fn:
                fallback_note = f" (fallback dari {agent_name})" if agent_error else ""
                dur = f"{task_ms/1000:.0f}s"
                clean = _strip_markdown(output)
                header = f"✅ [{done_count}/{total}] {title}\n({used}{fallback_note}, {dur})\n\n"
                msg = header + clean
                try:
                    await send_fn(msg, [])
                except Exception as ex:
                    logger.warning(f"send_fn gagal: {ex}")

    if send_fn:
        try:
            await send_fn(
                f"Semua {total} tasks project '{project_name}' selesai.\n"
                f"Lihat di dashboard: http://127.0.0.1:8000/projects/{project_id}",
                [],
            )
        except Exception:
            pass


def _strip_markdown(text: str) -> str:
    """Hapus sintaks markdown supaya plain text bersih di Telegram."""
    import re
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).replace("```", "").strip(), text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    return text.strip()


def _match_agent_for_task(task_title: str, project_name: str) -> tuple[str | None, str]:
    """
    Cocokkan task ke agent yang ada berdasarkan keyword.
    Return (agent_name, alasan). agent_name=None → tidak ada agent cocok.
    """
    text = f"{task_title} {project_name}".lower()

    # Hanya match carousel agent kalau benar-benar mau bikin gambar/carousel PNG
    if any(w in text for w in ["carousel", "buat slide", "bikin slide", "render slide", "desain slide", "gambar konten", "konten ig", "post instagram"]):
        if any(w in text for w in ["account2", "tour", "wisata", "destinasi"]):
            return "carousel-account2", "carousel-account2"
        return "carousel-wildan", "carousel-wildan"

    if any(w in text for w in ["tour", "wisata", "destinasi", "itinerary", "perjalanan", "jadwal tour"]):
        return "tour-helper-agent", "tour-helper-agent"

    if any(w in text for w in ["dalil", "quran", "hadits", "islami", "kajian", "fiqih"]):
        return "dalil-agent", "dalil-agent"

    return None, "el-solver-direct"


async def _run_task_with_agent(
    agent_name: str, task_title: str, project_name: str, user_context: str = ""
) -> tuple[str, str]:
    """Jalankan agent via subprocess untuk mengerjakan task. Return (output, agent_name)."""
    import asyncio as _aio
    from el_solver.core.agent_io import invoke_subagent as _invoke_sub

    agent_dir = AGENTS_DIR / agent_name
    ctx = f" | Konteks: {user_context}" if user_context else ""
    prompt = f"[Project: {project_name}{ctx}] {task_title}"

    result = await _aio.to_thread(_invoke_sub, agent_dir, prompt, 300)
    if result.exit_code != 0 and not result.text.strip():
        raise RuntimeError(result.error or f"exit {result.exit_code}, tidak ada output")
    return result.text, agent_name


def _run_task_with_claude(task_title: str, project_name: str, user_context: str = "") -> tuple[str, str, int, int]:
    """Kerjakan task langsung via Claude CLI. Return (output, agent, prompt_chars, output_chars)."""
    from el_solver.llm import call_claude_cli

    ctx_block = f"\n\n{user_context}" if user_context else ""
    prompt = (
        f"Kamu adalah eksekutor project AI. Kerjakan task berikut SEKARANG — langsung hasilkan output konkret.\n\n"
        f"Project: {project_name}{ctx_block}\n\n"
        f"Task yang harus dikerjakan: {task_title}\n\n"
        f"ATURAN WAJIB:\n"
        f"- JANGAN tanya balik atau minta klarifikasi\n"
        f"- JANGAN bilang 'butuh info lebih' atau 'tidak bisa tanpa data X'\n"
        f"- Kalau info kurang, buat asumsi yang masuk akal dan sebut asumsinya\n"
        f"- Output harus langsung bisa dipakai: draft, daftar, outline, ringkasan — bukan rencana\n"
        f"- Maksimal 400 kata"
    )
    try:
        output, *_ = call_claude_cli(prompt, timeout=90)
        return output, "el-solver-direct", len(prompt), len(output)
    except Exception as e:
        msg = f"Gagal dieksekusi: {e}"
        return msg, "el-solver-direct", len(prompt), len(msg)


async def _spawn_agent_for_task(task_title: str, project_name: str) -> None:
    """
    Buat agent baru yang spesialis untuk tipe task ini.
    Dipanggil background saat tidak ada agent yang cocok.
    """
    import asyncio as _aio

    request = (
        f"Buatkan agent yang bisa mengerjakan task seperti ini secara rutin: "
        f"'{task_title}' dalam konteks project '{project_name}'. "
        f"Agent harus bisa diinvoke dengan perintah task dan menghasilkan output konkret."
    )
    try:
        plan = await _aio.to_thread(planner.generate_plan, request, "create_agent")
        risk_result = risk.assess_plan(plan)
        if risk.gate(risk_result, auto_approve_up_to="L1"):
            factory.materialize(plan)
            reg = AgentRegistry()
            for spec in plan.agents:
                reg.register(
                    name=spec.name,
                    archetype=spec.archetype.value,
                    role_description=spec.role_description,
                    manifest={"tools": spec.tools_required, "memory_scopes": spec.memory_scopes},
                    overwrite=True,
                )
            logger.info(f"Agent baru dibuat untuk task '{task_title}': {[s.name for s in plan.agents]}")
    except Exception as e:
        logger.warning(f"_spawn_agent_for_task gagal (non-critical): {e}")


def _update_issue_status(
    issue_id: str,
    status: str,
    notes: str | None = None,
    agent_used: str | None = None,
) -> None:
    """Update status issue di DB, opsional simpan notes dan agent_used."""
    try:
        conn = _get_db_connection()
        if notes is not None or agent_used is not None:
            conn.execute(
                """UPDATE issues SET status=?, notes=?, agent_used=?, executed_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (status, notes, agent_used, issue_id),
            )
        else:
            conn.execute("UPDATE issues SET status=? WHERE id=?", (status, issue_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"_update_issue_status gagal (non-critical): {e}")


# ── MAINTAIN_AGENT ─────────────────────────────────────────────────────────────

def _maintain_agent_handler(intent: IntentResult) -> HandlerResponse:
    """Stub — akan diimplementasikan di iterasi berikutnya."""
    return HandlerResponse(
        text=(
            "Maintenance mode belum diimplementasikan. "
            "Untuk sekarang, edit file agent secara manual di folder `agents/<nama>/`."
        )
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve_agent_name(name: str, reg: AgentRegistry) -> str | None:
    """
    Cari agent berdasarkan nama yang diekstrak dari intent.
    Urutan lookup:
    1. Exact match di registry
    2. Exact match di disk (has valid entry point via agent_io)
    3. name + "-agent" di registry
    4. name + "-agent" di disk
    5. Prefix match di disk
    """
    from el_solver.core.agent_io import _probe_entry_point as _probe

    def _has_entry(dir_name: str) -> bool:
        d = AGENTS_DIR / dir_name
        return d.is_dir() and _probe(d) is not None

    # 1. Exact registry
    if reg.get(name) is not None:
        return name
    # 2. Exact disk
    if _has_entry(name):
        return name
    # 3. With "-agent" suffix — registry
    alt = name + "-agent"
    if reg.get(alt) is not None:
        return alt
    # 4. With "-agent" suffix — disk
    if _has_entry(alt):
        return alt
    # 5. Prefix match di disk
    candidates = [
        d.name for d in AGENTS_DIR.iterdir()
        if d.is_dir() and d.name.startswith(name) and _probe(d) is not None
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _parse_json_from_output(text: str) -> dict | None:
    """Ekstrak JSON object pertama yang valid dari teks output (bisa mixed dengan log lines)."""
    import json as _json
    start = text.find("{")
    if start == -1:
        return None
    # Cari closing brace yang matching
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return _json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


def _record_run(
    run_id: str,
    channel: str,
    user_id: str,
    agent_name: str | None,
    mode: str,
    trigger: str,
    input_preview: str | None,
    output_preview: str | None,
    status: str,
    error_message: str | None,
    duration_ms: int,
    input_chars: int = 0,
    output_chars: int = 0,
) -> None:
    """Insert satu baris ke tabel runs. Fail silently agar tidak ganggu response."""
    # Estimasi token: ~4 chars per token (Indonesian/English mixed)
    tok_in = max(input_chars // 4, 1)
    tok_out = max(output_chars // 4, 1)
    # Sonnet pricing: $3/M input, $15/M output
    cost_usd = (tok_in * 3 + tok_out * 15) / 1_000_000
    try:
        conn = _get_db_connection()
        conn.execute(
            """INSERT INTO runs
               (id, channel, user_id, agent_name, mode, trigger, input_preview,
                output_preview, status, error_message, duration_ms, finished_at,
                tokens_estimate, tokens_in, tokens_out, cost_usd)
               VALUES (?,?,?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP, ?,?,?,?)""",
            (run_id, channel, user_id, agent_name, mode, trigger, input_preview,
             output_preview, status, error_message, duration_ms,
             tok_in + tok_out, tok_in, tok_out, cost_usd),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"_record_run failed (non-critical): {e}")

    # Reflection — best-effort, TIDAK boleh gagalkan run utama
    if agent_name and status in ("success", "error"):
        _reflect_async(agent_name, input_preview or "", output_preview or "", status)


def _reflect_async(agent_name: str, task_input: str, task_output: str, outcome: str) -> None:
    """Reflection best-effort: spawn thread, timeout ketat, tidak pernah raise."""
    import threading
    t = threading.Thread(
        target=_do_reflect,
        args=(agent_name, task_input, task_output, outcome),
        daemon=True,
    )
    t.start()


def _do_reflect(agent_name: str, task_input: str, task_output: str, outcome: str) -> None:
    """Panggil Claude via subprocess untuk 1 kalimat lesson, append ke lessons.md."""
    import json as _json
    import subprocess as _sub
    from datetime import datetime as _dt, timezone as _tz
    from el_solver.config import PROJECT_ROOT, settings

    try:
        from el_solver.utils.db import get_db
        with get_db() as conn:
            registered = conn.execute(
                "SELECT 1 FROM agents_registry WHERE name=?", (agent_name,)
            ).fetchone()
        if not registered:
            logger.debug(f"reflection skipped: '{agent_name}' not in registry")
            return
        prompt = (
            f"Buat SATU kalimat ringkas (maks 20 kata) lesson dari run agent ini. "
            f"Jawab hanya kalimatnya saja.\n"
            f"Agent: {agent_name} | Outcome: {outcome}\n"
            f"Input: {task_input[:120]}\n"
            f"Output: {task_output[:120]}"
        )
        result = _sub.run(
            [settings.claude_cli_path, "-p", prompt, "--model", "claude-haiku-4-5-20251001"],
            capture_output=True, text=True, timeout=20,
            cwd=str(PROJECT_ROOT),
        )
        lesson = (result.stdout.strip() or "").split("\n")[0][:200]
        if not lesson:
            return

        lessons_dir = PROJECT_ROOT / "memory" / agent_name
        lessons_dir.mkdir(parents=True, exist_ok=True)
        lessons_file = lessons_dir / "lessons.md"

        entry = _json.dumps({
            "timestamp": _dt.now(_tz.utc).isoformat(),
            "task": task_input[:100],
            "action": f"invoke {agent_name}",
            "outcome": outcome,
            "brief_lesson": lesson,
        }, ensure_ascii=False)

        with open(lessons_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")

        logger.debug(f"reflection appended for {agent_name}: {lesson[:60]}")
    except Exception as exc:
        logger.debug(f"reflection failed (non-critical): {exc}")


def _record_task_run(
    project_id: str,
    task_title: str,
    agent_used: str,
    prompt_chars: int,
    output_chars: int,
    duration_ms: int,
    status: str = "success",
) -> None:
    """Record satu task project execution ke tabel runs."""
    import uuid as _uuid
    _record_run(
        run_id=str(_uuid.uuid4()),
        channel="project",
        user_id="system",
        agent_name=agent_used,
        mode="project_task",
        trigger="auto",
        input_preview=task_title[:200],
        output_preview=f"[project:{project_id}]",
        status=status,
        error_message=None,
        duration_ms=duration_ms,
        input_chars=prompt_chars,
        output_chars=output_chars,
    )


def _ensure_conversation_record(channel: str, user_id: str, mode: str) -> None:
    """Upsert baris ke tabel conversations di SQLite. Fail silently."""
    try:
        conn = _get_db_connection()
        conv_id = f"{channel}:{user_id}"
        conn.execute(
            """INSERT INTO conversations (id, channel, user_id, mode, last_active)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                 mode=excluded.mode, last_active=CURRENT_TIMESTAMP""",
            (conv_id, channel, user_id, mode),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"_ensure_conversation_record failed (non-critical): {e}")


def _get_db_connection():
    """Get DB connection, jalankan migrate() dulu kalau tabel belum ada."""
    from el_solver.utils.db import get_connection
    try:
        conn = get_connection()
        # Test tabel exists
        conn.execute("SELECT 1 FROM conversations LIMIT 1")
        return conn
    except Exception:
        conn.close()
        migrate()
        return get_connection()
