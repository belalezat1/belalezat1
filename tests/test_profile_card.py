"""Tests for the belalezat1 terminal profile card pipeline."""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest import mock

import update_stats


ROOT = Path(__file__).resolve().parents[1]


class ProfileCardTests(unittest.TestCase):
    def test_generated_svgs_parse_as_xml(self):
        for name in ("dark_mode.svg", "light_mode.svg"):
            path = ROOT / name
            self.assertTrue(path.exists(), name)
            ET.parse(path)

    def test_required_dynamic_field_ids_exist(self):
        required = (
            "repo_data",
            "pr_data",
            "contribution_data",
            "follower_data",
            "loc_data",
            "loc_add",
            "loc_del",
            "repo_data_dots",
            "pr_data_dots",
            "contribution_data_dots",
            "follower_data_dots",
            "loc_data_dots",
        )
        for name in ("dark_mode.svg", "light_mode.svg"):
            svg = (ROOT / name).read_text(encoding="utf-8")
            for field_id in required:
                self.assertIn(f'id="{field_id}"', svg, (name, field_id))

    def test_identity_content_from_evidence(self):
        for name in ("dark_mode.svg", "light_mode.svg"):
            svg = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("CS student @ NJIT", svg)
            self.assertIn("New York City Metropolitan Area", svg)
            self.assertIn("software engineering, cloud, AI/ML", svg)
            self.assertIn("belal.ezat@protonmail.com", svg)
            self.assertIn("belalezat.me", svg)
            self.assertIn("React Native", svg)
            self.assertIn("GitHub Stats", svg)
            self.assertIn("Lines of Code", svg)
            self.assertNotIn("Projects", svg)
            self.assertNotIn("Navly", svg)
            self.assertNotIn("Learning", svg)
            self.assertNotIn("kayahickin", svg)
            self.assertNotIn("MyFutureSelf", svg)
            self.assertNotIn("Cleveland", svg)

    def test_cards_scale_with_viewbox(self):
        for name in ("dark_mode.svg", "light_mode.svg"):
            root = ET.parse(ROOT / name).getroot()
            self.assertEqual(root.attrib.get("viewBox"), "0 0 985 545", name)

    def test_portrait_sources_fit_left_panel(self):
        for polarity in ("dark", "light"):
            lines = (ROOT / "tools" / f"ascii_art_{polarity}.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertLessEqual(len(lines), 112, polarity)
            self.assertLessEqual(max(map(len, lines)), 120, polarity)
            # Transparent matte leaves leading blank rows; ink should still
            # land in the upper third of the panel.
            upper = lines[: max(1, round(len(lines) * 0.40))]
            self.assertTrue(any(line.strip() for line in upper), polarity)

    def test_portrait_has_visible_feature_contrast(self):
        """Coarse grid should keep more than a flat silhouette of one glyph."""
        for polarity in ("dark", "light"):
            text = (ROOT / "tools" / f"ascii_art_{polarity}.txt").read_text(
                encoding="utf-8"
            )
            glyphs = {c for c in text if c not in " \n"}
            self.assertGreaterEqual(len(glyphs), 4, polarity)
            # Eye/glasses band sits in the upper-middle of the head crop.
            lines = text.splitlines()
            band = "\n".join(lines[28:58])
            self.assertTrue(any(c in band for c in "#@*%+="), polarity)

    def test_each_panel_has_distinct_portrait_polarity(self):
        dark_json = json.loads(
            (ROOT / "tools" / "ascii_art_dark.json").read_text(encoding="utf-8")
        )
        light_json = json.loads(
            (ROOT / "tools" / "ascii_art_light.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(dark_json["runs"], light_json["runs"])

        def art_block(name: str) -> str:
            svg = (ROOT / name).read_text(encoding="utf-8")
            return svg[svg.index('class="ascii"') : svg.index("</text>")]

        for name in ("dark_mode.svg", "light_mode.svg"):
            block = art_block(name)
            fills = re.findall(r'fill="(#[0-9a-fA-F]{6})"', block)
            self.assertGreaterEqual(len(set(fills)), 8, name)
            # Colored dense ASCII: nested tspans carry per-run fills.
            self.assertIn("<tspan fill=", block, name)

    def test_portrait_is_colored_ascii(self):
        for polarity in ("dark", "light"):
            payload = json.loads(
                (ROOT / "tools" / f"ascii_art_{polarity}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(payload["cols"], 120)
            self.assertEqual(payload["rows"], 112)
            colored = 0
            for row in payload["runs"]:
                for run in row:
                    if run.get("color", "").startswith("#") and run.get("ch") != " ":
                        colored += int(run.get("n") or 1)
            self.assertGreater(colored, 5000, polarity)
    def test_language_bar_segments_fill_the_track(self):
        totals = json.loads((ROOT / "language_stats.json").read_text(encoding="utf-8"))
        self.assertTrue(totals["languages"])
        for name in ("dark_mode.svg", "light_mode.svg"):
            svg = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn('clip-path="url(#barClip)"', svg)
            group = svg.split('<g clip-path="url(#barClip)">')[1].split("</g>")[0]
            segments = re.findall(
                r'<rect x="([\d.]+)" y="318" width="([\d.]+)"', group
            )
            self.assertGreaterEqual(len(segments), 2, name)
            covered = sum(float(width) for _, width in segments)
            self.assertAlmostEqual(covered, 260.0, delta=0.5, msg=name)
            self.assertIn(totals["languages"][0]["name"], svg, name)

    def test_terminal_rows_share_equal_width(self):
        for name in ("dark_mode.svg", "light_mode.svg"):
            svg = (ROOT / name).read_text(encoding="utf-8")
            rows = re.findall(r'<tspan x="318" y="(\d+)" class="cc">\. </tspan>', svg)
            self.assertTrue(rows)
            for row_y in rows:
                line = svg.split(f'<tspan x="318" y="{row_y}" class="cc">. </tspan>')[1]
                line = line.split('<tspan x="318" y=')[0]
                text = re.sub(r"<[^>]+>", "", line).replace("\n", "")
                text = (
                    text.replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&quot;", '"')
                )
                self.assertEqual(len(text) + 2, 63, (name, row_y, text))

    def test_xml_escaping_for_special_characters(self):
        from tools import build_svg

        self.assertEqual(build_svg.xml_escape("a&b<c>d\"e"), "a&amp;b&lt;c&gt;d&quot;e")

    def test_rebuild_is_stable_and_preserves_dynamic_values(self):
        original = (ROOT / "dark_mode.svg").read_text(encoding="utf-8")
        # Mutate a dynamic value, rebuild, and confirm the builder keeps it.
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            mutated = original.replace(
                'id="follower_data">6</tspan>',
                'id="follower_data">99</tspan>',
                1,
            )
            source = scratch / "dark_mode.svg"
            source.write_text(mutated, encoding="utf-8")
            (scratch / "light_mode.svg").write_text(
                (ROOT / "light_mode.svg").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "build_svg.py"),
                    "--output-dir",
                    str(scratch),
                    "--stats-from",
                    str(source),
                    "--art-dark",
                    str(ROOT / "tools" / "ascii_art_dark.json"),
                    "--art-light",
                    str(ROOT / "tools" / "ascii_art_light.json"),
                    "--languages",
                    str(ROOT / "language_stats.json"),
                ],
                check=True,
                capture_output=True,
                cwd=ROOT,
            )
            rebuilt = (scratch / "dark_mode.svg").read_text(encoding="utf-8")
            self.assertIn('id="follower_data">99</tspan>', rebuilt)

        # Clean rebuild from committed inputs is reproducible.
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "build_svg.py"),
                    "--output-dir",
                    directory,
                ],
                check=True,
                capture_output=True,
                cwd=ROOT,
            )
            rebuilt = (Path(directory) / "dark_mode.svg").read_text(encoding="utf-8")
        self.assertEqual(rebuilt, original)

    def test_readme_references_resolve(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("dark_mode.svg", readme)
        self.assertIn("light_mode.svg", readme)
        self.assertTrue((ROOT / "dark_mode.svg").exists())
        self.assertTrue((ROOT / "light_mode.svg").exists())
        self.assertIn("raw.githubusercontent.com/belalezat1/belalezat1/output/", readme)
        self.assertIn("github-snake.svg", readme)
        self.assertIn("github-snake-dark.svg", readme)
        self.assertIn("<picture>", readme)
        self.assertIn("alt=", readme)
        # Accessible markdown links outside the images
        self.assertIn("mailto:belal.ezat@protonmail.com", readme)
        self.assertIn("https://belalezat.me", readme)
        self.assertIn("https://github.com/belalezat1/Recova", readme)
        self.assertIn("### Skills", readme)
        self.assertIn("### Projects", readme)
        self.assertIn("New York City Metropolitan Area", readme)
        self.assertNotIn("navly", readme.lower())
        self.assertNotIn("**Focus**", readme)
        self.assertNotIn("**Experience**", readme)
        self.assertIn("software engineering, cloud, AI/ML", (ROOT / "dark_mode.svg").read_text(encoding="utf-8"))

    def test_github_server_error_is_returned_for_retry(self):
        error = urllib.error.HTTPError(
            "https://api.github.com/test",
            500,
            "Internal Server Error",
            {},
            io.BytesIO(b'{"message":"try again"}'),
        )
        with mock.patch.object(update_stats.urllib.request, "urlopen", side_effect=error):
            status, body = update_stats.gh("/test", "secret")
        self.assertEqual(status, 500)
        self.assertEqual(body, {"message": "try again"})

    def test_svg_value_update_preserves_line_width(self):
        source = ROOT / "dark_mode.svg"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "card.svg"
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            update_stats.update_svg(
                target,
                {
                    "contribution_data": "12,345",
                    "follower_data": "10",
                    "repo_data": "20",
                    "pr_data": "3",
                    "loc_data": "9,999",
                    "loc_add": "12,000",
                    "loc_del": "2,001",
                },
            )
            svg = target.read_text(encoding="utf-8")
            self.assertIn('id="contribution_data">12,345</tspan>', svg)
            self.assertIn('id="follower_data">10</tspan>', svg)
            self.assertIn('id="loc_data">9,999</tspan>', svg)
            ET.parse(target)
            rows = re.findall(r'<tspan x="318" y="(\d+)" class="cc">\. </tspan>', svg)
            for row_y in rows:
                line = svg.split(f'<tspan x="318" y="{row_y}" class="cc">. </tspan>')[1]
                line = line.split('<tspan x="318" y=')[0]
                text = re.sub(r"<[^>]+>", "", line).replace("\n", "")
                text = (
                    text.replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                )
                self.assertEqual(len(text) + 2, 63, (row_y, text))

    def test_refresh_state_gates_same_day_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text('{"refreshedOn":"2026-09-08"}\n', encoding="utf-8")
            self.assertTrue(
                update_stats.refreshed_today(state_path, today=date(2026, 9, 8))
            )
            self.assertFalse(
                update_stats.refreshed_today(state_path, today=date(2026, 9, 9))
            )

    def test_fetch_public_profile_uses_contribution_calendar(self):
        def fake_gh(path, _token, method="GET", body=None):
            if path.startswith("/users/"):
                return 200, {"public_repos": 16, "followers": 6}
            if path.startswith("/search/issues"):
                return 200, {"total_count": 0}
            if path == "/graphql":
                return 200, {
                    "data": {
                        "user": {
                            "contributionsCollection": {
                                "startedAt": "2025-09-08T00:00:00Z",
                                "endedAt": "2026-09-08T00:00:00Z",
                                "contributionCalendar": {"totalContributions": 421},
                            }
                        }
                    }
                }
            raise AssertionError(path)

        with mock.patch.object(update_stats, "gh", side_effect=fake_gh):
            values = update_stats.fetch_public_profile("secret")
        self.assertEqual(values["contribution_data"], "421")
        self.assertEqual(values["repo_data"], "16")
        self.assertEqual(values["pr_data"], "0")
        self.assertEqual(values["follower_data"], "6")

    def test_empty_language_totals_fail_closed(self):
        with mock.patch.object(update_stats, "list_owned_repos", return_value=[]):
            totals = update_stats.fetch_language_totals(None, include_private=False)
        self.assertEqual(totals, {})

    def test_unchanged_repository_reuses_cached_loc(self):
        cache = {
            "belalezat1/example": {
                "headOid": "same-head",
                "additions": 120,
                "deletions": 20,
            }
        }
        with (
            mock.patch.object(update_stats, "fetch_repo_head", return_value="same-head"),
            mock.patch.object(update_stats, "fetch_repo_loc") as fetch_repo_loc,
        ):
            result = update_stats.fetch_cached_repo_loc(
                "belalezat1/example", "U_1", "secret", cache
            )
        self.assertEqual(result[:2], (120, 20))
        fetch_repo_loc.assert_not_called()

    def test_graphql_loc_skips_merge_commits(self):
        def fake_gh(path, _token, method="GET", body=None):
            if path != "/graphql":
                raise AssertionError(path)
            if body["variables"]["cursor"] is None:
                return 200, {
                    "data": {
                        "repository": {
                            "defaultBranchRef": {
                                "target": {
                                    "history": {
                                        "nodes": [
                                            {
                                                "additions": 20,
                                                "deletions": 5,
                                                "parents": {"totalCount": 1},
                                            },
                                            {
                                                "additions": 100,
                                                "deletions": 100,
                                                "parents": {"totalCount": 2},
                                            },
                                        ],
                                        "pageInfo": {
                                            "hasNextPage": True,
                                            "endCursor": "next",
                                        },
                                    }
                                }
                            }
                        }
                    }
                }
            return 200, {
                "data": {
                    "repository": {
                        "defaultBranchRef": {
                            "target": {
                                "history": {
                                    "nodes": [
                                        {
                                            "additions": 8,
                                            "deletions": 3,
                                            "parents": {"totalCount": 1},
                                        }
                                    ],
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                }
                            }
                        }
                    }
                }
            }

        with mock.patch.object(update_stats, "gh", side_effect=fake_gh):
            additions, deletions = update_stats.fetch_repo_loc(
                "belalezat1/Relay", "U_1", "secret"
            )
        self.assertEqual(additions, 28)
        self.assertEqual(deletions, 8)

    def test_workflow_yaml_files_parse(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            with path.open(encoding="utf-8") as handle:
                docs = list(yaml.safe_load_all(handle))
            self.assertTrue(docs and docs[0] is not None, path)


if __name__ == "__main__":
    unittest.main()
