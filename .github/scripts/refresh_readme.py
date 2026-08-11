#!/usr/bin/env python3
"""Rewrite the dynamic blocks in README.md. Runs daily from Actions.

Three blocks, each delimited by <!-- NAME:START --> / <!-- NAME:END --> markers:

  STATS   streaks and totals, computed from assets/contributions.json
  RECENT  latest public pushes across both accounts, from the events API
  QUOTE   one line, rotated by day-of-year so it changes every morning

Everything degrades on its own: a block whose data source is unavailable keeps
whatever it said yesterday instead of blanking out. The script only ever touches
text between markers, so hand-edits to the rest of the README survive.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
README = os.path.join(ROOT, "README.md")
CONTRIB = os.path.join(ROOT, "assets", "contributions.json")
QUOTES = os.path.join(os.path.dirname(__file__), "quotes.json")

ACCOUNTS = ["notpritam", "notpritamm"]
RECENT_LIMIT = 5

# Emergent's repos are private, so those events never surface. Anything that does
# show up here is public side-project work, which is what this block is for.
INTERESTING = {"PushEvent", "PullRequestEvent", "CreateEvent", "ReleaseEvent"}

# This repo would otherwise top the list every day off its own refresh commits.
SKIP_REPOS = {"notpritam/notpritam"}


# ---------------------------------------------------------------- helpers

def badge(label, value, color):
    """shields.io static badge. Dashes and underscores need doubling."""
    def enc(s):
        return (str(s).replace("_", "__").replace("-", "--").replace(" ", "_")
                .replace("%", "%25").replace("?", "%3F").replace("&", "%26")
                .replace("#", "%23"))
    return (f"https://img.shields.io/badge/{enc(label)}-{enc(value)}-{color}"
            f"?style=flat-square&labelColor=161b22")


def ago(when):
    delta = datetime.now(timezone.utc) - when
    mins = int(delta.total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)}m ago"
    if mins < 60 * 24:
        return f"{mins // 60}h ago"
    days = mins // (60 * 24)
    return "yesterday" if days == 1 else f"{days}d ago"


def replace_block(text, name, body):
    """Swap the content between a marker pair. Returns text unchanged if absent."""
    pattern = re.compile(
        rf"(<!-- {name}:START -->)(.*?)(<!-- {name}:END -->)", re.DOTALL
    )
    if not pattern.search(text):
        print(f"::warning::No {name} markers in README — skipping that block.")
        return text
    return pattern.sub(lambda m: f"{m.group(1)}\n{body}\n{m.group(3)}", text)


# ---------------------------------------------------------------- stats

def streaks(days, today):
    """(current, longest) run of consecutive days with at least one contribution.

    Today counts if it's already green but doesn't break the streak if it isn't —
    the day isn't over yet, and a graph that goes red at midnight UTC is a lie.
    """
    dated = {datetime.strptime(k, "%Y-%m-%d").date(): v for k, v in days.items()}
    if not dated:
        return 0, 0

    longest = run = 0
    for d in sorted(dated):
        run = run + 1 if dated[d] > 0 else 0
        longest = max(longest, run)

    cursor = today if dated.get(today, 0) > 0 else today - timedelta(days=1)
    current = 0
    while dated.get(cursor, 0) > 0:
        current += 1
        cursor -= timedelta(days=1)
    return current, longest


def stats_block():
    try:
        with open(CONTRIB) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"::warning::Can't read contributions.json ({exc}) — leaving stats alone.")
        return None

    days = data.get("days") or {}
    if not days:
        print("::warning::contributions.json has no per-day data — leaving stats alone.")
        return None

    today = date.today()
    current, longest = streaks(days, today)

    recent = {datetime.strptime(k, "%Y-%m-%d").date(): v for k, v in days.items()}
    last30 = sum(v for d, v in recent.items() if (today - d).days < 30)
    best_day, best_count = max(recent.items(), key=lambda kv: kv[1])
    active = sum(1 for v in recent.values() if v > 0)

    rows = [
        ("current streak", f"{current} days", "39d353"),
        ("longest streak", f"{longest} days", "26a641"),
        ("last 30 days", f"{last30:,}", "006d32"),
        ("busiest day", f"{best_count} on {best_day:%d %b}", "0e4429"),
        ("active days", f"{active} of 365", "30363d"),
    ]
    imgs = " ".join(
        f'<img src="{badge(l, v, c)}" alt="{l}: {v}">' for l, v, c in rows
    )
    return f'<p align="center">\n  {imgs}\n</p>'


# ---------------------------------------------------------------- recent

def events(login, token):
    req = urllib.request.Request(
        f"https://api.github.com/users/{login}/events/public?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "notpritam-readme-refresh",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    except (urllib.error.URLError, ValueError) as exc:
        print(f"::warning::Events for {login} unavailable: {exc}")
        return []


def describe(ev):
    kind = ev["type"]
    payload = ev.get("payload", {})
    if kind == "PushEvent":
        # /users/:u/events/public returns a trimmed payload with no commit count,
        # so report the branch instead of inventing a number.
        n = payload.get("size")
        if n:
            return f"pushed {n} commit{'s' if n != 1 else ''}"
        branch = payload.get("ref", "").rsplit("/", 1)[-1]
        return f"pushed to `{branch}`" if branch else "pushed"
    if kind == "PullRequestEvent":
        action = payload.get("action")
        if action == "closed" and payload.get("pull_request", {}).get("merged"):
            action = "merged"
        return f"{action} a pull request"
    if kind == "CreateEvent":
        ref_type = payload.get("ref_type", "ref")
        return "created the repo" if ref_type == "repository" else f"created a {ref_type}"
    if kind == "ReleaseEvent":
        tag = payload.get("release", {}).get("tag_name", "")
        return f"released {tag}".strip()
    return kind


def recent_block(token):
    collected = []
    for login in ACCOUNTS:
        for ev in events(login, token):
            if ev.get("type") in INTERESTING:
                collected.append(ev)
    if not collected:
        print("::warning::No public events — leaving the recent block alone.")
        return None

    collected.sort(key=lambda e: e["created_at"], reverse=True)

    seen, rows = set(), []
    for ev in collected:
        repo = ev["repo"]["name"]
        if repo in seen or repo in SKIP_REPOS:
            continue
        seen.add(repo)
        when = datetime.strptime(ev["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        rows.append(
            f"| **[{repo.split('/')[-1]}](https://github.com/{repo})** "
            f"| {describe(ev)} | {ago(when)} |"
        )
        if len(rows) == RECENT_LIMIT:
            break

    return "\n".join(["| Repo | What | When |", "|---|---|---|", *rows])


# ---------------------------------------------------------------- quote

def quote_block():
    try:
        with open(QUOTES) as fh:
            quotes = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"::warning::Can't read quotes.json ({exc}) — leaving the quote alone.")
        return None
    if not quotes:
        return None

    # Day-of-year indexing: one quote per day, same one all day, no repeats
    # until the list wraps.
    text, who = quotes[date.today().toordinal() % len(quotes)]
    return f"> *{text}*\n>\n> — **{who}**"


# ---------------------------------------------------------------- main

def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    with open(README) as fh:
        original = fh.read()

    updated = original
    for name, body in (
        ("STATS", stats_block()),
        ("RECENT", recent_block(token)),
        ("QUOTE", quote_block()),
    ):
        if body is not None:
            updated = replace_block(updated, name, body)

    if updated == original:
        print("README unchanged.")
        return 0

    with open(README, "w") as fh:
        fh.write(updated)
    print("README updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
