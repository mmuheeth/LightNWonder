# LightNWonder

Monorepo scaffold: FastAPI backend, React + Vite frontend.

| Part                     | Stack                                                                      | Port |
| ------------------------ | -------------------------------------------------------------------------- | ---- |
| [backend/](backend/)     | Python, FastAPI, LangChain, LangGraph, uvicorn, pydantic v2                 | 8001 |
| [frontend/](frontend/)   | React 19, Vite, Tailwind v4, shadcn/ui, react-query, Zustand (plain JS)     | 3001 |

Each side has its own README with the detail:
[backend/README.md](backend/README.md) · [frontend/README.md](frontend/README.md)

## Quick start

Two terminals.

**Backend**

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); .venv\Scripts\Activate.ps1 on PowerShell
# source .venv/bin/activate        # macOS / Linux
pip install -r requirements-dev.txt
cp .env.example .env
python -m app
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:3001**. The dashboard lets you choose the active game
and control the OBS and Virtual OLED integrations.

> Use `localhost`, not `127.0.0.1`, for the frontend: Vite binds the hostname
> `localhost`, which resolves to IPv6 `[::1]` on Windows.

| URL                            | What                        |
| ------------------------------ | --------------------------- |
| `http://localhost:3001`        | Web app                     |
| `http://localhost:8001/health` | Health report               |
| `http://localhost:8001/docs`   | Swagger UI (hidden in prod) |
| `http://localhost:8001/api/…`  | API endpoints               |

In development the frontend makes **relative** requests and Vite proxies `/api`
to the backend. Dev is therefore same-origin: cookies work and CORS never
applies.

## Checks

```bash
cd backend  && pytest && ruff check . && ruff format --check . && mypy
cd frontend && npm run check          # lint + format:check + test
```

## The response contract

This is the part worth reading before writing code. **Every** backend
response — success or failure — has the same five top-level keys:

```json
{
  "success": true,
  "message": "Game selected successfully",
  "data": { "game": "FortuneOx" },
  "error": null,
  "meta": { "request_id": "3f9a1c8e…", "timestamp": "2026-08-18T09:12:44.512Z" }
}
```

On failure, `data` is `null` and `error` is populated:

```json
{
  "success": false,
  "message": "Request validation failed",
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "details": [{ "field": "body.name", "message": "Field required", "type": "missing" }]
  },
  "meta": { "request_id": "3f9a1c8e…", "timestamp": "2026-08-18T09:12:44.512Z" }
}
```

Both keys are always present, so clients never branch on key existence, and
`success` always mirrors the HTTP status. List endpoints keep the same shape and
add pagination to `meta`.

**Backend** — declare the envelope as the `response_model` and raise typed
exceptions; handlers render every error:

```python
@router.get("/status", response_model=ApiResponse[StatusOut])
async def get_status() -> ApiResponse[StatusOut]:
    return ApiResponse[StatusOut].ok(data=service.get_status())

# elsewhere — never build an error response by hand
raise NotFoundError("The requested resource was not found")
```

**Frontend** — `lib/api.js` unwraps the envelope and `lib/api-error.js`
normalises every failure into one `ApiError`, with `fieldErrors` ready to bind
to form inputs.

Every request also carries an `X-Request-ID` end to end: the browser generates
one, the backend adopts it, echoes it in `meta.request_id`, and stamps it on
every log line for that request. That id is the fastest way to connect a UI
error to its server-side cause.

## Where things live

```
backend/app/
├── api/            router.py, health.py, endpoints/   ← routes only, kept thin
├── schemas/        response.py holds the envelope     ← the contract
├── exceptions/     base.py hierarchy, handlers.py     ← the only error renderer
├── services/       business logic (games.py, obs.py, ideck.py, event_capture.py)
├── config/         game data that ships with the code, and its reader
├── utils/          win32 interop, panel/game log formats, log tail, safe paths
├── middleware/     request id, timing, access log
└── core/           settings, logging, request context

backend/obs-captured-files/    OBS screenshots and recordings (gitignored)
└── event-capture/      one folder per capture run: images + run.json

frontend/src/
├── features/       one directory per feature (api + hooks + components)
├── lib/            http, api, api-error, query client/keys
├── components/     ui/ (shadcn), layout/, shared pieces
└── store/          Zustand UI state
```

## OBS Studio

The dashboard's **OBS Studio** card drives a local OBS instance over
[obs-websocket](https://github.com/obsproject/obs-websocket) v5 — connect,
capture a screenshot, and start/stop/pause a recording. OBS Studio 28+ bundles
the plugin; enable it under *Tools → WebSocket Server Settings*.

The integration is backend-owned: the password lives in `backend/.env` and never
reaches the browser, which only ever talks to `/api/obs/*`. Screenshots and
recordings default to `backend/obs-captured-files/`; each request can use a relative
use-case subfolder, and screenshot/recording roots can be configured separately.

When OBS connects, the active scene's window-capture source is pointed at the
`process` from the active game config, resolved against the window list OBS
itself enumerates — so the game must be running with a visible window. The
dashboard also exposes a “Select game window” action for retrying after
launching the game, changing scenes, or starting OBS. If a scene contains
multiple window-capture sources, the selected game's JSON can disambiguate them
with `obs.window_source`.

It is **optional and off by default** — the app boots and stays healthy with OBS
closed, and a dropped connection re-establishes itself on the next request. See
[backend/README.md](backend/README.md#obs-studio) for the endpoints and the
settings.

## Event Based Capture

The dashboard's **Event Based Capture** card watches the active game's own log
and takes an OBS screenshot the moment something happens -- a spin, the reels
landing, a bet or denomination change, a win, a gamble offer. Press *Start
tracking*, play, press *Stop tracking*; everything in between becomes one record
under `backend/obs-captured-files/event-capture/<date>_<time>/`: the screenshots plus a
`run.json` naming each event, the values pulled out of its log line, and the
image taken for it. The **Captures** page replays a run as screenshots paired
with their event details.

The log-reading half is deliberately generic. `app/utils/game_log.py` turns log
lines into named events and knows nothing about OBS or capture runs, so anything
else that wants to react to gameplay can reuse it.

The shipped rules cover the visual events both games log -- the spin cycle,
bets and denominations, gamble from offer to result, free spins, bonuses and
progressives, help and attract, service and lockups -- but not every message
the log names: the internal bookkeeping between one visible change and the next
gets no rule at all.

Being a visual event is not the same as being worth a screenshot, so each rule
also says whether to capture it. A run is made of the moments somebody opens it
for -- `spin-started`, `reels-stopped`, `bet-changed`, `denomination-changed`,
`win-collected`, the gamble and feature events -- while the ones that happen
constantly or land on a frame another event already took, such as the credit
meter ticking or attract cycling to its next scene, are recognised and skipped.
Anything game-specific is declared in that game's JSON:

```json
"events": {
  "rules": [
    { "event": "jackpot-hit", "pattern": "MoneyLinkOutroSM.*stateStarted",
      "capture": true, "only_on_change": false }
  ],
  "disable": ["win-collected"]
}
```

Starting **requires OBS**, and says so rather than quietly producing a run with
no images. A screenshot that fails once the run is going is recorded against
that event and the run carries on. See
[backend/README.md](backend/README.md#event-based-capture) for the endpoints,
the rule format and the settings.

## Virtual OLED i-deck

The dashboard's **i-deck** card presses the emulated button deck of the
currently selected game. Choose the active game from the dashboard selector;
the deck is an SDL window served by
`OledPanelSvc.exe`, which exposes no API, so presses are delivered as mouse
messages posted straight to that window. **Your cursor never moves.** Button
names and game-specific details come from the selected file in
`backend/app/config/game_config/games/`.

Key positions are read from the panel's own layout file rather than hardcoded,
and every press is confirmed against `C:\logs\OledPanelSvc.log` before the
request succeeds — a press that did not land returns a 502 rather than a
cheerful lie.

One gotcha worth knowing up front: Windows refuses input sent from a lower
integrity level to a higher one, so **if the panel was launched elevated, the
backend must be started elevated too**. The card says `access_denied` when that
is the case. See
[backend/README.md](backend/README.md#virtual-oled-i-deck) for the endpoints and
the settings.

## Reading text (OCR)

Reads the game's meters off a frame — the regions the selected game declares in
its `roi` block, turned into text and numbers by
[Tesseract](https://github.com/UB-Mannheim/tesseract/wiki):

```
GET  /api/ocr/status     the engine, its version and languages
GET  /api/ocr/regions    what can be read, and the options each is read with
POST /api/ocr/read       read regions off the screen now, or off a capture run
```

Tesseract is an external program, so it is installed per machine
(`winget install --id UB-Mannheim.TesseractOCR`) rather than pinned in
`requirements.txt`. **Its installer does not put it on `PATH`**, so the backend
looks where the installers actually put it and reports what it found; set
`OCR_TESSERACT_CMD` only for an install somewhere else. Nothing fails without an
engine — the service starts, health stays green, and only a read is refused.

Every reading carries the engine's confidence and the exact options that produced
it, because the same crop can read perfectly at one page-segmentation mode and
come back as punctuation at another. Options layer: `OCR_*` in `.env`, then the
game config's per-region `ocr` block, then a request's own overrides for one read
— which, pointed at a screenshot a capture run already took, is how a region gets
tuned against a frame that does not move. See
[backend/README.md](backend/README.md#reading-text-ocr) for the options, the
tuning loop and the failure codes.

## Notes

- Health is mounted at the backend **root** (`/health`, `/health/live`,
  `/health/ready`), deliberately outside the `/api` prefix, so probes never
  depend on the API's base path.
- `ENVIRONMENT=production` hides `/docs`, `/redoc` and `/openapi.json`.
- Backend commands must run from `backend/` — `app` is imported from the working
  directory rather than installed into site-packages.
- Tailwind v4 has no `tailwind.config.js`; theming is CSS-first in
  `frontend/src/index.css`.
