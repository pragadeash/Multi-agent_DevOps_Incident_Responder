"""Standalone demo agent: full incident-response pipeline, zero LLM required.

Works by:
  1. Parsing the alert to identify the service.
  2. Reading logs, metrics and deployments directly from agent/data/ (SIMENV).
  3. Running rule-based diagnosis on the metrics.
  4. Checking every risky action through ProductionActionGuard with an
     evidence-based fake decider (probabilities derived from the real metrics).
  5. Writing an incident report.
  6. Returning reply + decisions in the same shape HackathonAgent.chat() uses,
     so the demo UI's decision panel works unchanged.

No Ollama, no Bedrock, no credentials needed.
"""

from __future__ import annotations

import json
import re
import time
from types import SimpleNamespace
from typing import Any

NAME = "DevOps Incident Responder (standalone demo)"


# ---------------------------------------------------------------------------
# Helper: call a Strands @tool-decorated function directly
# ---------------------------------------------------------------------------

def _call(tool_obj, **kwargs):
    """Call a Strands @tool object's underlying Python function."""
    # Strands wraps the function; the original is accessible via __wrapped__
    # or by calling the object directly with keyword args (it accepts them).
    fn = getattr(tool_obj, "__wrapped__", None) or getattr(tool_obj, "func", None)
    if fn is not None:
        return fn(**kwargs)
    # Fallback: call the object — Strands tools accept plain kwargs at runtime
    return tool_obj(**kwargs)


# ---------------------------------------------------------------------------
# Evidence-based fake decider (no network, derives probs from SIMENV metrics)
# ---------------------------------------------------------------------------

class _EvidenceDecider:
    """Derives calibrated probabilities from the state text — no model call."""

    def __init__(self):
        self.last_latency_ms: float = 4.0

    def ask(self, state: str, questions: dict, images=None) -> dict:
        from decider_kit.client import Decision

        grounded   = self._grounded(state)
        appropriate = self._appropriate(state)

        out = {}
        for name in questions:
            p = grounded if name == "evidence_grounded" else appropriate
            out[name] = Decision.parse({"type": "noul", "noul": float(p)})
        return out

    # ---- probability rules ------------------------------------------------

    def _grounded(self, s: str) -> float:
        sl = s.lower()
        score = 0.15
        if any(x in sl for x in ("high", "critical", "exhausted", "oom",
                                  "outofmemory", "nullpointerexception",
                                  "hikaripool", "heap", "crash")):
            score += 0.45
        if any(x in sl for x in ("many", "restarts_last_hour",
                                  "very recent", "deployed 15",
                                  "deployed 1")):
            score += 0.25
        # Service is healthy — no evidence
        if "error_rate=0" in sl and "normal" in sl and "oom" not in sl:
            score = max(score - 0.35, 0.12)
        # External dependency errors — action not grounded
        if "stripe" in sl or "upstream" in sl or "external" in sl:
            score = max(score - 0.35, 0.12)
        return round(min(score, 0.95), 2)

    def _appropriate(self, s: str) -> float:
        sl = s.lower()

        if "rollback_deployment" in sl or "roll back" in sl:
            if ("very recent" in sl or "deployed 15") and "high" in sl:
                return 0.88
            if "not recent" in sl:
                return 0.16
            return 0.52

        if "restart_service" in sl or "restart service" in sl:
            if ("critical" in sl and "memory" in sl) or "oom" in sl or (
                    "many" in sl and "restart" in sl):
                return 0.87
            if "error_rate=0" in sl and "normal" in sl:
                return 0.12
            return 0.50

        if "scale_service" in sl or "scale '" in sl:
            if "high" in sl and "cpu" in sl and ("error_rate=0" in sl
                                                  or "normal" in sl):
                return 0.85
            if "exhausted" in sl or "oom" in sl:
                return 0.14
            return 0.50

        return 0.50


# ---------------------------------------------------------------------------
# Service detection
# ---------------------------------------------------------------------------

def _detect_service(alert: str) -> str | None:
    from agent.tools import SIMENV
    known = list(SIMENV["metrics"].keys())
    al = alert.lower()
    for svc in known:
        if svc in al:
            return svc
    words = re.findall(r"[a-z0-9][a-z0-9-]*", al)
    for w in words:
        for svc in known:
            parts = svc.split("-")
            if w in parts or any(p.startswith(w[:4]) for p in parts if len(p) > 3):
                return svc
    return None


# ---------------------------------------------------------------------------
# Rule-based diagnosis
# ---------------------------------------------------------------------------

def _diagnose(service: str, metrics: dict, logs: list, current: str,
              previous: str) -> dict:
    er       = metrics.get("error_rate_pct")  or 0
    cpu      = metrics.get("cpu_pct")          or 0
    mem      = metrics.get("memory_pct")       or 0
    restarts = metrics.get("restarts_1h")      or 0
    cu       = metrics.get("connections_used")
    cm       = metrics.get("connections_max")
    age      = metrics.get("deployment_age_min")

    err_logs = [l["msg"] for l in logs if l.get("level") == "ERROR"][:3]

    # 1. Connection pool exhausted → rollback (deployment caused the leak)
    if cu is not None and cm is not None and cu >= cm:
        return {
            "root_cause": (f"Database connection pool exhausted ({cu}/{cm}). "
                           f"Deployment {current} introduced a connection leak."),
            "severity": "critical",
            "action": "rollback_deployment",
            "action_args": {"service": service, "version": previous},
            "evidence": (f"connections={cu}/{cm} EXHAUSTED; "
                         f"deployment {current} {age} min ago"),
        }

    # 2. High error rate after very recent deployment → rollback
    if er > 10 and age is not None and age < 30:
        detail = f" Log: {err_logs[0]}" if err_logs else ""
        return {
            "root_cause": (f"Deployment {current} (rolled out {age} min ago) "
                           f"introduced errors ({er:.0f}% error rate).{detail}"),
            "severity": "critical",
            "action": "rollback_deployment",
            "action_args": {"service": service, "version": previous},
            "evidence": (f"error_rate={er}% HIGH; "
                         f"deployment {current} only {age} min ago"),
        }

    # 3. OOM crash loop → restart
    if restarts > 3 and mem > 85:
        return {
            "root_cause": (f"Memory leak causing repeated OOM restarts "
                           f"({restarts} restarts, memory {mem:.0f}%). "
                           "Heap is not reclaiming after GC."),
            "severity": "high",
            "action": "restart_service",
            "action_args": {"service": service},
            "evidence": f"memory={mem:.0f}% CRITICAL; restarts_1h={restarts}",
        }

    # 4. CPU-bound traffic spike, no errors, stable deployment → scale
    if cpu > 85 and er < 2 and (age is None or age > 120):
        return {
            "root_cause": (f"Traffic spike saturating CPU ({cpu:.0f}%). "
                           "No errors and no recent deployment — scale up."),
            "severity": "medium",
            "action": "scale_service",
            "action_args": {"service": service, "replicas": 4},
            "evidence": (f"cpu={cpu:.0f}% HIGH; error_rate={er}% normal; "
                         f"last deployment {age} min ago (not recent)"),
        }

    # 5. High errors, old deployment → external dependency
    if er > 10 and (age is None or age > 120):
        detail = f" Log: {err_logs[0]}" if err_logs else ""
        return {
            "root_cause": (f"High error rate ({er:.0f}%) with no recent deployment. "
                           f"Likely an external dependency issue.{detail}"),
            "severity": "high",
            "action": None,
            "action_args": {},
            "evidence": (f"error_rate={er}% HIGH; "
                         f"deployment {current} was {age} min ago (not recent)"),
        }

    # 6. Nothing significant
    return {
        "root_cause": ("No significant anomaly detected. "
                       "Metrics are within normal range. No production action warranted."),
        "severity": "low",
        "action": None,
        "action_args": {},
        "evidence": f"error_rate={er}%, cpu={cpu}%, memory={mem}% — all normal",
    }


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------

class StandaloneAgent:
    """Incident-response pipeline with no external LLM dependency."""

    def __init__(self):
        self._decider = _EvidenceDecider()
        from agent.decision import ProductionActionGuard
        self._guard = ProductionActionGuard(self._decider)
        self._guard_fresh = True  # reset per session

    def chat(self, message: str, session_id: str = "default",
             images=None) -> dict[str, Any]:
        from agent.tools import (
            SIMENV, check_metrics, list_deployments, read_logs,
            restart_service, rollback_deployment, scale_service,
            write_incident_report,
        )
        from agent.decision import _build_state
        from strands.interventions import Confirm, Deny, Proceed

        t0 = time.perf_counter()

        # --- 1. Identify service -------------------------------------------
        service = _detect_service(message)
        if not service:
            known = ", ".join(SIMENV["metrics"].keys())
            return {
                "reply": (
                    "I couldn't identify a service name in the alert.\n\n"
                    f"**Known services:** {known}\n\n"
                    "Try something like:\n"
                    "> `CRITICAL: api-gateway error rate 42%. Deployment v2.3.1 rolled out 15 minutes ago.`"
                ),
                "decisions": [],
                "model_id": "standalone",
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "agent": NAME,
            }

        # --- 2. Gather evidence --------------------------------------------
        logs_r    = _call(read_logs,        service=service, minutes=60)
        metrics_r = _call(check_metrics,    service=service, metric="all")
        deps_r    = _call(list_deployments, service=service)

        metrics  = metrics_r.get("values", {})
        logs     = logs_r.get("lines", [])
        current  = deps_r.get("current_version",  "unknown")
        previous = deps_r.get("previous_version", "unknown")

        # --- 3. Diagnose ---------------------------------------------------
        diag = _diagnose(service, metrics, logs, current, previous)

        # --- 4. Guard check + action ---------------------------------------
        action_taken  = "none"
        action_status = "none"
        action_note   = ""
        decisions: list[dict] = []

        if diag["action"]:
            tool_name = diag["action"]
            args      = diag["action_args"]

            fake_conv = (
                f"user: {message}\n"
                f"assistant: {diag['root_cause']}"
            )
            state_text = _build_state(fake_conv, tool_name, args)

            fake_agent = SimpleNamespace(messages=[
                {"role": "user",      "content": [{"text": message}]},
                {"role": "assistant", "content": [{"text": diag["root_cause"]}]},
            ])
            fake_event = SimpleNamespace(
                tool_use={"name": tool_name, "input": args},
                agent=fake_agent,
            )

            # Clear guided set for each new chat turn
            self._guard._guided.clear()
            guard_result = self._guard.before_tool_call(fake_event)
            if self._guard.log:
                decisions = [self._guard.log[-1]]

            if isinstance(guard_result, Proceed):
                if tool_name == "restart_service":
                    res = _call(restart_service, service=args["service"])
                elif tool_name == "rollback_deployment":
                    res = _call(rollback_deployment,
                                service=args["service"],
                                version=args["version"])
                elif tool_name == "scale_service":
                    res = _call(scale_service,
                                service=args["service"],
                                replicas=args["replicas"])
                else:
                    res = {"result": "done"}

                action_taken  = f"{tool_name}({json.dumps(args)})"
                action_status = "completed"
                action_note   = res.get("result", "executed")

            elif isinstance(guard_result, Confirm):
                action_taken  = f"{tool_name}({json.dumps(args)})"
                action_status = "pending-approval"
                conf = decisions[-1].get("confidence", 0) if decisions else 0
                action_note   = f"Escalated — confidence {conf:.0%}. Human approval required."

            else:  # Deny
                action_taken  = f"{tool_name}({json.dumps(args)}) [BLOCKED]"
                action_status = "blocked"
                reason = decisions[-1].get("reason", "guardrail blocked") if decisions else ""
                action_note   = reason
        else:
            action_note = "No production action taken (external dependency or healthy service)."

        # --- 5. Write incident report -------------------------------------
        inc_id = f"INC-{service.upper().replace('-','')[:6]}-{int(time.time()) % 100000}"
        _call(
            write_incident_report,
            incident_id=inc_id,
            service=service,
            severity=diag["severity"],
            timeline=(
                "Alert received → logs & metrics read → root cause identified "
                f"→ action {action_status}."
            ),
            root_cause=diag["root_cause"],
            evidence=diag["evidence"],
            action_taken=action_taken,
            action_status=action_status,
            next_steps=(
                "Monitor service and confirm full recovery."
                if action_status == "completed"
                else "Await human approval before proceeding."
                if action_status == "pending-approval"
                else "Review evidence manually. No automated action was taken."
            ),
        )

        # --- 6. Build reply -----------------------------------------------
        reply = _format_reply(
            service, diag, action_taken, action_status, action_note,
            inc_id, decisions,
        )

        return {
            "reply":      reply,
            "decisions":  decisions,
            "model_id":   "standalone (no LLM required)",
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "agent":      NAME,
        }


# ---------------------------------------------------------------------------
# Reply formatter
# ---------------------------------------------------------------------------

def _format_reply(service, diag, action_taken, action_status,
                  action_note, inc_id, decisions):
    sev_icon = {"critical": "[CRITICAL]", "high": "[HIGH]",
                "medium": "[MEDIUM]", "low": "[LOW]"}.get(diag["severity"], "[INFO]")

    # Guardrail block
    guard_block = ""
    if decisions:
        d   = decisions[-1]
        v   = d.get("verdict", "")
        c   = d.get("confidence", 0)
        gr  = d.get("evidence_grounded", 0)
        ap  = d.get("action_appropriate", 0)
        ico = {"proceed": "ALLOWED", "confirm": "ESCALATED TO HUMAN",
               "deny": "BLOCKED"}.get(v, v.upper())
        guard_block = (
            f"\n\n---\n"
            f"**Strands Decider 2B** -> {ico}  "
            f"(confidence **{c:.0%}**)\n"
            f"- evidence_grounded: **{gr:.0%}** | "
            f"action_appropriate: **{ap:.0%}**"
        )
        if v in ("deny", "confirm") and action_note:
            guard_block += f"\n- Reason: *{action_note}*"

    # Action block
    if action_status == "completed":
        action_block = (
            f"\n\n**Action taken:** `{action_taken.split('(')[0]}`  "
            f"**{action_status.upper()}** -- {action_note}"
        )
    elif action_status in ("blocked", "none"):
        action_block = (
            f"\n\n**Action:** {action_note or 'No action taken.'}"
        )
    elif action_status == "pending-approval":
        action_block = (
            f"\n\n**Action:** `{action_taken.split('(')[0]}`  "
            f"**PENDING APPROVAL**\n{action_note}"
        )
    else:
        action_block = ""

    return (
        f"{sev_icon} **{service}**\n\n"
        f"**Root cause:** {diag['root_cause']}\n\n"
        f"**Evidence:** `{diag['evidence']}`"
        f"{action_block}"
        f"{guard_block}\n\n"
        f"**Incident report:** `{inc_id}` written."
    )
