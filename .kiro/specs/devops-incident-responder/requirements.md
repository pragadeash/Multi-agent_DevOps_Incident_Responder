# Requirements: DevOps Incident Responder

## Overview
An AI on-call team that reads an incident alert plus logs and metrics, finds the root cause,
proposes a remediation, and writes a structured incident report. Every risky production action
is checked by Strands Decider 2B before it runs.

## Users
Engineers and small DevOps teams who lose time reading logs when a website or API breaks.
The agent acts as a first responder: it does the triage, proposes a safe fix, and escalates
when uncertain.

## Functional requirements

### FR-1 Alert intake
The agent accepts a plain-text incident alert as its initial message.
Sample incidents are provided in agent/data/incidents.json.

### FR-2 Detection (Detector role)
The agent calls read_logs and check_metrics to find what is abnormal.
It reports specific signals: error rates, CPU/memory readings, restart counts,
connection pool exhaustion, and deployment timestamps.

### FR-3 Diagnosis (Diagnosis role)
The agent states the most likely root cause in one sentence, citing the exact log lines
or metric values it used.

### FR-4 Fix proposal (Fix role)
The agent proposes the most appropriate remediation:
- error spike after recent deployment => rollback_deployment
- OOM crash loop => restart_service
- high load, no errors, no bad deployment => scale_service
- external dependency failure => no production action, escalate

### FR-5 Guardrail decision
Before any risky production action (restart_service, rollback_deployment, scale_service),
Strands Decider 2B answers two typed questions:
1. evidence_grounded: is the action supported by specific observations in the logs/metrics?
2. action_appropriate: is this action the right intervention for the diagnosed cause?

Outcomes:
- both >= 0.65 (HIGH_CONF): allow (Proceed)
- both >= 0.45 (LOW_CONF) but either < HIGH_CONF: escalate to human (Confirm)
- either < 0.45 (LOW_CONF): block (Deny), with a reason

### FR-6 Incident report
The agent always calls write_incident_report at the end, with:
- incident_id, service, severity, timeline, root_cause, evidence
- action_taken, action_status (completed / pending-approval / blocked / none), next_steps

### FR-7 Decision panel (demo UI)
Each decision entry in the response includes:
- pattern tag, tool name, evidence_grounded, action_appropriate, confidence, verdict, reason, decider_ms

### FR-8 Simulated environment
All tools work against an in-memory environment loaded from agent/data/. No real cloud APIs
are ever called. SIMENV can be reset between test runs.

## Non-functional requirements

### NR-1 Guardrail convergence
If the agent retries a guided/denied call with identical arguments, it is denied immediately
(Strands requires Guide to converge).

### NR-2 Kit contract
- HackathonAgent.chat(message, session_id, images=None) returns dict with reply, decisions, model_id, latency_ms
- agent/decision.py defines PATTERN_TAG = "#guardrails" and QUESTIONS
- agent/decision.py docstring starts with "REUSABLE DECISION PATTERN:"
- No IS_KIT_EXAMPLE in decision.py

### NR-3 No real secrets or infrastructure
.env holds all keys. No keys are hardcoded. No real cloud, DB or infrastructure is touched.

### NR-4 Benchmark
At least 30 eval cases in agent/eval_cases.jsonl covering allow, escalate, and block outcomes,
across all four incident scenarios and the two tricky cases.
