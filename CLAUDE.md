# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows-hosted control surface for slot-game simulators. The FastAPI backend
(`backend/`, port 8001) drives five local integrations — OBS Studio over
obs-websocket v5, a Virtual OLED button deck served by `OledPanelSvc.exe`, a
log-following screenshot recorder, Tesseract OCR over the frames OBS wrote, and
posted mouse clicks into the game's own Unity window — and the React dashboard
(`frontend/`, port 3001) is the UI for them. Every one is host-machine specific:
they talk to processes, windows and log files on the developer's own PC, not to a
network service.

The dashboard covers the first three, plus `features/games` for choosing the
active game and `features/roi` for cropping a configured region out of the
latest screenshot. `game-input` and `ocr` are backend-only so far — endpoints and
services with no `src/features/` slice — so don't go hunting for their UI.

`README.md`, `backend/README.md` and `frontend/README.md` are unusually detailed
and current; read the relevant one before changing an integration.

## Commands

`.\start.ps1` from the repo root launches both dev servers — the backend
**elevated** (Windows Terminal cannot mix integrity levels in one window, hence
two windows) and the frontend not. `-NoAdmin` gives one window with two tabs and
i-deck presses refused; everything else works. Read the comments in `start.ps1`
before editing it: the `wt` tab payloads must contain **no semicolons**, because
Windows Terminal splits its commandline on `;` after PowerShell has stripped the
quotes.

Backend commands **must run from `backend/`** — `app` is imported from the
working directory, not installed into site-packages.

```bash
cd backend
source .venv/Scripts/activate      # Git Bash; .venv\Scripts\Activate.ps1 in PowerShell
python -m app                      # run (reads PORT from .env)
pytest                             # tests
pytest tests/test_obs.py::test_disconnect_is_idempotent      # one test
pytest --cov
ruff check . && ruff format --check .
mypy                               # strict, over `app` only
```

```bash
cd frontend
npm run dev                        # http://localhost:3001
npm run check                      # lint + format:check + test
npx vitest run src/lib/api.test.js # one test file
```

Use `localhost`, not `127.0.0.1`, for the frontend — Vite binds the hostname,
which resolves to IPv6 `[::1]` on Windows.

## The response envelope is the contract

Every backend response — success *and* failure — is
`{ success, message, data, error, meta }`, with `data` null on failure and
`error` null on success. Both keys always present.

- Endpoints declare `ApiResponse[T]` as `response_model` and return
  `ApiResponse[T].ok(data=...)`. Wrapping is explicit, never middleware, so
  OpenAPI advertises the real shape.
- Errors are **never** constructed by hand. Raise an `AppException` subclass
  from `app/exceptions/base.py`; `app/exceptions/handlers.py` is the only place
  an error response is built. New error type = new subclass with
  `status_code` / `error_code` / `message`.
- `frontend/src/lib/api.js` unwraps the envelope; `api-error.js` normalises every
  failure into one `ApiError` with `fieldErrors` ready for form binding. Feature
  code never sees the envelope.
- The one exception: `GET /api/event-capture/runs/{id}/screenshots/{file}`
  returns a raw image, because an `<img>` src cannot unwrap JSON.
- `tests/asserts.py` (`assert_success`, `assert_failure`) enforces the shape;
  use it in new endpoint tests.

Adding a backend resource is three files plus one line: `app/schemas/<thing>.py`,
`app/services/<thing>.py`, `app/api/endpoints/<thing>.py`, then register in
`app/api/router.py`. Endpoints stay thin; logic lives in the service.

## Backend architecture

**Services are module-level singletons, not classes.** `obs.py`, `ideck.py`,
`event_capture.py`, `game_input.py` and `ocr.py` each hold their state (client,
lock, running run, cached engine) in module globals and expose a `reset()` that
`tests/conftest.py` calls autouse before and after every test. Callers import the
namespace, not the functions: `from app.services import obs as obs_service`. Only
`event_capture.reset()` is async — it has a watcher task to cancel.

**Settings are composed by inheritance.** `Settings` in `app/config/runtime.py`
inherits `AgentSettings`, `ObsSettings`, `IDeckSettings`,
`EventCaptureSettings`, `GameInputSettings` and `OcrSettings` (each in its own
`app/config/*.py`) while env var names stay flat — a new integration is a new
mixin, not a new settings object. `get_settings()` is `lru_cache`d and a
module-level `settings` instance is imported directly by services — so tests
override behaviour with
`monkeypatch.setattr(settings, "IDECK_GAME_CONFIG_DIR", tmp_path)` rather than
by building new settings. `app/core/config.py` is a re-export shim.

**Per-game data is separate from env config.** `app/config/game_config/games/<Game>.json`
ships with the code and carries the process name, log path, i-deck aliases,
OBS window source, ROIs, click targets and event rules.
`active_game.json` holds the current selection and is rewritten atomically by
`PUT /api/games/active` — no restart, no `.env` edit. Adding a game is adding a
JSON file.

**Foreign formats get their own `app/utils` module**, deliberately ignorant of
who consumes them: `panel_xml.py` (i-deck layout), `panel_log.py` (panel service
log), `game_log.py` (game log → named events, plus `DEFAULT_RULES`),
`log_tail.py` (rotation-aware cursor), `win32.py` (the only ctypes),
`ocr.py` (runs the Tesseract program and reads its TSV back),
`image_roi.py` (crops a named region out of a frame),
`click_target.py` (reads the `button_targets` block),
`paths.py` (resolves untrusted filenames inside a root — use it for anything
that came off the wire).

**Regions and click targets are fractions of the frame, never pixels.** `roi`
crops *out* of a captured image and `button_targets` aims a click *into* a window,
but both are `0.0..1.0` against the **whole frame, letterboxing included** — so a
resized simulator or a different capture resolution needs no re-measurement. Keep
that convention when adding either.

**`services/roi.py` and `services/ocr.py` are the same joinery, one step apart.**
Both resolve the active game's `roi` block against a frame; ROI returns the crop
and OCR hands it to Tesseract. Keep them apart — checking that a region is aimed
correctly must not require an engine install. ROI reads the newest file in
`settings.obs_dashboard_screenshot_dir` and never asks OBS for a frame, so the
same crop extracted twice is the same picture. It is also the one service holding
no state, so it has no `reset()` and nothing in `conftest.py`.

**Request correlation.** `RequestContextMiddleware` is registered last, so it
runs outermost. The id lives both in a `ContextVar` (for loggers and envelope
builders) and on the ASGI scope, because Starlette's `ServerErrorMiddleware`
runs after the context var is reset and unhandled 500s still need an id.

**Health is at the root**, not under `/api`. `/health` and `/health/live` always
200 with the verdict in the payload; only `/health/ready` fails (503). Register
a probe with `@register_probe` in the lifespan for anything the service cannot
work without — OBS is deliberately *not* one, since a closed screen recorder
should not mark the service unavailable.

**Database.** `app/services/database.py` opens an asyncpg pool in the lifespan
and does nothing else — no schema, tables or migrations. An empty or missing
`DATABASE_URL` disables it silently so local dev and tests run without a
database; when configured, a failed connection aborts startup.

**`app/agents/` is wired to nothing yet.** LangChain v1 + LangGraph v1 building
blocks — `build_chat_model`, `build_agent`, `new_workflow`/`compile_workflow`,
and sync/async `checkpoint_context` — imported by no service and no endpoint.
Treat it as the sanctioned shape for the first real agent, not as dead code.
Three rules it exists to enforce:

- **Nothing is constructed at import time.** No model, no database connection.
  The caller composes an agent in its service layer and owns invocation, thread
  ids and the checkpointer's lifetime, which keeps request lifecycles and
  background workflows independently testable.
- **`AGENT_ENABLED` is `false` by default** and `build_agent` raises
  `AgentDisabledError` until it is set. `.env.example` ships dummy credentials,
  so `is_placeholder_secret()` in `app/config/agents.py` screens values like
  `dummy-replace-with-real-key` and refuses to forward them to a provider or to
  LangSmith. Add a marker there rather than working around it.
- **Provider-neutral.** `AGENT_MODEL` uses LangChain's `provider:model`
  notation (`openai:gpt-4o-mini`, `anthropic:claude-sonnet-4-6`); only
  `langchain-openai` and `langchain-anthropic` are installed. A new provider is
  a new `langchain-<provider>` package, plus `AGENT_API_KEY_PARAM` if its
  constructor does not take `api_key`.

`AGENT_CHECKPOINT_BACKEND` is `memory` locally; `postgres` imports the Postgres
saver lazily inside the context manager and falls back to `DATABASE_URL` when
`AGENT_CHECKPOINT_URL` is empty. `recursion_limit` is invocation config, not a
`compile()` argument — it comes from `default_run_config()`.

## Test conventions that bite

- `pytest.ini` sets `filterwarnings = error`. A task cancelled but not awaited
  becomes a pending-task warning, which becomes a test failure — that is why
  `event_capture_service.reset()` is async and why services have no background
  reconnect task.
- `asyncio_mode = auto`: async tests need no marker.
- The `client` fixture uses `httpx.ASGITransport`, which **does not run the
  lifespan**. Startup work (the DB pool, OBS auto-connect) never happens in
  tests; anything that must be exercised has to be called directly.
- `raise_app_exceptions=False` on the transport, so handler output is asserted
  rather than the exception propagating.
- `mypy.ini` has `files = app` — tests are not strictly typed. It pins
  `python_version = 3.12` because numpy's stubs (via `opencv-python`) use syntax
  mypy rejects below it; ruff still targets `py311`.
- Ruff deliberately does **not** enable `TCH`: moving Pydantic/FastAPI imports
  into `TYPE_CHECKING` breaks runtime annotation resolution.

## Frontend architecture

Plain JavaScript, no TypeScript. Feature-first: each directory under
`src/features/` owns its `api.js`, hooks and components, so a feature deletes in
one directory. Shared plumbing is in `src/lib/`.

- Cache keys come from the `queryKeys` registry in `lib/query-keys.js` — never
  inline an array literal.
- react-query owns server state; Zustand (`store/ui-store.js`) owns only the
  client-side theme. Do not copy server data into Zustand.
- **Background polling is opt-in.** `useObsStatus` and `useIDeckStatus` default
  to `refetchInterval: false` with `staleTime: 30_000`; mutations invalidate the
  subtree, so an action still refreshes status immediately. Don't reintroduce a
  standing poll for these. `useCaptureStatus` in `features/event-capture/` is the
  reference for a functional `refetchInterval` — 2s while a run is live,
  `false` when idle, so no panel polls without a reason to. That slice also
  owns `captureImageUrl()`, the one fetch that bypasses `apiRequest`.
- `features/obs/` is still the reference slice for the query + mutate shape.
- Tailwind v4 has no `tailwind.config.js`; theming is CSS-first in
  `src/index.css` (`@theme inline` + `:root`/`.dark` oklch values).
- `src/components/ui/` is vendored shadcn output — excluded from Prettier and
  from the `react-refresh` lint rule. Add components with
  `npx shadcn@latest add <name>`.
- Dev is same-origin: leave `VITE_API_BASE_URL` empty and let the Vite proxy
  forward `/api` to the backend, so cookies work and CORS never applies.

## Windows-specific gotchas

- **i-deck presses need matching integrity levels.** Windows UIPI drops input
  sent from a lower integrity level to a higher one, so if `OledPanelSvc.exe` was
  launched elevated the backend must be too. `GET /api/ideck/status` reports
  `access_denied` instead of leaving you to guess.
- **Nothing moves the cursor.** Presses are `PostMessage`d as
  `WM_MOUSEMOVE` + `WM_LBUTTONDOWN` + `WM_LBUTTONUP`; the move first is required
  because SDL takes a click's position from the last motion event.
- **Presses are confirmed, not assumed.** Each one reads only what was appended
  to the panel log and returns 502 if the press did not land.
- **Take-win and gamble are not on the i-deck.** The panel has fourteen keys and
  neither is among them — the games' logs prove it, arriving as a `TouchMsg` from
  the glass. So `services/game_input.py` posts clicks into the simulator's Unity
  window. There is no API to ask politely with: Unity implements neither UI
  Automation nor MSAA, and GDK's GAF Thrift server (which has a literal
  `SimulateTouch`) is present in `Assembly-CSharp.dll` but not started by a
  normal launch. If it ever is, it belongs behind that module's public API.
- **A missed click is silent** — no error, no exception, just nothing. So each
  one records the game log's size first and reads only what was appended.
  A target's `confirm` key names a `game_log` event, which proves *which* button
  was hit; without one the fallback is `TOUCH_REGISTERED`, which proves only that
  input arrived. The two are reported apart, never blurred — don't collapse them.
- **OBS window selection needs the game running.** The window identifier is
  `title:class:executable` matched against the list OBS enumerates itself; one
  built from the process name alone binds to nothing and fails silently as a
  blank screenshot much later.
- **OBS state settles asynchronously.** Recording start/stop polls for the real
  state before answering rather than reading it immediately.
- **Tesseract is not on `PATH`.** Its Windows installer does not add it, so
  `app/config/ocr.py` discovers the binary where the installers put it and
  `OCR_TESSERACT_CMD` overrides that. OCR is optional like OBS: no engine means
  `GET /api/ocr/status` says `not_installed` and only reads fail.
- `obs-captured-files/` (screenshots, recordings, `event-capture/<run>/`) is
  gitignored output, not source.
