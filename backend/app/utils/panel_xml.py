"""Reader for the button-panel layout that ``OledPanelSvc`` itself renders from.

The panel service parses an XML layout at startup and logs the file it chose::

    root=C:\\ssd\\cabinet\\deployment\\cfg\\ButtonPanel\\ file=virtual_oled.xml

Reading that same file is what keeps this integration honest: button geometry
has exactly one source of truth, and a panel re-layout does not need a matching
edit here.

A button's *outer* size is not stated directly. It comes from the referenced
``ButtonTemplate``: the ``TextBox`` extent grown by the bezel on each side::

    <ButtonTemplate id="small_button" bezel_width="5">
      <TextBox x="5" y="5" width="96" height="64" />
    </ButtonTemplate>

    -> 96 + 2*5 = 106 wide, 64 + 2*5 = 74 tall

which reproduces the ``POS: x=.. y=.. w=106 h=74`` lines the service logs while
parsing. A template may also state ``width``/``height`` outright, and those win.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


class PanelXmlError(ValueError):
    """The layout file is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True)
class PanelButton:
    """One key, positioned in the panel's own coordinate space."""

    xml_id: str
    """Name from the layout. Case is preserved."""

    button_id: int
    """Hardware switch number. The panel service logs this in *hex*."""

    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        """Middle of the key, which is where a press is aimed."""
        return (self.x + self.width // 2, self.y + self.height // 2)


@dataclass(frozen=True)
class PanelLayout:
    """A parsed panel: its declared size and every key on it."""

    panel_id: str
    width: int
    height: int
    buttons: tuple[PanelButton, ...]

    def by_xml_id(self, xml_id: str) -> PanelButton | None:
        """Look a key up by name, case-insensitively."""
        wanted = xml_id.casefold()
        return next((b for b in self.buttons if b.xml_id.casefold() == wanted), None)

    def to_client(
        self, button: PanelButton, *, client_width: int, client_height: int
    ) -> tuple[int, int]:
        """Where a key's centre sits in a window showing this panel.

        The layout states a fixed panel size; a window may be showing it at a
        different scale, so the centre is mapped through the ratio of the two
        rather than used raw.

        The result is clamped into the window: a rounded edge case could land one
        pixel outside, and a press must fall inside the window it is addressed
        to.
        """
        x, y = button.center
        if self.width > 0 and self.height > 0:
            x = round(x * client_width / self.width)
            y = round(y * client_height / self.height)
        return (
            max(0, min(x, client_width - 1)),
            max(0, min(y, client_height - 1)),
        )


def _require_int(element: ElementTree.Element, attribute: str, *, where: str) -> int:
    raw = element.get(attribute)
    if raw is None:
        raise PanelXmlError(f"{where} is missing the {attribute!r} attribute")
    try:
        return int(raw)
    except ValueError as exc:
        raise PanelXmlError(
            f"{where} has a non-numeric {attribute!r} attribute: {raw!r}"
        ) from exc


def _template_sizes(root: ElementTree.Element) -> dict[str, tuple[int, int]]:
    """Map every ``ButtonTemplate`` id to the outer size it implies."""
    sizes: dict[str, tuple[int, int]] = {}
    for template in root.iterfind("./ButtonTemplates/ButtonTemplate"):
        template_id = template.get("id")
        if not template_id:
            raise PanelXmlError("a <ButtonTemplate> is missing its 'id' attribute")
        where = f"<ButtonTemplate id={template_id!r}>"

        explicit_w, explicit_h = template.get("width"), template.get("height")
        if explicit_w is not None and explicit_h is not None:
            sizes[template_id] = (
                _require_int(template, "width", where=where),
                _require_int(template, "height", where=where),
            )
            continue

        text_box = template.find("./TextBox")
        if text_box is None:
            raise PanelXmlError(
                f"{where} states neither width/height nor a <TextBox> to derive them from"
            )
        bezel = int(template.get("bezel_width", "0"))
        sizes[template_id] = (
            _require_int(text_box, "width", where=f"{where} <TextBox>") + 2 * bezel,
            _require_int(text_box, "height", where=f"{where} <TextBox>") + 2 * bezel,
        )
    return sizes


def parse_panel(path: Path) -> PanelLayout:
    """Read a panel layout file.

    Raises:
        PanelXmlError: if the file is absent, is not well-formed XML, declares no
            ``<Panel>``, or references a ``template_id`` it never defines.
    """
    try:
        tree = ElementTree.parse(path)
    except FileNotFoundError as exc:
        raise PanelXmlError(f"Panel layout not found at {path}") from exc
    except ElementTree.ParseError as exc:
        raise PanelXmlError(f"Panel layout at {path} is not valid XML: {exc}") from exc
    except OSError as exc:
        raise PanelXmlError(
            f"Could not read the panel layout at {path}: {exc}"
        ) from exc

    root = tree.getroot()
    panel = root.find("./Panel") if root.tag != "Panel" else root
    if panel is None:
        raise PanelXmlError(f"Panel layout at {path} contains no <Panel> element")

    sizes = _template_sizes(root)
    buttons: list[PanelButton] = []
    seen: dict[int, str] = {}

    for element in panel.iterfind("./Buttons/Button"):
        xml_id = element.get("id")
        if not xml_id:
            raise PanelXmlError("a <Button> is missing its 'id' attribute")
        where = f"<Button id={xml_id!r}>"

        template_id = element.get("template_id")
        if template_id is None:
            raise PanelXmlError(f"{where} is missing the 'template_id' attribute")
        if template_id not in sizes:
            raise PanelXmlError(f"{where} references unknown template {template_id!r}")

        button_id = _require_int(element, "button_id", where=where)
        # Two keys reporting the same switch would make a press unverifiable:
        # the log only ever names the switch, never the key.
        if button_id in seen:
            raise PanelXmlError(
                f"{where} reuses button_id {button_id}, already used by "
                f"{seen[button_id]!r}"
            )
        seen[button_id] = xml_id

        width, height = sizes[template_id]
        buttons.append(
            PanelButton(
                xml_id=xml_id,
                button_id=button_id,
                x=_require_int(element, "x", where=where),
                y=_require_int(element, "y", where=where),
                width=width,
                height=height,
            )
        )

    if not buttons:
        raise PanelXmlError(f"Panel layout at {path} declares no buttons")

    where_panel = "<Panel>"
    return PanelLayout(
        panel_id=panel.get("id") or path.stem,
        width=_require_int(panel, "width", where=where_panel),
        height=_require_int(panel, "height", where=where_panel),
        buttons=tuple(buttons),
    )
