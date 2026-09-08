"""Generate theme-aware ASCII portraits from the committed headshot.

Ink is treated as halftone density. On the dark card, bright glyphs mark ink,
so dark clothing falls away and the lit face carries detail. On the light card
the polarity flips. Tone is error-diffused across a short glyph ramp so the
portrait reads as a stipple instead of a solid slab of characters.

Crop presets only change how much shoulder is kept. Regenerate from the repo
root after installing tools/requirements.txt:

    python3 tools/ascii_portrait.py --preset balanced
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps


HERE = Path(__file__).resolve().parent
COLS, ROWS = 430, 412
CHAR_WIDTH, LINE_HEIGHT = 0.6, 1.0
SUPERSAMPLE = 5

# Short density ramp: Floyd–Steinberg fills the gaps as stipple texture.
RAMP: tuple[tuple[str, float], ...] = (
    (" ", 0.00),
    (".", 0.25),
    (":", 0.50),
    ("*", 0.75),
    ("o", 1.00),
)
EDGE_FLOOR = 0.16

RAMP_CHARS = np.array([glyph for glyph, _ in RAMP])
RAMP_INK = np.array([ink for _, ink in RAMP], dtype=np.float64)


@dataclass(frozen=True)
class ToneCurve:
    black_point: float
    white_point: float
    gamma: float
    ceiling: float


@dataclass(frozen=True)
class CropPreset:
    source: str
    center_x: int
    top: int
    height: int
    base_gain: float
    detail_gain: float
    edge_gain: float
    rim_gain: float
    interior_floor: float
    dark: ToneCurve
    light: ToneCurve


# Source is 460×460; framing is measured on the head, not the full body box.
# Dark panel: keep midtones lifted so hair/suit don't vanish into a silhouette.
DARK_TONE = ToneCurve(2.0, 98.0, 0.78, 1.00)
LIGHT_TONE = ToneCurve(1.0, 99.0, 1.45, 0.70)

PRESETS = {
    # 460×460 avatar: keep the hairline near the top of the glyph grid.
    "open": CropPreset(
        "headshot.png", 230, 32, 420,
        0.88, 2.70, 0.40, 0.78, 0.24, DARK_TONE, LIGHT_TONE,
    ),
    "balanced": CropPreset(
        "headshot.png", 230, 50, 395,
        0.88, 2.70, 0.40, 0.78, 0.24, DARK_TONE, LIGHT_TONE,
    ),
    "tight": CropPreset(
        "headshot.png", 230, 70, 360,
        0.88, 2.70, 0.40, 0.78, 0.24, DARK_TONE, LIGHT_TONE,
    ),
}

POLARITIES = ("dark", "light")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=PRESETS, default="balanced")
    parser.add_argument(
        "--polarity",
        choices=POLARITIES,
        default=None,
        help="build one panel; omit to write both polarities",
    )
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument(
        "--center-x",
        type=int,
        default=None,
        help="optional crop center override (pixels in source image)",
    )
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def art_path(polarity: str) -> Path:
    return HERE / f"ascii_art_{polarity}.txt"


def crop_box(preset: CropPreset) -> tuple[int, int, int, int]:
    width = round(preset.height * (COLS * CHAR_WIDTH) / (ROWS * LINE_HEIGHT))
    left = preset.center_x - width // 2
    return left, preset.top, left + width, preset.top + preset.height


def flood_from_border(candidate: np.ndarray) -> np.ndarray:
    """Keep only the border-connected component of a boolean mask."""
    reached = np.zeros_like(candidate)
    reached[0] |= candidate[0]
    reached[-1] |= candidate[-1]
    reached[:, 0] |= candidate[:, 0]
    reached[:, -1] |= candidate[:, -1]
    while True:
        grown = reached.copy()
        grown[1:] |= reached[:-1]
        grown[:-1] |= reached[1:]
        grown[:, 1:] |= reached[:, :-1]
        grown[:, :-1] |= reached[:, 1:]
        grown &= candidate
        if grown.sum() == reached.sum():
            return grown
        reached = grown


def subject_mask(hsv: np.ndarray) -> np.ndarray:
    """True on the subject; false on the near-white studio backdrop."""
    saturation, value = hsv[..., 1], hsv[..., 2]
    backdrop = (saturation <= 35) & (value >= 220)
    return ~flood_from_border(backdrop)


def local_contrast(gray: np.ndarray, preset: CropPreset) -> np.ndarray:
    blurred = np.asarray(
        Image.fromarray(gray.astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(radius=SUPERSAMPLE * 2.5)
        ),
        dtype=np.float64,
    )
    mid = 128.0
    broad = mid + (blurred - mid) * preset.base_gain
    return broad + (gray - blurred) * preset.detail_gain


def largest_mass(drawn: np.ndarray) -> np.ndarray:
    """Drop stray background islands by growing from the centre column."""
    rows, cols = drawn.shape
    seed = np.zeros_like(drawn)
    band = slice(int(cols * 0.35), int(cols * 0.65))
    seed[int(rows * 0.20) : int(rows * 0.95), band] = drawn[
        int(rows * 0.20) : int(rows * 0.95), band
    ]
    if not seed.any():
        return drawn
    reached = seed
    while True:
        grown = reached.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                grown |= np.roll(np.roll(reached, dy, axis=0), dx, axis=1)
        grown &= drawn
        grown[0] &= drawn[0]
        grown[-1] &= drawn[-1]
        if grown.sum() == reached.sum():
            return grown
        reached = grown


def edge_energy(gray: np.ndarray) -> np.ndarray:
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[1:-1, 1:-1] = (
        gray[:-2, 2:] + 2 * gray[1:-1, 2:] + gray[2:, 2:]
        - gray[:-2, :-2] - 2 * gray[1:-1, :-2] - gray[2:, :-2]
    )
    gy[1:-1, 1:-1] = (
        gray[2:, :-2] + 2 * gray[2:, 1:-1] + gray[2:, 2:]
        - gray[:-2, :-2] - 2 * gray[:-2, 1:-1] - gray[:-2, 2:]
    )
    magnitude = np.hypot(gx, gy)
    ceiling = float(np.percentile(magnitude, 99.0) or 1.0)
    return np.clip(magnitude / ceiling, 0.0, 1.0)


def cell_fields(
    source: Path, preset: CropPreset
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    image = Image.open(source).convert("RGB").crop(crop_box(preset))
    high_res = image.resize(
        (COLS * SUPERSAMPLE, ROWS * SUPERSAMPLE), Image.Resampling.LANCZOS
    )
    hsv = np.asarray(high_res.convert("HSV"), dtype=np.int32)
    gray = np.asarray(
        ImageOps.autocontrast(high_res.convert("L"), cutoff=1), dtype=np.float64
    )
    gray = np.clip(local_contrast(gray, preset), 0.0, 255.0)
    subject = subject_mask(hsv).astype(np.float64)
    blocks = (ROWS, SUPERSAMPLE, COLS, SUPERSAMPLE)
    coverage = subject.reshape(blocks).mean(axis=(1, 3))
    weighted = (gray * subject).reshape(blocks).sum(axis=(1, 3))
    counted = subject.reshape(blocks).sum(axis=(1, 3))
    luminance = np.divide(
        weighted, counted, out=np.zeros_like(weighted), where=counted > 0
    )
    edges = (edge_energy(gray) * subject).reshape(blocks).mean(axis=(1, 3))
    return coverage, luminance, edges


def ink_field(
    coverage: np.ndarray,
    luminance: np.ndarray,
    edges: np.ndarray,
    preset: CropPreset,
    polarity: str,
) -> np.ndarray:
    tone = preset.light if polarity == "light" else preset.dark
    drawn = largest_mass(coverage > 0.40)
    if not drawn.any():
        return np.zeros_like(luminance)

    values = luminance[drawn]
    black = np.percentile(values, tone.black_point)
    white = np.percentile(values, tone.white_point)
    span = max(1.0, float(white - black))
    normalized = np.clip((luminance - black) / span, 0.0, 1.0)
    if polarity == "light":
        normalized = 1.0 - normalized

    ink = (normalized**tone.gamma) * tone.ceiling
    ink = np.clip(
        ink + np.where(edges > EDGE_FLOOR, edges, 0.0) * preset.edge_gain,
        0.0,
        1.0,
    )
    ink = np.where(drawn, np.maximum(ink, preset.interior_floor), ink)

    interior = drawn.copy()
    interior[1:] &= drawn[:-1]
    interior[:-1] &= drawn[1:]
    interior[:, 1:] &= drawn[:, :-1]
    interior[:, :-1] &= drawn[:, 1:]
    ink = np.where(drawn & ~interior, np.maximum(ink, preset.rim_gain), ink)
    ink *= np.clip((coverage - 0.15) / 0.55, 0.0, 1.0)
    # Re-assert silhouette mass after the coverage fade; without this the dark
    # card collapses dark hair/suit into an empty shadow outline.
    floor = preset.interior_floor * (0.85 if polarity == "dark" else 0.55)
    ink = np.where(drawn & (coverage > 0.25), np.maximum(ink, floor), ink)
    ink = np.where(drawn & ~interior, np.maximum(ink, preset.rim_gain * 0.85), ink)

    fringe = np.zeros_like(drawn)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            fringe |= np.roll(np.roll(drawn, dy, axis=0), dx, axis=1)
    return np.where(fringe & (coverage > 0.15), ink, 0.0)


def floyd_steinberg(ink: np.ndarray) -> list[str]:
    field = ink.astype(np.float64).copy()
    rows, cols = field.shape
    out = np.full((rows, cols), " ", dtype="<U1")
    for y in range(rows):
        for x in range(cols):
            target = field[y, x]
            index = int(np.argmin(np.abs(RAMP_INK - target)))
            out[y, x] = RAMP_CHARS[index]
            error = target - RAMP_INK[index]
            if x + 1 < cols:
                field[y, x + 1] += error * 7 / 16
            if y + 1 < rows:
                if x > 0:
                    field[y + 1, x - 1] += error * 3 / 16
                field[y + 1, x] += error * 5 / 16
                if x + 1 < cols:
                    field[y + 1, x + 1] += error * 1 / 16
    return ["".join(row).rstrip() for row in out]


def generate(source: Path, preset: CropPreset, polarity: str) -> list[str]:
    coverage, luminance, edges = cell_fields(source, preset)
    return floyd_steinberg(ink_field(coverage, luminance, edges, preset, polarity))


def with_overrides(preset: CropPreset, args: argparse.Namespace) -> CropPreset:
    return CropPreset(
        source=preset.source,
        center_x=args.center_x if args.center_x is not None else preset.center_x,
        top=args.top if args.top is not None else preset.top,
        height=args.height if args.height is not None else preset.height,
        base_gain=preset.base_gain,
        detail_gain=preset.detail_gain,
        edge_gain=preset.edge_gain,
        rim_gain=preset.rim_gain,
        interior_floor=preset.interior_floor,
        dark=preset.dark,
        light=preset.light,
    )


def main() -> None:
    args = parse_args()
    polarities = [args.polarity] if args.polarity else list(POLARITIES)
    if args.output and len(polarities) > 1:
        raise SystemExit("--output requires a single --polarity")

    preset = with_overrides(PRESETS[args.preset], args)
    source = args.source or HERE / preset.source
    if not source.exists():
        raise SystemExit(
            f"missing portrait source: {source}\n"
            "Place a headshot at tools/headshot.png (or pass --source)."
        )

    for polarity in polarities:
        lines = generate(source, preset, polarity)
        output = args.output or art_path(polarity)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {output} preset={args.preset} polarity={polarity}")


if __name__ == "__main__":
    main()
