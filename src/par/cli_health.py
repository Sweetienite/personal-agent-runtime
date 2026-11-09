"""``par health`` and ``par provider login``: offline health reports and the vendor CLI's own sign-in."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from par.cli_task import _cli, _dump, clean, handled, session
from par.config import ProviderEntry, load_config
from par.core.policy import PolicyViolation
from par.paths import inspect_data_dir
from par.providers.antigravity import login_plan, remove_keychain_link
from par.providers.manifests import manifest_for


@handled
def cmd_health(args):
    with session(args) as kernel:
        if getattr(args, "health_command", None) == "clear":
            n = kernel.clear_health(args.provider, args.model)
            print(f"cleared {n} health record(s) for {args.provider}" + (f"/{args.model}" if args.model else ""))
            return 0
        rep = kernel.health_report()
        probes = kernel.probe_providers() if getattr(args, "probe", False) else None
    if probes is not None:
        rep["probes"] = probes
    if args.json:
        print(_dump(rep))
    else:
        print(f"profile {rep['profile'] or '(none configured)'}   failure policy: {rep['policy']}   adapters registered: {', '.join(rep['adapters']) or 'none'}")
        for role, r in rep["roles"].items():
            print(f"  role {role}: {r['primary']}" + "".join(f" -> {f}" for f in r["fallback"]))
        for w in rep["workers"]:
            state = "ready" if w["usable_write"] else ("read-only ready" if w["usable_read_only"] else "NOT READY")
            print(f"  worker {w['worker']:<14} {w['provider']}/{w['model']:<24} {state}" + (f"  ({'; '.join(w['reasons'])})" if w["reasons"] else ""))
        for h in rep["records"]:
            print(f"  health  {h['provider_id']}/{h['model'] or '*'}  {h['status']}  until {h['until'] or 'cleared by you or a success'}")
        if not rep["workers"]:
            print("  no workers configured: copy config/profiles.example.toml to <data dir>/config/profiles.toml")
        for pr in rep.get("probes", []):
            print(f"  probe   {pr['provider']}: {pr['status']}  {clean(pr['detail'])}" + (f"  (version {pr['version']})" if pr["version"] else ""))
            iso = (pr["facts"] or {}).get("isolation")
            if iso:
                print(f"          extras switched off: {iso['features_disabled']} feature flag(s)"
                      + (f", MCP server(s) {', '.join(iso['mcp_servers_switched_off'])}" if iso["mcp_servers_switched_off"] else "")
                      + ("" if iso["verified"] else "  - NOT VERIFIED"))
                if iso["features_dropped"]:
                    print(f"          not passed (unknown to this version): {', '.join(iso['features_dropped'])}")
                for problem in iso["problems"]:
                    print(f"          problem: {clean(problem)}")
        if rep.get("probes") == []:
            print("  probe   no adapter is registered (enable a provider and name its executable in providers.toml)")
    return 0


@handled
def cmd_provider_login(args):
    """Start the vendor CLI's own sign-in, once, in the Runtime's private HOME (see providers/antigravity.py::login_plan)."""
    cli = _cli()
    data = cli._data_dir(args)
    status = inspect_data_dir(data)
    if not status.usable:
        why = "; ".join(status.problems) or "it does not exist"
        raise PolicyViolation(f"the data directory {data} is not ready ({why}); run `par init` first")
    profiles, providers = data / "config" / "profiles.toml", data / "config" / "providers.toml"
    loaded = load_config(profiles if profiles.is_file() else None, providers if providers.is_file() else None)
    if loaded.errors:
        raise PolicyViolation("configuration: " + "; ".join(f"{i.where}: {i.message}" for i in loaded.errors))
    entry = loaded.config.providers.get(args.provider)
    if entry is None or entry.kind != "antigravity":
        raise PolicyViolation(f"{args.provider!r} is not an antigravity provider in providers.toml (only the Antigravity CLI is signed in this way)")
    if not entry.enabled or not entry.executable:
        raise PolicyViolation("enable the provider and name the absolute path of the signed `agy` binary in providers.toml first")
    manifest = manifest_for(entry.kind)
    if manifest is None:
        raise PolicyViolation("this build has no adapter manifest for the Antigravity CLI")
    adapter = manifest.adapter_cls.from_entry(ProviderEntry(entry.id, entry.kind, entry.enabled, os.path.expanduser(entry.executable)))
    problem = adapter.executable_problem() or adapter.signature_problem()
    if problem:
        raise PolicyViolation(problem)
    plan = login_plan(adapter.executable, data, os.environ, create=not args.print_only)
    print(f"Sign in to Antigravity (agy) once. It runs in the Runtime's own home, not in your ~/.gemini:\n  home  {plan['home']}\n  start {plan['cwd']}")
    print("  - the CLI opens your browser: sign in with the Google account that holds your Antigravity plan\n"
          "  - macOS may ask whether agy may use a keychain item: that is the CLI saving its sign-in\n"
          "  - when the prompt appears you are signed in; type /exit to leave\n"
          "  - the Runtime does not see, store or copy anything you type or approve")
    for note in plan["notes"]:
        print("  note: " + note)
    if args.print_only:
        print("(--print-only: nothing was created or started)")
        return 0
    sys.stdout.flush()
    try:                                                              # a child, not an exec: the keychain link is removed when it has ended
        try:
            proc = subprocess.Popen(plan["argv"], env=plan["env"], cwd=plan["cwd"])
        except OSError as exc:
            raise PolicyViolation(f"could not start {plan['argv'][0]}: {exc.strerror or exc}") from None
        while True:
            try:
                return int(proc.wait())
            except KeyboardInterrupt:                                 # Ctrl-C reaches the CLI as well; it decides what to do with it
                continue
    finally:
        remove_keychain_link(Path(plan["home"]))
