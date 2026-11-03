"""``par coordinator ...``: which model coordinates (route A - selectable coordinators).

``status`` says who coordinates now and who has been seen; ``use NAME`` is the person's switch (a takeover packet is written and confirmed in one
step - typing the command is the confirmation); ``kit NAME`` prints what to paste into that application to connect it, ``prompt NAME`` its starter
prompt. Nothing here edits any application's configuration or starts any application: the person pastes the kit themselves.
"""
from __future__ import annotations

from datetime import datetime, timezone

from par import coordinators as registry
from par.cli_task import _dump, clean, handled, session


def add_parser(sub) -> None:
    p = sub.add_parser("coordinator", help="which model coordinates: see, switch, connect (the Kernel and every task live independently of it)")
    actions = p.add_subparsers(dest="coordinator_command", required=True, metavar="ACTION")
    names = sorted(registry.known_names())

    def common(c, *, json_flag=True):
        c.add_argument("--data-dir", metavar="PATH", help="data directory (default: the standard one)")
        if json_flag:
            c.add_argument("--json", action="store_true", help="machine-readable output")

    for name, text in (("status", "who coordinates now, who has been seen, what the configuration suggests"),
                       ("list", "the same as status")):
        c = actions.add_parser(name, help=text)
        common(c)
        c.set_defaults(func=cmd_coordinator)
    c = actions.add_parser("use", help="make NAME the coordinator: writes a takeover packet and records the switch (your confirmation)")
    c.add_argument("name", choices=names)
    c.add_argument("--note", metavar="TEXT", help="why (kept in the ledger)")
    common(c)
    c.set_defaults(func=cmd_coordinator)
    c = actions.add_parser("kit", help="what to paste into NAME's application to connect it (printed only; nothing is written)")
    c.add_argument("name", choices=names)
    common(c)
    c.set_defaults(func=cmd_coordinator)
    c = actions.add_parser("prompt", help="the starter prompt to give NAME: the rules it must follow")
    c.add_argument("name", choices=names)
    c.add_argument("--data-dir", metavar="PATH", help="(accepted for uniformity; the prompt does not depend on it)")
    c.set_defaults(func=cmd_coordinator, json=False)


def _ago(iso: "str | None", now: "datetime | None" = None) -> str:
    if not iso:
        return "never seen (not connected yet)"
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "seen at an unknown time"
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - then).total_seconds()))
    unit = f"{seconds} s" if seconds < 90 else f"{seconds // 60} min" if seconds < 5400 else f"{seconds // 3600} h" if seconds < 172800 else f"{seconds // 86400} days"
    return f"last seen {then.astimezone().strftime('%Y-%m-%d %H:%M')} ({unit} ago)"


def render_status(st: dict) -> str:
    cur = st["current"]
    lines = [f"coordinating now: {cur['name'] or cur['id'] or 'nobody is configured'}" + (f" [{cur['id']}]" if cur["id"] else "")
             + (f"   (switched {cur['since']}, packet {cur['takeover_id']})" if cur["since"] else "   (as configured)")]
    for c in st["coordinators"]:
        lines.append(f"  {c['id']:<15} {c['name']:<26} {_ago(c['last_seen'])}" + ("   <- coordinating now" if c["is_current"] else ""))
    conf = st["configured"]
    lines.append(f"configured: default {conf['default'] or '-'}, suggested fallback {conf['fallback'] or '-'}")
    for h in st["history"][:3]:
        lines.append(f"  switch {h['at']}: {h['from'] or '-'} -> {h['to']}  ({h['takeover_id']})" + (f"  {clean(h['note'])}" if h.get("note") else ""))
    other = next((c["id"] for c in st["coordinators"] if not c["is_current"]), None)
    if other:
        lines.append(f"switch: par coordinator use {other}      connect: par coordinator kit {other}      its starter prompt: par coordinator prompt {other}")
    return "\n".join(lines)


def render_kit(kit: dict) -> str:
    lines = [f"接入 {kit['name']}（{kit['id']}）", ""]
    lines += [f"  {i}. {step}" for i, step in enumerate(kit["steps"], 1)]
    lines += ["", f"--- 要粘贴的 {kit['snippet']['format']} ---", kit["snippet"]["text"], "--- 结束 ---"]
    if kit.get("command"):
        lines += ["", "另一种方式（一行命令）：", "  " + kit["command"]]
    lines += ["", *(f"说明：{n}" for n in kit["notes"]), "", f"它的起手提示词：par coordinator prompt {kit['id']}",
              "par 不会修改任何应用的配置：以上文字只是打印出来。"]
    return "\n".join(lines)


@handled
def cmd_coordinator(args) -> int:
    command = args.coordinator_command
    if command == "prompt":
        print(registry.starter_prompt(args.name))
        return 0
    with session(args) as kernel:
        if command in ("status", "list"):
            st = kernel.coordinator_status()
            print(_dump(st) if args.json else render_status(st))
        elif command == "use":
            done = kernel.coordinator_switch(args.name, note=args.note or "")
            if args.json:
                print(_dump(done))
            elif not done["switched"]:
                print(f"{done['to']} already coordinates: nothing was changed")
            else:
                print(f"{done['from'] or '-'} -> {done['to']}: recorded (takeover packet {done['takeover_id']}, {done['packet']['tasks']} task(s))")
                print(f"  read by the new coordinator: {done['packet']['markdown']}")
                print(f"  next: par coordinator kit {done['to']}   (connect it)   and   par coordinator prompt {done['to']}   (give it the rules)")
                print("  the previous coordinator can no longer submit tasks or start work through the bridge; you are never locked out")
        else:                                                    # kit
            kit = registry.full_kit(args.name, data_dir=kernel.data_dir, config=kernel.config)
            print(_dump(kit) if args.json else render_kit(kit))
    return 0
