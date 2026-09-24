# Controlling the game through GAF

How to drive a running GDK slot simulator by calling methods, instead of posting
synthetic clicks at measured coordinates.

Everything marked **verified** was measured against a live `HuffNPuffHighRise`
on 2026-09-24. Everything else is labelled as inference. If a figure here
disagrees with the machine, trust the machine and update this file.

---

## 1. TL;DR

The game hosts an automation server. A second local process translates plain
keyword calls into it. From Python you need **nothing but the standard library**:

```python
import xmlrpc.client
p = xmlrpc.client.ServerProxy("http://127.0.0.1:8270/RFTestCode.IDeck.IDeckLibrary",
                              allow_none=True)
p.run_keyword("PRESSMECHANICALSPINBUTTON", [])
# -> {'status': 'PASS', 'return': 'True', 'output': '', 'error': '', 'traceback': ''}
```

That single call spins the machine. No cursor movement, no coordinates, no
elevation, no OCR.

**This contradicts the repo docs.** `CLAUDE.md:911-917` and
`backend/README.md:1961-1969` both say GAF "is **not started by a normal
simulator launch**". That was true of the older FortuneOx build; it is **false**
for `HuffNPuffHighRise`, whose client log records `Starting the Thrift Server ....`
on every launch. Those two passages need revising when this work lands.

---

## 2. The chain

```
your Python                      stdlib xmlrpc.client, no dependency
     |  HTTP POST (XML-RPC)
     v
:8270  NRobot.Server.exe          .NET Robot Framework remote-library host
     |                            hosts 18 RFTestCode.* keyword libraries
     |  Thrift (TFramedTransport, multiplexed)
     v
:9090  HuffNPuffHighRise.exe      AGFService, hosted in-process by the game
     |
     v
Unity scene                       finds the GameObject, fires a real touch event
```

The game cannot distinguish this from a finger on the glass — which is why the
game log shows an ordinary `HandleBetButtonPressed` and an ordinary
`BetChangeMsg` to the server.

### Port 9090 carries two services, multiplexed

`Assembly-CSharp.dll` registers **`AGFService`** and **`RUSTService`** on one
`TThreadedServer` via `TMultiplexedProcessor`. This is why `RUSTClient.exe`
reports `Connected to localhost : 9090` while the game listens on only one port.

Consequence for anyone bypassing NRobot: a raw `TBinaryProtocol` on 9090 **will
fail**. You must wrap it — `TMultiplexedProtocol(protocol, "AGFService")`.
Going direct is not recommended anyway: the `.thrift` IDL is **not on this
machine** (it is generated on the Jenkins build hosts; only compiled output
ships), so you would be reconstructing structs and field ids by reflection.

---

## 3. Prerequisites

| Thing | Where | Notes |
|---|---|---|
| The game, running | `C:\re\games\3093998_HuffNPuffHighRise\games\HuffNPuffHighRise` | Thrift server starts automatically |
| `NRobot.Server.exe`, running | `C:\ProgramData\chocolatey\lib\AGF-LnW\content\` | Started by `NRobotStartUpScript.bat`. **Not** started or supervised by this repo |
| The 8 object-query JSON files | the AGTF Perforce workspace — see §4 | **Hard dependency** |
| Python | any 3.11+ | stdlib only |

Diagnostics:

- NRobot server log: `C:\AGTF\logs\NRobotServer.log`
- Game client log: `C:\logs\Game\HuffNPuffHighRise\Logs\HuffNPuffHighRise_Client.log`
- Minimum AGF image version is `0.45.2.321`; read the live one with
  `RunCMDLibrary.GETVERSIONNUMBER`. **Verified:** this machine is exactly `0.45.2.321`.

`NRobot.Server.exe` uses `HttpListener`, so port 8270's owning PID shows as
`4`/System in `Get-NetTCPConnection`. That is normal, not a permissions problem.

---

## 4. The object-query files — a hard dependency

The client cannot resolve a single named control without a dictionary mapping
friendly names to Unity objects. An entry looks like:

```json
"IDeck_TakeWinButton": {
  "GameObjectIdentifier": "DoubleUp_BB3Style/DoubleUp_ButtonPanel/TakeWinButton",
  "TypeFilter": null,
  "SceneToSearch": null
}
```

Root: `C:\Users\mmuheeth\Perforce\mmuheeth_L5CG3496V1K-BLR_9100_AGTF_HNPHighRise_1`

Merge **in this order** — later keys overwrite earlier, game-specific last:

**General** → passed to `InitializeGameClient`
1. `GameCommon/JsonConfigFiles/GameObjectQueryFiles/BallyStyleThemeGameObjectQuery.json`
2. `GameCommon/JsonConfigFiles/GameObjectQueryFiles/WagerSaverOneViewObjectQuery.json`
3. `GameSpecific/JsonConfigFiles/GameObjectQueryFiles/BallyStyleThemeGameObjectQuery.json`

**Generic** → passed to `InitializeGenericGameClient`
1. `GameCommon/.../GenericGameObjectQuery.json`
2. `GameCommon/.../IDeckBetSliderObjectQuery.json`
3. `GameCommon/.../ProgressiveObjectQuery.json`
4. `GameCommon/.../GenericWagerSaverObjectQuery.json`
5. `GameSpecific/.../GenericGameObjectQuery.json`

**Verified by experiment** — all 8 are needed:

| Initialised with | `InitializeGameClient` |
|---|---|
| `{}` | **FAILS** — "The given key was not present in the dictionary" |
| GameSpecific general only | **FAILS** |
| GameSpecific general + generic | **FAILS** |
| **All 8 merged** | **OK** |

The common files are not optional; the game-specific pair only *overrides* a
subset of their keys.

**Read them with `utf-8-sig`.** They carry a UTF-8 BOM and plain `utf-8` throws.

> The other ~350 files in that workspace — 85 `.robot`, 123 `.resource`, 136
> `.py` — are the Robot Framework test layer and are **not** needed. Calling
> XML-RPC directly bypasses them entirely. You do not need Robot Framework
> installed.

---

## 5. The protocol

One endpoint per library. Both forms work (**verified**):

```
http://127.0.0.1:8270/RFTestCode.IDeck.IDeckLibrary      # dotted
http://127.0.0.1:8270/RFTestCode/IDeck/IDeckLibrary      # slashed (AGTF uses this)
```

Standard Robot Framework remote methods:

| Method | Returns |
|---|---|
| `get_keyword_names()` | list of keyword names (UPPERCASE over the wire) |
| `get_keyword_arguments(name)` | argument names |
| `get_keyword_documentation(name)` | doc string |
| `run_keyword(name, args)` | `{status, return, output, error, traceback}` |

`status` is `"PASS"` or `"FAIL"`. **A failed keyword is a `PASS`-shaped reply
with `status: "FAIL"`, not an XML-RPC fault** — you must check it explicitly:

```python
def run(library, keyword, *args):
    proxy = xmlrpc.client.ServerProxy(f"http://127.0.0.1:8270/{library}", allow_none=True)
    reply = proxy.run_keyword(keyword, [str(a) for a in args])
    if reply.get("status") != "PASS":
        raise RuntimeError(f"{keyword}: {reply.get('error')}")
    return reply.get("return")
```

All arguments go over the wire as strings. Returns are strings or lists of strings.

---

## 6. Session lifecycle

```python
CONNECT = "RFTestCode.ConnectGame.ConnectGameLibrary"

run(CONNECT, "INIT", "127.0.0.1", "9090")                    # seeds host/port
run(CONNECT, "CONNECTGAMECLIENTTOSERVER", "127.0.0.1", "9090")  # -> "True"
run(CONNECT, "INITIALIZEGAMECLIENT", "BallyStyle", json.dumps(general))
run(CONNECT, "INITIALIZEGENERICGAMECLIENT", json.dumps(generic), "", "12")
#   ... work ...
run(CONNECT, "DESTROYGAMECLIENT")
run(CONNECT, "DISCONNECTGAMECLIENTFROMSERVER")
```

Four things that are easy to get wrong:

1. **`INIT` must come first.** Without it `CONNECTGAMECLIENTTOSERVER` raises
   "no session has been initialized".
2. **`gameType` is `"BallyStyle"`** for this game. The only other value is
   `"ShuffleStyle"`.
3. **`gdkVersion` is `"12"`** for HuffNPuffHighRise — the AGTF common default is
   `"10"`, and `GameSpecific/ConfigVariables/GameSpecificConnectGameVariables.py`
   overrides it. The wrong value selects a different client wrapper
   (`GenericClientWrapper` vs `GenericClientWrapperGDK12`).
4. **Always tear down** in a `finally`. Leaving a session open blocks the next one.
5. Wrap the connect in a retry — AGTF uses `Wait Until Keyword Succeeds 12x 10s`
   because a freshly-launched game takes a while to accept.

**Per-library `Init` keywords are deprecated no-ops.** Only `ConnectGameLibrary`
needs initialising; every other library resolves the client live per call.

**Untested:** whether the game accepts two concurrent clients. `RUSTClient.exe`
holds a session when it is running; close it first, or find out.

---

## 7. What works — verified recipes

### Spin

```python
run("RFTestCode.IDeck.IDeckLibrary", "PRESSMECHANICALSPINBUTTON")   # -> "True"
```

Proven in the game's own log: `OledButtonPressedMsg` → `[SlotGameApp.StartGame]`
→ `PanelStateSpinWithStops` → `PanelStateIdleWithCredits` → `GameOverMsg`.

### Take win

```python
IDECK = "RFTestCode.IDeck.IDeckLibrary"
run(IDECK, "PRESSNONWAGERBUTTON", "TakeWinButton")   # -> "True"
```

Valid non-wager button names: `TakeWinButton`, `GambleButton`, `ReserveButton`.

### Waiting — the single most important trap

**A losing spin leaves `IdleStateMachine.statePlaying` on its own. A win HOLDS
the game in `statePlaying` until the win is collected.** A settle loop that
waits only for idle will therefore time out on exactly the spins you care about,
and look like a hang. Wait for *either*:

```python
def settle(timeout=120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        idle = run_ok("GETCURRENTSTATE", "IdleStateMachine")
        if idle and idle != "statePlaying":
            return "idle", idle
        if win_offered():
            return "win-offered", idle
        time.sleep(0.5)
    return "timeout", None

def win_offered():
    return (state("GambleOfferStateMachine") == "offerState"
            or truthy(run(IDECK, "ISNONWAGERBUTTONINTERACTABLE", "TakeWinButton")))
```

A normal losing spin settles in **~13 s**.

### Set the bet

```python
run(IDECK, "COUNTCONFIGUREDBOTTOMROWBUTTONS")     # -> "5"
run(IDECK, "GETBOTTOMROWBUTTONLABEL", 3)          # -> ['PLAY','300','CREDITS','x 3']
run(IDECK, "SELECTBOTTOMROWBUTTON", 3)            # -> "True"; bet becomes 300 credits
```

**Indexes are 1-based.** Index `0` answers `['-1']`, the library's invalid-index
sentinel. Reel columns are 1-based too.

**On this build, pressing a bet button also spins.** AGTF's own
`Place a bet on the IDeck` probes for this once and remembers it, because it
varies by build. Budget for it.

Bet arithmetic: `betsPerUnit x unitCost = total`. Here `unitCost = 100` and the
ladder is `[1,2,3,5,8]`, giving 100/200/300/500/800 credits.

### Read meters and state

```python
METER = "RFTestCode.GameMeter.GameMeterLibrary"
run(METER, "METERINFO", "CreditMeter", "value")   # -> "$995.80"
run(METER, "METERINFO", "BetMeter", "name")       # -> "BET"
run(METER, "TOGGLEMETERVALUE", "CreditMeter")     # cash <-> credits

STATE = "RFTestCode.GameState.GameStateLibrary"
run(STATE, "GETCURRENTSTATE", "IdleStateMachine") # -> "stateIdleWithCredits"

DENOM = "RFTestCode.Denom.DenomLibrary"
run(DENOM, "GETCURRENTACTIVEDENOM")               # -> "1.000"  (a count of cents)
run(DENOM, "GETCONFIGUREDDENOMBUTTONS")           # -> ['1.000','2.000','5.000','10.000']
```

`meterType` is `CreditMeter` / `BetMeter` / `WinMeter` (PascalCase — the C# doc
comment says `creditMeter`, which is wrong). `valueType` is `value` / `name`.

**The win meter is read mid-rack-up.** It reported `$53.67` on its way to `$75.00`
and `$0.19` on its way to `$0.90`. Only the post-collect value is the real figure.

### State machine vocabulary

From `GameSpecific/ConfigVariables/GameSpecificGameStateConfigVariables.py`:

| Machine | States that matter |
|---|---|
| `IdleStateMachine` | `statePlaying`, `stateIdleWithCredits`, `stateIdleWithNoCredits`, `stateAttract` |
| `SlotGameStateMachine` | `stateIdle`, `stateSpinWithStops`, `stateReelSpinDone`, `stateBonus`, `stateEnd` |
| `GambleOfferStateMachine` | `offerState`, `playState`, `waitForOKToOffer` |
| `GameStateMachine` | `stateIdle`, `statePlay`, `stateResults`, `stateEnd`, `stateWaitForWagerFinalized` |

### Arbitrary objects

`GenericWrapperLibrary` takes any object you declare at runtime:

```python
GENERIC = "RFTestCode.GenericWrapper.GenericWrapperLibrary"
run(GENERIC, "EXTENDOBJECTLIST", json.dumps({"MyButton": {"GameObjectIdentifier": "PaysButton"}}), "")
run(GENERIC, "PRESSBUTTON", "MyButton")
run(GENERIC, "GETOBJECTINFORMATION", "MyButton")   # JSON: isActive, Scene, ...
```

`GETOBJECTINFORMATION` returns a JSON string with an `__isset` map. **If every
`__isset` flag is `false`, the object was not found** — the call still reports
PASS. Check `__isset`, not the call status.

---

## 8. What does NOT work

### Reels cannot be read on this build

`ISREELSETVISIBLE` is `False` for both `BaseGameReel` and `FreeGameReel`, before
*and* after a completed spin, so every reel keyword fails with
`Reel set name 'BaseGameReel' is not visible.`

**Root cause, diagnosed:** the GameObject `BaseGameReelGroup` that AGTF's
object-query file names **does not exist** in this build. Probing by type instead
finds `GDK.Client.Reels.ReelSet` in scene `FreeSpinBonus` and
`GDK.Client.Reels.VisibleSymbol` in `ThemeMain`. The AGTF
`GameSpecific/.../BallyStyleThemeGameObjectQuery.json` is **stale** against this
game build.

**To fix:** find the real reel-group name in the running scene (enumerate by type
with `FindGameObjects`) and correct the `ReelSets` block. That is a change in the
Perforce workspace, not in this repo.

Also note `GETALLREELDATA` takes **exactly one** argument despite AGTF's own
wrapper calling it with none, and `RETRIEVEREELSYMBOLDATA` must run first to fill
the library's store.

### Denomination cannot be set on this theme

`SELECTDENOM` returns `False` for every format tried (`5¢`, `$0.05`, `0.05`,
`5.000`, `5`). The library explains why:

> "Bally Style theme games just cycle denom on touching the denom panel and
> cannot able to select a specific denom button"

Cycling via `PRESSDENOMCHANGEBUTTON` **delivers the touch** — the game log shows
`InputManager - dispatchMessage: denom_window` once per press — but the game
answers `[WagerGameApp.UpdateDenom] New denom[1.000] Did denom Change[False]`.

**Inference, not verified:** it refuses because credits are on the meter. Most
cabinets forbid a denomination change with a banked balance. To test, clear
credits first.

---

## 9. Integrating into this repo

`CLAUDE.md:916` already states the intent: an enabled GAF "**belongs behind that
module's public API**" — i.e. inside `app/services/game_input.py`, keeping the
`ClickResult` schema and `/api/game-input/click` contract intact.

**The seam.** `game_input.click()` resolves a target name, then calls
`_inject_click()` (cursor move + `SendInput`) and confirms via `_watch_log()`.
A GAF backend replaces both halves and leaves callers untouched. The entire
coupling surface is four call sites in `analyze_spin.py`:

| Line | Call |
|---|---|
| `579` | `ideck_service.status()` |
| `580` | `game_input_service.status()` |
| `724` | `ideck_service.press(button)` |
| `809` | `game_input_service.click(target)` |

**Two schema changes it forces:**

- `ClickConfirmation` (`app/schemas/game_input.py`) needs a third member. Today
  confirmation is `target-event` or `touch`, both derived from log tailing; GAF
  returns its own acknowledgement, which is a different kind of evidence and
  should not be squeezed into `touch`.
- `ClickTarget.from_value` (`app/utils/click_target.py`) needs a third form
  alongside `[x, y]` and `{"point": …, "confirm": …}` — a named GameObject
  instead of a coordinate. The loader passes `button_targets` through
  unvalidated, so no loader change is needed.

**What it buys.** No coordinates to re-measure, no cursor theft, no UIPI/High
integrity requirement for clicks, and exact meter strings instead of OCR.

**One caution worth preserving.** `CLAUDE.md` argues the spin checker must not
read the game's own answer, since "a checker that read the answer there would
agree with the game by construction and could never catch a reel drawing the
wrong symbol". GAF reads the Unity **scene graph** — closer to the glass than the
log, but still not pixels. Treat it as a control channel and a **cross-check**
against the image classifier and OCR, not as a silent replacement for them. Two
independent readings that agree are worth more than either alone; a disagreement
is a finding.

**Operational reality.** `NRobot.Server.exe` is a second process this repo
neither starts nor supervises, and the 8 object-query JSON files live outside the
repo in a Perforce workspace that can change under you. Any integration needs a
status endpoint that says plainly when either is missing — the same bargain
`GET /api/obs/status` and `GET /api/ocr/status` already make.

---

## 10. Quick reference

```
Ports          8270  NRobot.Server.exe (XML-RPC)      9090  the game (Thrift)
gameType       BallyStyle                 gdkVersion  12
Endpoint       http://127.0.0.1:8270/RFTestCode.<Area>.<Area>Library
Latency        ~90-130 ms per keyword; ~2.4 s for a cold InitializeGameClient
Losing spin    ~13 s to settle
Indexes        1-based (buttons and reel columns)
JSON files     read with utf-8-sig
```

Libraries loaded by the server (from `NRobot.Server.exe.config`): `ConnectGame`,
`DemoMenu`, `Denom`, `DoubleUp`, `FullGaffer`, `GameMeter`, `GamePlay`,
`GameState`, `GenericWrapper`, `GlobalUI`, `IDeck`, `MultiGame`, `Progressive`,
`Reel`, `RunCMD`, `WagerSaver`, `WebControl`, `WindowControl`.

`FullGafferLibrary.ENTERFULLGAFFERSTOP` forces reel outcomes — useful for
deterministic tests once the reel-reading gap is closed.

Working reference scripts produced alongside this document: `gaf_probe.py`
(six-phase connect/read/press probe) and `spin_and_take_win.py` (spin until a
win, then collect it).
