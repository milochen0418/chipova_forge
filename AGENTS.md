# AGENTS.md

How an AI coding agent (and humans) work in this repository.

## 0) Read the docs first

Before changing anything or running commands, read the README files and follow them:

- Overview and workflow: `README.md`
- How it is built: `docs/architecture.md`
- What is done and what isn't: `docs/status.md` (update it when you change what Forge can do)
- The browser suites: `testcases/README.md`

## 1) Python 3.11 with Poetry

```bash
poetry env use python3.11
poetry install
```

The venv is in-project (`.venv`). Run scripts with `poetry run <command>`.

## 2) Running the app

```bash
poetry run ./reflex_rerun.sh
```

It uses ports 3000 and 8000 and frees them by force. Other apps on this machine may hold them:
check `lsof -nP -iTCP:3000 -iTCP:8000 -sTCP:LISTEN` first and use other ports when they are
taken (`REFLEX_API_URL=http://localhost:8310 poetry run reflex run --frontend-port 3310 --backend-port 8310`).
Run one Forge server per checkout at a time: two share `.web/` and overwrite each other's build.

Builds and simulations need Docker and the images `chipova-forge/sim:1` and
`chipova-forge/board:1` (`infra/dockerfiles`).

## 3) Tests

```bash
poetry run pytest -m "not docker"
poetry run pytest -m docker
FRONTEND_PORT=3320 BACKEND_PORT=8320 poetry run ./run_test_suite.sh <suite>
```

After a change, run the closest tests: the unit tests always, the container tests when builds,
sessions, the agent or the PCB change, and the related browser suite when a page changes.

## 4) Rules of the code

- Anything that runs inside a container (`chipova_forge/sessions/agent/*`, the `chipova_forge/pcb`
  modules but `netlist.py`) uses the standard library only.
- Containers get their inputs copied in and their outputs copied out; never mount host folders.
- An AI never edits files, decides proposals or approves releases (`domain/roles.py`); keep it so.
- Every change, run, tool call and refusal goes to the audit log.
- Keep the change set small; say in `docs/status.md` what changed in what Forge can do.
