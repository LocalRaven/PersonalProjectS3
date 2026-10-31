"""Remove sensitive / irrelevant columns and tables from the restored MySQL copy.

Run this ONLY against the throwaway local database you restored the dump into.
It permanently alters that database. It is safe to re-run (missing columns are skipped).

    python src/clean_db.py
"""
import os
import re

from sqlalchemy import bindparam, create_engine, text

URL = os.environ.get("DATABASE_URL", "mysql+pymysql://root:lingo@127.0.0.1:3306/lingo")

# Columns that identify a person, hold credentials, or are free text we don't need.
DROP_COLUMNS = {
    "users": [
        "name", "surname", "en_name", "en_surname", "phone_country_code", "phone_number",
        "identity_number", "email", "username", "refund_account_number", "password",
        "remember_token", "referral_code", "access_key", "messenger_id",
        "messenger_password", "avatar_path", "parent_id", "balance",
    ],
    "teachers": ["name", "surname", "en_surname", "short_bio", "biography", "image_path", "user_id"],
    "payments": ["uid", "payment_number", "transaction_id", "card_number", "receipt_path", "details"],
    "club_profiles": ["city", "address", "zip_code", "image_path", "admin_message", "birth_date"],
    "club_achievable_proofs": ["doc_path", "description", "admin_message"],
    "course_attendees": ["notes", "parent_user_id"],
    "courses": [
        "notes", "it_notes", "room_link", "group_link", "image_path", "messenger_group_id",
        "short_description", "full_description",
    ],
    "coupons": ["code"],
}

# Tables that are free text, secrets/config, or simply irrelevant to the analysis.
DROP_TABLES = [
    "user_messages",   # ~2/3 of the dump, free-text messages to students
    "failed_jobs", "settings", "migrations",
    "permissions", "roles", "role_permissions", "departments", "user_roles",
    "classroom_servers", "expressions", "journals", "journal_issues",
    "podcasts", "podcast_episodes", "course_episodes",
]

# Remaining columns matching this are printed for a manual look at the end.
SUSPICIOUS = re.compile(r"name|mail|phone|pass|token|card|address|identity|account|secret|key|path|link|note|bio|zip|birth", re.I)
ALLOWED = {  # reviewed and fine to keep
    ("teachers", "en_name"), ("course_categories", "category_name"), ("course_categories", "category_slug"),
    ("proficiency_levels", "level_name"), ("proficiency_levels", "equivalent_level_name"),
    ("club_profiles", "birth_year"), ("payments", "gateway_name"),
    ("users", "verified_phone"), ("users", "verified_email"),  # booleans, not the values
}


def columns_of(conn, table):
    rows = conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t"), {"t": table})
    return {r[0] for r in rows}


def main():
    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

        # Keep a coarse birth year (useful for age trends) before dropping the exact date.
        if "birth_date" in columns_of(conn, "club_profiles"):
            if "birth_year" not in columns_of(conn, "club_profiles"):
                conn.execute(text("ALTER TABLE club_profiles ADD COLUMN birth_year SMALLINT NULL"))
            conn.execute(text("UPDATE club_profiles SET birth_year = YEAR(birth_date)"))

        for table, cols in DROP_COLUMNS.items():
            present = [c for c in cols if c in columns_of(conn, table)]
            if not present:
                continue
            # A column that is part of a foreign key must have the constraint dropped first.
            fks = conn.execute(text(
                "SELECT DISTINCT constraint_name FROM information_schema.key_column_usage "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND referenced_table_name IS NOT NULL AND column_name IN :cols"
            ).bindparams(bindparam("cols", expanding=True)),
                {"t": table, "cols": present}).fetchall()
            for (fk,) in fks:
                conn.execute(text(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{fk}`"))
            drops = ", ".join(f"DROP COLUMN `{c}`" for c in present)
            conn.execute(text(f"ALTER TABLE `{table}` {drops}"))
            print(f"{table}: dropped {len(present)} columns")

        # Remove foreign keys that point at tables we are about to drop, so none dangle.
        for table in DROP_TABLES:
            refs = conn.execute(text(
                "SELECT DISTINCT table_name, constraint_name FROM information_schema.key_column_usage "
                "WHERE table_schema = DATABASE() AND referenced_table_name = :t"), {"t": table}).fetchall()
            for child, fk in refs:
                if child not in DROP_TABLES:
                    conn.execute(text(f"ALTER TABLE `{child}` DROP FOREIGN KEY `{fk}`"))
        for table in DROP_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
        print(f"dropped {len(DROP_TABLES)} tables")

        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

        # Audit: anything left that still looks sensitive?
        rows = conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() ORDER BY table_name, ordinal_position")).fetchall()
        left = [(t, c) for t, c in rows if SUSPICIOUS.search(c) and (t, c) not in ALLOWED]
        print("\nAUDIT - columns still matching sensitive patterns:", left or "none")


if __name__ == "__main__":
    main()
