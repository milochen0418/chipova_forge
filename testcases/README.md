# E2E testcases

Playwright end-to-end suites, one per folder:

- `testcases/<suite_name>/run_test.py`
- `testcases/<suite_name>/output/` (screenshots and logs, ignored by git)

Each `run_test.py` exits `0` on success and non-zero on failure, and writes its screenshots into
`OUTPUT_DIR` (provided by the runner). Shared helpers are in `common.py`: a browser tab per person
(a guest, or signed in with the platform's cookie signed by the test secret), making and opening
products, switching sections, waiting for a row's status, calling Forge's AI tools as the AI Hub
does, and the REST API with a token.

Every run starts its own server with its own data folder (`FORGE_HOME=$OUTPUT_DIR/home`, fresh
each time), so your products are never touched. Builds and simulations are real containers on
your Docker, so Docker must be running with the simulation and board images installed
(`chipova-forge/sim:1` and `chipova-forge/board:1`, see the main README). The runner sets `DDNS_AUTH_SECRET` to a test value
and `SERVICE_SUBDOMAIN=chipova-forge`.

## Suites

| Suite | What it checks |
|-------|----------------|
| `smoke_home` | Each product template makes a working product: the twelve steps, the chip's ports and SPI interface, its registers in sync with its RTL, the board diagram and pins, the firmware in the editor, the tests and the release checks; the bring-up board puts the Heartbeat (no bus) in the register harness; the home page lists both. |
| `lab_flow` | Two guests on one product: one starts the simulation, both see the board start, the alarm at 40 °C on the console and the red LED; the chip's COUNT register reads 1 over SPI; a console command; the chip's waveform is drawn; the App page's threshold moves the tiles; stopping leaves evidence that downloads. |
| `release_review` | Two signed-in people: the board is laid out from the Board page, routed and clean (ERC, DRC, the board matching the schematic) with pictures and Gerbers; both system tests pass from the Tests page; the checks pass and the release is READY; its maker can't approve it; the owner makes the other a reviewer and closes the link; an outsider is refused on the page and on the REST API; the reviewer approves and the package downloads with its manifest and evidence. |
| `analog_loop` | The enclosure fan controller: the Overview names its analog side; in the Lab ngspice runs in step with the board, the enclosure warms past the threshold, the console says ALARM, the fan comes on, it cools, NORMAL, the fan goes off, and again; the analog inputs replace the sensor's slider and a 0 W load lets it cool with the fan off; its system test passes; the module carries the fan's MOSFET and connector, clean in KiCad's checks. |
| `ai_api` | An AI through the AI Hub's tool endpoint reads, builds, runs the board, warms the sensor and reads a register; its proposals are rejected with a reason it reads, then accepted; it can't read outside the project; its release can't be approved by it; its calls are in the audit log. The Python SDK with the person's token: status, threshold, events, a register, a refusal. |

## Running

```bash
poetry run ./run_test_suite.sh smoke_home
poetry run ./run_test_suite.sh lab_flow
poetry run ./run_test_suite.sh release_review
poetry run ./run_test_suite.sh ai_api
poetry run ./run_test_suite.sh analog_loop
```

If ports 3000/8000 belong to another app, use others: `FRONTEND_PORT=3320 BACKEND_PORT=8320`.
Run one suite at a time: the runner stops whatever holds its two ports when it ends.

The unit and container tests are pytest: `poetry run pytest -m "not docker"` (seconds) and
`poetry run pytest -m docker` (about a minute, real builds and simulations).

Optional env vars:
- `BASE_URL` (default `http://127.0.0.1:3000`) and `BACKEND_URL`
- `HEADLESS=0` to watch the browser
- `PW_SLOWMO_MS=100` to slow actions down
- `PW_TIMEOUT_MS=60000` to change Playwright timeouts
