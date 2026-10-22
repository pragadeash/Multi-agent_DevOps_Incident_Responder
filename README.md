# Multi-agent DevOps Incident Responder

An AI on-call team that reads an incident alert plus logs and metrics, finds the root cause, proposes a fix, and writes an incident report. Every risky production action is checked by Strands Decider 2B before it runs.

Built for the [Agent Experience Hackathon: Strands Decider](https://builder.aws.com/build/hackathons) on AWS Builder Center.

---

## What it does

When a service breaks, engineers lose hours reading logs. This agent acts as a first responder:

1. **Detect** — reads logs and metrics for the affected service, finds what is abnormal (CPU spike, error rate, OOM restarts, connection pool exhausted).
2. **Diagnose** — states the most likely root cause in one sentence, citing the specific evidence lines it relied on.
3. **Fix** — proposes the right action (rollback, restart, scale, or escalate) and calls the tool — but only after Strands Decider 2B approves it.
4. **Report** — writes a structured incident report: timeline, root cause, evidence, action taken or pending approval, and next steps.

---

## Features

- Reads logs and metrics to detect what is abnormal
- Diagnoses the root cause with the specific evidence lines
- Checks every risky production action with Strands Decider 2B before it runs
- Blocks unjustified actions and escalates uncertain ones to a human
- Writes a structured incident report with timeline, evidence and next steps
- Live decision panel in the demo UI: allowed / escalated / blocked with confidence numbers
- Works with 6 realistic incident scenarios including 2 tricky "trap" cases

---

## The decision handed to Strands Decider 2B

**Pattern:** `#guardrails`

Before any risky production action (restart service, roll back deployment, scale service), Strands Decider 2B answers two questions about the proposed action:

```python
QUESTIONS = {
    "evidence_grounded": Decider.noul(
        "Is the proposed action supported by specific observations in the logs or metrics?",
        {
            "true":  "logs/metrics contain a specific signal that directly justifies this action",
            "false": "no such signal appears; the action would be a guess",
        },
    ),
    "action_appropriate": Decider.noul(
        "Given the diagnosed root cause, is this specific action the right intervention?",
        {
            "true":  "the action directly addresses the root cause the evidence points to",
            "false": "a different action would be more appropriate",
        },
    ),
}
```

**Confidence branching (in `agent/decision.py`):**

| Both scores | Outcome |
|---|---|
| >= 0.65 (HIGH_CONF) | Proceed — action runs |
| >= 0.45 but < 0.65 | Confirm — escalate to human |
| either < 0.45 (LOW_CONF) | Deny — blocked with reason |

Computed facts (thresholds, counts, timestamps) are evaluated in plain Python and put into the state text. The decider only reads language.

---

## Benchmark results

Benchmark run against the `test` split (16 held-out cases) using the hosted Strands Decider 2B API.

| Metric | Strands Decider 2B | Bedrock LLM baseline |
|---|---|---|
| Accuracy | measured via `./kit bench --split test` | measured via `./kit bench --split test` |
| p50 latency | ~1.2 s (hosted API) | ~2.1 s |
| p95 latency | ~1.8 s | ~3.4 s |
| Cost per 1,000 decisions | ~$0.00 (hosted API, hackathon) | ~$0.24 (Nova Pro) |
| Low-confidence rate | reported in submission/bench/results.md | n/a |

> Numbers above are estimates. Real measured numbers are in `submission/bench/results.json` after running `./kit bench --split test` with live credentials.

**Low-confidence handling:** when either `evidence_grounded` or `action_appropriate` falls between 0.45 and 0.65, the action is escalated to a human via `Confirm` rather than blocked or allowed. This prevents both false positives (blocking a correct action) and false negatives (allowing an unjustified one).

---

## Reusable decision pattern

`agent/decision.py` contains the fully labelled `ProductionActionGuard` pattern. To reuse it in your own agent:

1. Copy `agent/decision.py` into your project.
2. Change `GUARDED_TOOLS` to your irreversible tool names.
3. Reword the two questions for your domain.
4. Tune `HIGH_CONF` and `LOW_CONF` on a held-out eval split.
5. Pass the guard to your Strands `Agent`:

```python
from agent.decision import ProductionActionGuard
from decider_kit import Decider

guard = ProductionActionGuard(Decider())
agent = Agent(model=..., tools=[...], interventions=[guard])
```

---

## Sample incidents

| Incident | Service | Expected action | Guardrail verdict |
|---|---|---|---|
| Deployment error spike | api-gateway | rollback to v2.3.0 | ALLOWED |
| DB connection pool exhausted | user-db | rollback to v1.7.9 | ALLOWED |
| OOM crash loop | worker-service | restart | ALLOWED |
| Black Friday traffic spike | checkout-api | scale to 4 replicas | ALLOWED |
| Healthy service (tricky) | cache-service | no action | BLOCKED |
| External dependency errors (tricky) | payment-api | no action | BLOCKED |

---

## Running locally

```bash
# 1. Install packages (first time, ~2 min)
.\kit.cmd venv          # Windows
./kit venv              # macOS / Linux

# 2. Configure .env (copy from .env.example, add your decider key)
cp .env.example .env

# 3. Start the demo server
.\kit.cmd demo-bg       # Windows  ->  http://localhost:8501
./kit demo-bg           # macOS / Linux

# 4. Run tests
.\kit.cmd test
./kit test

# 5. Run benchmark (needs live decider)
.\kit.cmd bench --split test
```

---

## Project structure

```
agent/
  AGENT.md          use case description
  decision.py       REUSABLE DECISION PATTERN: ProductionActionGuard (#guardrails)
  tools.py          7 tools: read_logs, check_metrics, list_deployments,
                    restart_service, rollback_deployment, scale_service,
                    write_incident_report (all in-memory, no real infra)
  agent.py          HackathonAgent: Bedrock + tools + guard
  eval_cases.jsonl  32 labelled eval cases (16 test, 16 tune)
  demo_script.yaml  3-turn video script
  features.yaml     AgentCore features (memory: off, gateway: off)
  data/             logs.json, metrics.json, deployments.json, incidents.json
demo/
  server.py         FastAPI demo server
  standalone_agent.py  LLM-free demo agent (works without Bedrock/Ollama)
  static/index.html   Dark-mode UI with live decision panel
decider_kit/        Reusable library: Decider client, 5 patterns, bench
.kiro/specs/devops-incident-responder/  requirements, design, tasks
```

---

## Technology

- **Agent framework:** [Strands Agents SDK](https://strandsagents.com) >= 1.60
- **Decision model:** [Strands Decider 2B](https://huggingface.co/StrandsAgents/strands-decider-2B-qwen3.5-v1-2610) (Apache-2.0)
- **LLM:** Amazon Bedrock (Amazon Nova Pro, `us.amazon.nova-pro-v1:0`) for the generative parts
- **Deployment:** Amazon Bedrock AgentCore Runtime (`./kit deploy`)

---

Built with the AWS User Group Madurai Strands Decider hackathon kit
