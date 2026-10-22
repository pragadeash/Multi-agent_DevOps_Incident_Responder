# Use case

- **Pitch:** an AI on-call team that reads an incident alert plus logs and metrics, finds the
  root cause, proposes a fix, and writes an incident report — and every risky production action
  is checked before it runs.
- **Who it is for:** engineers and small DevOps teams who lose hours reading logs when a website
  or API breaks; the agent acts as a first responder that does the triage and proposes a safe fix.
- **Features:**
  - reads logs and metrics for the affected service;
  - diagnoses the most likely root cause with the evidence lines it used;
  - proposes a remediation action (restart, rollback, scale up, config change);
  - every risky production action is checked by Strands Decider 2B before it runs —
    ungrounded or unsafe actions are blocked or escalated to a human;
  - writes a structured incident report: timeline, root cause, evidence, action taken or
    pending approval, and next steps;
  - decision panel in the demo UI shows allowed / escalated / blocked with a confidence number
    and reason for each proposed action;
  - an approve/reject button for escalated actions.
- **The decision handed to Strands Decider 2B:** before any risky production action (restart
  service, roll back deployment, scale down, change a config limit), is the action (a) grounded
  in evidence found in the logs and metrics, and (b) explicitly appropriate for the diagnosed
  cause? Grounded and appropriate actions run; ungrounded or inappropriate ones are blocked;
  low-confidence cases are escalated to a human. (`#guardrails`)
- **Why a decision model:** the guardrail runs on every proposed action, so it must be fast,
  cheap and calibrated — it must return a confidence number the code branches on, not generate
  free text.
- **Amazon Bedrock:** the three specialist sub-roles (Detector, Diagnosis, Fix) and the report
  writer are all driven through the Strands Agents SDK with Amazon Nova Pro by default.
