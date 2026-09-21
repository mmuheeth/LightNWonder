"""JSON reader for the per-game configuration files."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.config.game_config.models import GameConfig, GameConfigError, freeze_mapping
from app.utils import paylines as payline_config
from app.utils.game_log import EventRule, LogRuleError, compile_rules
from app.utils.ocr import OcrOptionsError
from app.utils.ocr import parse_overrides as parse_ocr_overrides

__all__ = ["load_game_config"]


def _object(raw: Any, *, where: str) -> Mapping[str, Any]:
    """Narrow a decoded JSON value to an object, or explain what it was."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise GameConfigError(f"{where} must be a JSON object")
    return raw


def _ocr(raw: Any, *, path: Path) -> dict[str, Mapping[str, Any]]:
    """Read the optional ``ocr`` block: option overrides per named region."""
    block = _object(raw, where=f"'ocr' in {path}")
    overrides: dict[str, Mapping[str, Any]] = {}
    for region, values in block.items():
        where = f"'ocr.{region}' in {path}"
        try:
            overrides[region] = parse_ocr_overrides(values, where=where)
        except OcrOptionsError as exc:
            raise GameConfigError(str(exc)) from exc
    return overrides


def _fractions(raw: Any, *, where: str) -> tuple[float, float]:
    """Narrow a decoded JSON value to an ordered pair of fractions."""
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) != 2:
        raise GameConfigError(f"{where} must be an array of two numbers")
    pair = []
    for value in raw:
        # `bool` is an `int`, and `true` as a coordinate is a mistake, not a 1.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GameConfigError(f"{where} must contain only numbers, got {value!r}")
        if not 0.0 <= float(value) <= 1.0:
            raise GameConfigError(
                f"{where} must be fractions between 0 and 1, got {value!r}"
            )
        pair.append(float(value))
    if pair[0] >= pair[1]:
        raise GameConfigError(f"{where} must be [low, high], got {pair}")
    return pair[0], pair[1]


def _meter(raw: Any, *, path: Path) -> dict[str, Any]:
    """Read the optional ``meter`` block: how to read the cash meter strip."""
    block = _object(raw, where=f"'meter' in {path}")
    parsed: dict[str, Any] = {}
    if "band" in block:
        parsed["band"] = _fractions(block["band"], where=f"'meter.band' in {path}")
    if "windows" in block:
        windows = _object(block["windows"], where=f"'meter.windows' in {path}")
        parsed["windows"] = {
            str(field): _fractions(span, where=f"'meter.windows.{field}' in {path}")
            for field, span in windows.items()
        }
    unknown = set(block) - {"band", "windows"}
    if unknown:
        known = "band, windows"
        raise GameConfigError(
            f"'meter' in {path} has no {', '.join(sorted(unknown))} setting "
            f"(settings are: {known})"
        )
    return parsed


def _path(raw: Any, *, where: str) -> Path | None:
    """Read an optional absolute path to something the game installed."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise GameConfigError(f"{where} must be a string")
    value = raw.strip()
    return Path(value) if value else None


def _symbols(raw: Any, *, path: Path) -> dict[str, str]:
    """Read the optional ``symbols`` block: display name per symbol code."""
    block = _object(raw, where=f"'symbols' in {path}")
    names: dict[str, str] = {}
    for code, label in block.items():
        if not isinstance(label, str):
            raise GameConfigError(f"'symbols.{code}' in {path} must be a string")
        if label.strip():
            names[str(code).strip().upper()] = label.strip()
    return names


def _symbol_codes(raw: Any, *, where: str) -> tuple[str, ...]:
    """Narrow a decoded JSON value to an ordered, de-duplicated list of symbol
    codes -- the shape both ``wild_card_replacement`` and ``scatter_symbols`` take."""
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise GameConfigError(f"{where} must be an array of symbol codes")
    codes: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise GameConfigError(
                f"{where} must contain only symbol codes, got {entry!r}"
            )
        code = entry.strip().upper()
        if code and code not in codes:
            codes.append(code)
    return tuple(codes)


def _wild_card_replacement(raw: Any, *, path: Path) -> tuple[str, ...]:
    """Read the optional ``wild_card_replacement`` block: the symbol codes the wild
    stands in for."""
    where = f"'wild_card_replacement' in {path}"
    codes = _symbol_codes(raw, where=where)
    if payline_config.WILD_SYMBOL in codes:
        raise GameConfigError(
            f"{where} lists {payline_config.WILD_SYMBOL!r}, which is the wild "
            "itself -- the block is what the wild stands in for, not what stands "
            "in for it"
        )
    return codes


def _scatter_symbols(raw: Any, *, path: Path) -> tuple[str, ...]:
    """Read the optional ``scatter_symbols`` block: the codes this game pays by
    counting across the grid. Checked against ``wild_card_replacement`` by the
    caller, which is the only place both are in hand."""
    return _symbol_codes(raw, where=f"'scatter_symbols' in {path}")


def _events(raw: Any, *, path: Path) -> tuple[tuple[EventRule, ...], tuple[str, ...]]:
    """Read the optional ``events`` block: extra rules, and defaults to drop."""
    block = _object(raw, where=f"'events' in {path}")

    disable = block.get("disable")
    if disable is None:
        disabled: tuple[str, ...] = ()
    elif isinstance(disable, list) and all(isinstance(item, str) for item in disable):
        disabled = tuple(disable)
    else:
        raise GameConfigError(f"'events.disable' in {path} must be an array of strings")

    try:
        rules = compile_rules(block.get("rules"), where=f"'events.rules' in {path}")
    except LogRuleError as exc:
        raise GameConfigError(str(exc)) from exc

    return rules, disabled


def load_game_config(path: Path) -> GameConfig:
    """Read and validate one game config; every block is optional, but an
    unreadable or malformed file raises :class:`GameConfigError`."""
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GameConfigError(f"No game config at {path}") from exc
    except json.JSONDecodeError as exc:
        raise GameConfigError(
            f"Game config at {path} is not valid JSON: {exc}"
        ) from exc
    except OSError as exc:
        raise GameConfigError(
            f"Could not read the game config at {path}: {exc}"
        ) from exc

    document = _object(raw, where=f"The game config at {path}")

    log = document.get("log")
    if log is not None and not isinstance(log, str):
        raise GameConfigError(f"'log' in {path} must be a string")

    name = document.get("name") or path.stem
    if not isinstance(name, str):
        raise GameConfigError(f"'name' in {path} must be a string")

    process = document.get("process")
    if process is not None and not isinstance(process, str):
        raise GameConfigError(f"'process' in {path} must be a string")

    event_rules, disabled_events = _events(document.get("events"), path=path)

    obs = _object(document.get("obs"), where=f"'obs' in {path}")
    obs_window_source = obs.get("window_source")
    if obs_window_source is not None and not isinstance(obs_window_source, str):
        raise GameConfigError(f"'obs.window_source' in {path} must be a string")

    wild_card_replacement = _wild_card_replacement(
        document.get("wild_card_replacement"), path=path
    )
    scatter_symbols = _scatter_symbols(document.get("scatter_symbols"), path=path)
    # The two blocks are complements: a scatter is exactly what the wild must not
    # be read as. A code in both makes the payline check and the scatter report
    # disagree about the same tile, which is worth refusing at load time.
    both = [code for code in scatter_symbols if code in wild_card_replacement]
    if both:
        raise GameConfigError(
            f"'scatter_symbols' in {path} lists {', '.join(both)}, which "
            "'wild_card_replacement' also lists -- a scatter is what the wild "
            "must not stand in for"
        )

    return GameConfig(
        name=name,
        path=path,
        process=process,
        obs_window_source=obs_window_source,
        log_path=Path(log) if log else None,
        game_config_dir=_path(
            document.get("game_config"), where=f"'game_config' in {path}"
        ),
        win_geometry_path=_path(
            document.get("win_geometry"), where=f"'win_geometry' in {path}"
        ),
        symbols=freeze_mapping(_symbols(document.get("symbols"), path=path)),
        wild_card_replacement=wild_card_replacement,
        scatter_symbols=scatter_symbols,
        roi=freeze_mapping(_object(document.get("roi"), where=f"'roi' in {path}")),
        reel_bounds=freeze_mapping(
            _object(document.get("reel_bounds"), where=f"'reel_bounds' in {path}")
        ),
        paylines=freeze_mapping(
            _object(document.get("paylines"), where=f"'paylines' in {path}")
        ),
        ocr=freeze_mapping(_ocr(document.get("ocr"), path=path)),
        button_targets=freeze_mapping(
            _object(document.get("button_targets"), where=f"'button_targets' in {path}")
        ),
        meter=freeze_mapping(_meter(document.get("meter"), path=path)),
        event_rules=event_rules,
        disabled_events=disabled_events,
    )
