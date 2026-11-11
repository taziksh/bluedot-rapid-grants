# bluedot-rapid-grants

All public [BlueDot Rapid Grants](https://bluedot.org/grants/rapid), updated weekly.

## Querying

```sh
git pull
python3 refresh.py --db-only
sqlite3 grants.db
```

```sql
-- search
SELECT g.amount_usd, g.month, g.grantee, g.title
FROM grants_fts f JOIN grants g ON g.id = f.rowid
WHERE grants_fts MATCH 'interpretability' ORDER BY rank;

-- biggest grants
SELECT amount_usd, grantee, title FROM grants ORDER BY amount_usd DESC LIMIT 20;

-- new since a date
SELECT * FROM grants WHERE first_seen >= '2026-12-01';
```

## Data

`data/grants.jsonl`, one grant per line:

- `grantee`, `title`, `amount_usd`, `month` (`YYYY-MM`)
- `summary`, `link`: often empty
- `first_seen`: when we first saw it
- `removed_on`: when it left the public list, if ever

`data/stats.csv`: BlueDot's overall totals (all grants, including ones not shown publicly), one row per refresh.

`git log -p data/` shows what changed each week.

## Updates

A weekly cron job (GitHub Action, Mondays 7am PT) pulls the latest grants and commits any changes.

To update now instead of waiting:

```sh
gh workflow run refresh
```
