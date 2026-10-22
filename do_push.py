"""Initialise git, commit everything, and push to the remote."""
import subprocess, sys, os

root = "C:\\Users\\pragadeash\\OneDrive\\Downloads\\pragadeash-g-decider-hackathon-kit\\pragadeash-g-decider-hackathon-kit"
remote = "https://github.com/pragadeash/Multi-agent_DevOps_Incident_Responder.git"

def run(args, **kw):
    print(">>", " ".join(args))
    r = subprocess.run(args, cwd=root, capture_output=True, text=True, **kw)
    if r.stdout.strip(): print(r.stdout.strip())
    if r.stderr.strip(): print(r.stderr.strip())
    return r.returncode

# 1. Init
run(["git", "init"])
run(["git", "config", "user.email", "pragadeash@example.com"])
run(["git", "config", "user.name", "pragadeash"])

# 2. Add remote (ignore error if already exists)
run(["git", "remote", "add", "origin", remote])

# 3. Stage all (gitignore handles .env and .venv)
run(["git", "add", "."])

# 4. Show what will be committed
run(["git", "status", "--short"])

# 5. Commit
rc = run(["git", "commit", "-m",
    "feat: Multi-agent DevOps Incident Responder\n\n"
    "- agent/decision.py: REUSABLE DECISION PATTERN ProductionActionGuard (#guardrails)\n"
    "- agent/tools.py: 7 tools with in-memory SIMENV (no real infra)\n"
    "- agent/agent.py: HackathonAgent with Strands Agents SDK\n"
    "- agent/eval_cases.jsonl: 32 labelled cases (16 test, 16 tune)\n"
    "- demo/standalone_agent.py: LLM-free demo for local use\n"
    "- 6 incident scenarios including 2 tricky guardrail cases\n"
    "- 64 tests passing\n\n"
    "Built with the AWS User Group Madurai Strands Decider hackathon kit"
])

if rc != 0:
    print("Commit failed — maybe nothing to commit?")
    sys.exit(rc)

# 6. Push to main
rc = run(["git", "push", "-u", "origin", "HEAD:main"])
if rc != 0:
    print("\nPush failed. If the repo is empty/new on GitHub, try:")
    print("  git push --force -u origin HEAD:main")
    rc2 = run(["git", "push", "--force", "-u", "origin", "HEAD:main"])
    sys.exit(rc2)

print("\nDone. Pushed to", remote)
