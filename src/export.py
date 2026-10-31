"""Export every table of the CLEANED database to data/processed/<table>.parquet.

    python src/export.py
"""
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, inspect, text

URL = os.environ.get("DATABASE_URL", "mysql+pymysql://root:lingo@127.0.0.1:3306/lingo")
OUT = Path(__file__).resolve().parent.parent / "data" / "processed"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL)
    for table in sorted(inspect(engine).get_table_names()):
        df = pd.read_sql(text(f"SELECT * FROM `{table}`"), engine)
        df.to_parquet(OUT / f"{table}.parquet", index=False)
        print(f"{table:28} {len(df):>8,} rows")


if __name__ == "__main__":
    main()
