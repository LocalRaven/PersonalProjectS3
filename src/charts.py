"""Dropout and teacher-popularity analysis. Reads data/processed/*.parquet via DuckDB,
writes interactive charts to reports/figures/*.html.

    python src/charts.py

In a notebook:  from charts import *;  dropout_by_level().show()

Definitions (change them here if you disagree):
  * "dropout"  = a registration with status CANCELED
  * only COMPLETED courses and REGISTERED/CANCELED registrations are counted
    (INITIAL = never finished registering; ongoing/future courses would skew recent months)
  * "level" = the course's proficiency level; courses spanning several levels are left out of
    per-level charts so nobody is counted twice
"""
from pathlib import Path

import duckdb
import plotly.express as px

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"
FIGS = ROOT / "reports" / "figures"

con = duckdb.connect()
for f in DATA.glob("*.parquet"):
    con.execute(f"CREATE VIEW {f.stem} AS SELECT * FROM read_parquet('{f.as_posix()}')")

# One row per registration, enriched with course, level and the student's course number.
con.execute("""
CREATE VIEW single_level AS
SELECT cl.course_id, any_value(pl.level_code) AS level_code, any_value(pl."order") AS level_order
FROM course_levels cl JOIN proficiency_levels pl ON pl.id = cl.proficiency_level_id
GROUP BY cl.course_id HAVING count(*) = 1
""")
con.execute("""
CREATE VIEW enrol AS
SELECT ca.id, ca.user_id, ca.course_id, c.teacher_id, c.course_type, c.start_date,
       (ca.status = 'CANCELED')::INT AS dropped,
       sl.level_code, sl.level_order,
       row_number() OVER (PARTITION BY ca.user_id ORDER BY c.start_date, ca.id) AS course_no
FROM course_attendees ca
JOIN courses c ON c.id = ca.course_id
LEFT JOIN single_level sl ON sl.course_id = c.id
WHERE c.status = 'COMPLETED' AND ca.status IN ('REGISTERED', 'CANCELED')
""")


def q(sql):
    return con.execute(sql).df()


def save(fig, name):
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.write_html(FIGS / f"{name}.html", include_plotlyjs="cdn")
    return fig


# --------------------------------------------------------------------------- dropout
def dropout_by_level():
    df = q("""SELECT level_code, level_order, count(*) registrations,
                     round(100 * avg(dropped), 1) AS cancel_pct
              FROM enrol WHERE level_code IS NOT NULL GROUP BY 1, 2 ORDER BY level_order""")
    fig = px.bar(df, x="level_code", y="cancel_pct", text="cancel_pct", hover_data=["registrations"],
                 title="Cancellation rate by course level (%)", labels={"level_code": "Level", "cancel_pct": "% canceled"})
    return save(fig, "dropout_by_level")


def dropout_over_time():
    df = q("""SELECT date_trunc('quarter', start_date)::DATE AS quarter, count(*) registrations,
                     round(100 * avg(dropped), 1) AS cancel_pct
              FROM enrol WHERE start_date >= DATE '2021-01-01' GROUP BY 1 ORDER BY 1""")
    fig = px.line(df, x="quarter", y="cancel_pct", markers=True, hover_data=["registrations"],
                  title="Cancellation rate by quarter the course started (%)")
    return save(fig, "dropout_over_time")


def dropout_by_course_number():
    df = q("""SELECT least(course_no, 6) AS course_no, count(*) registrations,
                     round(100 * avg(dropped), 1) AS cancel_pct
              FROM enrol GROUP BY 1 ORDER BY 1""")
    df["course_no"] = df["course_no"].astype(str).replace({"6": "6+"})
    fig = px.bar(df, x="course_no", y="cancel_pct", text="cancel_pct", hover_data=["registrations"],
                 title="Cancellation rate by how many courses the student had already started",
                 labels={"course_no": "Student's Nth course", "cancel_pct": "% canceled"})
    return save(fig, "dropout_by_course_number")


def continuation_by_level():
    """Of students who attended level X, how many later attended a higher level?"""
    df = q("""
    WITH att AS (
      SELECT user_id, start_date, level_code, level_order,
             max(level_order) OVER (PARTITION BY user_id ORDER BY start_date, id
                                    ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) AS max_later
      FROM enrol WHERE dropped = 0 AND level_order IS NOT NULL)
    SELECT level_code, level_order, count(*) students_at_level,
           round(100 * avg((coalesce(max_later, 0) > level_order)::INT), 1) AS continued_pct
    FROM att
    WHERE level_order < 8                                   -- top level has nowhere to go
      AND start_date < current_date - INTERVAL 1 YEAR       -- give students a year to continue
    GROUP BY 1, 2 ORDER BY 2""")
    fig = px.bar(df, x="level_code", y="continued_pct", text="continued_pct", hover_data=["students_at_level"],
                 title="% of students who later moved to a higher level (courses started over a year ago)",
                 labels={"level_code": "Level attended", "continued_pct": "% continued"})
    return save(fig, "continuation_by_level")


# --------------------------------------------------------------------------- teachers
def teacher_table(min_courses=10):
    return q(f"""
    WITH per_student AS (
      SELECT teacher_id, user_id, count(DISTINCT course_id) n FROM enrol WHERE dropped = 0 GROUP BY 1, 2),
    fb AS (
      SELECT c.teacher_id, avg(f.score)::DOUBLE AS avg_feedback
      FROM course_feedback f JOIN courses c ON c.id = f.course_id GROUP BY 1)
    SELECT t.en_name || ' #' || t.id AS teacher,
           count(DISTINCT e.course_id) AS courses,
           count(DISTINCT e.user_id) FILTER (WHERE e.dropped = 0) AS students,
           round(100 * avg(e.dropped), 1) AS cancel_pct,
           round(100 * (SELECT avg((n >= 2)::INT) FROM per_student p WHERE p.teacher_id = t.id), 1) AS repeat_pct,
           round(any_value(fb.avg_feedback), 2) AS avg_feedback
    FROM enrol e JOIN teachers t ON t.id = e.teacher_id LEFT JOIN fb ON fb.teacher_id = t.id
    GROUP BY t.id, t.en_name
    HAVING count(DISTINCT e.course_id) >= {min_courses}
    ORDER BY students DESC""")


def teacher_popularity(top=15):
    df = teacher_table().head(top).sort_values("students")
    fig = px.bar(df, x="students", y="teacher", orientation="h", text="students",
                 hover_data=["courses", "repeat_pct", "avg_feedback", "cancel_pct"],
                 title=f"Top {top} teachers by distinct students taught")
    return save(fig, "teacher_popularity")


def teacher_quality_vs_popularity():
    df = teacher_table()
    fig = px.scatter(df, x="students", y="avg_feedback", size="courses", hover_name="teacher",
                     color="repeat_pct", color_continuous_scale="Viridis",
                     title="Teacher popularity vs. feedback (colour = % students who came back for a 2nd course)")
    return save(fig, "teacher_quality_vs_popularity")


if __name__ == "__main__":
    for fn in (dropout_by_level, dropout_over_time, dropout_by_course_number,
               continuation_by_level, teacher_popularity, teacher_quality_vs_popularity):
        fn()
        print("wrote", fn.__name__)
    print("\nTop teachers:\n", teacher_table().head(10).to_string(index=False))
