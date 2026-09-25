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

**This contradicted the repo docs.** `CLAUDE.md` and `backend/README.md` both
said GAF "is **not started by a normal simulator launch**". That was true of
the older FortuneOx build; it is **false** for `HuffNPuffHighRise`, whose
client log records `Starting the Thrift Server ....` on every launch. Both
passages have been corrected, and the integration described in §9 is built —
`POST /api/gaf/spin`.

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
| `NRobot.Server.exe`, running | `C:\ProgramData\chocolatey\lib\AGF-LnW\content\` | Started by `NRobotStartUpScript.bat`. **Not** started or supervised by this repo — a logon task does it on this machine, see below |
| The 8 object-query JSON files | the AGTF Perforce workspace — see §4 | **Hard dependency** |
| Python | any 3.11+ | stdlib only |

Diagnostics:

- NRobot server log: `C:\AGTF\logs\NRobotServer.log`
- Game client log: `C:\logs\Game\HuffNPuffHighRise\Logs\HuffNPuffHighRise_Client.log`
- Minimum AGF image version is `0.45.2.321`; read the live one with
  `RunCMDLibrary.GETVERSIONNUMBER`. **Verified:** this machine is exactly `0.45.2.321`.

`NRobot.Server.exe` uses `HttpListener`, so port 8270's owning PID shows as
`4`/System in `Get-NetTCPConnection`. That is normal, not a permissions problem.

### It does not survive a reboot, and nothing in the repo restarts it

**Verified 2026-09-24:** the log stopped at 16:26:57 and the machine booted at
16:28:09 — no crash, no error, just gone. Every GAF action then fails with
`unreachable` until someone starts it again. This machine now carries a
per-user scheduled task, `NRobot Server (AGF)`, which is **machine setup and
not part of this repo** — the repo still neither starts nor supervises it:

```powershell
Get-ScheduledTask -TaskName 'NRobot Server (AGF)'      # registered, AtLogOn
Start-ScheduledTask -TaskName 'NRobot Server (AGF)'    # start it now
```

It runs the exe with `WorkingDirectory` set rather than invoking the .bat,
because that directory is the only thing the .bat's `cd %~dp0` exists to
establish, and a task holding the process open lets Task Scheduler restart it
if it dies. Starting it by hand does the same job:

```powershell
Start-Process 'C:\ProgramData\chocolatey\lib\AGF-LnW\content\NRobot.Server.exe' -WorkingDirectory 'C:\ProgramData\chocolatey\lib\AGF-LnW\content'
```

**Elevation is irrelevant here, unlike the i-deck.** Verified by accident: a
Medium-integrity NRobot answered the elevated backend fine. UIPI gates
synthetic *input*, and this is HTTP on loopback — so the `access_denied` rule
from `GET /api/ideck/status` does not transfer to GAF.

### Two different faults both read as `unreachable`

The working directory matters because the 18 `RFTestCode.*` assemblies resolve
relative to it. A server started elsewhere **binds 8270 and answers**, with no
keywords — so "nothing is listening" and "the wrong thing is listening" look
identical from outside. `RemoteLibrary.probe()` hands the reason back instead
of a bool for exactly this, and `GafStatus.detail` carries it. The second case
is **verified** to read:

```
RFTestCode.ConnectGame.ConnectGameLibrary.get_keyword_names faulted:
<Fault 1: 'Type RFTestCode.ConnectGame.ConnectGameLibrary is not loaded'>
```

An XML-RPC **fault**, note — not the HTTP 404 you would expect from a missing
endpoint.

### Do not force-kill it while a session is open

**Verified 2026-09-24.** `Stop-Process -Force` on NRobot mid-session leaves the
socket to the game half-closed (`FinWait2`, owner PID already gone), and the
game keeps its end. The next `connect` then fails after a **20s** timeout with
`Game Client is unable to connect to Thrift server` *while a raw TCP connect to
9090 still succeeds in 43ms* — so the port answering proves nothing here. A
retry a minute later succeeded, and the game passed briefly through
`stateRecoveryStarted` before settling back to `stateIdleWithCredits` on its
own. Prefer `POST /api/gaf/disconnect` first; note the same failure text takes
**2s** when the game simply is not there, so the duration is what separates the
two.

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

## 9. How it is integrated — **built**

Landed as its own integration rather than behind `game_input.click()`, which
is where §9 of the first draft expected it. The reason is scope: GAF does not
just deliver a click, it *drives* the game — spin, collect, read state, read
meters — and squeezing that through a schema whose vocabulary is
`ClickTarget`/`ClickConfirmation` would have made both harder to read.
`game_input.py` is untouched, and stays the answer for a game whose simulator
does not host the service.

| Piece | What it is |
|---|---|
| `app/utils/nrobot.py` | the Robot Framework remote protocol over XML-RPC. Knows nothing about slot games |
| `app/utils/gaf_objects.py` | reads and merges the eight object-query files. Knows nothing about NRobot |
| `app/config/gaf.py` | `GafSettings` + `resolve_target()`: the environment's defaults and a game's `gaf` block |
| `app/services/gaf.py` | the session and the order. `status`/`connect`/`disconnect`/`spin`/`take_win` |
| `app/schemas/gaf.py`, `app/api/endpoints/gaf.py` | `/api/gaf/*`, in the usual envelope |
| `frontend/src/features/gaf/` | the dashboard card |

The per-game block, in `app/config/game_config/games/<Game>.json`:

```json
"gaf": {
  "host": "127.0.0.1", "port": 9090,
  "game_type": "BallyStyle", "gdk_version": "12",
  "object_query_root": "C:\Users\...\<AGTF workspace>",
  "take_win_button": "TakeWinButton"
}
```

Only `object_query_root` has no default. `object_query_root` joins
`game_config` and `win_geometry` as a key pointing *out* of the repo, and is
deliberately unchecked at load time for the same reason.

### What measuring it against the live game changed

Everything in §7 held. Three things it did not cover:

- **A spin does not start the instant the button is pressed.** The game stays
  in its pre-press state for a beat, so the §7 settle loop's first question —
  "are we idle?" — is answered *yes* by the state the game was already in, and
  a spin still turning reads as finished. There is now a departure wait before
  the settle loop.
- **A bonus outlasts the settle timeout.** One spin in twelve triggered free
  games, held `statePlaying` past 120s and came back `timeout` — correctly.
  What was not correct is what happened next: the following spin pressed
  straight into the running bonus, so its "result" belonged to the previous
  spin. `spin()` now refuses a game in `statePlaying` (409 `GAF_NOT_IDLE`)
  unless forced.
- **The mid-rack-up warning is bigger than it looks.** The same spin read
  `$115.16` on its way to a collect that reported **`$327.50`**. Only the
  post-collect figure is the win.

Measured on this machine: ~4.4–12.8s for a base spin to settle (not the ~13s
of §7), ~0.9–2.4s for a collect, ~110ms for a state or meter read, 106 named
objects in the merged dictionary.

### Still open

- **Reels cannot be read** (§8), so the image classifier remains the only
  thing that names a symbol. That is no loss: `CLAUDE.md` argues the spin
  checker must not read the game's own answer, and a GAF reel read would be
  exactly that. Treat GAF as a control channel and a cross-check.
- **`analyze_spin` still uses the i-deck and posted clicks.** Swapping its
  press and take-win steps for `gaf_service` is the obvious next move and
  would remove that pipeline's elevation requirement, but it changes what a
  step's `error` means and is worth doing deliberately.
- **Nothing here starts `NRobot.Server.exe`**, and the object-query files can
  change under us in Perforce. `GET /api/gaf/status` reports `unreachable` and
  `files_missing` for exactly those two.

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

The working code this document produced is now in the repo:
`backend/app/utils/nrobot.py` (the protocol),
`backend/app/utils/gaf_objects.py` (the dictionary) and
`backend/app/services/gaf.py` (the session and the order), behind
`/api/gaf/*`. See §9.
