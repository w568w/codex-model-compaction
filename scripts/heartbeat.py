#!/usr/bin/env python3
"""Keep a quiet public fork's scheduled workflow enabled without daily commits."""

import base64
from datetime import datetime, timezone
import json
import os
import subprocess

repo = os.environ["GITHUB_REPOSITORY"]
branch = "packaging"
timestamp = subprocess.check_output(["gh", "api", f"repos/{repo}/commits/{branch}",
                                     "--jq", ".commit.committer.date"], text=True).strip()
now = datetime.now(timezone.utc)
if (now - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).days < 45:
    print("Maintenance branch is active; no heartbeat commit needed")
else:
    endpoint = f"repos/{repo}/contents/.github/last-scheduled-check"
    existing = subprocess.run(["gh", "api", "--method", "GET", endpoint, "-f", f"ref={branch}"],
                              capture_output=True, text=True)
    data = {"message": "chore: keep the release schedule active", "branch": branch,
            "content": base64.b64encode((now.isoformat() + "\n").encode()).decode()}
    if existing.returncode == 0:
        data["sha"] = json.loads(existing.stdout)["sha"]
    elif "404" not in existing.stderr:
        raise RuntimeError(existing.stderr)
    subprocess.run(["gh", "api", "--method", "PUT", endpoint, "--input", "-"],
                   input=json.dumps(data).encode(), check=True, stdout=subprocess.DEVNULL)
    print("Recorded a successful scheduled check")
