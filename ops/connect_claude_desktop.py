#!/usr/bin/env python3
"""Connect Claude Desktop (the chat app) to the Personal Agent Runtime: add the "par" MCP server to claude_desktop_config.json.

    .venv/bin/python ops/connect_claude_desktop.py            # dry run: shows what it would do, changes nothing
    .venv/bin/python ops/connect_claude_desktop.py --apply    # does it (Claude Desktop must be quit first)
    .venv/bin/python ops/connect_claude_desktop.py --apply --background --reopen
                                                              # the easy way: starts, detaches, and waits until YOU quit Claude Desktop (⌘Q); then it writes the entry,
                                                              # checks it and opens Claude Desktop again. It survives the app quitting, so it can be started from inside the app
    .venv/bin/python ops/connect_claude_desktop.py --remove   # takes the entry out again (also needs the app quit)

Why a script, and why "quit the app first": Claude Desktop keeps its own copy of that file in memory and writes it back from time to time, so an edit made while
the app runs is silently erased (that happened on the first attempt). With the app closed the edit is stable. The script backs the file up next to it
(claude_desktop_config.json.par-backup-<time>), changes only the "par" entry of "mcpServers", writes atomically with the file's own permissions and checks the result.
Nothing else in the file is touched. This is an ops script the person runs; `par` itself never edits an application's configuration.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from par import coordinators                    # noqa: E402
from par.paths import default_data_dir          # noqa: E402

CONFIG = Path("~/Library/Application Support/Claude/claude_desktop_config.json")
SERVER = coordinators.SERVER_NAME


class Refused(Exception):
    """Something the person must look at; the file was not changed."""


APP_FOLDER = "/Claude.app/Contents/"          # the app's main program and every helper of it start from a path with this folder in it


def claude_running(run: Callable[..., Any] = subprocess.run) -> bool:
    """True while any process of the Claude desktop app exists. `pgrep -x Claude` does NOT find it on macOS (found out the hard way), so this reads the
    program path of every process; when that cannot be read the answer is "running": refusing is safer than writing under a live app."""
    done = run(["ps", "-axo", "command="], capture_output=True, text=True)
    if done.returncode != 0:
        return True
    return any(APP_FOLDER in line.strip().split(" ", 1)[0] for line in done.stdout.splitlines())


def wait_until_quit(running: Callable[[], bool], seconds: float, *, out: Callable[[str], None], sleep: Callable[[float], None] = time.sleep,
                    interval: float = 1.0, settle: int = 3) -> bool:
    """Wait until the app has been gone for `settle` checks in a row (so its quit-time write has finished); False when `seconds` pass first."""
    waited, gone, said = 0.0, 0, False
    while True:
        if running():
            gone = 0
            if not said:
                out(f"Claude Desktop 还在运行：等你把它完全退出（菜单栏 Claude > 退出，或 ⌘Q），最多等 {int(seconds)} 秒……")
                said = True
        else:
            gone += 1
            if gone >= settle:
                return True
        if waited >= seconds:
            return False
        sleep(interval)
        waited += interval


def reopen_claude(out: Callable[[str], None], run: Callable[..., Any] = subprocess.run) -> None:
    done = run(["open", "-a", "Claude"], capture_output=True, text=True)
    out("已重新打开 Claude Desktop。" if done.returncode == 0 else "没能自动打开 Claude Desktop，请你自己打开它。")


def entry_for(python: str, data_dir: str) -> "dict[str, Any]":
    kit = coordinators.connection_kit("claude_desktop", python=python, data_dir=data_dir, package_parent=coordinators.package_parent())
    return json.loads(kit["snippet"]["text"])[SERVER]


def _dump(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def changed_text(text: str, entry: "Optional[dict[str, Any]]") -> "Optional[str]":
    """The file's new text with the par entry set (or removed when entry is None); None when nothing has to change. Refuses what it cannot change safely."""
    try:
        original = json.loads(text)
    except ValueError as exc:
        raise Refused(f"the configuration file is not valid JSON ({exc}); fix or restore it first") from exc
    if not isinstance(original, dict):
        raise Refused("the configuration file is not a JSON object")
    servers = original.get("mcpServers")
    if servers is not None and not isinstance(servers, dict):
        raise Refused('"mcpServers" in the file is not an object')
    if entry is None:
        if not servers or SERVER not in servers:
            return None
        expected = json.loads(text)
        del expected["mcpServers"][SERVER]
        if not expected["mcpServers"]:
            del expected["mcpServers"]
    else:
        if servers and servers.get(SERVER) == entry:
            return None
        expected = json.loads(text)
        expected.setdefault("mcpServers", {})[SERVER] = entry
    tail = "\n" if text.endswith("\n") else ""
    if _dump(original) + tail == text:                       # the file is exactly what a JSON writer with 2-space indent makes: re-writing it loses nothing
        new = _dump(expected) + tail
    elif entry is not None and servers is None:             # otherwise insert before the last brace, leaving every other byte alone
        end = text.rstrip().rfind("}")
        block = _dump({SERVER: entry})
        indented = "\n".join(("  " + line if i else line) for i, line in enumerate(block.split("\n")))
        new = text[:end].rstrip() + ',\n  "mcpServers": ' + indented + "\n" + text[end:]
    else:
        raise Refused("the file is formatted in a way this script will not rewrite; edit it by hand (the entry to add is printed by `par coordinator kit claude_desktop`)")
    if json.loads(new) != expected:
        raise Refused("internal check failed: the new text would not mean what was intended; nothing was written")
    return new


def write_atomically(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o777
    tmp = path.with_name(path.name + ".par-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise


def run(args: argparse.Namespace, *, running: Callable[[], bool] = claude_running, out: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep, reopen: Callable[[Callable[[str], None]], None] = reopen_claude) -> int:
    path = Path(os.path.expanduser(str(args.config)))
    entry = None if args.remove else entry_for(args.python, args.data_dir)
    if args.apply:
        wait = getattr(args, "wait", 0) or 0
        if wait > 0:
            if not wait_until_quit(running, wait, out=out, sleep=sleep):
                out(f"没有改动：等了 {int(wait)} 秒，Claude Desktop 还没有退出。")
                return 1
        elif running():
            out("没有改动：Claude Desktop 还在运行。它会把自己内存里的配置写回文件，现在改会被抹掉。请先完全退出它（菜单栏 Claude > 退出，或 ⌘Q），再运行；"
                "或者加上 --wait 秒数，让本工具等你退出。")
            return 1
    try:
        if not path.is_file():
            if args.remove:
                out(f"没有这个文件，无需移除：{path}")
                return 0
            raise Refused(f"找不到 Claude Desktop 的配置文件：{path}（先打开并退出一次 Claude Desktop，让它生成这个文件）")
        text = path.read_text(encoding="utf-8")
        new = changed_text(text, entry)
    except Refused as exc:
        out(f"没有改动：{exc}")
        return 1
    what = "移除 par 条目" if args.remove else "加入 par 条目"
    if new is None:
        out("无需改动：" + ("文件里本来就没有 par 条目。" if args.remove else "par 条目已经在文件里，内容一致。"))
        return 0
    if not args.apply:
        out(f"试运行（不会改动任何文件）：会{what}，文件：{path}")
        if entry is not None:
            out(_dump({SERVER: entry}))
        out("确认无误后，先完全退出 Claude Desktop（菜单栏 Claude > 退出，或 ⌘Q），再加 --apply 重新运行。")
        return 0
    backup = path.with_name(path.name + ".par-backup-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    write_atomically(path, new)
    check = json.loads(path.read_text(encoding="utf-8"))
    ok = (SERVER not in (check.get("mcpServers") or {})) if args.remove else (check.get("mcpServers", {}).get(SERVER) == entry)
    if not ok:
        shutil.copy2(backup, path)
        out("写入后的自检没通过，已经用备份恢复原样。")
        return 1
    out(f"完成：已{what}。备份：{backup}")
    if getattr(args, "reopen", False):
        reopen(out)
    else:
        out("现在重新打开 Claude Desktop")
    out("新开一个聊天，工具列表里应出现 par 的工具；让它调用 list_tasks，再运行 `par coordinator status` 看「最近出现」。")
    out(f"想恢复原样：cp '{backup}' '{path}'（先退出 Claude Desktop）。")
    return 0


def background(argv: "list[str]", args: argparse.Namespace, spawn: Callable[..., Any] = subprocess.Popen,
               out: Callable[[str], None] = print) -> int:
    """Start this same command again in a session of its own (it survives the app quitting) and return at once; it logs to <data dir>/logs/."""
    logs = Path(args.data_dir) / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    log = logs / "connect_claude_desktop.log"
    forward = [a for a in argv if a != "--background"]
    if not any(a == "--wait" or a.startswith("--wait=") for a in forward):
        forward += ["--wait", "1800"]
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    child = spawn([sys.executable, "-u", str(Path(__file__).resolve()), *forward, "--timestamps"], stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.STDOUT,
                  start_new_session=True, close_fds=True)
    os.close(fd)
    out(f"已在后台等待（进程 {getattr(child, 'pid', '?')}）。现在请完全退出 Claude Desktop（菜单栏 Claude > 退出，或 ⌘Q）；"
        "它退出之后，本工具会写入配置、自检" + ("、并重新打开 Claude Desktop" if "--reopen" in forward else "") + "。")
    out(f"进度和结果写在：{log}")
    return 0


def main(argv: "Optional[list[str]]" = None, *, spawn: Callable[..., Any] = subprocess.Popen) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="把 par 接进 Claude Desktop 的配置（默认只是试运行）")
    parser.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="和 --apply 一起用：等你退出 Claude Desktop，最多等这么多秒，再写入")
    parser.add_argument("--reopen", action="store_true", help="写入成功之后自动重新打开 Claude Desktop")
    parser.add_argument("--background", action="store_true", help="和 --apply 一起用：在后台等你退出 Claude Desktop 再写入（退出应用不会终止它）；进度写进数据目录的 logs/")
    parser.add_argument("--timestamps", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--apply", action="store_true", help="真的写入（需要先退出 Claude Desktop）")
    parser.add_argument("--remove", action="store_true", help="改为移除 par 条目（和 --apply 一起用才会真的写入，同样需要先退出 Claude Desktop）")
    parser.add_argument("--config", default=str(CONFIG), help="配置文件路径（默认是 Claude Desktop 的那个）")
    parser.add_argument("--python", default=sys.executable, help="桥使用的 Python（默认：运行本脚本的这个）")
    parser.add_argument("--data-dir", default=str(default_data_dir()), help="Runtime 的数据目录（默认：标准位置）")
    args = parser.parse_args(argv)
    for label, value in (("--python", args.python), ("--data-dir", args.data_dir)):
        if not os.path.isabs(value):
            parser.error(f"{label} 必须是绝对路径")
    if args.background and not args.apply:
        parser.error("--background 要和 --apply 一起用")
    if args.wait < 0:
        parser.error("--wait 不能是负数")
    if args.background:
        return background(argv, args, spawn)
    if args.timestamps:
        return run(args, out=lambda line: print(time.strftime("%Y-%m-%d %H:%M:%S ") + line, flush=True))
    return run(args, out=lambda line: print(line, flush=True))


if __name__ == "__main__":
    raise SystemExit(main())
