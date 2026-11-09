## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Scope

- [ ] Runtime code (`src/par/`)
- [ ] Tests
- [ ] Docs (English and/or Chinese)
- [ ] Packaging / CI / release

## Behaviour

<!-- What changes for a user or another component? Exact commands/output if relevant. -->

## Safety impact

<!-- Does this change permissions, path policy, redaction, what a tool may execute, or what a coordinator may decide? If yes, name the new tests and mutation entries. If no, say why not. -->

## Evidence

- [ ] `python3 -B -m unittest discover -s tests -t .` passes
- [ ] `python3 -B tests/tools/link_check.py` passes (if docs changed)
- [ ] `python3 -B tests/tools/docs_contract.py` passes (if product facts changed)
- [ ] `python3 -B tests/tools/mutation_check.py` passes for touched safety lines
- [ ] Package builds/installs (if packaging changed)
- [ ] Not run: <!-- list anything you could not verify and why -->

## Docs and compatibility

- [ ] English and Chinese facts agree
- [ ] `docs/product-capabilities.json` updated for a new/changed capability claim
- [ ] No `VERIFIED` label added without a dated run and evidence link

## Risk and rollback

<!-- What can this break? How is it reverted without deleting user data or rewriting the ledger? -->
