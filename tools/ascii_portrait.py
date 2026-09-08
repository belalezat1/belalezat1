"""Generate theme-aware ASCII portraits from the committed headshot.

Uses a coarse glyph grid so eyes/glasses survive GitHub README scaling.
Dark-card glyphs ink *dark* image regions (glasses, eyes, hair). Light-card
glyphs ink the opposite end of the tone range. Tone is error-diffused across
a short density ramp.

    python3 tools/ascii_portrait.py --preset face
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps


HERE = Path(__file__).resolve().parent

# Coarse grid: each cell is large enough to survive README downscaling.
COLS, ROWS = 92, 128
CHAR_WIDTH, LINE_HEIGHT = 0.6, 1.0
SUPERSAMPLE = 8

# Density ramp — enough steps for face tones without becoming a solid slab.
RAMP: tuple[tuple[str, float], ...] = (
    (" ", 0.00),
    (".", 0.14),
    (":", 0.28),
    ("-", 0.42),
    ("=", 0.56),
    ("+", 0.70),
    ("*", 0.82),
    ("#", 0.92),
    ("@", 1.00),
)
EDGE_FLOOR = 0.08

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


# Panel polarity = which end of the tone range becomes glyph ink.
# Dark card: ink *dark* image regions (eyes, glasses, hair) with bright glyphs.
# Light card: ink *bright* regions inverted — same as dark features on white.
DARK_TONE = ToneCurve(1.0, 99.0, 1.25, 0.78)
LIGHT_TONE = ToneCurve(3.0, 99.0, 1.15, 0.90)

PRESETS = {
    # 1024×1024 studio headshot. `face` is the committed card crop.
    "open": CropPreset(
        "headshot.png", 512, 80, 900,
        0.70, 3.40, 0.55, 0.45, 0.04, DARK_TONE, LIGHT_TONE,
    ),
    "balanced": CropPreset(
        "headshot.png", 512, 110, 820,
        0.70, 3.40, 0.55, 0.45, 0.04, DARK_TONE, LIGHT_TONE,
    ),
    "face": CropPreset(
        "headshot.png", 512, 150, 720,
        0.65, 3.80, 0.70, 0.55, 0.03, DARK_TONE, LIGHT_TONE,
    ),
    "tight": CropPreset(
        "headshot.png", 512, 190, 640,
        0.65, 3.80, 0.70, 0.55, 0.03, DARK_TONE, LIGHT_TONE,
    ),
}

POLARITIES = ("dark", "light")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=PRESETS, default="face")
    parser.add_argument(
        "--polarity",
        choices=POLARITIES,
        default=None,
        help="build one panel; omit to write both polarities",
    )
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--center-x", type=int, default=None)
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
    saturation, value = hsv[..., 1], hsv[..., 2]
    backdrop = (saturation <= 35) & (value >= 220)
    return ~flood_from_border(backdrop)


def local_contrast(gray: np.ndarray, preset: CropPreset) -> np.ndarray:
    # Smaller blur radius in *cells* so eyes/glasses survive as local detail.
    blurred = np.asarray(
        Image.fromarray(gray.astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(radius=SUPERSAMPLE * 1.6)
        ),
        dtype=np.float64,
    )
    mid = 128.0
    broad = mid + (blurred - mid) * preset.base_gain
    return broad + (gray - blurred) * preset.detail_gain


def largest_mass(drawn: np.ndarray) -> np.ndarray:
    rows, cols = drawn.shape
    seed = np.zeros_like(drawn)
    band = slice(int(cols * 0.30), int(cols * 0.70))
    seed[int(rows * 0.10) : int(rows * 0.95), band] = drawn[
        int(rows * 0.10) : int(rows * 0.95), band
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
    ceiling = float(np.percentile(magnitude, 98.0) or 1.0)
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
    drawn = largest_mass(coverage > 0.35)
    if not drawn.any():
        return np.zeros_like(luminance)

    values = luminance[drawn]
    black = np.percentile(values, tone.black_point)
    white = np.percentile(values, tone.white_point)
    span = max(1.0, float(white - black))
    normalized = np.clip((luminance - black) / span, 0.0, 1.0)
    # Dark panel: ink dark image features (glasses, eyes, hair, suit).
    # Light panel: ink bright image features (lit face/shirt as dark glyphs).
    if polarity == "dark":
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
    ink *= np.clip((coverage - 0.12) / 0.50, 0.0, 1.0)

    fringe = np.zeros_like(drawn)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            fringe |= np.roll(np.roll(drawn, dy, axis=0), dx, axis=1)
    return np.where(fringe & (coverage > 0.12), ink, 0.0)


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
