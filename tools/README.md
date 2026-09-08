# Card generators

`ascii_portrait.py` draws the portrait. `build_svg.py` assembles both theme cards.

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements.txt
.venv/bin/python tools/ascii_portrait.py --preset balanced
.venv/bin/python tools/build_svg.py
```

## Portrait

Committed source: `tools/headshot.png` (1024×1024 studio headshot).
Default preset is `face` (tight on the head so eyes/glasses survive README
scaling). `open` / `balanced` / `tight` change shoulder crop. Optional
overrides: `--center-x`, `--top`, `--height`, `--source`.

The portrait uses a coarse 92×128 glyph grid at 3px — sub-pixel 430-col
stipple looked like a shadow on GitHub.

Dark and light polarities are generated separately — do not reuse one art file
for both panels.

```bash
.venv/bin/python tools/ascii_portrait.py --preset balanced
# or one polarity:
.venv/bin/python tools/ascii_portrait.py --polarity dark --preset balanced
```

Outputs: `tools/ascii_art_dark.txt`, `tools/ascii_art_light.txt`.

## Stats refresh

`update_stats.py` rewrites dynamic SVG values, `language_stats.json`, and
`loc_cache.json`.

GitHub Stats on the card (same shape as a typical engineering profile card):

- public repos
- merged PRs
- contributions (current contribution-calendar window)
- followers
- lines of code (additions − deletions on default branches; cached by HEAD oid)

- Default: public profile via `GITHUB_TOKEN` / anonymous API.
- Optional secret `ACCESS_TOKEN` (`read:user`, `repo`): include private owned
  repositories in the language bar and LOC total. Without it, private projects
  are omitted from those figures only; public totals still refresh.
- LOC requires a token (Actions `GITHUB_TOKEN` is enough for public repos).
  Set `REQUIRE_LOC=1` to fail the job instead of leaving stale LOC values.
