# lingophoenix-analysis

Small data-science project on the LingoPhoenix MySQL export: where do students drop out,
which teachers are most popular, and related trends.

## Workflow

```bash
# 0. one-time setup
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1. throwaway MySQL 8 (the dump needs MySQL 8, not MariaDB)
docker run -d --name lingo-mysql -e MYSQL_ROOT_PASSWORD=lingo -e MYSQL_DATABASE=lingo \
  -p 3306:3306 mysql:8.0 --character-set-server=utf8mb4

# 2. restore the raw dump (data/raw/lingo-export.sql - unzip it first; takes a few minutes)
docker exec -i lingo-mysql mysql -uroot -plingo --default-character-set=utf8mb4 lingo < data/raw/lingo-export.sql

# 3. strip sensitive columns/tables from that copy (prints an audit at the end)
python src/clean_db.py

# 4. (optional) save the sanitised database as the only dump you keep in the repo
docker exec lingo-mysql mysqldump -uroot -plingo --default-character-set=utf8mb4 lingo > data/clean/lingo-clean.sql

# 5. export tables to parquet, then draw the charts
python src/export.py
python src/charts.py          # opens nothing; look in reports/figures/*.html
```

Afterwards `docker rm -f lingo-mysql` and delete `data/raw/` (or move it out of the repo folder).

## What is removed (src/clean_db.py)
Names, emails, phone numbers, ID/bank/card numbers, password hashes and tokens, avatars, addresses,
free-text notes and descriptions, exact birth dates (kept as birth year), plus the message table
(`user_messages`), settings, jobs, and CMS/permission tables. Edit the two lists at the top to change this.

## Metric definitions (src/charts.py)
- dropout = registration with status CANCELED
- only COMPLETED courses; INITIAL registrations (never finished signing up) are ignored
- per-level charts skip courses that span several levels
