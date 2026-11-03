"""Run one Kernel in its own process, so a test can kill it the way a crash or a power cut would.

    python -m tests.kernel_child '<json>'
"""
import json
import sys
from pathlib import Path

from par.config import ProjectEntry
from par.core.kernel import Kernel
from par.providers.fake import Scenario, ScenarioSequence
from par.providers.registry import AdapterRegistry
from tests.kernel_fixture import fast_settings
from tests.world import sample_config


def main(argv):
    args = json.loads(argv[1])
    scenarios = [Scenario(s["name"], tuple(s["steps"]), s.get("on_sigterm")) for s in args["scenarios"]]
    seq = ScenarioSequence(*scenarios)
    reg = AdapterRegistry()
    reg.register("codex", seq.factory("codex"))
    reg.register("antigravity", seq.factory("antigravity"))
    projects = {"fixture": ProjectEntry("fixture", Path(args["project"]), "git", True)}
    kernel = Kernel(args["data"], config=sample_config(policy=args.get("policy", "ask")), projects=projects, adapters=reg,
                    home=args["home"], settings=fast_settings(**args.get("settings", {})))
    print("ready", flush=True)
    report = kernel.run(args["task_id"])
    print(report.status, flush=True)
    kernel.close()


if __name__ == "__main__":
    main(sys.argv)
