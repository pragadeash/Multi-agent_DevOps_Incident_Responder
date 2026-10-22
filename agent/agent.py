"""Multi-agent DevOps Incident Responder.

An AI on-call team that reads an incident alert plus logs and metrics, finds the root cause,
proposes a fix, and writes an incident report. Every risky production action is checked by
Strands Decider 2B before it runs.

    from agent.agent import HackathonAgent
    out = HackathonAgent().chat("CRITICAL: api-gateway error rate 42%. Deployment v2.3.1 rolled out 15 min ago.")
    out["reply"], out["decisions"]

The kit contract:
- HackathonAgent.chat(message, session_id="default", images=None) -> dict
  dict keys: reply, decisions, model_id, latency_ms
- NAME: the agent's display name
"""

from __future__ import annotations

import time
from typing import Any

from strands import Agent

from decider_kit import Decider
from decider_kit.llm import agent_model, default_model_id, image_blocks, strip_thinking

from .decision import ProductionActionGuard
from .tools import TOOLS

NAME = "DevOps Incident Responder"

SYSTEM_PROMPT = """You are an AI on-call incident response team for a production service.
Your job is to read an incident alert, gather evidence (logs and metrics), diagnose the root cause,
propose a safe remediation, and write a structured incident report.

Work in this order:
1. DETECT: call read_logs and check_metrics to find what is abnormal.
2. DIAGNOSE: state the most likely root cause in one sentence, citing the specific evidence lines.
3. FIX: propose the most appropriate action. Call the action tool ONLY if the evidence clearly
   justifies it. If a deployment recently caused errors, use rollback_deployment.
   If OOM crashes are the signal, use restart_service.
   If traffic is high with no errors, use scale_service.
   If the root cause is external (upstream dependency), do NOT take a production action —
   write the report with action_status=none and escalate.
4. REPORT: always call write_incident_report at the end with the timeline, root cause, evidence,
   action taken or blocked, and next steps.

Rules:
- Never restart, rollback or scale without first reading logs and metrics for that service.
- Never take a production action if the root cause points to an external dependency.
- If an action is blocked by the guardrail, write the report with action_status=blocked and
  explain why in next_steps.
- Be concise: the final reply to the engineer is a 3-5 sentence summary plus the report ID."""

# For demos: the agent acts eagerly so the guardrail has something to catch on camera.
EAGER_PROMPT = """You are an AI on-call incident response team. Act immediately and decisively.
For every alert, immediately read logs, diagnose, and take a remediation action (restart, rollback or scale).
Do not hesitate — pick the most common fix for the symptom and call the action tool right away.
Always call write_incident_report at the end."""


class HackathonAgent:
    """One Strands agent per session, with the production-action guardrail wired in."""

    def __init__(
        self,
        decider: Decider | None = None,
        model_id: str | None = None,
        eager: bool | None = None,
    ):
        import os

        self.decider = decider or Decider()
        self.eager = eager if eager is not None else os.environ.get("DEMO_EAGER") == "1"
        self.model_id = model_id or default_model_id()
        self.sessions: dict[str, tuple[Agent, ProductionActionGuard]] = {}

    def _session(self, session_id: str) -> tuple[Agent, ProductionActionGuard]:
        if session_id not in self.sessions:
            guard = ProductionActionGuard(self.decider)
            agent = Agent(
                model=agent_model(self.model_id),
                system_prompt=EAGER_PROMPT if self.eager else SYSTEM_PROMPT,
                tools=TOOLS,
                interventions=[guard],
                callback_handler=None,
            )
            self.sessions[session_id] = (agent, guard)
        return self.sessions[session_id]

    def chat(
        self,
        message: str,
        session_id: str = "default",
        images: list[Any] | None = None,
    ) -> dict[str, Any]:
        """`images` (optional): photos the user attached.  Passed through to the LLM as
        Strands image blocks and to the decider via invocation_state."""
        agent, guard = self._session(session_id)
        before = len(guard.log)
        t0 = time.perf_counter()
        content = ([*image_blocks(images), {"text": message}] if images else message)
        result = agent(
            content,
            invocation_state={"images": list(images or [])},
        )
        reply = strip_thinking(str(result))
        return {
            "reply": reply,
            "decisions": guard.log[before:],
            "model_id": self.model_id,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "agent": NAME,
            "eager": self.eager,
        }


if __name__ == "__main__":
    import json
    import sys

    a = HackathonAgent()
    alerts = sys.argv[1:] or [
        "CRITICAL: api-gateway error rate 42%. Deployment v2.3.1 rolled out 15 minutes ago.",
    ]
    for msg in alerts:
        print(json.dumps(a.chat(msg), indent=2, default=str))
