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

The one thing that is *not* host-specific is `image-classifier`: it trains
EfficientNet-B0 on symbol artwork and reads back the tiles the reel grid already
wrote, so it needs no running game, no OBS and no elevation. torch is a real
dependency but a lazily imported one, so a machine without it still boots.

The dashboard covers the first three, plus `features/games` for choosing the
active game, `features/roi` for cropping a configured region out of the latest
screenshot, `features/paylines` for checking which of the game's winning patterns
those tiles satisfy, and `features/paytable` for the maths the running game
actually loaded. `game-input` and `ocr` are backend-only so far — endpoints
and services with no `src/features/` slice — so don't go hunting for their UI.
**`features/grid` no longer exists**: the frontend slice was deleted in
`f0b5721` ("Dashboard Cleanup") while `/api/grid` and its `queryKeys.grid` entry
stayed, so a reel split is a backend-only operation now.

Three slices are whole routes rather than dashboard cards, all because their
content does not survive half a row.

`features/paytable` (`/game-config`, the "Game Config" tab) renders four cards —
current paytable, win geometry, payline combos, reel strips — out of a response
that also carries the symbol table those names come from and the scatter awards;
the slice is deliberately not a one-to-one rendering of the payload.

`features/analyze-spin` (`/analyze-spin`, the "Analyze Spin" tab) is every other
integration in one press: it records, screenshots, spins the i-deck, follows the
game log to the result, clicks take-win if anything was won, screenshots each
moment, stops recording, and then validates the cash meter, names the symbols on
the reels with the image classifier, and checks the paylines those symbols paid.
It is the only slice whose live half is not react-query — a run publishes a
snapshot per step over a WebSocket — and the only one that composes other
features' services rather than wrapping one of its own.

`features/image-classifier` (`/image-classifier`, the "Image Classifier" tab)
trains a model and then names the tiles of a written split. Its own route because
a classification is fifteen tiles each carrying a picture, a code, a display name
and three probabilities, and training is a five-stage list with its own figures.
Its live half is a poll, not a socket, deliberately — see the service note below.

`README.md`, `backend/README.md` and `frontend/README.md` are unusually detailed
and current; read the relevant one before changing an integration.

## Commands

`.\start.ps1` from the repo root launches the backend and frontend **each in its
own window via `Start-Process pwsh`** (not Windows Terminal tabs). The backend
always starts elevated — accept the UAC/Avecto prompt in its own window — because
it needs High integrity to drive the i-deck and click the game (UIPI drops
Medium→High input; the whole cabinet is auto-elevated to High here). It takes no
parameters. Confirm from `GET /api/ideck/status`: `ready` means the backend
can drive the panel, `access_denied` means it cannot. `Start-Process` payloads may
contain `;`, so the launch strings use `Set-Location …; & $venvPython -m app`.

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
`event_capture.py`, `game_input.py`, `ocr.py`, `analyze_spin.py` and
`image_classifier.py` each hold their state (client, lock, running run, cached
engine, loaded model) in module globals and expose a `reset()` that
`tests/conftest.py` calls autouse before and after every test. Callers import the
namespace, not the functions: `from app.services import obs as obs_service`.
`event_capture.reset()` and `analyze_spin.reset()` are async because both have a
task to cancel; `image_classifier` splits the two — a sync `reset()` for the
caches and an async `abort()` for the training task, and `conftest.py` awaits the
latter.

**Settings are composed by inheritance.** `Settings` in `app/config/runtime.py`
inherits `AgentSettings`, `ObsSettings`, `IDeckSettings`, `EventCaptureSettings`,
`GameInputSettings`, `OcrSettings`, `FrameSettings`, `PaylineSettings`,
`PaytableSettings`, `AnalyzeSpinSettings` and `ImageClassifierSettings`
(each in its own `app/config/*.py`) while env var names stay flat — a new
integration is a new mixin, not a new settings object. `get_settings()` is `lru_cache`d and a
module-level `settings` instance is imported directly by services — so tests
override behaviour with
`monkeypatch.setattr(settings, "IDECK_GAME_CONFIG_DIR", tmp_path)` rather than
by building new settings. Everything imports `app.config.runtime` directly --
the old `app/core/config.py` re-export shim is gone.

**Per-game data is separate from env config.** `app/config/game_config/games/<Game>.json`
ships with the code and carries the process name, log path, OBS window source,
ROIs, reel bounds, paylines, click targets and event rules. The i-deck is not
in that list: a key is pressed by the id the panel layout gives it, so the deck
needs nothing per-game.
`active_game.json` holds the current selection and is rewritten atomically by
`PUT /api/games/active` — no restart, no `.env` edit. Adding a game is adding a
JSON file.

Two keys point *out* of the repo, at the game's own install: `game_config`
(its `GameConfig` directory) and `win_geometry` (that directory's
`winGeometry.xml`). Neither is checked at load time: the install is not part of
this repo and a config naming it stays valid on a machine without it, so the
service reading the file is where a missing one becomes an error.

A third, `symbols`, is the display name per two-letter symbol code. It is the
one thing about a game's maths that is *declared* rather than read, and only
because it cannot be read — see below.

A fourth, `wild_card_replacement`, is the codes the wild (`WC`) stands in for.
Declared rather than read for a different reason: the maths *does* carry it
(`WildSymbolList`), but `services/paylines.py` checks a screenshot on machines
with no game installed. The list is **closed on purpose** — the codes left out
are the scatters and feature symbols the game pays by counting across the grid,
so a wild beside two orbs is not three orbs. Omitting the block substitutes
nothing, which is exactly what every config did before it existed.

**Foreign formats get their own `app/utils` module**, deliberately ignorant of
who consumes them: `panel_xml.py` (i-deck layout), `panel_log.py` (panel service
log), `game_log.py` (game log → named events, plus `DEFAULT_RULES`),
`log_tail.py` (rotation-aware cursor), `log_search.py` (the opposite
question — reads a log *backwards* for the last line matching a pattern),
`game_math.py` (a paytable folder's `math.xml` and `gameConfig.cfg`),
`bet_config.py` (that folder's `betPerUnitConfig.xml` and `betUnitConfig.xml` —
the rungs a player may stake on each bet unit, and what a spin costs at one;
their product is the total bet, and is the only place the two factors are
separate — `SpecificMaxBets` and `AllowedBetsTbl` both carry only the product),
`win_geometry.py` (`winGeometry.xml`, plus the conversion between its
0-indexed reel-first lines and a config's 1-indexed `[row, column]` ones),
`win32.py` (the only ctypes),
`ocr.py` (runs the Tesseract program and reads its TSV back),
`image_roi.py` (crops a named region out of a frame),
`letterbox.py` (finds the part of a frame the game fills),
`reel_grid.py` (reads the `reel_bounds` block into positioned tiles),
`click_target.py` (reads the `button_targets` block),
`paylines.py` (reads the `paylines` block into ordered grid positions, and
reads one line of symbol codes -- `read_run` -- with the wild substituted for
whatever the run pays as; `WildRule`/`WILD_SYMBOL` live here),
`similarity.py` (cosine similarity between two pictures -- still what
`services/paylines.check`/`check_lines` and the standalone payline panel use, and
no longer what `analyze_spin` reads a spin by),
`symbol_dataset.py` (a folder of symbol artwork read back as training pictures,
with the reel background the cut-outs ship without put back — torch-free on
purpose), `symbol_model.py` (the only module that imports torch: EfficientNet-B0,
its transforms, the training loop and a checkpoint),
`tile_video.py` (one of the two modules that import cv2 — the writing one:
buffers tile crops and writes one short video per reel position, probing its
codec by writing a frame because OpenCV reports a writer as open for an encoder
that then fails to initialise),
`video_frames.py` (the reading one: a recorded clip back as still frames at a
fixed interval, decoding *forward* with `grab()`/`retrieve()` rather than
seeking — `CAP_PROP_POS_FRAMES` lands on the preceding keyframe and decodes
forward anyway, so seeking each sample re-decodes most of the clip once per
sample and measured 12x slower over a 90s file),
`symbol_overlay.py` (draws
rings the cells that were named, over the reels -- no
text on it, because the codes and confidences are a table beside it and drawing
them over the artwork duplicates them at their least readable size),
`payline_overlay.py` (draws evaluated lines over a crop, and owns their palette),
`paths.py` (resolves untrusted filenames inside a root — use it for anything
that came off the wire).

**Regions and click targets are fractions of the game window, never pixels.**
`roi` crops *out* of a captured image and `button_targets` aims a click *into* a
window, but both are `0.0..1.0` against the rectangle **the game itself fills** —
so neither a different capture resolution nor a resized simulator needs a
re-measurement. Keep that convention when adding either.

For a click target that rectangle is the window client area, which `win32.py`
asks Windows for. For a region it is the **content box**: OBS writes every frame
at its canvas size and fits the window capture inside it, so a portrait game in a
landscape canvas arrives with black bars whose width is a property of the
window's shape at that moment. `utils/letterbox.py` finds the box by trimming
near-black borders, `services/roi.py` owns the question for ROI, OCR and grid
alike (`content_box()` / `resolve_box()`), and `Roi.to_box_within()` is what
resolves fractions against it. Three things are deliberate: detection refuses to
believe a box under a quarter of the frame or a frame with nothing above the
threshold (both come back as the whole frame, because a fade to black is a normal
thing for a screenshot to catch), the box is found once per frame rather than
once per region, and every response reports it beside the region's own box —
a crop of the wrong thing is either a badly measured region or a misdetected box,
and only the pair says which. `FRAME_LETTERBOX_TRIM=false` reverts to canvas
fractions. **Region values in a game config are therefore fractions of the game,
not of the canvas** — measuring one off a letterboxed screenshot means
subtracting the bars first.

**`reel_bounds` is the one exception, deliberately.** It is not fractions of
anything: it is `rows` and `columns`, counts of how many equal shares the
**`roi.reels` crop** divides into. The reels fill their own crop by definition,
so reel 3 is its third fifth and no spans need measuring. That is what keeps the
two blocks independent: the reel window moving on screen is a change to
`roi.reels` alone, and a sixth reel a change to `columns` alone. The per-span
`col_bounds`/`row_bounds` form this replaced is **rejected**, not ignored — a
config still carrying it would split evenly anyway and look correct. An unequal
grid (a wider centre reel) can no longer be described; that was the trade.
Its optional `inset` is a rectangle
again: a fraction of **each tile**, trimmed off every edge, which is what removes
the frame a game draws inside a reel to highlight a win. A request may override
it for one split — config errors are 500, request errors 400 — and a trim that
would leave no tile is rejected rather than clamped.

**`services/roi.py`, `services/ocr.py` and `services/grid.py` are the same
joinery, one step apart each.** All three resolve the active game's `roi` block
against the content box of a frame; ROI returns the crop, OCR hands it to
Tesseract, and grid divides it by `reel_bounds`. Keep them apart — checking that a region is aimed
correctly must not require an engine install. ROI reads the newest file in
`settings.obs_dashboard_screenshot_dir` and never asks OBS for a frame, so the
same crop extracted twice is the same picture, and **`roi.py` owns that question
for everyone**: its `resolve_frame`/`open_frame`/`content_box`/`resolve_box`/
`describe`/`describe_path`/`encode_png` are public and `grid.py` and `ocr.py`
call them rather than re-deriving "the latest screenshot" or re-reading a
letterboxed frame. ROI, grid, paylines and `tile_clips` are the four services
holding no state, so none has a `reset()` nor anything in `conftest.py`.

**Every tile is the same pixel size, deliberately.** `ReelGrid.place()` rounds
each tile's *position* on its own and then gives them all one shared width and
height (the smallest that fits every span) — because rounding both edges of every
tile independently varies them by a pixel, and a grid of unequal tiles cannot be
stacked or fed to anything expecting one input size. Don't "simplify" it back to
`tile.roi.to_box()` per tile.

**`services/paylines.py` reads a written split rather than a screenshot, and
that is the whole point of the grid writing one.** ROI asks *is this rectangle
aimed right*, grid asks *does it divide into the symbols I expect*, paylines asks
*do those symbols line up* — each reads the previous one's output instead of
redoing it, which is why a check is repeatable at a new threshold without the
simulator still showing the same spin. `grid.py` owns reading a split back
(`latest_split` / `resolve_split` / `read_split`) for the same reason `roi.py`
owns "the latest screenshot": it named the directory, the crop and the tiles.

**Two tiles are the same symbol by cosine similarity, and the threshold is not
intuitive.** Pixel channels are non-negative, so the measure does not start at
zero for unrelated pictures — on FortuneOx's captures two *different* symbols
score 0.60–0.89 and two crops of the *same* symbol score 0.967 and up. The
separation is wide but far above where "0.7 means similar" would put it, so a
threshold that sounds generous calls almost every pair a match. Do not "fix"
that by inventing a normalisation: every result already reports `matched_min`
and `rejected_max`, the two numbers a working cut sits between — tune
`PAYLINE_MATCH_THRESHOLD` (default `0.85`) against a split that does not move.

**A payline is read left to right and stops at the first position that does not
join the run**, so `pays` is the length of the *leading* run — 0 when reels 1 and
2 differ, otherwise 2 or more — and never a count of matches anywhere on the
line. Every adjacent pair is scored anyway and each step carries `matched` (did
the run continue here) *and* `counted` (did the read get this far). A matched
step that was never counted is the interesting case; collapsing the two flags
into one is what makes a payline checker look wrong.

**The wild is why that read is a line and not a bag of pairs, and this is the
invariant to protect.** A wild stands in for whatever the *run* is paying as, so
`utils/paylines.read_run()` carries the run's own symbol along the line and
judges each position against `line_symbol` rather than against the tile to its
left. `AA WC BB BB BB` is a run of **two** even though every adjacent pair in it
matches — the wild is already the Ox and cannot also be a Pisces. Folding the
substitution into the pairwise comparison reads it as three and credits a win the
cabinet never made. So a *counted* step's `matched` is line-level and an
*uncounted* one falls back to pairwise (past the break there is no run to
continue, and the pair is only evidence the break was real); `line_symbol` on the
step is what makes the difference readable instead of inferred. An all-wild run
pays as `WC`, which has its own paytable row. A similarity check substitutes
nothing and cannot — it never names a tile. Line colours come from
`utils/payline_overlay.py` and travel on the response, so the dashboard's swatch
and the drawn stroke cannot drift — the frontend never picks one.

**Every line gets its own picture, paying or not, with a break marker where its
run stopped.** `pays`'s counterpart is `break_position` -- the tile name where
the leading run failed, or null when the whole line paid -- and the picture at
`PaylineLine.image_data` draws exactly that line, its confirmed tiles ringed
green and (on this picture only) the break tile ringed red. The combined
`overlay_image` still draws only the *paying* lines and never the red ring --
several lines share that picture, and "here is where this one broke" is a
question about one of them. Both pictures are built from the one
`payline_overlay.DrawnLine` per config line that
`services/paylines._line_drawing()` produces: the combined overlay is that list
filtered to `pays >= 2` with `dataclasses.replace(drawing, break_point=None)`,
not a second drawing pass. A line's label on either picture is just `Line 4` --
`pays` is already numeric data on the response, not something to repeat as text
on the picture.

**`services/paytable.py` is a three-way join, and it reports how it joined.**
The game's log names the paytable it loaded (`paytableId[FortuneOx-1101YX-1c-90]`,
written on every denomination change); that string is byte-identical to a
directory under the game's installed `GameConfig` folder; and the game config in
this repo is what points at that folder and names its symbol codes. This is the
only module that knows all three, so every response carries `source.origin`
(`log` / `requested` / `only`) and the log line itself — a page showing the wrong
maths is a stale log or a hand-typed id, and only saying which lets a reader
tell. Failures split by whose fault it is: 409 the machine (no install), 404 the
log or the request (that id has no folder — and the message names *both* halves),
502 the files (present but unreadable). An unreadable `winGeometry.xml` is
deliberately none of those: it comes back as `win_geometry.error` on a 200,
because the symbols, strips and combos above it are all still true. `bet_config`
makes the same bargain twice over — unreadable is an `error` on a 200, and a
folder shipping neither file is plain `null`, since an older install not carrying
them is not a failure to read anything.

**A paytable folder holds ten files and this reads five.** `math.xml`,
`gameConfig.cfg`, `betPerUnitConfig.xml`, `betUnitConfig.xml`, plus the shared
`winGeometry.xml` at the `GameConfig` root. The five it does not open are
`maxBetConfig.xml`, `progConfig.xml`, `PersistenceData.xml`, the `.n40.x880`
duplicates of the two bet configs, and `gameConfig.unlimited.cfg`. Worth knowing
before concluding a number is not written down anywhere: the bet ladder sat in
two unopened files for as long as the award was priced without it.

It is also the one service that caches, keyed on each file's mtime *and* size,
because `math.xml` is close to a megabyte — hence a `reset()` and a `conftest.py`
entry, unlike `roi`/`grid`/`paylines`.

**Which payline set is in play comes from the paytable, not from the maths.**
`winGeometry.xml` sits at the `GameConfig` root and is shared by all 53 of
FortuneOx's paytable folders; the same `math.xml` ships in folders that play 5,
20 and 40 lines, so its own `DefaultConfiguration/PaylineSetID` cannot be the
answer. `gameConfig.cfg`'s `NumberOfLines` is, and `resolved_from` says which
file answered. Lines travel in both forms — `elements` as the file writes them
(`[reel, position]`, 0-indexed) and `grid` as a config's `paylines` block writes
them (`[row, column]`, 1-indexed) — because the hand-copied JSON block exists so
`services/paylines.py` can check a screenshot on a machine without the game
installed, and the two are only comparable if both forms are sent.

**A symbol row is two files joined, and the split is not arbitrary.** Which
codes exist, what they pay and how much of the reels they occupy are *read* from
`math.xml`; what to call them is *declared* in the game config's `symbols` block.
That block exists because these files carry no display text in any element — not
in `SymbolSetList`, not in `ReelStripList`, nowhere — so `WC` becomes "WILD"
there or nowhere. Underneath it `ROLE_LABELS` supplies `Wild`/`Scatter` from
what the maths' structure implies (`GameMath.roles()`: in `WildSymbolList` →
wild, counted by a `CountScatterCombo` → scatter), so a game whose config names
nothing yet is still readable.

**Counts come from `ReelStripList`, and each code is counted twice.**
`GameMath.reel_symbols()` reads the strips — what can actually land — for stops
on the base game's own reels *and* across every strip in the file, because 0
beside a non-zero total is what tells a feature-only symbol from one this game
does not have. A code the symbol set only declares is still listed
(`on_reels: false`, all zeros): FortuneOx declares eighteen and strips carry
seventeen, and the eighteenth is a named feature symbol with an award, so
dropping it would silently shorten the table the config was written against.

**`ANY` in a combo is not a symbol** — it is the trailing wildcard, so
`[X, X, X, ANY, ANY]` is the same *leading run* rule `services/paylines.py`
reads a split by. Because of that, every line pay is really a (symbol, run
length, value) triple, and the response carries both shapes: `payline_combos`
as `math.xml` declares them, and `pay_lengths`/`pay_table` pivoted into the grid
a paytable poster actually is (`GameMath.line_pays()` + `_pay_table()`), with
symbols paying identically at every length merged into one row. Don't drop the
declared list — a combo mixing two symbols has no per-symbol row to sit in and
would vanish with it.

**Grid always writes its output; ROI only writes the cash meter.** A split goes
to `obs-captured-files/grid/<frame stem>/` — `reels.png` plus `tiles/r1c1.png`,
1-indexed and row-major — one directory per source frame, so re-splitting a frame
replaces its own record. A payline check writes its annotated reels into that
same directory as `paylines/<set>.png`, one per (split, set), because a pay count
cannot be checked by reading it and re-checking at a new threshold should replace
its own record too. Stale tiles from a differently-shaped grid are cleared,
and only names matching a tile's own pattern are touched. ROI's own
`_SAVED_REGION` writes `cash-meter/<frame>.png` and every other region comes back
as a data URI only.

**`services/tile_clips.py` films what a screenshot cannot say.** The grid cuts a
still into tiles and the classifier names them; this cuts *frames* into tiles so
a reader can see which cells the cabinet animated. Four things: obs-websocket has
no video stream, so a clip is screenshots taken as fast as OBS answers (JPEG, not
PNG -- these are video frames, and PNG-encoding a 1080p canvas ten times a second
is the slowest part of the loop); the geometry comes from
`grid.place_on()` **once, off the first frame**, because letterbox detection
scans the whole picture and the window does not move mid-spin; the frame rate is
**measured, never assumed**, so `utils/tile_video.py` buffers the tile crops and
writes at the rate achieved rather than the one requested; and the codec is
probed by writing a throwaway frame per candidate, VP8/WebM first because it is
the only one of the four a browser plays inline. It never raises -- a clip is a
record, never a reading, so losing OBS halfway costs the clips and nothing else.

**`services/image_classifier.py` is the only reading that can disagree with the
game, and it is now what `analyze_spin` grades a spin by.** `similarity.py` asks
whether two tiles match *each other* and never learns what either is. This names
a tile from the picture — a network in
`utils/symbol_model.py` (the only module that imports torch) over the tiles
`grid.py` already wrote. So anything that moves here — the artwork, the
transforms, the confidence floor — moves a spin's verdict too. Five things it
exists to get right:

- **Two engines, both kept at once.** `CLASSIFIER_ARCHITECTURE` picks ResNet34
  (the default -- it names all fifteen tiles of the reference split correctly) or
  EfficientNet-B0; both share every transform, so only the backbone
  differs and a third is one entry in `_ARCHITECTURES` rather than a second code
  path. Each has its **own** `model-<arch>.pt` and `metrics-<arch>.json`, so
  training one leaves the other answering, and `/train` and `/classify` both take
  an optional `architecture`. That is the point — two independently-fitted
  networks agreeing about a tile is worth more than one being confident, and a
  disagreement says something about the tile. The architecture rides *on* the
  checkpoint because `load` would otherwise build the default backbone and the
  state dict would not fit: a shape error instead of "this is a ResNet".
- **The artwork is not what it sees, and putting the background back *is* the
  feature.** Of the classes shipped so far exactly one (`AA`) carries the game's
  field and frame — a framed portrait, 0.994 opaque inside its alpha box. Every
  other one, `BB`/`CC`/`DD` included, is a transparent cut-out at 0.45–0.71,
  composited over the reel background by the game at runtime. The routing keys on
  each *file's* measured opacity rather than a list of codes, because the artwork
  grows and a rule written against nine names stops applying to the tenth. An earlier
  cosine-similarity attempt compared cut-outs against composited tiles and scored
  0.35–0.44 on the picture symbols while missing the card symbols entirely.
  `utils/symbol_dataset.py` composes each source onto a synthesised reel cell,
  routing on the **measured opacity of that file** rather than a class list, with
  every constant measured off the 600 written tiles (field `rgb(35,0,56)`; a gold
  divider sliver 40% of the time at `rgb(201,121,37)`; black 2%). It is
  deliberately torch-free so the part most likely to be wrong is testable without
  the ML stack, and it writes one sample per class to `samples/` because whether
  the background went back is answerable by looking.
- **The two transforms are a pair.** Training random-crops at scale 0.65–1.0;
  evaluation resizes past the input and centre-crops back (`_EVAL_CROP = 0.90`,
  torchvision's ImageNet recipe). Not symmetry — without it the network only ever
  saw zoomed crops while inference showed it the whole tile, and `DD` scored 0.42
  on pictures it had trained on versus 1.00 with a 10% centre zoom (holdout 0.781
  → 1.000). `TRANSFORM_VERSION` rides on the checkpoint so an older model reports
  `stale` rather than quietly predicting differently than it was measured. Also:
  **no horizontal flip** (five symbols are A/K/Q/J/10 and a mirrored J is not a
  picture the game draws), and resolution is itself an augmentation (52–140px and
  back, because the artwork is 380–600px and tiles are 61–128px).
- **A tile below the floor is an answer, not a gap.** The game declares eighteen
  symbol codes and the artwork covers nine, so the model is constantly shown orbs
  and wilds it has no class for; a softmax cannot say "none of these", only spread.
  `CLASSIFIER_MIN_CONFIDENCE` decides it and the rejected tile keeps its ranked
  `predictions`, because a rejection with no numbers behind it is not checkable.
  0.90 is deliberately far above where the classes separate (the widest measured
  empty band is around 0.56–0.68), so correct readings are rejected whenever the
  model is only fairly sure — on the reference split ResNet34 reads all fifteen
  tiles right and five still come back blank. That is the trade, and the per-tile
  table is what makes it safe: it shows the leading candidate and its percentage
  anyway, greyed with the figure in red. **The grids mean "the model was sure";
  the table means "this is what it thought".** Any figure quoted here moves when
  the artwork or the transforms change — re-measure rather than trusting it.
- **Accuracy is two numbers and they are never averaged.** A class is an animation
  *loop* of 48 near-identical frames, so no split of it is honestly unseen.
  `frame_holdout_accuracy` holds a block out of the **middle** (not the end — the
  loop closes, so `AA`'s and `DD`'s last frames sit 0.02/255 from a retained one),
  `frame_holdout_leakage` says how much even that leaks, and
  `augmented_accuracy` covers all nine classes but re-augments the training
  pictures. The five single-picture classes contribute to the holdout figure not at
  all, and the payload names them.

Two smaller conventions: **torch is imported lazily**, so the app boots without it
and `GET /api/image-classifier/status` reports `not_installed` — the OCR/Tesseract
bargain, verified by `torch` being absent from `sys.modules` after `create_app()`.
And **there is no second WebSocket**: epoch ticks are ~20s apart, so the slice
polls `/status` with `useCaptureStatus`'s functional `refetchInterval`.
`CLASSIFIER_TORCH_THREADS` (half the cores) is load-bearing — torch takes every
core by default and this process also drives OBS, the i-deck and the game clicks.

**Unlike paylines, the classifier does not validate a split against the config.**
`paylines._evaluate_set` must (only the config says where a line runs, so a
differently shaped split makes its lines meaningless); a classifier names each tile
independently and rows/columns are only how answers are arranged. So an older split
still classifies, and the config is read for exactly one thing — the `symbols`
block, for display names, falling back to bare codes for a game declaring none.
`ClassifyResult.symbol_grid` is deliberately the same name and shape as
`SpinReelReading.symbol_grid` in `schemas/analyze_spin.py` — which is now *this*
grid, copied field by field by `analyze_spin._reading()` rather than a second
answer to compare it against. The log-derived counterpart it used to sit beside is
gone from that payload.

**`services/analyze_spin.py` is orchestration and nothing else.** It owns no
image handling, no XML, no win32 — it is the *order* the other services go in
(OBS record/screenshot, i-deck press, game log wait, game-input click, then
then ROI's meter, then grid+classifier, then paylines), and every step carries the underlying
service's own error code. `_step` also has one convention worth knowing: a body
that sets `step.error` **without raising** is recorded as failed and the run
carries on, for the step that did its work and knows the result is unusable. Six
things it exists to get right:

- **A losing spin is proven by silence.** The game logs `[WinBangDone]` when the
  win meter counts up and logs nothing at all when there is no win, so "no win"
  is that line's absence within `ANALYZE_SPIN_WIN_WAIT_SECONDS`. Set below the
  longest count-up, a win comes back as a loss — the only setting here that
  produces a confidently wrong answer rather than a timeout.
- **The steps exist before they run.** A run is created with all thirteen
  `pending`, so a failure on step four leaves the rest visibly unreached, and
  take-win on a losing spin is `skipped` — a different fact from unreached.
- **Cancellation is cooperative, never `task.cancel()`.** Every wait polls and
  every poll checks a flag, so a cancelled run unwinds through its own code and
  no `finally` runs under a pending `CancelledError` (which is also what keeps
  `filterwarnings = error` happy). `abort()` in the lifespan runs *before* OBS
  disconnects, because the one thing worth getting right at shutdown is OBS not
  being left recording.
- **Screenshots go where the validations read**: the dashboard screenshot dir
  (`OBS_SCREENSHOT_SUBDIR`), not a per-run one, because `roi.py` and `grid.py`
  open a frame *by name* there. `frame_path()` delegates to
  `roi.resolve_frame()` rather than re-deriving the guards.
- **An empty frame is read back, not trusted.** OBS renders nothing for a moment
  after its window source is re-pointed and reports a successful write of the
  black frame anyway, so `prepare` settles after retargeting and every capture is
  probed with `roi.is_blank` and retried. A frame that stays blank sets
  `blank` on itself and fails its step *without raising* — see the
  error-without-raising convention on `_step`.
- **The meter reports its units, once for the run.** `meter.mode`
  (`cash`/`credits`/`unknown`) and `meter.currency` come from
  `services/meter.combine()` over every frame's own classification, not from a
  vote: **cash wins a disagreement**, because money is read positively (a symbol,
  or a fractional amount) and credits is what `_classify` concludes from the
  absence of both — so a frame whose cells all happened to be whole reads as
  credits on a cash machine. One answer per run because a cabinet does not change
  denomination between the screenshots of one spin; `readings[].values.mode` keeps
  the per-frame reading.
- **The third unit is the denomination, and the value is not the rate.**
  `meter.denomination` comes from the game's log by way of the paytable — *not*
  off the strip: the games do draw an unlabelled `1c`/`2c` badge at the far right
  of the meter row, but its unit glyph does not OCR at any band, scale or mode, so
  `utils/meter.py` still discards it as chrome. **The log reports a count of
  cents** (`denom[2.000]` on a `-2c-` paytable, corroborated by that folder's
  `<MinDenomMultiplier>`), so the number that prices an award is
  `money_per_credit` (0.02) and never the value (2) — the two differ by a factor
  of a hundred and the wrong one yields a plausible figure rather than an error.
  `utils/denomination.py` is the only module that interprets one; the unit comes
  from the paytable id's `-Nc-` suffix and an id that names none leaves
  `money_per_credit` null and the award verdict `indeterminate` rather than
  guessing from the supported ladder.
- **The award is `combo_value × bet_per_unit × money_per_credit`, and the first
  multiplication is the one that gets forgotten.** A paytable combo's value is a
  rate **per bet unit**, not a flat credit amount, so a line reading "pays 25"
  awards 25 at one credit a unit and 250 at ten. `_award` does that one per line
  (`SpinLineAward.combo_value` is the paytable's own figure, `credits` the
  product); `_expected` only sums and converts to money. The captured win that
  reads `75` credits and `$0.75` at 1c is a spin at **n=1** — 88 is `MinTotalBet`,
  the unit cost times the first rung — so it is consistent with the multiplier
  and never was evidence against one. What it does rule out is scaling by the
  stake spread over a *line*: 88 over 40 lines is 2.2, a rung of no ladder.
  `bet_credits`/`credits_per_line` are still reported and still **not inputs**.
  On a credit meter no rate takes part at all — the meter is already counting
  credits, and `observed_win` is compared against `credits` directly
  (`expected.unit` says which side was compared).
- **The bet per unit is worked out, never configured.** No file and no log line
  says what the player staked — the meter's BET cell shows the *total* and the
  log mentions a bet only when one is changed — but it follows from two things
  the run already has: `bet_credits / unit_cost`, the bet the meter drew over
  what `betUnitConfig.xml` says a spin costs at one credit a unit (88 on
  FortuneOx). There is deliberately no setting and no request parameter: the two
  numbers are always there when the meter reads, and a hand-set rung is one more
  thing to get wrong. Deriving is **not** rounding — `88 × rungs` has to come back
  to the bet that was read (300 credits rounds to rung 3, but `88 × 3` is 264, so
  it is refused), and the rung has to be on the ladder the game shipped. When the
  BET cell cannot be read the award verdict is `indeterminate` rather than a
  guess: pricing at the minimum would understate a raised-bet spin while looking
  certain.
- **The meter reads before the reels, and that ordering is load-bearing.** It is
  the only one of the three closing steps that produces an *input* to another:
  which unit the strip drew and what a bet unit cost are what turn a line's rate
  into an award. So `_validate_meter` runs first, then `_read_reels`, then
  `_validate_paylines` — which is also why `expected` is built in
  `_check_paylines` where the awards are, rather than patched into the payline
  validation from the meter step afterwards.
- **Which unit the strip drew is settled by the paytable, not just by the
  digits.** `meter.combine()` reads the *shape* of the numbers — a symbol or a
  fractional amount means money, credits is what is left — which is thin enough
  that a credits cabinet OCR'ing one stray decimal reads as cash. Everything is
  then divided by the denomination a second time: an 88-credit bet becomes 8800,
  no rung of any ladder, so the stake is refused and the award falls to **0**
  while the table files the readings under cash. `_settle_mode()` fixes it with
  evidence of a different kind: a spin costs `unit_cost` a rung, so the drawn bet
  is one of `88, 176, 264, 440, 880` in credits or `0.88 … 8.80` in money, and
  those sets do not overlap. Only when exactly one matches — at a $1 denomination
  they are the same set, so the classification stands.
- **Both units, everywhere on the meter step.** The cabinet draws one and the
  paytable speaks the other, so `readings[].credits` and `readings[].cash` each
  carry the same `balance`/`win`/`bet`, and **every amount relation is checked in
  both** — `bet-deducted-credits` beside `bet-deducted-cash`, with
  `checks[].unit`, its own tolerance per unit
  (`ANALYZE_SPIN_METER_CREDIT_TOLERANCE` is 0.5 against the money 0.005), plus an
  end-to-end `balance-reconciled-<unit>` that the two pairwise relations do not
  imply. `win-registered` is the only unit-free check. The conversion happens once
  per run in `_in_both_units`, after `meter.combine()` settles which side was read
  and the paytable supplies the rate — a unit with no figures contributes **no
  checks** rather than indeterminate ones, and an `unknown` mode fills neither
  side, because without knowing which unit was read there is nothing to convert
  from.
- **The three readings at the end cannot fail each other**, and each catches its
  own exceptions so the run reaches all of them. A validation that runs and
  reports `failed` is a *completed* step; only one that could not run at all
  fails. The one dependency is that `paylines` reads what `classify` produced,
  and says so when there is nothing to read — there is deliberately no
  fallback to similarity.

**Payline validation is two judgements over one reading, kept strictly apart.**
This is the invariant to preserve if anything here is refactored:

- **The picture decides what landed, and the image classifier is what reads it.**
  `_read_reels` (step `classify`) splits the result frame and hands the tiles to
  `image_classifier.classify()`; `pays` is then the leading run of positions named
  with the *same code* — the wild read as whatever the run pays as — and both
  codes of every pair travel on `steps`. Nothing about the win comes out of the
  log: a checker that read the answer there would agree with the game by
  construction and could never catch a reel drawing the wrong symbol.
- **Two things it replaced.** Cosine similarity measured a run without naming it,
  so an award was every paytable row paying at that length; `similarity.py` and
  `paylines.check`/`check_lines` still do that for the standalone panel. The
  game's logged reel stops named the symbols by *agreeing with the game*, and
  that reading (`utils/reel_stops.py`, `game_log.REEL_STOPS`,
  `ANALYZE_SPIN_REEL_STOP_ANCHOR`) has been deleted -- recoverable from git if
  ever wanted. Don't reintroduce either as a fallback — a run measured by
  likeness and then priced as if it had been named is worse than one reported
  short.
- **Two unnamed tiles are never a match.** A tile below
  `CLASSIFIER_MIN_CONFIDENCE` came back with no code, and "I could not tell" twice
  is not evidence of a run — so a line through one **stops there**. That floor
  sits far above where the classes separate, so this is a real cost rather than a
  corner case: `unnamed_positions` on the validation and `leading`/`confidence`
  per tile on `run.reels` exist so a short run is diagnosable, and
  `ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE` is the knob — **0.85** here against
  the classifier page's own 0.90, because that page shows what it rejected and
  this one grades a spin. Blank (not absent) opts back into
  `CLASSIFIER_MIN_CONFIDENCE`.
- **Which network grades a spin is a per-run choice**, exactly as `record` is:
  `start(architecture=...)` — then `ANALYZE_SPIN_CLASSIFIER_ARCHITECTURE`, then
  `CLASSIFIER_ARCHITECTURE`. Resolved in `start()` via the now-public
  `image_classifier.resolve_architecture()` **before the game config is read**, so
  an unknown name is a 400 on the request rather than a failed step twelve steps
  in, and `_ActiveRun.architecture` records which one was asked for. The two are
  offered as a dropdown beside the spin button because running the same spin
  through each and comparing is worth more than either alone; the option list is
  hardcoded in `features/analyze-spin/spin-control-card.jsx` rather than fetched,
  so that slice still deletes in one directory.
- **The paytable decides whether it pays.** `paying` (two or more alike) is
  *evidence*; `awarded` is the win. A run of two of a symbol paying from three is
  cancelled — `awarded: false`, a `note` naming `min_pay_length`, no credits,
  and excluded from `expected`. `services/paylines.py` deliberately stops at "two
  or more" because what pays what is not its business; `analyze_spin` has the
  paytable, so the call belongs there — including `summary`, which is written
  from the awards rather than reused from the check.
- **A wild-led run is two combos and the paytable picks, which is also why that
  pricing lives here.** `WC WC WC WC AA` is five Ox (50 a bet unit on FortuneOx)
  or four wilds (100), and the cabinet pays 100 — so `_award` prices the
  substituted symbol at `pays` *and* the wild's own combo at `leading_wilds`, via
  the shared `_priced()`, and takes the greater. `combo_pays` then says how many
  positions the combo that paid covers: equal to `pays` except where the wild's
  own combo won. The check reports `symbol`/`leading_wilds` and prices neither —
  same separation as `awarded`, same reason. Don't move it into
  `services/paylines.py`, which has no paytable, and don't drop it: the
  substituted reading alone *understates* a real win while looking certain.
- **An award is one number, not a range.** Every awarded line names its symbol
  (`line.symbol`, the run's own code — *not* `symbols[0]`, which is the tile on
  reel 1 and is the wild whenever one landed there), so it resolves to one combo
  and one value: `SpinLineAward.credits` and
  `SpinExpectedAward.credits`/`cash` replaced `value_min`/`value_max`/`exact` and
  `credits_min`/`credits_max`/`cash_min`/`cash_max`, and `SpinLineAwardCandidate`
  is gone. Those spans were never a claim about the maths — they were the
  width of what the picture had failed to identify. Don't reintroduce them; an
  unreadable input makes the verdict `indeterminate`, which is a different
  statement.
- **A cancelled run is not a result.** Its per-line picture is dropped from the
  payload and the combined overlay is redrawn over the awarded lines only, via
  `paylines.redraw()` — which rebuilds from the finished result through the
  same `_line_drawing`, so a redrawn line cannot differ from a first-pass one, and
  replaces the file the check wrote. Skipped when nothing was cancelled. Its
  `steps` survive: the codes are the evidence the run was real.
- **"Pays" means credits, everywhere.** A line *matches* five symbols and *pays*
  twenty-five. Never use the word for the run length — that is `pays` the
  field, and rendering it as "pays 5" is what made a run of five read as five
  credits.
- **`run.reels` is its own top-level block**, beside `meter` and `paylines` rather
  than inside either, because it is one reading of one picture and is worth having
  on a run whose paytable never loaded. It carries no `error`: it exists only when
  the reading succeeded, and a failure shows on the `classify` step and again on
  the payline validation's `error`. It carries no picture either — the ringed
  reels are still *written* to `<split>/classifier/symbols.png`, but the rings only
  said "the model was sure", which `symbol_grid` says in codes, so there is no
  `overlay_image` and no lean/full split on the reading.

**`services/paylines.py` compares tiles two ways, and `method` says which.**
`PaylineMethod.SIMILARITY` is cosine similarity at a threshold;
`PaylineMethod.SYMBOL` is equality of classifier codes *or* substitution of the
wild. Both are `_Comparer` subclasses handed to `_evaluate_set` as a factory
(which takes the split **and** the game config, since the wild's list lives
there), so everything below the comparison — the grid geometry, the tiles, the
drawing, the files under the split's own directory — stays one code path. The
unit they differ over is `_Comparer.read(positions) -> _LineRead`, one whole
line: the base class is the pairwise loop, and `_SymbolComparer` overrides it
because a wild makes a line more than its pairs. It still calls `compare()` on
every pair — that fills the cache `stats.comparisons`/`matches` are counted off,
and is what an uncounted step reports. A symbol check reports `threshold: null`,
`steps[].similarity: null` and null score figures on `stats` rather than faking a
1.0, and carries `steps[].left_symbol`/`right_symbol`/`line_symbol`,
`lines[].symbols`/`symbol`/`leading_wilds` and `wild_symbol`/`wild_replaces`
instead. The pair cache is keyed on the *unordered* pair, so
`_Comparison.flipped()` is what stops a step reporting its two codes transposed.

**Which lines exist still comes from the game's own geometry.**
`services/paylines.check_lines()` and `check_symbols()` are the public doors for a
line set the caller assembled — `analyze_spin` builds one from the paytable's
applicable `winGeometry.xml` set and goes through the second, so the lines are the
ones the *running* game plays rather than the hand-copy in `games/<Game>.json`
(which exists for machines without the game installed). All three entry points
funnel through `_evaluate_set`, so a set from either source is scored, drawn and
written identically; the geometry set is named
`geometry-<id>` so its overlay never overwrites a config-sourced one. It is built
by handing a dict to `utils/paylines.read_set()` rather than constructing the
dataclasses, because that is where a line is checked to run left to right.

**The progress stream is the only WebSocket, and the only non-envelope JSON.**
`WS /api/analyze-spin/stream` sends a whole `SpinAnalysisState` on connect and one
per change — snapshots, not deltas, so a late or lossy subscriber is still
correct. It carries no images; `GET /status?include_images=true` is the report.
The endpoint runs a push task and a receive task and takes whichever finishes
first, because a one-way stream that never reads would only notice a closed tab
on its next send. Vite needs `ws: true` on the `/api` proxy or the upgrade gets
the HTML index.

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
- **`features/analyze-spin/` is the one live view that is not react-query.** A
  run pushes a snapshot per step over a WebSocket, so `useSpinStream` holds it in
  component state and `useSpinReport` fetches only the pictures (which the stream
  omits) once the run ends. `useSpinView` composes the two and uses the report's
  images only when its `run_id` matches the live one — the stream can already be
  on a new run while the report holds the last. Don't turn this into a poll, and
  don't copy the pattern into a slice whose data changes at human speed.
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
