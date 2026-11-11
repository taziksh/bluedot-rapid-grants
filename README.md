# bluedot-rapid-grants

Every publicly listed [BlueDot Rapid Grant](https://bluedot.org/grants/rapid), refreshed weekly.

A GitHub Action fetches BlueDot's public endpoints every Monday and commits any changes, so `git log -p data/` is a history of new and removed grants.

## Data

`data/grants.jsonl` has one grant per line, sorted newest month first:

| field | type | notes |
|---|---|---|
| `grantee` | string | person or group; a few list several people |
| `title` | string | short title or a sentence of free text |
| `summary` | string \| null | only on some recent grants |
| `amount_usd` | number | |
| `month` | `YYYY-MM` | month awarded; no day given |
| `link` | string \| null | grantee's site or project page |
| `first_seen` | `YYYY-MM-DD` | first refresh that saw it (rows from the first import show `2026-11-11`) |
| `removed_on` | `YYYY-MM-DD` \| null | refresh where it left the public list |

The API has no IDs, so a grant is identified by grantee + title + amount + month. If a grantee edits their title, the old row gets `removed_on` and the new title shows up as a new row.

`data/stats.csv` stores BlueDot's program-wide stats, one row per refresh: all grants made (including ones that aren't publicly listed), total awarded, average days to decision, and the public list size.

Source: `GET https://bluedot.org/api/trpc/grants.getAllPublicRapidGrantees` and `.getRapidGrantStats` (public, no auth).

## Query

```sh
python3 refresh.py --db-only   # build grants.db from data/ (no fetch)
sqlite3 grants.db
```

Tables are `grants`, `stats_snapshots`, and `grants_fts` (a full-text index over title, summary, grantee).

```sql
-- full-text search, best matches first
SELECT g.amount_usd, g.month, g.grantee, g.title
FROM grants_fts f JOIN grants g ON g.id = f.rowid
WHERE grants_fts MATCH 'interpretability OR probes'
ORDER BY rank LIMIT 20;

-- money and count by month
SELECT month, COUNT(*) n, SUM(amount_usd) total, AVG(amount_usd) avg
FROM grants GROUP BY month ORDER BY month DESC;

-- largest grants
SELECT amount_usd, month, grantee, title FROM grants
ORDER BY amount_usd DESC LIMIT 20;

-- people with more than one grant
SELECT grantee, COUNT(*) n, SUM(amount_usd) total FROM grants
GROUP BY grantee HAVING n > 1 ORDER BY n DESC;

-- what's new since a date
SELECT * FROM grants WHERE first_seen >= '2026-12-01';
```

## Refresh

- **Automatic:** `.github/workflows/refresh.yml` runs Mondays 15:00 UTC.
- **On demand:** Actions tab, then **refresh**, then **Run workflow**. Or run `gh workflow run refresh`.
- **Locally:** `python3 refresh.py --db` (fetch, update `data/`, rebuild `grants.db`).

The script refuses to update if the live list comes back less than half the size of what's stored, so an upstream glitch can't mark everything removed. It uses only the Python standard library.
