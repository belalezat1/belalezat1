"""Refresh dynamic GitHub totals baked into the profile card SVGs.

Public profile stats work with the default GITHUB_TOKEN (or anonymously with
stricter rate limits). Optional secret ACCESS_TOKEN with `read:user` + `repo`
includes private owned repositories in the language bar.

No product / business metrics are invented. The job exits without rewriting
files when every visible value is unchanged, so empty daily commits are avoided.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://api.github.com"
SVGS = ["dark_mode.svg", "light_mode.svg"]

LOGIN = os.environ.get("PROFILE_LOGIN") or os.environ.get("GITHUB_REPOSITORY_OWNER") or "belalezat1"
LANGUAGE_STATS_PATH = os.environ.get("LANGUAGE_STATS_PATH", "language_stats.json")
LOC_CACHE_PATH = os.environ.get("LOC_CACHE_PATH", "loc_cache.json")
REFRESH_STATE_PATH = os.environ.get(
    "PROFILE_REFRESH_STATE_PATH", "profile_refresh_state.json"
)
USER_AGENT = "belalezat1-profile-updater/1.0"

# value ids on one line -> dots tspan that absorbs length changes
LINE_GROUPS = [
    (("repo_data", "pr_data"), "pr_data_dots"),
    (("contribution_data", "follower_data"), "follower_data_dots"),
    (("loc_data", "loc_add", "loc_del"), "loc_data_dots"),
]


def write_json(path: str | Path, payload: object) -> None:
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def gh(path: str, token: str | None, method: str = "GET", body: dict | None = None):
    url = API + path if path.startswith("/") else path
    request = urllib.request.Request(url, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", USER_AGENT)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        request.data = json.dumps(body).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in (500, 502, 503, 504):
            raise
        status = exc.code
        raw = exc.read()
    return status, (json.loads(raw) if raw else None)


def graphql(query: str, variables: dict, token: str | None, label: str) -> dict:
    for attempt in range(4):
        status, payload = gh(
            "/graphql",
            token,
            method="POST",
            body={"query": query, "variables": variables},
        )
        if status == 200 and isinstance(payload, dict):
            if payload.get("errors"):
                raise RuntimeError(f"GitHub GraphQL error for {label}: {payload['errors']}")
            data = payload.get("data")
            if isinstance(data, dict):
                return data
        if attempt < 3:
            print(f"GraphQL {label} HTTP {status}; retry {attempt + 2}/4")
            time.sleep(2 + attempt * 2)
    raise RuntimeError(f"GitHub GraphQL {label} unavailable: HTTP {status}")


def auth_token() -> str | None:
    """Prefer optional ACCESS_TOKEN; fall back to Actions GITHUB_TOKEN."""
    return os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def list_owned_repos(token: str | None, include_private: bool) -> list[dict]:
    repos: list[dict] = []
    page = 1
    while True:
        if include_private and token:
            path = f"/user/repos?affiliation=owner&per_page=100&page={page}"
        else:
            path = f"/users/{LOGIN}/repos?type=owner&per_page=100&page={page}"
        status, batch = gh(path, token)
        if status != 200 or not isinstance(batch, list):
            raise RuntimeError(f"failed listing repositories: HTTP {status}")
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def fetch_language_totals(token: str | None, include_private: bool) -> dict[str, int]:
    totals: dict[str, int] = {}
    for repo in list_owned_repos(token, include_private):
        if repo.get("fork"):
            continue
        name = repo["full_name"]
        status, languages = gh(f"/repos/{name}/languages", token)
        if status == 404:
            print(f"skipping {name}: languages unavailable")
            continue
        if status != 200 or not isinstance(languages, dict):
            raise RuntimeError(f"languages failed for {name}: HTTP {status}")
        if not languages:
            print(f"skipping {name}: no detected source languages")
            continue
        for language, byte_count in languages.items():
            totals[language] = totals.get(language, 0) + int(byte_count)
    return totals


def save_language_stats(path: str | Path, totals: dict[str, int]) -> None:
    write_json(
        path,
        {
            "version": 1,
            "languages": [
                {"name": name, "bytes": count}
                for name, count in sorted(totals.items(), key=lambda item: -item[1])
            ],
        },
    )


def load_loc_cache(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ignoring unreadable LOC cache {path}: {exc}")
        return {}
    if payload.get("version") != 1 or not isinstance(payload.get("repositories"), dict):
        print(f"ignoring unsupported LOC cache {path}")
        return {}
    return payload["repositories"]


def save_loc_cache(path: str | Path, repositories: dict) -> None:
    write_json(path, {"version": 1, "repositories": repositories})


def fetch_repo_head(name: str, token: str | None) -> str | None:
    owner, repo_name = name.split("/", 1)
    data = graphql(
        """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            defaultBranchRef {
              target { ... on Commit { oid } }
            }
          }
        }
        """,
        {"owner": owner, "name": repo_name},
        token,
        f"default branch for {name}",
    )
    repository = data.get("repository")
    default_ref = repository.get("defaultBranchRef") if repository else None
    if not default_ref:
        return None
    oid = default_ref.get("target", {}).get("oid")
    return oid if isinstance(oid, str) and oid else None


def fetch_repo_loc(name: str, author_id: str, token: str | None) -> tuple[int, int]:
    """Sum non-merge commits by this user on a repository's default branch."""
    owner, repo_name = name.split("/", 1)
    adds = dels = 0
    cursor = None
    while True:
        data = graphql(
            """
            query($owner: String!, $name: String!, $author: ID!, $cursor: String) {
              repository(owner: $owner, name: $name) {
                defaultBranchRef {
                  target {
                    ... on Commit {
                      history(first: 100, after: $cursor, author: {id: $author}) {
                        nodes {
                          additions
                          deletions
                          parents(first: 2) { totalCount }
                        }
                        pageInfo { hasNextPage endCursor }
                      }
                    }
                  }
                }
              }
            }
            """,
            {
                "owner": owner,
                "name": repo_name,
                "author": author_id,
                "cursor": cursor,
            },
            token,
            f"commit history for {name}",
        )
        repository = data.get("repository")
        default_ref = repository.get("defaultBranchRef") if repository else None
        if not default_ref:
            return adds, dels
        history = default_ref.get("target", {}).get("history")
        if not isinstance(history, dict):
            raise RuntimeError(f"default branch is not a commit history for {name}")
        for commit in history["nodes"]:
            if commit["parents"]["totalCount"] > 1:
                continue
            adds += commit["additions"]
            dels += commit["deletions"]
        page_info = history["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
    return adds, dels


def fetch_cached_repo_loc(
    name: str, author_id: str, token: str | None, loc_cache: dict
):
    head_oid = fetch_repo_head(name, token)
    if not head_oid:
        return None
    cached = loc_cache.get(name)
    if (
        isinstance(cached, dict)
        and cached.get("headOid") == head_oid
        and isinstance(cached.get("additions"), int)
        and isinstance(cached.get("deletions"), int)
    ):
        print(f"LOC cache hit: {name}@{head_oid[:12]}")
        return cached["additions"], cached["deletions"], {
            "headOid": head_oid,
            "additions": cached["additions"],
            "deletions": cached["deletions"],
        }
    repo_adds, repo_dels = fetch_repo_loc(name, author_id, token)
    print(f"LOC cache refreshed: {name}@{head_oid[:12]}")
    return repo_adds, repo_dels, {
        "headOid": head_oid,
        "additions": repo_adds,
        "deletions": repo_dels,
    }


def fetch_loc_totals(
    token: str | None, include_private: bool, loc_cache_path: str | Path
) -> dict[str, str]:
    """Author LOC across owned (non-fork) repos with detected languages.

    Skips forks so upstream trees like notepad-plus-plus do not dominate.
    Fails closed instead of publishing a partial total when a repo errors.
    """
    data = graphql(
        """
        query($login: String!) {
          user(login: $login) { id }
        }
        """,
        {"login": LOGIN},
        token,
        "author id",
    )
    author_id = data["user"]["id"]
    loc_cache = load_loc_cache(loc_cache_path)
    refreshed: dict = {}
    adds = dels = 0
    counted = 0
    for repo in list_owned_repos(token, include_private):
        if repo.get("fork"):
            continue
        name = repo["full_name"]
        status, languages = gh(f"/repos/{name}/languages", token)
        if status == 404 or languages == {}:
            print(f"skipping LOC for {name}: no source languages")
            continue
        if status != 200 or not isinstance(languages, dict):
            raise RuntimeError(f"languages failed for {name}: HTTP {status}")
        result = fetch_cached_repo_loc(name, author_id, token, loc_cache)
        if not result:
            print(f"skipping LOC for {name}: no default-branch commit")
            continue
        repo_adds, repo_dels, cache_entry = result
        refreshed[name] = cache_entry
        adds += repo_adds
        dels += repo_dels
        counted += 1
    if counted == 0:
        raise RuntimeError("LOC totals empty; refusing to publish a blank figure")
    save_loc_cache(loc_cache_path, refreshed)
    return {
        "loc_data": f"{adds - dels:,}",
        "loc_add": f"{adds:,}",
        "loc_del": f"{dels:,}",
    }


def fetch_public_profile(token: str | None) -> dict[str, str]:
    status, user = gh(f"/users/{LOGIN}", token)
    if status != 200 or not isinstance(user, dict):
        raise RuntimeError(f"user profile unavailable: HTTP {status}")

    query = urllib.parse.quote(f"type:pr author:{LOGIN} is:merged")
    status, pr_search = gh(f"/search/issues?q={query}&per_page=1", token)
    if status != 200 or not isinstance(pr_search, dict) or "total_count" not in pr_search:
        raise RuntimeError(f"merged PR search unavailable: HTTP {status}")

    data = graphql(
        """
        query($login: String!) {
          user(login: $login) {
            contributionsCollection {
              startedAt
              endedAt
              contributionCalendar { totalContributions }
            }
          }
        }
        """,
        {"login": LOGIN},
        token,
        "contribution calendar",
    )
    collection = data["user"]["contributionsCollection"]
    contribution_total = collection["contributionCalendar"]["totalContributions"]
    print(
        "contribution calendar: "
        f"{collection['startedAt']} to {collection['endedAt']} = "
        f"{contribution_total:,}"
    )

    return {
        "repo_data": f"{user['public_repos']}",
        "pr_data": f"{pr_search['total_count']:,}",
        "follower_data": f"{user['followers']}",
        "contribution_data": f"{contribution_total:,}",
    }


def tspan_pattern(tid: str) -> re.Pattern[str]:
    return re.compile(rf'(<tspan[^>]*id="{tid}"[^>]*>)([^<]*)(</tspan>)')


def update_svg(path: str | Path, values: dict[str, str]) -> bool:
    path = Path(path)
    svg = path.read_text(encoding="utf-8")
    changed = False
    for value_ids, dots_id in LINE_GROUPS:
        delta = 0
        for vid in value_ids:
            if vid not in values:
                continue
            pattern = tspan_pattern(vid)
            match = pattern.search(svg)
            if not match:
                print(f"warning: id {vid} not found in {path}")
                continue
            old = match.group(2)
            new = values[vid]
            if old != new:
                changed = True
            delta += len(old) - len(new)
            svg = pattern.sub(
                lambda mm, replacement=new: mm.group(1) + replacement + mm.group(3),
                svg,
                count=1,
            )
        if delta:
            pattern = tspan_pattern(dots_id)
            match = pattern.search(svg)
            if match:
                dots = max(1, match.group(2).count(".") + delta)
                svg = pattern.sub(
                    lambda mm, count=dots: mm.group(1) + f" {'.' * count} " + mm.group(3),
                    svg,
                    count=1,
                )
                changed = True
    if changed:
        path.write_text(svg, encoding="utf-8")
    return changed


def refreshed_today(path: str | Path = REFRESH_STATE_PATH, today=None) -> bool:
    today = today or datetime.now(timezone.utc).date()
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return state.get("refreshedOn") == today.isoformat()


def save_refresh_state(values: dict[str, str], path: str | Path = REFRESH_STATE_PATH) -> None:
    now = datetime.now(timezone.utc)
    write_json(
        path,
        {
            "values": values,
            "refreshedAt": now.isoformat().replace("+00:00", "Z"),
            "refreshedOn": now.date().isoformat(),
            "trigger": os.environ.get("REFRESH_TRIGGER", "local"),
            "login": LOGIN,
        },
    )


def values_unchanged(values: dict[str, str], path: str | Path = REFRESH_STATE_PATH) -> bool:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return state.get("values") == values


def main() -> int:
    if (
        os.environ.get("SKIP_IF_REFRESHED_TODAY", "").lower() == "true"
        and refreshed_today()
    ):
        print("profile refresh already completed today; skipping")
        return 0

    token = auth_token()
    include_private = bool(os.environ.get("ACCESS_TOKEN"))
    if include_private:
        print("ACCESS_TOKEN present: including private owned repositories in language bar")
    elif os.environ.get("REQUIRE_PRIVATE_LANGUAGES") == "1":
        raise RuntimeError(
            "ACCESS_TOKEN is required when REQUIRE_PRIVATE_LANGUAGES=1 "
            "(scopes: read:user, repo). Without it, private repos like Atlas "
            "are omitted from the language bar."
        )
    else:
        print("no ACCESS_TOKEN; language bar uses public owned non-fork repositories only")

    values = fetch_public_profile(token)
    language_totals = fetch_language_totals(token, include_private=include_private)
    if not language_totals:
        raise RuntimeError("language totals empty; refusing to publish a blank bar")
    save_language_stats(LANGUAGE_STATS_PATH, language_totals)

    if token:
        values.update(fetch_loc_totals(token, include_private, LOC_CACHE_PATH))
    elif os.environ.get("REQUIRE_LOC") == "1":
        raise RuntimeError(
            "GITHUB_TOKEN or ACCESS_TOKEN is required for Lines of Code "
            "(GraphQL commit history). Without it, LOC fields stay at the "
            "last committed values."
        )
    else:
        print("no token for LOC; keeping previously committed Lines of Code values")

    if values_unchanged(values) and os.environ.get("SKIP_IF_UNCHANGED", "1") == "1":
        # Language JSON may still have changed; surface that for the rebuild step.
        print("dynamic SVG values unchanged from last refresh")
        if os.environ.get("WRITE_REFRESH_STATE") == "1":
            save_refresh_state(values)
        print("updated:", ", ".join(f"{k}={v}" for k, v in sorted(values.items())))
        return 0

    changed_any = False
    for name in SVGS:
        path = ROOT / name if not Path(name).is_absolute() else Path(name)
        if not path.exists():
            path = Path(name)
        changed_any = update_svg(path, values) or changed_any

    if os.environ.get("WRITE_REFRESH_STATE") == "1":
        save_refresh_state(values)

    print("updated:", ", ".join(f"{k}={v}" for k, v in sorted(values.items())))
    if not changed_any:
        print("SVG text already matched fetched values")
    return 0


if __name__ == "__main__":
    sys.exit(main())
