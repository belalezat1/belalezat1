"""Generate theme-aware colored ASCII portraits from the committed headshot.

Dense glyph grid like HTML colored-ASCII exporters: character from luminance
(`@%#*+=-:.`), fill from the photo RGB — including pale "." cells for the
studio matte. Dark/light outputs differ by a mild tone remap.

    python3 tools/ascii_portrait.py --preset head
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


HERE = Path(__file__).resolve().parent

# Must stay in sync with tools/build_svg.py layout constants.
COLS, ROWS = 120, 112
CHAR_WIDTH, LINE_HEIGHT = 0.6, 1.0

# Dark → light (bright matte / highlights → ".").
RAMP = "@%#*+=-:."

POLARITIES = ("dark", "light")


@dataclass(frozen=True)
class CropPreset:
    source: str
    center_x: int
    top: int
    height: int
    contrast: float
    sharpness: float


PRESETS = {
    # Face-only, white-matted window-lit portrait (no glasses). 1024² canvas.
    "head": CropPreset("headshot.png", 512, 20, 980, 1.08, 1.25),
    "open": CropPreset("headshot.png", 512, 0, 1024, 1.06, 1.18),
    "balanced": CropPreset("headshot.png", 512, 30, 960, 1.10, 1.30),
    "tight": CropPreset("headshot.png", 512, 50, 900, 1.12, 1.35),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=PRESETS, default="head")
    parser.add_argument("--polarity", choices=POLARITIES, default=None)
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--center-x", type=int, default=None)
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def art_txt_path(polarity: str) -> Path:
    return HERE / f"ascii_art_{polarity}.txt"


def art_json_path(polarity: str) -> Path:
    return HERE / f"ascii_art_{polarity}.json"


def crop_box(preset: CropPreset) -> tuple[int, int, int, int]:
    width = round(preset.height * (COLS * CHAR_WIDTH) / (ROWS * LINE_HEIGHT))
    left = preset.center_x - width // 2
    return left, preset.top, left + width, preset.top + preset.height


def prepare(image: Image.Image, preset: CropPreset) -> Image.Image:
    # Light touch — heavy autocontrast flattens the soft studio look of the
    # colored-ASCII reference.
    image = ImageOps.autocontrast(image, cutoff=0.2)
    image = ImageEnhance.Contrast(image).enhance(preset.contrast)
    image = ImageEnhance.Color(image).enhance(1.05)
    image = ImageEnhance.Sharpness(image).enhance(preset.sharpness)
    return image.filter(ImageFilter.UnsharpMask(radius=1.0, percent=90, threshold=2))


def glyph_for_luma(luma: float) -> str:
    # Bright subject / matte → "."; dark → "@".
    t = float(np.clip(luma / 255.0, 0.0, 1.0))
    index = min(len(RAMP) - 1, int(t * len(RAMP)))
    return RAMP[index]


def remap_rgb(rgb: np.ndarray, polarity: str) -> np.ndarray:
    """Keep photo hue; nudge value for each card background."""
    x = np.asarray(rgb, dtype=np.float64)
    if polarity == "dark":
        # Mild lift so skin/hair don't sink into #161b22.
        out = 8.0 + x * 0.97
    else:
        # Keep near-white matte (~249,247,250); soft midtone pull-down only.
        # Use x*x form so we never divide the working buffer in place.
        out = x * 0.88 + x * x * (0.12 / 255.0)
    return np.clip(out, 0, 255).astype(np.uint8)


def cell_grid(source: Path, preset: CropPreset) -> tuple[np.ndarray, np.ndarray]:
    image = Image.open(source).convert("RGB")
    left, top, right, bottom = crop_box(preset)
    left = max(0, left)
    top = max(0, top)
    right = min(image.width, right)
    bottom = min(image.height, bottom)
    crop = prepare(image.crop((left, top, right, bottom)), preset)
    rgb = np.asarray(
        crop.resize((COLS, ROWS), Image.Resampling.LANCZOS), dtype=np.uint8
    )
    luma = (
        0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    ).astype(np.float64)
    return rgb, luma


def encode_runs(
    glyphs: np.ndarray, colors: np.ndarray
) -> list[list[dict[str, str | int]]]:
    rows: list[list[dict[str, str | int]]] = []
    for y in range(ROWS):
        runs: list[dict[str, str | int]] = []
        x = 0
        while x < COLS:
            ch = str(glyphs[y, x])
            color = "#{:02x}{:02x}{:02x}".format(*colors[y, x])
            n = 1
            while (
                x + n < COLS
                and glyphs[y, x + n] == ch
                and "#{:02x}{:02x}{:02x}".format(*colors[y, x + n]) == color
            ):
                n += 1
            runs.append({"ch": ch, "color": color, "n": n})
            x += n
        rows.append(runs)
    return rows


def generate(source: Path, preset: CropPreset, polarity: str) -> tuple[list[str], list]:
    rgb, luma = cell_grid(source, preset)
    colors = remap_rgb(rgb, polarity)
    glyphs = np.empty((ROWS, COLS), dtype="<U1")
    for y in range(ROWS):
        for x in range(COLS):
            glyphs[y, x] = glyph_for_luma(float(luma[y, x]))
    # Keep trailing spaces for layout width; matte is inked as colored ".".
    lines = ["".join(glyphs[y]) for y in range(ROWS)]
    runs = encode_runs(glyphs, colors)
    return lines, runs


def with_overrides(preset: CropPreset, args: argparse.Namespace) -> CropPreset:
    return CropPreset(
        source=preset.source,
        center_x=args.center_x if args.center_x is not None else preset.center_x,
        top=args.top if args.top is not None else preset.top,
        height=args.height if args.height is not None else preset.height,
        contrast=preset.contrast,
        sharpness=preset.sharpness,
    )


def main() -> None:
    args = parse_args()
    polarities = [args.polarity] if args.polarity else list(POLARITIES)
    if args.output and len(polarities) > 1:
        raise SystemExit("--output requires a single --polarity")

    preset = with_overrides(PRESETS[args.preset], args)
    source = args.source or (HERE / preset.source)
    if not source.exists():
        raise SystemExit(f"missing headshot {source}")

    for polarity in polarities:
        lines, runs = generate(source, preset, polarity)
        txt = args.output or art_txt_path(polarity)
        if args.output:
            txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"wrote {txt} preset={args.preset} polarity={polarity}")
            continue
        txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
        payload = {"cols": COLS, "rows": ROWS, "polarity": polarity, "runs": runs}
        art_json_path(polarity).write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )
        ink = sum(1 for line in lines for c in line if c != " ")
        print(
            f"wrote {txt.name} + {art_json_path(polarity).name} "
            f"preset={args.preset} polarity={polarity} ink={ink}"
        )


if __name__ == "__main__":
    main()
