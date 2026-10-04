"""docs/views.md names every view and column of the dash schema."""

import re

from conftest import REPO_DIR, query

CONTRACT_VIEWS = [
    "installs", "activity_days", "steps_hourly", "steps_raw_days", "heart_rate", "heart_rate_hourly",
    "heart_days", "measurements", "measurement_days", "weight_days", "sleep_nights", "sleep_stages",
    "workouts", "screen_days", "screen_apps", "type_summary", "records_days",
]


def test_views_md_lists_every_view_and_column(db):
    text = (REPO_DIR / "docs" / "views.md").read_text()
    rows = query(db, "SELECT table_name, column_name FROM information_schema.columns"
                     " WHERE table_schema = 'dash' ORDER BY table_name, ordinal_position")
    views = {}
    for view, column in rows:
        views.setdefault(view, []).append(column)
    for view in CONTRACT_VIEWS:
        assert view in views, f"dash.{view} missing from the database"
    for view, columns in views.items():
        heading = re.search(rf"^#+ `dash\.{view}`", text, re.M)
        assert heading, f"dash.{view} has no section in docs/views.md"
        section = text[heading.end():]
        nxt = re.search(r"^#+ `dash\.", section, re.M)
        section = section[: nxt.start()] if nxt else section
        for column in columns:
            assert f"`{column}`" in section, f"dash.{view}.{column} is not in docs/views.md"


def test_no_long_dashes_in_shipped_text():
    for path in [REPO_DIR / "docs" / "views.md", REPO_DIR / "receiver" / "receiver.py",
                 REPO_DIR / "receiver" / "views.sql", REPO_DIR / "docker-compose.yml",
                 REPO_DIR / "tools" / "sample_data.py", REPO_DIR / "tools" / "upgrade-postgres.sh",
                 REPO_DIR / "tools" / "pg-guard.sh", REPO_DIR / ".env.example",
                 *(REPO_DIR / "receiver" / "migrations").glob("*.sql")]:
        text = path.read_text()
        assert "\u2013" not in text and "\u2014" not in text, path
