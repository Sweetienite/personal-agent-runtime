"""Run ``par`` in its own process with scripted workers registered, so a test can signal it.

    python -m tests.cli_child '<json: {"argv": [...], "scenarios": [...]}>'
"""
import json
import sys

from par import cli, cli_task
from par.providers.fake import Scenario, ScenarioSequence
from par.providers.registry import AdapterRegistry


def main(argv):
    args = json.loads(argv[1])
    seq = ScenarioSequence(*[Scenario(s["name"], tuple(s["steps"]), s.get("on_sigterm")) for s in args["scenarios"]])
    reg = AdapterRegistry()
    reg.register("codex", seq.factory("codex"))
    reg.register("antigravity", seq.factory("antigravity"))
    cli_task.build_adapters = lambda config=None: reg
    print("ready", flush=True)
    return cli.main(args["argv"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
