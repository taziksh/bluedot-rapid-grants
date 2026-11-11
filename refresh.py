"""Refresh the BlueDot Rapid Grants dataset.

Fetches the public grantee list and program stats from bluedot.org, merges
them into data/grants.jsonl (one grant per line, sorted for clean git diffs),
and appends a row to data/stats.csv. With --db, also builds grants.db, a
SQLite copy with full-text search.

Usage:
    python3 refresh.py          # fetch + update data/
    python3 refresh.py --db     # same, then rebuild grants.db
    python3 refresh.py --db-only  # rebuild grants.db from data/, no fetch

Uses only the Python standard library.
"""

import argparse
import csv
import datetime
import json
import pathlib
import sqlite3
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
GRANTS_PATH = ROOT / "data" / "grants.jsonl"
STATS_PATH = ROOT / "data" / "stats.csv"
DB_PATH = ROOT / "grants.db"

API = "https://bluedot.org/api/trpc/grants."
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
STATS_FIELDS = ["date", "grant_count", "total_usd", "avg_days_to_decision",
                "public_count"]

# Guard: if the live list shrinks below this fraction of what we hold, treat
# it as an upstream glitch instead of marking most grants removed.
MIN_KEEP_RATIO = 0.5


def fetch(procedure):
    """Fetches one tRPC procedure from bluedot.org.

    tRPC is the RPC layer the site's frontend uses; each procedure is a plain
    GET that returns {"result": {"data": ...}}.

    Args:
        procedure: Procedure name, e.g. "getAllPublicRapidGrantees".

    Returns:
        The decoded `data` payload.
    """
    req = urllib.request.Request(
        API + procedure,
        headers={"Accept": "application/json",
                 "User-Agent": "rapid-grants-refresh"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["result"]["data"]


def to_month(label):
    """Converts "Nov 2026" to "2026-11" so months sort as text."""
    mon, year = label.split()
    return f"{year}-{MONTHS.index(mon) + 1:02d}"


def normalize(raw):
    """Maps an API record to our field names. Missing optionals become None."""
    amount = raw["amountUsd"]
    return {
        "grantee": raw["granteeName"].strip(),
        "title": raw["projectTitle"].strip(),
        "summary": (raw.get("projectSummary") or "").strip() or None,
        "amount_usd": int(amount) if amount == int(amount) else amount,
        "month": to_month(raw["monthLabel"]),
        "link": (raw.get("link") or "").strip() or None,
    }


def key(grant):
    """Identity of a grant. The API has no IDs, so these four fields stand in."""
    return (grant["grantee"], grant["title"], grant["amount_usd"],
            grant["month"])


def sort_key(grant):
    """Newest month first, then alphabetical, so line order is stable."""
    return (-int(grant["month"].replace("-", "")), grant["grantee"].lower(),
            grant["title"].lower())


def load_grants():
    """Reads data/grants.jsonl, or returns [] on first run."""
    if not GRANTS_PATH.exists():
        return []
    with GRANTS_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def merge(existing, live, today):
    """Merges the live list into stored grants.

    Each stored grant carries `first_seen` (date we first saw it) and
    `removed_on` (date it left the public list, else None). Only real changes
    touch a line, so weekly diffs show just new, edited or removed grants.

    Args:
        existing: Grants from data/grants.jsonl.
        live: Normalized grants from the API.
        today: ISO date string for this run.

    Returns:
        (merged list, count added, count removed, count returned).
    """
    stored = {key(g): g for g in existing}
    live_keys = set()
    added = returned = 0
    for g in live:
        k = key(g)
        live_keys.add(k)
        if k in stored:
            old = stored[k]
            if old.get("removed_on"):
                returned += 1
            # Summary/link can be filled in later; keep the latest values.
            old.update(summary=g["summary"], link=g["link"], removed_on=None)
        else:
            stored[k] = {**g, "first_seen": today, "removed_on": None}
            added += 1
    removed = 0
    for k, g in stored.items():
        if k not in live_keys and not g.get("removed_on"):
            g["removed_on"] = today
            removed += 1
    return sorted(stored.values(), key=sort_key), added, removed, returned


def write_grants(grants):
    """Writes one JSON object per line with a fixed field order."""
    order = ["grantee", "title", "summary", "amount_usd", "month", "link",
             "first_seen", "removed_on"]
    with GRANTS_PATH.open("w") as f:
        for g in grants:
            f.write(json.dumps({k: g.get(k) for k in order},
                               ensure_ascii=False) + "\n")


def append_stats(stats, public_count, today):
    """Adds today's program-wide stats to data/stats.csv (one row per day)."""
    rows = []
    if STATS_PATH.exists():
        with STATS_PATH.open() as f:
            rows = [r for r in csv.DictReader(f) if r["date"] != today]
    rows.append({
        "date": today,
        "grant_count": stats["count"],
        "total_usd": round(stats["totalAmountUsd"], 2),
        "avg_days_to_decision": round(stats["averageDaysToDecision"], 2),
        "public_count": public_count,
    })
    with STATS_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=STATS_FIELDS)
        w.writeheader()
        w.writerows(rows)


def build_db(grants):
    """Rebuilds grants.db from scratch.

    Tables: `grants` (one row per grant), `stats_snapshots` (from stats.csv),
    and `grants_fts`, an FTS5 full-text index over title, summary and grantee.
    """
    DB_PATH.unlink(missing_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE grants (
          id          INTEGER PRIMARY KEY,
          grantee     TEXT NOT NULL,
          title       TEXT NOT NULL,
          summary     TEXT,
          amount_usd  REAL NOT NULL,
          month       TEXT NOT NULL,
          link        TEXT,
          first_seen  TEXT NOT NULL,
          removed_on  TEXT,
          UNIQUE (grantee, title, amount_usd, month)
        );
        CREATE INDEX grants_month ON grants(month);
        CREATE TABLE stats_snapshots (
          date                  TEXT PRIMARY KEY,
          grant_count           INTEGER,
          total_usd             REAL,
          avg_days_to_decision  REAL,
          public_count          INTEGER
        );
        CREATE VIRTUAL TABLE grants_fts USING fts5(
          title, summary, grantee, content='grants', content_rowid='id'
        );
    """)
    con.executemany(
        "INSERT INTO grants (grantee, title, summary, amount_usd, month, link,"
        " first_seen, removed_on) VALUES (:grantee, :title, :summary,"
        " :amount_usd, :month, :link, :first_seen, :removed_on)",
        grants)
    con.execute("INSERT INTO grants_fts(grants_fts) VALUES ('rebuild')")
    if STATS_PATH.exists():
        with STATS_PATH.open() as f:
            con.executemany(
                "INSERT INTO stats_snapshots VALUES (:date, :grant_count,"
                " :total_usd, :avg_days_to_decision, :public_count)",
                csv.DictReader(f))
    con.commit()
    con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", action="store_true",
                        help="also rebuild grants.db")
    parser.add_argument("--db-only", action="store_true",
                        help="rebuild grants.db from data/ without fetching")
    args = parser.parse_args()

    if args.db_only:
        grants = load_grants()
        build_db(grants)
        print(f"built {DB_PATH.name}: {len(grants)} grants")
        return

    today = datetime.date.today().isoformat()
    live = [normalize(r) for r in fetch("getAllPublicRapidGrantees")]
    stats = fetch("getRapidGrantStats")

    existing = load_grants()
    listed = sum(1 for g in existing if not g.get("removed_on"))
    if not live or len(live) < listed * MIN_KEEP_RATIO:
        sys.exit(f"refusing to update: API returned {len(live)} grants,"
                 f" we hold {listed} listed")

    grants, added, removed, returned = merge(existing, live, today)
    GRANTS_PATH.parent.mkdir(exist_ok=True)
    write_grants(grants)
    append_stats(stats, len(live), today)

    summary = f"+{added} new, -{removed} removed"
    if returned:
        summary += f", {returned} returned"
    print(f"{summary} ({len(live)} listed, {stats['count']} total grants)")

    if args.db:
        build_db(grants)
        print(f"built {DB_PATH.name}")


if __name__ == "__main__":
    main()
