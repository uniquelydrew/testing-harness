from __future__ import annotations

import json
import sys
from pathlib import Path


request = json.load(sys.stdin)
if request.get("protocol") != 1:
    raise SystemExit("unsupported script-step protocol")

configuration = request.get("inputs", {}).get("configuration")
if not isinstance(configuration, str) or not configuration:
    raise SystemExit("configuration must be a non-empty string")

workspace = Path("runs") / ("workspace-" + configuration)
workspace.mkdir(parents=True, exist_ok=True)

json.dump(
    {
        "protocol": 1,
        "outputs": {
            "workspace": str(workspace.resolve()),
        },
    },
    sys.stdout,
)
