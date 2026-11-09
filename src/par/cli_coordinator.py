"""``par coordinator ...``: which model coordinates (route A - selectable coordinators).

``status`` says who coordinates now and who has been seen; ``use NAME`` is the person's switch (a takeover packet is written and confirmed in one
step - typing the command is the confirmation); ``kit NAME`` prints what to paste into that application to connect it, ``prompt NAME`` its starter
prompt. Nothing here edits any application's configuration or starts any application: the person pastes the kit themselves.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from par import coordinators as registry
from par.cli_task import _dump, clean, handled, session
from par.core.approvals import APPROVAL_KINDS, DECISION_ACTIONS, RISK_TIERS
from par.core.clock import iso


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

    c = actions.add_parser("grant", help="let NAME decide inside an explicit scope and expiry (a person's CLI action; recorded in the ledger)")
    c.add_argument("name", choices=names)
    c.add_argument("--action", action="append", choices=DECISION_ACTIONS, required=True, dest="grant_actions",
                   help="which decisions the grant covers (repeatable)")
    c.add_argument("--approval-kind", action="append", choices=APPROVAL_KINDS, dest="grant_kinds",
                   help="limit approve/reject to these approval kinds (default: any)")
    c.add_argument("--project", metavar="ID", help="limit the grant to one registered project (default: any)")
    c.add_argument("--risk", action="append", choices=RISK_TIERS, dest="grant_risks",
                   help="limit the grant to these risk tiers (default: R0, R1, R2)")
    c.add_argument("--expires", required=True, metavar="TIME",
                   help="when it stops being valid: an ISO time, or a duration such as 1h, 24h, 7d")
    c.add_argument("--note", metavar="TEXT")
    common(c)
    c.set_defaults(func=cmd_coordinator)
    c = actions.add_parser("grants", help="active coordinator grants (with --all: revoked and expired ones too)")
    c.add_argument("--all", action="store_true", help="include revoked and expired grants")
    common(c)
    c.set_defaults(func=cmd_coordinator)
    c = actions.add_parser("revoke", help="revoke a coordinator grant immediately (a person's CLI action)")
    c.add_argument("grant_id", type=int, metavar="N")
    c.add_argument("--note", metavar="TEXT")
    common(c)
    c.set_defaults(func=cmd_coordinator)


def _expiry(text: str) -> str:
    """``24h``/``7d`` from now, or an ISO-8601 time. A time without a zone is read as local time."""
    raw = text.strip().lower()
    try:
        if raw.endswith("h") and raw[:-1].replace(".", "", 1).isdigit():
            return iso(datetime.now(timezone.utc) + timedelta(hours=float(raw[:-1])))
        if raw.endswith("d") and raw[:-1].replace(".", "", 1).isdigit():
            return iso(datetime.now(timezone.utc) + timedelta(days=float(raw[:-1])))
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("expires must be an ISO time (2026-11-10T00:00:00+08:00) or a duration such as 24h or 7d") from None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return iso(moment)


def render_grants(rows: "list[dict]") -> str:
    if not rows:
        return "no coordinator grants"
    lines = []
    for g in rows:
        state = "active" if g["active"] else ("revoked" if g["revoked_at"] else "expired")
        scope = ", ".join(g["actions"])
        if g["approval_kinds"]:
            scope += " (" + ", ".join(g["approval_kinds"]) + ")"
        lines.append(f"#{g['grant_id']:<4} {g['coordinator']:<15} {state:<8} {scope:<40} "
                     f"project {(g['project_id'] or '*'):<12} risk {','.join(g['risk_tiers']):<7} until {g['expires_at'] or 'revoked'}")
    return "\n".join(lines)


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
    lines.append(f"approval mode: {st['approval_mode']}" + ("   (coordinator decisions allowed)"
                 if st["approval_mode"] == "coordinator_autonomous" else
                 "   (a live grant is required)" if st["approval_mode"] == "coordinator_scoped" else
                 "   (only a person can decide)"))
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
        elif command == "grant":
            try:
                expires_at = _expiry(args.expires)
            except ValueError as exc:
                print(f"error: {exc}", file=__import__("sys").stderr)
                return 2
            grant = kernel.grant_create(args.name, actions=args.grant_actions, project_id=args.project,
                                         approval_kinds=args.grant_kinds or (), risk_tiers=args.grant_risks or RISK_TIERS,
                                         expires_at=expires_at, note=args.note or "")
            print(_dump(grant) if args.json else
                  f"grant #{grant['grant_id']} recorded for {grant['coordinator']}: {', '.join(grant['actions'])} "
                  f"until {grant['expires_at']}")
        elif command == "grants":
            rows = kernel.grant_list(include_inactive=args.all)
            print(_dump(rows) if args.json else render_grants(rows))
        elif command == "revoke":
            grant = kernel.grant_revoke(args.grant_id, note=args.note or "")
            print(_dump(grant) if args.json else f"grant #{grant['grant_id']} revoked")
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
