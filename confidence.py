"""Proxy A: a one-call agent self-confidence scorer (0-100), model-agnostic.

GATED + ADDITIVE. Returns ``None`` (no LLM call, no cost, no latency) unless
``RESEARCH_LOG`` is truthy, so the shipping product is byte-identical
with the flag off. Never raises into the caller.

Proxy A = the agent's own confidence, 0-100, that the user's request was FULLY
and CORRECTLY completed. It is measured by ONE extra, post-hoc request issued
AFTER the task finalizes. The request is self-contained, it does NOT use the
live AI client, its conversation history, or the big skill SYSTEM_PROMPT, so
it cannot perturb the agent's in-loop reasoning or control flow. It uses the
agent's own model (``AI_MODEL`` via OpenRouter), so the score is genuinely the
model grading itself.

This fills paper gap #2 (Proxy A "NOT BUILT YET"). The score is logged into the
existing ``proxy_A_self_confidence`` field by ``research_log.log_turn``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import requests

AI_MODEL = os.getenv("EVAL_MODEL", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

_OR_URL = "https://openrouter.ai/api/v1/chat/completions"

_SYS = "You are a strict self-assessment grader. Output ONLY an integer 0-100."

_USER_TMPL = (
    "I just attempted this user request and need an honest confidence score.\n"
    "On a scale of 0 to 100, how confident are you that the request was FULLY "
    "and CORRECTLY completed? 0 means certainly not completed, 100 means "
    "certainly completed correctly. Do not be charitable: if anything is "
    "uncertain or you cannot verify it, lower the score.\n\n"
    "User request: {command}\n"
    "Actions taken: {actions}\n"
    "What was said back to the user: {spoken}\n"
    "Reported outcome: {outcome}\n\n"
    "Reply with ONLY the integer."
)


def _enabled() -> bool:
    return os.getenv("RESEARCH_LOG", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _marker_path() -> Path:
    """Cross-process 'current eval task' marker that run_eval.py writes."""
    override = os.getenv("RESEARCH_DIR", "").strip()
    base = Path(override) if override else Path(__file__).resolve().parent / "runs"
    return base / ".eval_current"


def eval_task_id() -> str:
    """Current eval task id, or '' if no eval run is in progress.

    Resolves ``EVAL_TASK_ID`` first (same-process), then the marker
    file written by ``research/eval/run_eval.py``, the runner and the live
    agent are separate processes, so the file is how the agent learns
    which task each turn belongs to.
    """
    env = os.getenv("EVAL_TASK_ID", "").strip()
    if env:
        return env
    try:
        p = _marker_path()
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _parse_score(text: str) -> int | None:
    """First integer in ``text``, clamped to [0, 100]; None if no digits."""
    m = re.search(r"\d{1,3}", text or "")
    if not m:
        return None
    return max(0, min(100, int(m.group(0))))


def score(
    command: str,
    *,
    actions: Any = None,
    spoken: str = "",
    outcome: str = "",
    model: str = "",
    timeout: float = 20.0,
) -> int | None:
    """Agent's 0-100 self-confidence that the request was completed, or None.

    No-op (returns None immediately, no network) unless research logging is on.
    Never raises, any error / timeout / missing key degrades to None so a
    research run is never broken by the scorer.
    """
    if not _enabled() or not OPENROUTER_API_KEY:
        return None
    try:
        action_str = ", ".join(str(a) for a in (actions or []))[:1200] or "(none)"
        user = _USER_TMPL.format(
            command=(command or "")[:600],
            actions=action_str,
            spoken=(spoken or "")[:600],
            outcome=outcome or "unknown",
        )
        payload = {
            "model": model or AI_MODEL,
            "messages": [
                {"role": "system", "content": _SYS},
                {"role": "user", "content": user},
            ],
            "max_tokens": 8,
            "temperature": 0,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            
            
        }
        resp = requests.post(_OR_URL, headers=headers, json=payload, timeout=(10, timeout))
        if not resp.ok:
            return None
        data = resp.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return _parse_score(text)
    except Exception:
        return None
