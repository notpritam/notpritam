#!/usr/bin/env python3
"""Render a single contribution heatmap merging @notpritam and @notpritamm.

GitHub has no way to combine contribution graphs across accounts, so we pull each
account's calendar from the GraphQL API, sum them day by day, and draw the grid
ourselves. Emits a dark and a light SVG; the README picks one via <picture>.

Needs a token with read:user. Either account's token can read the other's totals
(including its private-contribution count), so one PAT covers both.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from collections import OrderedDict
from datetime import date, datetime

ACCOUNTS = ["notpritam", "notpritamm"]
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "assets")

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      restrictedContributionsCount
      totalCommitContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalRepositoriesWithContributedCommits
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""

# dark, light
THEMES = {
    "dark": {
        "bg": "#0d1117", "border": "#30363d", "text": "#e6edf3", "muted": "#7d8590",
        "empty": "#161b22",
        "scale": ["#0e4429", "#006d32", "#26a641", "#39d353"],
    },
    "light": {
        "bg": "#ffffff", "border": "#d1d9e0", "text": "#1f2328", "muted": "#59636e",
        "empty": "#ebedf0",
        "scale": ["#aceebb", "#4ac26b", "#2da44e", "#116329"],
    },
}

CELL = 11
GAP = 3
PITCH = CELL + GAP
PAD = 20
LEFT = 30          # room for Mon/Wed/Fri labels
TOP = 78           # room for title, subtitle, and month labels
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fetch(login, token):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "notpritam-readme-contrib-graph",
        },
    )
    try:
        payload = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    except urllib.error.HTTPError as exc:
        sys.exit(f"GitHub API error for {login}: {exc.code} {exc.read().decode()[:300]}")
    if "errors" in payload:
        sys.exit(f"GraphQL error for {login}: {payload['errors']}")
    user = payload["data"]["user"]
    if user is None:
        sys.exit(f"No such user: {login}")
    return user["contributionsCollection"]


def collect(token):
    """Return (per-day totals, aggregate stats) merged across both accounts."""
    days = {}
    stats = {"total": 0, "private": 0, "commits": 0, "prs": 0, "reviews": 0, "repos": 0}
    per_account = {}

    for login in ACCOUNTS:
        cc = fetch(login, token)
        cal = cc["contributionCalendar"]
        per_account[login] = cal["totalContributions"]

        stats["total"] += cal["totalContributions"]
        stats["private"] += cc["restrictedContributionsCount"]
        stats["commits"] += cc["totalCommitContributions"]
        stats["prs"] += cc["totalPullRequestContributions"]
        stats["reviews"] += cc["totalPullRequestReviewContributions"]
        stats["repos"] += cc["totalRepositoriesWithContributedCommits"]

        for week in cal["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = days.get(day["date"], 0) + day["contributionCount"]

    ordered = OrderedDict(sorted(days.items()))
    return ordered, stats, per_account


def buckets(days):
    """Quartile thresholds over active days, so the scale suits this data."""
    active = sorted(v for v in days.values() if v > 0)
    if not active:
        return [1, 2, 3, 4]
    def q(p):
        return max(1, active[min(len(active) - 1, int(len(active) * p))])
    return [q(0.25), q(0.50), q(0.75), active[-1]]


def level(count, cuts):
    if count <= 0:
        return -1
    for i, c in enumerate(cuts):
        if count <= c:
            return i
    return 3


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(days, stats, per_account, theme_name):
    t = THEMES[theme_name]
    cuts = buckets(days)
    items = list(days.items())

    # Align to weeks: GitHub's calendar starts on a Sunday.
    first = datetime.strptime(items[0][0], "%Y-%m-%d").date()
    lead = (first.weekday() + 1) % 7           # Python Mon=0; we want Sun=0
    cells = [None] * lead + items
    weeks = [cells[i:i + 7] for i in range(0, len(cells), 7)]

    width = LEFT + len(weeks) * PITCH - GAP + PAD * 2
    height = TOP + 7 * PITCH - GAP + 44

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="-apple-system,BlinkMacSystemFont,'
        f'Segoe UI,Helvetica,Arial,sans-serif">',
        f'<rect width="{width}" height="{height}" rx="6" fill="{t["bg"]}" '
        f'stroke="{t["border"]}"/>',
    ]

    # title
    total = f'{stats["total"]:,}'
    out.append(
        f'<text x="{PAD}" y="30" fill="{t["text"]}" font-size="14" font-weight="600">'
        f'{total} contributions in the last year</text>'
    )
    sub = " + ".join(f'@{a} {per_account[a]:,}' for a in ACCOUNTS)
    out.append(
        f'<text x="{PAD}" y="48" fill="{t["muted"]}" font-size="11">{esc(sub)}</text>'
    )

    # month labels
    seen = set()
    for wi, week in enumerate(weeks):
        for cell in week:
            if not cell:
                continue
            d = datetime.strptime(cell[0], "%Y-%m-%d").date()
            if d.day <= 7 and d.month not in seen:
                seen.add(d.month)
                x = PAD + LEFT + wi * PITCH
                out.append(
                    f'<text x="{x}" y="{TOP - 6}" fill="{t["muted"]}" font-size="10">'
                    f'{MONTHS[d.month - 1]}</text>'
                )
            break

    # weekday labels
    for idx, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        y = TOP + idx * PITCH + CELL - 2
        out.append(
            f'<text x="{PAD}" y="{y}" fill="{t["muted"]}" font-size="9">{label}</text>'
        )

    # cells
    for wi, week in enumerate(weeks):
        for di, cell in enumerate(week):
            if not cell:
                continue
            day, count = cell
            lv = level(count, cuts)
            fill = t["empty"] if lv < 0 else t["scale"][lv]
            x = PAD + LEFT + wi * PITCH
            y = TOP + di * PITCH
            out.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" '
                f'fill="{fill}"><title>{count} on {day}</title></rect>'
            )

    # legend
    ly = TOP + 7 * PITCH + 14
    lx = width - PAD - (4 * PITCH + 60)
    out.append(
        f'<text x="{lx}" y="{ly + CELL - 2}" fill="{t["muted"]}" font-size="10">Less</text>'
    )
    out.append(
        f'<rect x="{lx + 30}" y="{ly}" width="{CELL}" height="{CELL}" rx="2" '
        f'fill="{t["empty"]}"/>'
    )
    for i, c in enumerate(t["scale"]):
        out.append(
            f'<rect x="{lx + 30 + (i + 1) * PITCH}" y="{ly}" width="{CELL}" '
            f'height="{CELL}" rx="2" fill="{c}"/>'
        )
    out.append(
        f'<text x="{lx + 30 + 5 * PITCH + 4}" y="{ly + CELL - 2}" fill="{t["muted"]}" '
        f'font-size="10">More</text>'
    )

    out.append("</svg>")
    return "\n".join(out)


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Set GH_TOKEN (needs read:user).")

    days, stats, per_account = collect(token)
    os.makedirs(OUT_DIR, exist_ok=True)

    for theme in THEMES:
        path = os.path.join(OUT_DIR, f"contributions-{theme}.svg")
        with open(path, "w") as fh:
            fh.write(render(days, stats, per_account, theme))
        print(f"wrote {os.path.relpath(path)}")

    with open(os.path.join(OUT_DIR, "contributions.json"), "w") as fh:
        json.dump(
            {"generated": date.today().isoformat(), "accounts": per_account, **stats},
            fh,
            indent=2,
        )
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
