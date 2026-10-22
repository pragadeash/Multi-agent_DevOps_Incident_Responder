"""REUSABLE DECISION PATTERN: production-action guardrail  (#guardrails)

What it does
    Before any risky production action (restart service, roll back deployment, scale down,
    change a config limit), Strands Decider 2B checks the proposed action against the incident
    evidence (logs and metrics already gathered):

    1. Is the action grounded in evidence found in the logs and metrics?
       (If the logs show no OOM, a restart is not grounded.)
    2. Is the action explicitly appropriate for the diagnosed root cause?
       (A traffic spike calls for scaling, not a rollback.)

    Grounded and appropriate actions are allowed to run.
    Ungrounded or inappropriate actions are blocked with a reason.
    Low-confidence cases (the decider is unsure) are escalated to a human for approval.

    Exact threshold checks (CPU > 90%, restart count > 5, error rate > 30%) happen in plain
    Python before the decider is called and are put in the state. The decider only reads
    language — it judges whether the textual evidence justifies the proposed action.

Typed questions and options
    evidence_grounded (noul)
        Is the proposed action supported by specific observations in the logs or metrics?
        true  = the logs/metrics contain a specific signal that directly justifies this action
        false = no such signal appears in the evidence; the action would be a guess

    action_appropriate (noul)
        Given the diagnosed root cause, is this specific action (restart / rollback / scale /
        config-change) the right intervention — not just plausible, but explicitly appropriate?
        true  = the action directly addresses the root cause that the evidence points to
        false = a different action would be more appropriate, or the root cause is not clear enough

Wiring
    `ProductionActionGuard` is a Strands `InterventionHandler`. agent/agent.py passes it to
    the Agent:

        guard = ProductionActionGuard(decider)
        agent = Agent(..., interventions=[guard])

    It inspects only tools in GUARDED_TOOLS; read-only tools (read_logs, check_metrics,
    list_deployments, write_incident_report) pass through untouched.

    Confidence branching:
        both >= HIGH_CONF         => Proceed (allowed)
        both >= LOW_CONF           => Confirm (escalate to human)
        either <  LOW_CONF         => Deny    (block, with reason)
        guide-retry (same call)   => Deny    (Strands requires Guide to converge)

How to reuse it
    1. Copy this file into your own agent/ directory.
    2. Change GUARDED_TOOLS to the names of your irreversible tools.
    3. Reword the two questions for your domain.
       Keep the structure: one noul for "is the evidence there", one noul for "is it the right action".
    4. Tune HIGH_CONF and LOW_CONF on a held-out eval split with `./kit bench --split test`.
    5. Put computed facts (counts, thresholds, timestamps) in the state before calling ask().
       The decider reads language, not numbers.
"""

from __future__ import annotations

import json
from typing import Any

from strands.interventions import Confirm, Deny, Guide, InterventionHandler, Proceed

from decider_kit import Decider
from decider_kit.patterns.guardrails import render_messages

# Confidence thresholds — tune with ./kit bench --split test
HIGH_CONF: float = 0.65   # both questions above this => allowed
LOW_CONF: float  = 0.45   # either question below this => blocked; between LOW and HIGH => escalate

PATTERN_TAG = "#guardrails"

# Only these tools are checked before running; read-only tools and the report writer pass through
GUARDED_TOOLS = {"restart_service", "rollback_deployment", "scale_service"}

QUESTIONS = {
    "evidence_grounded": Decider.noul(
        "Is the proposed action supported by specific observations in the logs or metrics that appear in the incident state?",
        {
            "true":  "the logs or metrics contain a specific signal — an error message, a crash, an OOM, "
                     "a connection-pool count, a CPU reading, a deployment timestamp — that directly justifies "
                     "taking this exact action on this service right now",
            "false": "no such specific signal appears in the incident evidence; the action would be a guess "
                     "or is based on reasoning not present in the logs or metrics",
        },
    ),
    "action_appropriate": Decider.noul(
        "Given the diagnosed root cause in the incident state, is this specific action the right intervention "
        "rather than a different one?",
        {
            "true":  "the proposed action (restart / rollback / scale / config-change) directly and specifically "
                     "addresses the root cause that the evidence points to — for example, rollback when a "
                     "deployment introduced errors, scale when traffic is high but no errors exist, restart "
                     "when repeated OOM crashes are the signal",
            "false": "a different action would be more appropriate for the diagnosed cause, or the cause is not "
                     "clear enough to justify any risky action yet — for example, rollback when there was no "
                     "recent deployment, or restart when the service is healthy",
        },
    ),
}


class ProductionActionGuard(InterventionHandler):
    """Strands InterventionHandler: blocks, escalates or allows risky production actions.

    The decider reads the incident state (logs + metrics + diagnosis + proposed action) and
    returns calibrated probabilities for two questions. The code then branches on thresholds.
    """
    name = "production-action-guard"

    def __init__(
        self,
        decider: Decider,
        high_conf: float = HIGH_CONF,
        low_conf: float = LOW_CONF,
    ):
        self.decider = decider
        self.high_conf = high_conf
        self.low_conf = low_conf
        self.log: list[dict[str, Any]] = []
        self._guided: set[str] = set()

    def before_invocation(self, event, **kwargs):
        self._guided.clear()
        return Proceed()

    def before_tool_call(self, event, **kwargs):
        name = event.tool_use["name"]
        if name not in GUARDED_TOOLS:
            return Proceed()

        args = event.tool_use.get("input") or {}
        sig = f"{name}:{json.dumps(args, sort_keys=True)}"

        # Guide must converge: the same call retried unchanged is denied
        if sig in self._guided:
            rec = {
                "pattern": PATTERN_TAG,
                "tool": name,
                "args": args,
                "verdict": "deny",
                "reason": "retried a guided call without new evidence",
                "confidence": 0.0,
            }
            self.log.append(rec)
            return Deny(
                reason=(
                    f"The action '{name}' was already sent back for more evidence. "
                    "Do not retry it with the same arguments. Gather more diagnostic "
                    "information or write the incident report with action_status=blocked."
                )
            )

        # Build the state: conversation + computed facts + proposed action
        conversation = render_messages(event.agent.messages)
        state = _build_state(conversation, name, args)

        # Ask the decider — one call, two questions in parallel
        a = self.decider.ask(state, QUESTIONS)
        grounded = a["evidence_grounded"].p_yes or 0.0
        appropriate = a["action_appropriate"].p_yes or 0.0
        confidence = min(grounded, appropriate)

        rec: dict[str, Any] = {
            "pattern": PATTERN_TAG,
            "tool": name,
            "args": args,
            "evidence_grounded": round(grounded, 3),
            "action_appropriate": round(appropriate, 3),
            "confidence": round(confidence, 3),
            "decider_ms": round(self.decider.last_latency_ms or 0),
        }

        # Branch on confidence thresholds
        if grounded < self.low_conf:
            rec["verdict"] = "deny"
            rec["reason"] = (
                f"The action '{name}' is not grounded in the incident evidence "
                f"(evidence_grounded={grounded:.2f} < {self.low_conf}). "
                "Check logs and metrics first, or write the report with action_status=blocked."
            )
            self.log.append(rec)
            return Deny(reason=rec["reason"])

        if appropriate < self.low_conf:
            rec["verdict"] = "deny"
            rec["reason"] = (
                f"The action '{name}' is not appropriate for the diagnosed root cause "
                f"(action_appropriate={appropriate:.2f} < {self.low_conf}). "
                "The evidence points to a different fix."
            )
            self.log.append(rec)
            return Deny(reason=rec["reason"])

        if confidence < self.high_conf:
            rec["verdict"] = "confirm"
            rec["reason"] = (
                f"The decider is unsure (confidence={confidence:.2f}). "
                "A human must approve before this action runs."
            )
            self.log.append(rec)
            return Confirm(
                prompt=(
                    f"Approve '{name}' with args {json.dumps(args)} for this incident?\n"
                    f"evidence_grounded={grounded:.2f}, action_appropriate={appropriate:.2f}"
                ),
                response="yes",
            )

        # Both above HIGH_CONF: allow the action
        rec["verdict"] = "proceed"
        rec["reason"] = (
            f"Action grounded (evidence_grounded={grounded:.2f}) and appropriate "
            f"(action_appropriate={appropriate:.2f})."
        )
        self.log.append(rec)
        return Proceed()


# --------------------------------------------------------------------------- #
#  State builder: puts computed facts in text for the decider                   #
# --------------------------------------------------------------------------- #

def _build_state(conversation: str, tool_name: str, args: dict[str, Any]) -> str:
    """Build the state text the decider reads.

    Computed facts (counts, thresholds, timestamps) are included as labelled lines so the
    decider only needs to read language, not arithmetic.
    """
    service = args.get("service", "unknown")

    # Pull live metrics from the simulated environment for the service
    from .tools import SIMENV
    metrics = SIMENV["metrics"].get(service, {})
    deps = SIMENV["deployments"].get(service, [])
    current_ver = next((d["version"] for d in deps if d["status"] == "current"), "unknown")
    previous_ver = next((d["version"] for d in deps if d["status"] == "previous"), "unknown")
    age_min = metrics.get("deployment_age_min")

    # Computed facts (thresholds evaluated in plain code)
    facts: list[str] = []
    if metrics.get("error_rate_pct") is not None:
        er = metrics["error_rate_pct"]
        facts.append(f"error_rate={er:.1f}% ({'HIGH — above 10%' if er > 10 else 'normal'})")
    if metrics.get("cpu_pct") is not None:
        cpu = metrics["cpu_pct"]
        facts.append(f"cpu={cpu:.0f}% ({'HIGH — above 90%' if cpu > 90 else 'normal'})")
    if metrics.get("memory_pct") is not None:
        mem = metrics["memory_pct"]
        facts.append(f"memory={mem:.0f}% ({'CRITICAL — above 90%' if mem > 90 else 'normal'})")
    if metrics.get("restarts_1h") is not None:
        r = metrics["restarts_1h"]
        facts.append(f"restarts_last_hour={r} ({'many — above 3' if r > 3 else 'few'})")
    if metrics.get("connections_used") is not None and metrics.get("connections_max") is not None:
        used, mx = metrics["connections_used"], metrics["connections_max"]
        facts.append(f"db_connections={used}/{mx} ({'EXHAUSTED' if used >= mx else 'ok'})")
    if age_min is not None:
        facts.append(
            f"last_deployment={current_ver} deployed {age_min} minutes ago "
            f"({'VERY RECENT — within 30min' if age_min < 30 else 'not recent' if age_min > 120 else 'recent'})"
        )
    if previous_ver != "unknown":
        facts.append(f"previous_stable_version={previous_ver}")

    # Action description
    action_desc = {
        "restart_service": f"restart service '{service}' (causes brief outage; justified by crashes or OOM)",
        "rollback_deployment": (
            f"roll back '{service}' from {current_ver} to {args.get('version', '?')} "
            "(justified when the current deployment introduced errors)"
        ),
        "scale_service": (
            f"scale '{service}' to {args.get('replicas', '?')} replicas "
            "(justified when load is high but no errors or bad deployment exists)"
        ),
    }.get(tool_name, f"run {tool_name} on {service}")

    return (
        "An incident response session. The agent has read logs and metrics and is about to "
        "take a risky production action. Assess whether the action is grounded in the evidence.\n\n"
        f"--- INCIDENT CONVERSATION (last turns) ---\n{conversation}\n\n"
        f"--- COMPUTED FACTS FOR '{service}' ---\n" + "\n".join(facts or ["(no metrics found)"]) +
        f"\n\n--- PROPOSED ACTION ---\n{action_desc}\n"
        f"tool: {tool_name}\narguments: {json.dumps(args)}"
    )
