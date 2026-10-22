"""DevOps Incident Responder tools: read logs/metrics, take remediation actions.

All tools work against an in-memory simulated environment loaded from agent/data/*.json.
No real cloud APIs or real infrastructure are ever called.
The action tools (restart, rollback, scale) only mutate the in-memory state dict SIMENV.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from strands import tool

# --------------------------------------------------------------------------- #
#  Simulated environment: loaded once from agent/data/                         #
# --------------------------------------------------------------------------- #

_DATA = Path(__file__).parent / "data"


def _load(name: str) -> dict[str, Any]:
    p = _DATA / f"{name}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


# In-memory state: mutated by action tools, reset between test runs via reset_simenv()
SIMENV: dict[str, Any] = {
    "logs": _load("logs"),
    "metrics": _load("metrics"),
    "deployments": _load("deployments"),
    "reports": {},       # incident_id -> report dict
    "actions_taken": [], # list of action dicts, newest first
}


def reset_simenv() -> None:
    """Reset the simulated environment to the data-file state. Used by tests."""
    SIMENV["logs"] = _load("logs")
    SIMENV["metrics"] = _load("metrics")
    SIMENV["deployments"] = _load("deployments")
    SIMENV["reports"] = {}
    SIMENV["actions_taken"] = []


# --------------------------------------------------------------------------- #
#  Read-only diagnostic tools                                                   #
# --------------------------------------------------------------------------- #

@tool
def read_logs(service: str, minutes: int = 60) -> dict:
    """Return the most recent log lines for a service (last `minutes` of activity).

    Returns a dict with fields: service, log_count, lines (list of log entry dicts).
    Each log entry has: ts (ISO timestamp), level (INFO/WARN/ERROR), msg.
    """
    all_logs = SIMENV["logs"]
    if service not in all_logs:
        return {"service": service, "error": f"no logs found for '{service}'",
                "known_services": list(all_logs.keys())}
    lines = all_logs[service]
    # Return last `minutes`-worth; with synthetic data, return all lines
    return {"service": service, "log_count": len(lines), "lines": lines[-50:]}


@tool
def check_metrics(service: str, metric: str = "all") -> dict:
    """Return current metrics for a service: error_rate_pct, cpu_pct, memory_pct, restarts_1h, etc.

    Pass metric="all" to get all metrics, or a specific key like "error_rate_pct".
    Returns a dict with keys: service, metric (or "all"), value (or full metrics dict).
    """
    all_metrics = SIMENV["metrics"]
    if service not in all_metrics:
        return {"service": service, "error": f"no metrics for '{service}'",
                "known_services": list(all_metrics.keys())}
    data = all_metrics[service]
    if metric == "all":
        return {"service": service, "metric": "all", "values": data}
    if metric not in data:
        return {"service": service, "metric": metric, "error": f"unknown metric '{metric}'",
                "available": list(data.keys())}
    return {"service": service, "metric": metric, "value": data[metric]}


@tool
def list_deployments(service: str) -> dict:
    """List all known deployment versions for a service, newest first.

    Returns a dict with: service, deployments (list), current_version, previous_version.
    Each deployment has: version, deployed_at, status, deployed_by.
    """
    all_deps = SIMENV["deployments"]
    if service not in all_deps:
        return {"service": service, "error": f"no deployment history for '{service}'",
                "known_services": list(all_deps.keys())}
    deps = all_deps[service]
    current = next((d["version"] for d in deps if d["status"] == "current"), None)
    previous = next((d["version"] for d in deps if d["status"] == "previous"), None)
    return {"service": service, "deployments": deps, "current_version": current,
            "previous_version": previous}


# --------------------------------------------------------------------------- #
#  Action tools (risky — every call goes through the guardrail decision first) #
# --------------------------------------------------------------------------- #

@tool
def restart_service(service: str) -> dict:
    """Restart a service (RISKY: causes a brief outage). Only justified when logs show crashes or OOM.

    Returns: service, action, result ("restarted" or error).
    """
    all_metrics = SIMENV["metrics"]
    if service not in all_metrics:
        return {"service": service, "action": "restart", "error": f"unknown service '{service}'"}
    # Simulate restart: reset memory and restart counters
    all_metrics[service]["memory_pct"] = max(all_metrics[service]["memory_pct"] * 0.4, 20.0)
    all_metrics[service]["restarts_1h"] = all_metrics[service].get("restarts_1h", 0) + 1
    entry = {"action": "restart", "service": service}
    SIMENV["actions_taken"].insert(0, entry)
    return {"service": service, "action": "restart", "result": "restarted",
            "note": "simulated — no real infrastructure was touched"}


@tool
def rollback_deployment(service: str, version: str) -> dict:
    """Roll back a service to a previous deployment version (RISKY: may break other services).

    Only justified when logs show errors introduced by the current deployment.
    `version` must be a known previous version listed by list_deployments.
    Returns: service, action, rolled_back_to, result.
    """
    all_deps = SIMENV["deployments"]
    if service not in all_deps:
        return {"service": service, "action": "rollback", "error": f"no deployment history for '{service}'"}
    deps = all_deps[service]
    versions = [d["version"] for d in deps]
    if version not in versions:
        return {"service": service, "action": "rollback",
                "error": f"version '{version}' not found — known: {versions}"}
    # Simulate rollback: mark the requested version as current
    for d in deps:
        if d["version"] == version:
            d["status"] = "current"
        elif d["status"] == "current":
            d["status"] = "previous"
    # Reset error rate to indicate recovery
    if service in SIMENV["metrics"]:
        SIMENV["metrics"][service]["error_rate_pct"] = 0.5  # normal level
    entry = {"action": "rollback", "service": service, "version": version}
    SIMENV["actions_taken"].insert(0, entry)
    return {"service": service, "action": "rollback", "rolled_back_to": version,
            "result": "success", "note": "simulated — no real infrastructure was touched"}


@tool
def scale_service(service: str, replicas: int) -> dict:
    """Scale a service to the given number of replicas (RISKY: cost impact and brief disruption).

    Only justified when metrics show high CPU/load without errors or bad deployments.
    `replicas` must be between 1 and 20.
    Returns: service, action, replicas, result.
    """
    if replicas < 1 or replicas > 20:
        return {"service": service, "action": "scale",
                "error": f"replicas must be between 1 and 20, got {replicas}"}
    all_metrics = SIMENV["metrics"]
    if service not in all_metrics:
        return {"service": service, "action": "scale", "error": f"unknown service '{service}'"}
    # Simulate scale: reduce CPU and latency proportionally
    factor = min(1.0, 2.0 / max(1, replicas))
    all_metrics[service]["cpu_pct"] = round(all_metrics[service]["cpu_pct"] * factor, 1)
    if all_metrics[service].get("latency_p50_ms"):
        all_metrics[service]["latency_p50_ms"] = int(all_metrics[service]["latency_p50_ms"] * factor)
    entry = {"action": "scale", "service": service, "replicas": replicas}
    SIMENV["actions_taken"].insert(0, entry)
    return {"service": service, "action": "scale", "replicas": replicas,
            "result": "scaled", "note": "simulated — no real infrastructure was touched"}


@tool
def write_incident_report(
    incident_id: str,
    service: str,
    severity: str,
    timeline: str,
    root_cause: str,
    evidence: str,
    action_taken: str,
    action_status: str,
    next_steps: str,
) -> dict:
    """Write a structured incident report and store it.

    `severity` should be one of: critical, high, medium, low.
    `action_status` should be one of: completed, pending-approval, blocked, none.
    Returns: incident_id, result ("written"), and the full report dict.
    """
    report = {
        "incident_id": incident_id,
        "service": service,
        "severity": severity,
        "timeline": timeline,
        "root_cause": root_cause,
        "evidence": evidence,
        "action_taken": action_taken,
        "action_status": action_status,
        "next_steps": next_steps,
    }
    SIMENV["reports"][incident_id] = report
    return {"incident_id": incident_id, "result": "written", "report": report}


TOOLS = [
    read_logs,
    check_metrics,
    list_deployments,
    restart_service,
    rollback_deployment,
    scale_service,
    write_incident_report,
]
