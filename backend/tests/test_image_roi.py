"""Fractional region cropping.

Two things are worth proving here. The first is the promise the module is built
on: a region declared as fractions crops the *same part of the picture* at any
resolution -- which is asserted against real captures rescaled to 720p, 1080p
and 4K, not only against synthetic images.

The second is that a bad region is rejected where it is read, with a message
naming the region, rather than surfacing later as a blank crop nobody can trace
back to a config file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from app.utils.image_roi import Roi, RoiError, crop, crop_file, named_roi

# The reference region: FortuneOx's cash meter, as its config declares it.
CASH_METER = [0.229264, 0.844468, 0.762349, 0.884554]

GAME_CONFIGS = Path(__file__).resolve().parents[1] / "app/config/game_config/games"
CAPTURES = Path(__file__).resolve().parents[1] / "obs-captured-files/event-capture"


def solid(width: int, height: int, colour: str = "black") -> Image.Image:
    return Image.new("RGB", (width, height), colour)


# --- Reading a region -------------------------------------------------------


def test_reads_the_four_fractions_a_config_declares() -> None:
    region = Roi.from_sequence(CASH_METER)
    assert (region.left, region.top, region.right, region.bottom) == tuple(CASH_METER)


def test_an_existing_region_passes_through() -> None:
    region = Roi.from_sequence(CASH_METER)
    assert Roi.from_sequence(region) is region


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ("0.1,0.2,0.3,0.4", "array of four numbers"),
        ({"left": 0.1}, "array of four numbers"),
        ([0.1, 0.2, 0.3], "got 3 numbers"),
        ([0.1, 0.2, 0.3, 0.4, 0.5], "got 5 numbers"),
        ([0.1, 0.2, "0.3", 0.4], "only numbers"),
        # A bool is an int; as a coordinate it is a mistake, not a 1.
        ([0.1, 0.2, True, 0.4], "only numbers"),
        ([-0.1, 0.2, 0.3, 0.4], "between 0 and 1"),
        ([0.1, 0.2, 1.5, 0.4], "between 0 and 1"),
        ([float("nan"), 0.2, 0.3, 0.4], "between 0 and 1"),
        ([0.8, 0.2, 0.3, 0.4], "must be less than right"),
        ([0.1, 0.9, 0.3, 0.4], "must be less than bottom"),
        # Zero-area is rejected too: it would crop nothing, silently.
        ([0.3, 0.2, 0.3, 0.4], "must be less than right"),
    ],
)
def test_a_malformed_region_is_rejected_with_a_reason(
    values: object, expected: str
) -> None:
    with pytest.raises(RoiError, match=expected):
        Roi.from_sequence(values, where="roi.cash_meter")


def test_the_error_names_the_region_it_came_from() -> None:
    with pytest.raises(RoiError, match=r"roi\.cash_meter"):
        Roi.from_sequence([0.9, 0.2, 0.3, 0.4], where="roi.cash_meter")


def test_a_region_is_looked_up_by_name() -> None:
    assert named_roi({"cash_meter": CASH_METER}, "cash_meter") == Roi(*CASH_METER)


def test_an_unknown_region_lists_the_ones_that_exist() -> None:
    with pytest.raises(RoiError, match=r"win_meter.*cash_meter"):
        named_roi({"cash_meter": CASH_METER}, "win_meter")


# --- Measuring one in pixels instead ----------------------------------------


def test_a_pixel_measurement_becomes_fractions() -> None:
    region = Roi.from_pixels([320, 180, 960, 540], width=1280, height=720)
    assert region == Roi(0.25, 0.25, 0.75, 0.75)


def test_a_pixel_measurement_round_trips_through_the_frame_it_was_made_on() -> None:
    box = (293, 608, 976, 637)
    region = Roi.from_pixels(box, width=1280, height=720)
    assert region.to_box(1280, 720) == box


def test_a_pixel_measurement_outside_its_frame_is_rejected() -> None:
    with pytest.raises(RoiError, match="1280x720"):
        Roi.from_pixels([0, 0, 1400, 720], width=1280, height=720)


# --- Turning fractions into pixels ------------------------------------------


def test_fractions_become_a_pixel_box() -> None:
    assert Roi(0.25, 0.5, 0.75, 1.0).to_box(1280, 720) == (320, 360, 960, 720)


def test_the_cash_meter_lands_where_it_was_measured() -> None:
    assert Roi(*CASH_METER).to_box(1280, 720) == (293, 608, 976, 637)


def test_a_region_thinner_than_a_pixel_still_keeps_one() -> None:
    # 0.001 of a 100px frame rounds both edges to 0; an empty crop would be a
    # confusing way to learn the frame was too small.
    left, top, right, bottom = Roi(0.0001, 0.0001, 0.001, 0.001).to_box(100, 100)
    assert right - left == 1
    assert bottom - top == 1


def test_a_region_at_the_far_edge_stays_inside_the_frame() -> None:
    left, top, right, bottom = Roi(0.9999, 0.9999, 1.0, 1.0).to_box(100, 100)
    assert (right, bottom) == (100, 100)
    assert (left, top) == (99, 99)


def test_a_frame_with_no_area_is_rejected() -> None:
    with pytest.raises(RoiError, match="non-zero width and height"):
        Roi(*CASH_METER).to_box(0, 720)


# --- The promise: the same region at any resolution --------------------------


@pytest.mark.parametrize("size", [(640, 360), (1280, 720), (1920, 1080), (3840, 2160)])
def test_the_same_fractions_crop_the_same_proportion_of_any_frame(
    size: tuple[int, int],
) -> None:
    width, height = size
    left, top, right, bottom = Roi(*CASH_METER).to_box(width, height)

    # Every edge within half a pixel of where the fraction says it should be.
    assert abs(left - CASH_METER[0] * width) <= 0.5
    assert abs(top - CASH_METER[1] * height) <= 0.5
    assert abs(right - CASH_METER[2] * width) <= 0.5
    assert abs(bottom - CASH_METER[3] * height) <= 0.5


def test_the_crop_scales_with_the_frame() -> None:
    region = Roi(*CASH_METER)
    small = region.to_box(1280, 720)
    large = region.to_box(2560, 1440)
    # Doubling the frame doubles the box, give or take the rounding of an edge.
    assert all(
        abs(big - 2 * little) <= 1 for big, little in zip(large, small, strict=True)
    )


def test_the_same_content_is_cropped_from_a_rescaled_frame() -> None:
    """A marked band survives being cropped out of the frame at 3x the size."""
    frame = solid(400, 200)
    frame.paste(Image.new("RGB", (200, 20), "red"), (100, 100))
    scaled = frame.resize((1200, 600), Image.NEAREST)

    region = Roi(0.25, 0.5, 0.75, 0.6)
    from_small = crop(frame, region)
    from_large = crop(scaled, region)

    assert from_small.size == (200, 20)
    assert from_large.size == (600, 60)
    # Both are the red band and nothing else.
    assert from_small.getcolors() == [(200 * 20, (255, 0, 0))]
    assert from_large.getcolors() == [(600 * 60, (255, 0, 0))]


# --- Cropping ---------------------------------------------------------------


def test_crop_takes_the_pixels_inside_the_region() -> None:
    frame = solid(100, 100)
    frame.paste(Image.new("RGB", (10, 10), "white"), (20, 30))

    cropped = crop(frame, [0.2, 0.3, 0.3, 0.4])

    assert cropped.size == (10, 10)
    assert cropped.getcolors() == [(100, (255, 255, 255))]


def test_crop_leaves_the_source_alone() -> None:
    frame = solid(100, 100)
    crop(frame, CASH_METER)
    assert frame.size == (100, 100)


def test_crop_takes_the_fractions_straight_from_a_config() -> None:
    config = json.loads((GAME_CONFIGS / "FortuneOx.json").read_text(encoding="utf-8"))
    frame = solid(1280, 720)

    cropped = crop(frame, named_roi(config["roi"], "cash_meter"))

    assert cropped.size == (683, 29)


def test_crop_file_reads_writes_and_closes(tmp_path: Path) -> None:
    source = tmp_path / "frame.png"
    solid(1280, 720, "blue").save(source)
    destination = tmp_path / "crops" / "cash_meter.png"

    cropped = crop_file(source, CASH_METER, destination=destination)

    assert cropped.size == (683, 29)
    assert destination.exists()
    with Image.open(destination) as written:
        assert written.size == (683, 29)
    # The source is closed, so a later run can overwrite it on Windows.
    source.unlink()


def test_crop_file_without_a_destination_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "frame.png"
    solid(64, 64).save(source)

    crop_file(source, CASH_METER)

    assert list(tmp_path.iterdir()) == [source]


def test_a_missing_file_says_so(tmp_path: Path) -> None:
    with pytest.raises(RoiError, match="could not be read as an image"):
        crop_file(tmp_path / "nope.png", CASH_METER)


def test_a_file_that_is_not_an_image_says_so(tmp_path: Path) -> None:
    source = tmp_path / "frame.png"
    source.write_text("this is not a png", encoding="utf-8")

    with pytest.raises(RoiError, match="could not be read as an image"):
        crop_file(source, CASH_METER)


# --- Against real captures ---------------------------------------------------


def captured_frames() -> list[Path]:
    """Screenshots from past event-capture runs, newest run first.

    ``obs-captured-files/`` is gitignored output: it is there on the machine the
    captures were taken on and empty on a fresh clone or in CI. The tests that
    use it skip rather than fail, so a checkout without captures is not a broken
    test suite -- but on a machine that has them, the region is checked against
    the frames it was actually measured on.
    """
    if not CAPTURES.is_dir():
        return []
    return sorted(CAPTURES.glob("*/*.png"), reverse=True)


needs_captures = pytest.mark.skipif(
    not captured_frames(), reason="no event-capture screenshots on this machine"
)


@needs_captures
def test_the_cash_meter_crops_out_of_a_real_capture(tmp_path: Path) -> None:
    frame_path = captured_frames()[0]

    cropped = crop_file(frame_path, CASH_METER, destination=tmp_path / "meter.png")

    with Image.open(frame_path) as frame:
        expected = Roi(*CASH_METER).to_box(frame.width, frame.height)
    assert cropped.size == (expected[2] - expected[0], expected[3] - expected[1])
    # A meter is a wide, short strip; a transposed region would fail this.
    assert cropped.width > cropped.height
    # And it is not the letterbox: a solid crop would mean the region missed.
    assert len(cropped.convert("RGB").getcolors(maxcolors=1 << 16) or []) > 1


@needs_captures
@pytest.mark.parametrize("scale", [0.5, 1.5, 3.0])
def test_a_real_capture_crops_the_same_region_at_another_resolution(
    scale: float,
) -> None:
    """The point of fractions: rescale the frame, get the same picture back."""
    region = Roi(*CASH_METER)
    with Image.open(captured_frames()[0]) as frame:
        native = crop(frame, region).convert("RGB")
        resized = frame.resize(
            (round(frame.width * scale), round(frame.height * scale)), Image.LANCZOS
        )
    rescaled = crop(resized, region).convert("RGB")

    # Same region of the same picture, so the same content at a different size.
    # Each edge is rounded independently on each frame, so a side can be half a
    # pixel out here and half a scaled pixel out there: 1 + scale in total.
    assert abs(rescaled.width - native.width * scale) <= 1 + scale
    assert abs(rescaled.height - native.height * scale) <= 1 + scale

    # Compare them as pictures: reduce both to the same coarse grid of grey
    # levels, and the meter's light and dark bands still have to line up.
    #
    # The threshold is measured rather than guessed. On these captures the
    # rescales come out at a mean difference of 0.5 (3x) to 3.0 (0.5x) grey
    # levels -- resampling loss, not misalignment -- while sliding the region
    # sideways by 2% of the frame, the smallest shift worth catching, jumps it
    # to 14. Anything under 6 is resampling; anything over it is a bug.
    thumb = (32, 8)
    native_bands = native.resize(thumb, Image.LANCZOS).convert("L").tobytes()
    rescaled_bands = rescaled.resize(thumb, Image.LANCZOS).convert("L").tobytes()
    differences = [
        abs(a - b) for a, b in zip(native_bands, rescaled_bands, strict=True)
    ]
    drift = sum(differences) / len(differences)
    assert drift <= 6, (
        f"the rescaled crop is a different picture (mean drift {drift:.1f})"
    )
