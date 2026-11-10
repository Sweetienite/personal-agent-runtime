#!/usr/bin/env python3
"""Check that the published product facts, READMEs and executable structure still agree with the code.

    python -B tests/tools/docs_contract.py

The single fact source is ``docs/product-capabilities.json``. This script compares it with:

* ``par.__version__``, ``par.RELEASE_STATUS``, the default approval mode and the real MCP tool list;
* the adapter manifests in ``src/par/providers/manifests.py``;
* the jobs actually declared in ``.github/workflows/tests.yml``;
* the required English pages and bilingual README links;
* stable heading order, table column counts and Mermaid presence in the two READMEs;
* dated evidence files for every ``VERIFIED_*`` claim.

It exits 1 on any mismatch. No third-party parser is used.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from par import RELEASE_STATUS, SUPPORTED_ROUTES, SUPPORT_TIER, __version__  # noqa: E402
from par.config import DEFAULT_APPROVAL_MODE  # noqa: E402
from par.mcp.catalog import TOOL_NAMES  # noqa: E402
from par.providers.manifests import BUILTIN_MANIFESTS  # noqa: E402

FACTS = ROOT / "docs" / "product-capabilities.json"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
EN_DOCS = ("GETTING_STARTED", "USER_GUIDE", "INTEGRATIONS", "COMPATIBILITY", "SECURITY", "ARCHITECTURE", "TROUBLESHOOTING")
EN_HEADINGS = ("## Why PAR?", "## 60-second view", "## Choose how you connect", "## Try it in a few minutes (no account)",
               "## Your first safe task", "## What the Kernel actually guarantees", "## Compatibility and limitations",
               "## Documentation", "## Contributing, support and license")
ZH_HEADINGS = ("## 为什么需要 PAR", "## 60 秒概念图", "## 选择接入方式", "## 几分钟内试一遍（零账号）",
               "## 第一个安全任务", "## Kernel 实际保证什么", "## 兼容性与限制", "## 文档", "## 贡献、支持与许可")
FORBIDDEN_CLAIMS = ("all platforms supported", "every coding assistant", "production-grade",
                    "所有平台均支持", "所有 AI 工具自动", "生产级")
VERIFIED = ("VERIFIED_AUTO", "VERIFIED_COORDINATOR", "VERIFIED_MANUAL")


class Problem(Exception):
    pass


def load_facts() -> dict:
    try:
        return json.loads(FACTS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Problem(f"docs/product-capabilities.json cannot be read: {exc}") from None


def read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise Problem(f"missing file: {path.relative_to(ROOT)}") from None


def check_identity(facts: dict) -> None:
    if facts.get("version") != __version__:
        raise Problem(f"product-capabilities.version is {facts.get('version')!r}, code says {__version__!r}")
    if facts.get("release_status") != RELEASE_STATUS:
        raise Problem(f"release_status is {facts.get('release_status')!r}, code says {RELEASE_STATUS!r}")
    if facts.get("support_tier") != SUPPORT_TIER:
        raise Problem(f"support_tier is {facts.get('support_tier')!r}, code says {SUPPORT_TIER!r}")
    if list(facts.get("supported_routes") or []) != list(SUPPORTED_ROUTES):
        raise Problem(f"supported_routes are {facts.get('supported_routes')!r}, code says {list(SUPPORTED_ROUTES)!r}")
    runtime = facts.get("runtime") or {}
    if runtime.get("mcp_tools") != len(TOOL_NAMES):
        raise Problem(f"mcp_tools is {runtime.get('mcp_tools')!r}, catalog has {len(TOOL_NAMES)}")
    if runtime.get("default_approval_mode") != DEFAULT_APPROVAL_MODE:
        raise Problem(f"default_approval_mode is {runtime.get('default_approval_mode')!r}, code says {DEFAULT_APPROVAL_MODE!r}")
    if runtime.get("entry_point") != "par":
        raise Problem("runtime.entry_point must be 'par'")
    if runtime.get("third_party_dependencies") != 0:
        raise Problem("runtime.third_party_dependencies must be 0")
    if runtime.get("network_calls_from_src") is not False:
        raise Problem("runtime.network_calls_from_src must be false")


def workflow_jobs() -> "list[str]":
    text = read(WORKFLOW)
    if not re.search(r"^jobs:\s*$", text, re.M):
        raise Problem("tests.yml has no jobs: block")
    in_jobs = False
    found = []
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if in_jobs:
            if re.match(r"^\S", line) and not line.startswith("#"):
                break
            match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if match:
                found.append(match.group(1))
    return found


def check_ci(facts: dict) -> None:
    runtime = facts.get("runtime") or {}
    workflow = runtime.get("ci_workflow")
    if workflow != ".github/workflows/tests.yml":
        raise Problem("runtime.ci_workflow must name .github/workflows/tests.yml")
    declared = workflow_jobs()
    expected = runtime.get("ci_jobs") or []
    if sorted(declared) != sorted(expected):
        raise Problem(f"ci_jobs are {expected}, tests.yml declares {declared}")
    for job in declared:
        if job not in read(WORKFLOW):
            raise Problem(f"CI job {job} disappeared from the workflow")
    contributing = read(ROOT / "CONTRIBUTING.md")
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    spoken = words.get(len(declared), str(len(declared)))
    if f"{spoken} explicitly named" not in contributing and f"{len(declared)} explicitly named" not in contributing:
        raise Problem(f"CONTRIBUTING.md must say there are {spoken} explicitly named checks")


def check_adapters(facts: dict) -> None:
    adapters = {a["id"]: a for a in facts.get("worker_adapters") or []}
    if set(adapters) != set(BUILTIN_MANIFESTS):
        raise Problem(f"worker_adapters are {sorted(adapters)}, manifests are {sorted(BUILTIN_MANIFESTS)}")
    if adapters["codex"]["status"] != "VERIFIED_AUTO":
        raise Problem("codex must stay VERIFIED_AUTO only while its evidence holds")
    if adapters["antigravity"]["status"] != "EXPERIMENTAL":
        raise Problem("antigravity must be EXPERIMENTAL (terms risk, disabled by default)")
    if "prohibit" not in adapters["antigravity"].get("limitations", "").lower():
        raise Problem("antigravity limitations must mention the vendor terms prohibition")


def check_evidence(facts: dict) -> None:
    records = []
    for group in ("worker_adapters", "coordinator_clients", "manual_clients", "host_os"):
        records.extend(facts.get(group) or [])
    for record in records:
        status = str(record.get("status", ""))
        if status in VERIFIED or status.startswith("VERIFIED"):
            if not record.get("last_verified"):
                raise Problem(f"{record.get('id')} is {status} without last_verified")
            evidence = record.get("evidence_url")
            if not isinstance(evidence, str) or not (ROOT / evidence).exists():
                raise Problem(f"{record.get('id')} is {status} without an existing evidence_url")
    hosts = {h["id"]: h for h in facts.get("host_os") or []}
    if hosts["windows_native"]["status"] != "NOT_SUPPORTED":
        raise Problem("windows_native must be NOT_SUPPORTED")
    if hosts["windows_wsl2"]["status"] != "NOT_TESTED":
        raise Problem("windows_wsl2 must be NOT_TESTED")
    if hosts["linux"]["status"] != "CORE_EXPERIMENTAL":
        raise Problem("linux must stay CORE_EXPERIMENTAL until a full fake E2E run exists")


def check_readmes_and_pages(facts: dict) -> None:
    english = read(ROOT / "README.md")
    chinese = read(ROOT / "README.zh-CN.md")
    legacy = read(ROOT / "README.en.md")
    if "[简体中文](README.zh-CN.md)" not in english:
        raise Problem("README.md must link README.zh-CN.md as the Chinese page")
    if "[English](README.md)" not in chinese:
        raise Problem("README.zh-CN.md must link README.md as the English page")
    if "README.md" not in legacy or "README.zh-CN.md" not in legacy:
        raise Problem("README.en.md must point at both current pages")
    if "```mermaid" not in english or "```mermaid" not in chinese:
        raise Problem("both READMEs need the 60-second Mermaid diagram")
    missing = [h for h in EN_HEADINGS if h not in english]
    if missing:
        raise Problem("README.md is missing stable section(s): " + ", ".join(missing))
    missing = [h for h in ZH_HEADINGS if h not in chinese]
    if missing:
        raise Problem("README.zh-CN.md is missing stable section(s): " + ", ".join(missing))
    for page in EN_DOCS:
        path = ROOT / "docs" / "en" / f"{page}.md"
        if not path.exists():
            raise Problem(f"required English page is missing: docs/en/{page}.md")
    index = read(ROOT / "docs" / "INDEX.md")
    zh_index = read(ROOT / "docs" / "INDEX.zh-CN.md")
    for page in EN_DOCS:
        if f"en/{page}.md" not in index:
            raise Problem(f"docs/INDEX.md does not link en/{page}.md")
    if "INDEX.zh-CN.md" not in english or "INDEX.md" not in chinese:
        raise Problem("both READMEs must link their documentation index")
    if "INDEX.md" not in zh_index:
        raise Problem("docs/INDEX.zh-CN.md must link the English index")


def check_claims(facts: dict) -> None:
    english = read(ROOT / "README.md").lower()
    chinese = read(ROOT / "README.zh-CN.md")
    for phrase in FORBIDDEN_CLAIMS:
        if phrase in english or phrase in chinese:
            raise Problem(f"forbidden support claim present: {phrase!r}")
    for mode in ("VERIFIED_AUTO", "VERIFIED_COORDINATOR", "VERIFIED_MANUAL", "EXPERIMENTAL", "NOT_SUPPORTED"):
        if mode not in read(ROOT / "README.md") or mode not in chinese:
            raise Problem(f"READMEs must use the status word {mode}")
    if f"{len(TOOL_NAMES)} tools" not in read(ROOT / "README.md") or f"{len(TOOL_NAMES)} 个工具" not in chinese:
        raise Problem(f"READMEs must state the real MCP tool count ({len(TOOL_NAMES)})")
    if DEFAULT_APPROVAL_MODE not in read(ROOT / "README.md") or DEFAULT_APPROVAL_MODE not in chinese:
        raise Problem(f"READMEs must name the real default approval mode ({DEFAULT_APPROVAL_MODE})")
    security_en = read(ROOT / "docs" / "en" / "SECURITY.md")
    security_zh = read(ROOT / "docs" / "SECURITY.md")
    if "lease" not in security_en.lower():
        raise Problem("docs/en/SECURITY.md must document the coordinator lease")
    if "租约" not in security_zh:
        raise Problem("docs/SECURITY.md must document the coordinator lease")
    for page in (ROOT / "docs" / "en" / "COMPATIBILITY.md", ROOT / "docs" / "COMPATIBILITY.md"):
        text = read(page)
        if "NOT_SUPPORTED" not in text and "不支持" not in text:
            raise Problem(f"{page.relative_to(ROOT)} must state the unsupported host clearly")
    for page in (ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "docs" / "en" / "GETTING_STARTED.md"):
        if "/Users/" in read(page):
            raise Problem(f"{page.relative_to(ROOT)} contains an absolute user path")


def _cells(line: str) -> "list[str]":
    protected = re.sub(r"`[^`]*`", lambda m: m.group(0).replace("|", "\0"), line)
    return [part.replace("\0", "|") for part in re.split(r"(?<!\\)\|", protected)[1:-1]]


def check_tables(path: pathlib.Path) -> None:
    lines = read(path).splitlines()
    fence = False
    start = None
    columns = None
    for number, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if line.strip().startswith("|") and line.strip().endswith("|"):
            count = len(_cells(line))
            if start is None:
                start, columns = number, count
            elif count != columns:
                raise Problem(f"{path.relative_to(ROOT)}:{number}: table started on line {start} has {columns} columns, this row has {count}")
            continue
        start, columns = None, None


def check_package(facts: dict) -> None:
    pyproject = read(ROOT / "pyproject.toml")
    if 'readme = "README.md"' not in pyproject:
        raise Problem("pyproject.toml must publish README.md as the package readme")
    if 'par = "par.cli:main"' not in pyproject:
        raise Problem("pyproject.toml must keep the par console entry point")
    if "dependencies = []" not in pyproject:
        raise Problem("pyproject.toml must stay dependency-free at runtime")


def main() -> int:
    facts = load_facts()
    checks = (check_identity, check_ci, check_adapters, check_evidence, check_readmes_and_pages, check_claims, check_package)
    try:
        for fn in checks:
            fn(facts)
        check_tables(ROOT / "README.md")
        check_tables(ROOT / "README.zh-CN.md")
        check_tables(ROOT / "docs" / "en" / "COMPATIBILITY.md")
        check_tables(ROOT / "docs" / "COMPATIBILITY.md")
    except Problem as exc:
        print(f"docs_contract: FAIL: {exc}")
        return 1
    print("docs_contract: OK (version, release status, tools, adapters, CI jobs, evidence, bilingual pages, tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
