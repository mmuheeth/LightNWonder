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
├── utils/          win32 interop, panel/game log formats, log tail and
│                  backwards log search, the game's own math and geometry XML,
│                  safe paths
├── middleware/     request id, timing, access log
└── core/           settings, logging, request context

backend/dataset/              symbol artwork to train on, one folder per code
                              (gitignored -- large, and not reproducible from here)

backend/obs-captured-files/    OBS screenshots and recordings (gitignored)
├── event-capture/      one folder per capture run: images + run.json
├── classifier/         model.pt, metrics.json, samples/ (one per symbol)
└── grid/               one folder per split frame: reels.png + tiles/r1c1.png…
    ├── paylines/       the annotated reels, one picture per line set checked
    └── classifier/     symbols.png -- the reels with each tile's name on it

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
image taken for it. The **Event Capture** page replays a run as screenshots paired
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
integrity level to a higher one, so because the panel is launched elevated, **the
backend must run elevated too**. The card says `access_denied` when that is the
case. You are a standard user without "Run as administrator", but a
child process inherits its launcher's integrity — and on these managed
workstations BeyondTrust/Avecto silently elevates **VS Code**. So **start the
backend from the VS Code integrated terminal** and it inherits High integrity with
no prompt (this is exactly how the sibling GameplayScript app works). `.\start.ps1`
run from that terminal does the same via `Start-Process`; a Windows Terminal tab
would *not* (it is hosted by the separate Medium `wt` broker, not your elevated
shell). Confirm from the i-deck card / `GET /api/ideck/status` (`ready` vs
`access_denied`); if it is refused, use `.\start.ps1 -Elevate`. Full detail in
[docs/elevation.md](docs/elevation.md). See
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

## Reel grid

The dashboard's **Reel grid** card crops the reels out of the latest screenshot
and divides that crop into the individual symbol positions, as a matrix:

```
GET  /api/grid/layout    how many reels, how many rows, and the frame in hand
POST /api/grid/split     split one frame into tiles, and write them out
```

The shape comes from the selected game's config — `roi.reels` says where the
reels are, and `reel_bounds` says where each reel and row sits inside that crop.
**The two are measured against different rectangles**: `roi.reels` is fractions
of the game window, `reel_bounds` fractions of the crop, which is what keeps them
independent when the reel window moves or a sixth reel appears.

Fractions of the *game window* rather than of the frame, because OBS writes every
frame at its canvas size and fits the window capture inside it — so a portrait
simulator arrives with black bars whose width changes the moment the window is
resized. The bars are trimmed before a region is resolved, and every split
reports the rectangle it found, so one config works at any window size. See
[backend/README.md](backend/README.md#the-content-box).

Tiles are named `r1c1`…`r3c5`, 1-indexed and row-major, so `r1c1` is the top
symbol of reel 1. An optional `reel_bounds.inset` trims a fraction off each tile
edge, which is what removes the frame a game draws inside a reel when it
highlights a win — a request can override it for one split, so the number can be
found against a fixed frame before it is written into the config.

Every tile comes out the same number of pixels: each keeps its own rounded
position but they all share one width and height, because rounding each tile's
edges independently varies them by a pixel and a grid of unequal tiles cannot be
stacked or batched.

Unlike an ROI extraction the split is kept: the crop and every tile are written
to `obs-captured-files/grid/<frame>/`, one folder per source screenshot, because
fifteen tiles are the input to whatever looks at symbols next rather than
something to glance at. See
[backend/README.md](backend/README.md#splitting-the-reels-into-a-grid) for the
bounds format, the output layout and the failure codes.

## Payline check

The dashboard's **Payline check** card takes a split the Reel grid card already
wrote and says which of the game's winning patterns pay on it:

```
GET  /api/paylines/layout    the line sets the game declares, and the split in hand
POST /api/paylines/check     evaluate one set against one split, and draw the result
```

The patterns come from a `paylines` block in the selected game's config, keyed by
bet configuration — a cabinet playable for 5, 20 or 40 lines ships all three, and
one set is checked at a time. A position is `[row, column]`, 1-indexed, in the
same numbering the tiles are named in, so `[2,1]` is `r2c1`.

**Two tiles count as the same symbol when their cosine similarity clears a
threshold.** Cosine and not an exact match because a slot game glows, pulses and
scales its symbols continuously, so the same pot of gold on two reels is never
the same pixels — treating brightness as vector length rather than direction is
exactly the invariance wanted.

**That threshold is higher than it sounds.** Pixel channels are non-negative, so
the measure does not start at zero for unrelated pictures: on FortuneOx's own
captures two *different* symbols score 0.60–0.89 and two crops of the *same*
symbol score 0.967 and up. The separation is wide and clean but it sits well
above 0.7, so a generous-sounding cut calls everything a match. Every check
reports the distribution it measured — `matched_min` and `rejected_max` are the
two numbers a working cut sits between — so tune `PAYLINE_MATCH_THRESHOLD`
against a fixed split rather than by intuition.

**A line is read left to right and stops at the first pair that differs**, so
`pays` is the length of the *leading* run: three matching reels behind a break
pay nothing, and a line whose first two reels differ pays 0. Every adjacent pair
is scored anyway and reported with two flags — whether it matched, and whether
the left-to-right read got that far — because a real match on a line that pays
nothing is exactly the case a single flag would misrepresent.

The dashboard shows every paying line as its own box rather than a
comma-separated sentence, the run's statistics, and the per-line evidence behind
a dropdown each — which is where each line's own tracking picture is, paying or
not, with a green ring on every confirmed tile and (on that picture only) a red
ring on the tile where its run stopped. A combined picture of every *paying*
line is also written to `obs-captured-files/grid/<frame>/paylines/<set>.png`,
beside the tiles it was computed from — green rings, no red, since that picture
is several lines at once. A pay count cannot be checked by reading it, which is
why a picture comes back with every line. See
[backend/README.md](backend/README.md#checking-the-paylines) for the block
format, the threshold, and the failure codes.

## Image Classifier

Names the symbol on a reel tile **from the picture**. Nothing else here does: the
payline check only asks whether two tiles match *each other*, and the reel stops
read the answer out of the game's own log. So this is the one reading that can
disagree with the game, which is the only kind that could catch a reel drawing
the wrong symbol.

A network transfer-learned on the artwork under `backend/dataset/` (one folder per
two-letter symbol code), then read back against the tiles the reel grid already
wrote. Its own tab, `/image-classifier` — and, since it started grading spins,
the reading [Analyze Spin](#analyze-spin) rests on rather than a page of its own.

**Two engines, both kept at once.** ResNet34 (21.8M parameters, the default) and
EfficientNet-B0 (5.3M) share every transform, so only the backbone differs — and
each has its own checkpoint, so training one leaves the other alone. Train and
classify each take an optional `architecture`, and the page has a picker on both
cards. The point is comparison: two independently-fitted networks agreeing about
a tile is worth more than one of them being confident. ResNet34 leads because it
is the one that reads a real split better here — it names all fifteen tiles of the
reference split correctly.

```bash
# what can be trained on, and what is wrong with it
curl localhost:8001/api/image-classifier/dataset
# fit a model -- minutes on CPU; returns as soon as it is under way
curl -X POST localhost:8001/api/image-classifier/train -d '{}' -H 'Content-Type: application/json'
# or the other engine
curl -X POST localhost:8001/api/image-classifier/train -d '{"architecture": "resnet34"}' -H 'Content-Type: application/json'
# follow it, and see which engines are trained
curl localhost:8001/api/image-classifier/status
# name the tiles of the newest split
curl -X POST localhost:8001/api/image-classifier/classify -d '{}' -H 'Content-Type: application/json'
```

Three things are worth knowing before reading a result.

**The artwork is not what the classifier sees, and putting the background back is
the whole feature.** All but one of the symbols ship as transparent cut-outs with
no background at all, while the reel grid writes tiles where the symbol already
sits on the game's dark purple field. Train on the cut-outs directly and the
network learns that a symbol sits on *nothing* -- which is how an earlier
similarity-based attempt came to score 0.35-0.44 on the picture symbols and miss
the card symbols entirely. So every training picture is composited onto a
synthesised reel cell whose colours are measured off the 600 tiles already on
disk: field `rgb(35, 0, 56)`, a gold divider sliver at one edge 40% of the time,
black 2% of the time. One sample per class is written to
`obs-captured-files/classifier/samples/` so this can be checked by looking, and it
is the first thing to look at if accuracy disappoints.

**A tile below the confidence floor is an answer, not a gap.** FortuneOx declares
eighteen symbol codes and the artwork does not cover all of them, so the model is
regularly shown something it has no class for. A softmax cannot say "none of
these", only spread its mass, so `CLASSIFIER_MIN_CONFIDENCE` is where that gets
decided: the tile comes back with `symbol: null`, `label: "unknown"` and **its
ranked candidates still attached**, because a rejection with no numbers behind it
is not checkable. `GET /dataset` reports which codes still have no artwork.

The default is **0.90**, deliberately far stricter than where the classes actually
separate — measured, the widest empty band in the distribution sits around
0.56-0.68, so a much lower floor would still name every symbol on the reference
split correctly. At 0.90 a correct reading is rejected whenever the model is only
fairly sure, and that is the intended trade: the grids mean *the model was sure*,
while the per-tile table shows the leading candidate and its percentage anyway,
greyed with the figure in red. Two different questions, answered separately.

**Accuracy is two numbers and they are never averaged.** A class here is an
animation *loop* of 48 near-identical frames, so no split of it is honestly
unseen. `frame_holdout_accuracy` is over frames held out of the middle of the loop
-- not the end, because the loop closes and its last frame is a near-duplicate of
its first -- and `frame_holdout_leakage` says how unlike the training frames those
held-back ones actually were. `augmented_accuracy` covers all nine classes but
re-augments the training pictures, so it measures robustness to the augmentation
rather than generalisation. The five classes holding a single picture contribute
to the first number not at all, and the payload says so.

On the reference split `2026-08-24_14-16-16_002_spin-stop` ResNet34 reads all
fifteen tiles correctly; ten clear 90% (92.6-98.7%) and five do not — the two Arm
Bands at 88.5% and 89.3%, and the three Orbs at 43-58%, which are correct
identifications the floor declines to commit to.

## Game Config

The **Game Config** tab shows the maths the running game has actually loaded.
The game writes a `paytableId` to its own log on every denomination change
(`FortuneOx-1101YX-1c-90`), that string is byte-identical to a directory under
the game's installed `GameConfig` folder, and that directory's `math.xml` is the
symbols, the reel strips and the combos that pay.

The page is that join, and it shows its working: the id leads, with the log line
and timestamp it was read out of one click away under **Where this came from**,
because a page showing the wrong maths is a stale log or a hand-picked id and
only saying which lets you tell. Four cards, read down: **Current paytable**
(the id, its return, the denomination in play and the ones it can move to),
**Win geometry** (which payline set is live, each line drawn on the reels it
runs across), **Payline combos** (a row per symbol, a column per run length --
a paytable poster), and **Reel strips** (every stop in order, one column per
reel).

The API is fuller than the page: it also carries the symbol table it draws those
names from and the scatter/feature awards, both readable at
`GET /api/paytable/`.

Two things are worth knowing before reading it:

- **A symbol row joins two files.** What each code *is* -- what it pays, how
  much of the reels it occupies, whether it substitutes or scatters -- is read
  from `math.xml`. What it is *called* comes from the `symbols` block of
  `backend/app/config/game_config/games/<Game>.json`, because these files carry
  no display text in any element: `WC` becomes "WILD" there or nowhere. Reel
  counts come from `<ReelStripList>`, so a code the symbol set merely declares
  is listed with zeros rather than dropped -- it is named and it awards, it just
  cannot land in this paytable.
- **The line count comes from the paytable, not the maths.** The same `math.xml`
  ships in folders that play 5, 20 and 40 lines, so `gameConfig.cfg`'s
  `NumberOfLines` picks which set of `winGeometry.xml` is in play. The card says
  which file answered.

The dropdown at the top reads any other paytable the game ships without the game
running on it. See
[backend/README.md](backend/README.md#the-loaded-paytable-game-config) for the
endpoint, the failure codes and the file formats.

## Analyze Spin

The **Analyze Spin** tab is every other feature in one press. It starts a
recording, screenshots the machine at rest, spins it on the i-deck, follows the
game's own log until the reels stop, finds out whether anything was won, takes
the win if there was, screenshots each of those moments, stops the recording —
and then grades what it collected. Two screenshots on a losing spin, three on a
winning one.

Progress arrives over a WebSocket, so the page shows the sequence happening
rather than a spinner: every step of the run exists from the first frame, and one
that fails says so where it stands, carrying the error the equivalent direct
request would have given. A step that was deliberately not run — take-win, on a
spin that won nothing — reads as *skipped*, which is a different fact from never
having got there.

Then three readings over what it collected, deliberately independent so none can
fail another:

- **The cash meter**, read off every screenshot the run took, with the
  arithmetic between them checked: the bet came off the balance, the win
  registered, the win went onto the balance when it was collected, the win cell
  cleared. Each check shows what it expected, what it read and the sum it did,
  because a failing one is nearly always one misread digit and the numbers are
  the answer.
- **The symbols on the reels**, from the [Image Classifier](#image-classifier).
  The result screenshot's reels are split and every tile is named with its symbol
  code, or left blank when nothing cleared the confidence floor. Shown as two
  matrices — codes and display names — beside the reels with the named cells
  ringed, and with every blank tile's leading candidate and its probability, so a
  rejection is arguable rather than a hole.
- **The paylines**, checked against the lines the *running game* declares —
  its own `winGeometry.xml`, reached through the paytable its log named, not the
  hand-copied block in this repo's game config.

  Two judgements, and which does what is the point. **The picture decides what
  landed**: a line's run is the leading stretch of positions the classifier named
  with the same code, so nothing about the win comes out of the log — a checker
  that read the answer there would agree with the game by construction and could
  never catch a reel drawing the wrong symbol. **The paytable decides whether it
  pays**: a run of two of a symbol whose row starts at three is a real run and no
  win, and it says so rather than being shown as a pay. Because the symbol is
  *named*, an award is one combo and one number out of the game's own maths.

  Two earlier readings were replaced by that one. **Cosine similarity between
  tiles** measured a run without ever naming it, so an award stayed a range of
  every row paying at that length; it is still what the standalone
  [Payline check](#payline-check) panel uses. **The game's logged reel stops** named the
  symbols by agreeing with the game, which is exactly what a checker must not do.
  Both are still in the tree and neither takes part here.

And on a run that was **recording** and that **won**, one more thing, filmed
rather than read: each reel position on its own, for five seconds of exactly the
window the win presentation is on screen -- between the result screenshot and the
take-win click. They are laid out on the page the way the reels are, playing
themselves, because *which cells the game animated* is the question, and it is
only legible when they sit where they sat on the glass.

They are deliberately **not a step of the sequence**. Nothing is graded by them,
so a failure to film shows up in the run's errors rather than as a fourteenth row
that can go red -- the spin did not fail because OBS would not answer a
screenshot. They are also the one thing here built out of screenshots rather than
video: obs-websocket offers no stream, so a clip is frames taken as fast as OBS
answers and written back at the rate that was actually achieved, which is why the
card reports the measured rate beside the one that was asked for.

Which of the two trained networks reads the reels is a **per-run choice**, offered
as a dropdown between the spin button and the record toggle: ResNet34 by default,
EfficientNet-B0 the alternative. Both stay trained at once and they do not read
the same split equally well, so putting one spin through each and comparing is
worth more than trusting either alone.

Last on the page, and the only card that is a *check* rather than a reading: the
credits those paylines came to, converted through the line count and the
denomination, against the WIN cell OCR read off the glass. Every step of the
conversion is listed, because a wrong verdict is nearly always one of them rather
than the pay itself.

Two numbers worth tuning. `ANALYZE_SPIN_WIN_WAIT_SECONDS` first: there is no log
line saying a spin lost, so a loss is proven by the win meter's count-up *not*
arriving — set it below the longest count-up the game animates and a win comes
back as a loss. Then the pass threshold, `ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE`
(**0.85**, against the classifier page's own 0.90): a payline through an unnamed
tile **stops there**, since "I could not tell" twice is not a run — so a spin that
plainly paid and reports nothing is a threshold question first. See
[backend/README.md](backend/README.md#analyze-spin) for the endpoints, the step
list and the failure modes.

## Notes

- Health is mounted at the backend **root** (`/health`, `/health/live`,
  `/health/ready`), deliberately outside the `/api` prefix, so probes never
  depend on the API's base path.
- `ENVIRONMENT=production` hides `/docs`, `/redoc` and `/openapi.json`.
- Backend commands must run from `backend/` — `app` is imported from the working
  directory rather than installed into site-packages.
- Tailwind v4 has no `tailwind.config.js`; theming is CSS-first in
  `frontend/src/index.css`.
