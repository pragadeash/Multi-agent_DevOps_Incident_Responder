# Design: DevOps Incident Responder

## Architecture

```
User / Demo UI
      |
      v
HackathonAgent.chat(message)
      |
      |-- Agent (Strands, Amazon Bedrock Nova Pro)
      |       system_prompt: Detector + Diagnosis + Fix roles
      |       tools: read_logs, check_metrics, list_deployments,
      |              restart_service, rollback_deployment, scale_service,
      |              write_incident_report
      |       interventions: [ProductionActionGuard]
      |
      |-- ProductionActionGuard (InterventionHandler)
      |       intercepts: restart_service, rollback_deployment, scale_service
      |       reads-only pass through: read_logs, check_metrics, list_deployments,
      |                                write_incident_report
      |       |
      |       |-- Strands Decider 2B (one call, two questions)
      |       |       state: incident conversation + computed facts + proposed action
      |       |       questions: evidence_grounded (noul), action_appropriate (noul)
      |       |
      |       |-- confidence >= HIGH_CONF (0.65) => Proceed (allowed)
      |       |-- LOW_CONF <= confidence < HIGH_CONF => Confirm (escalate)
      |       |-- confidence < LOW_CONF (0.45) => Deny (blocked)
      |
      v
  response dict: reply, decisions (list), model_id, latency_ms
```

## Reusable decision pattern

File: agent/decision.py
Pattern: ProductionActionGuard (#guardrails)

The guard builds a state text containing:
1. The last N conversation turns (what the agent read and diagnosed)
2. Computed facts from SIMENV: error rate vs threshold, CPU vs threshold, restart count vs
   threshold, connection pool saturation, deployment age
3. The proposed action in plain English

Putting the computed facts in the state text means the decider only reads language.
Arithmetic thresholds are evaluated in plain Python before the call.

## Data flow

1. User sends alert text
2. HackathonAgent creates a Strands Agent for the session with ProductionActionGuard
3. Agent calls read_logs and check_metrics (pass through guard)
4. Agent calls list_deployments if needed (pass through guard)
5. Agent forms a fix hypothesis and calls an action tool
6. ProductionActionGuard intercepts: builds state, calls Decider.ask (two questions)
7. Decider returns p_yes for evidence_grounded and action_appropriate
8. Guard branches: Proceed / Confirm / Deny
9. Agent calls write_incident_report (pass through guard)
10. HackathonAgent returns reply + decisions list

## Simulated environment

agent/data/logs.json     — log lines per service
agent/data/metrics.json  — current metrics per service
agent/data/deployments.json — deployment history per service
agent/data/incidents.json   — sample incident scenarios

agent/tools.py SIMENV dict is loaded at import time and mutated by action tools.
tests call reset_simenv() between cases.

## Demo UI

The kit's demo/server.py and demo/static/index.html are used unchanged.
The decision panel displays evidence_grounded, action_appropriate, confidence, verdict, reason per call.
For escalated (confirm) actions the UI shows an approve/reject note.

## Eval cases

32 cases across the four incident scenarios and two tricky ones:
- 16 in test split (reported numbers)
- 16 in tune split (used for development)
- Labels: evidence_grounded: yes/no, action_appropriate: yes/no
- Expected verdicts: both yes => allow; grounded no => block; appropriate no => block/escalate
