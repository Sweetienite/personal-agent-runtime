# Examples

A tiny, disposable project for trying PAR without touching anything real.

## Ordinary Git project

```bash
cp -R examples/ordinary-git-project /tmp/par-example
cd /tmp/par-example
git init -q -b main
git add -A
git -c user.name=demo -c user.email=demo@example.invalid commit -qm init
cd -
par project add example /tmp/par-example
par project show example
```

`app/calc.py` contains a deliberate bug (`add` subtracts). `tests/test_calc.py` is the acceptance
check. The project has no `AGENTS.md` or `.ai/STATE.md`; generic context is the default.

Then use the strict contract in [task-contract.json](task-contract.json), replacing `example` only
if you registered a different id:

```bash
par task submit --contract examples/task-contract.json
par task approve example-0001
par task run example-0001
par task result example-0001
par task diff example-0001
```

With a real Codex worker this spends subscription quota. With a manual-only profile
(`provider = "manual_handoff"`), use the hand-off flow in
[docs/en/GETTING_STARTED.md](../docs/en/GETTING_STARTED.md#4-a-complete-manual-hand-off-with-no-account)
instead of `run`.

`accept` records the result; nothing is applied. Apply the patch yourself only after reviewing it:

```bash
par task diff example-0001 > change.patch
git -C /tmp/par-example apply --check change.patch
git -C /tmp/par-example apply change.patch
```

## Expected outcome

- `REVIEW_REQUIRED` with one Kernel check (`unit`) passed.
- A one-file, one-line diff (`- return a - b` → `+ return a + b`).
- `applied_to_project=false`; the real project stays clean until your `git apply`.
- A manual hand-off exports `mode: workspace`, a private copy and `PROMPT.md`; importing the wrong
  folder or a stale packet is refused.

## What is not included

No credentials, no provider configuration, no real project data and no automatic deployment. The
example is deliberately too small to be useful as a product; it is only a safe first run.
