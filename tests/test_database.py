import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import src.database as database


class DatabaseFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp_dir.name, "test.db")
        self.database_patch = patch.object(
            database, "DATABASE_NAME", self.database_path
        )
        self.database_patch.start()
        database.create_database()

    def tearDown(self):
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def test_period_summary_and_daily_totals(self):
        today = date.today()
        yesterday = today - timedelta(days=1)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executemany(
                """
                INSERT INTO activities (
                    timestamp, application_name, window_title,
                    duration_seconds, executable_path
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (f"{today}T09:00:00", "Browser.exe", "Docs", 120, "browser.exe"),
                    (f"{today}T10:00:00", "Browser.exe", "Mail", 60, "browser.exe"),
                    (f"{yesterday}T12:00:00", "Editor.exe", "Code", 300, "editor.exe"),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        rows = database.get_summary_for_period(
            today.isoformat(), (today + timedelta(days=1)).isoformat()
        )
        self.assertEqual(sum(row[2] for row in rows), 180)
        self.assertEqual(
            dict(
                database.get_daily_totals(
                    today.isoformat(), (today + timedelta(days=1)).isoformat()
                )
            )[today.isoformat()],
            180,
        )

    def test_timeline_settings_and_deletion(self):
        today = date.today()
        start, end = today.isoformat(), (today + timedelta(days=1)).isoformat()
        database.log_activity("Editor.exe", "Code", 42, "editor.exe")

        timeline = database.get_activities_for_period(start, end)
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0][1:], ("Editor.exe", "Code", 42.0))

        self.assertEqual(database.get_setting("daily_goal_minutes", "240"), "240")
        database.set_setting("daily_goal_minutes", "90")
        self.assertEqual(database.get_setting("daily_goal_minutes"), "90")
        self.assertEqual(database.delete_activities_for_period(start, end), 1)
        database.log_activity("Browser.exe", "Docs", 10)
        self.assertEqual(database.delete_all_activities(), 1)
        self.assertEqual(database.get_activities_for_period(start, end), [])

    def test_migrates_legacy_executable_database(self):
        legacy_path = os.path.join(self.temp_dir.name, "legacy.db")
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE activities (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    application_name TEXT NOT NULL,
                    window_title TEXT NOT NULL,
                    duration_seconds REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO activities (
                    timestamp, application_name, window_title, duration_seconds
                ) VALUES (?, ?, ?, ?)
                """,
                ("2026-01-01T09:00:00", "Legacy.exe", "Old data", 30),
            )
            connection.commit()
        finally:
            connection.close()

        target_path = os.path.join(self.temp_dir.name, "appdata", "activity.db")
        with (
            patch.object(database, "DATABASE_NAME", target_path),
            patch.object(database, "_legacy_database_name", legacy_path),
        ):
            database.create_database()
            rows = database.get_activities_for_period(
                "2026-01-01", "2026-01-02"
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:], ("Legacy.exe", "Old data", 30.0))

    def test_frozen_linux_database_uses_xdg_data_directory(self):
        xdg_data_home = os.path.join(self.temp_dir.name, "xdg-data")
        with (
            patch.object(database.sys, "platform", "linux"),
            patch.dict(os.environ, {"XDG_DATA_HOME": xdg_data_home}),
        ):
            self.assertEqual(
                database._get_frozen_data_directory(),
                os.path.join(xdg_data_home, "TimeTracker"),
            )

    def test_frozen_linux_database_falls_back_to_local_share(self):
        with (
            patch.object(database.sys, "platform", "linux"),
            patch.dict(os.environ, {"XDG_DATA_HOME": ""}),
            patch("src.database.os.path.expanduser", return_value=self.temp_dir.name),
        ):
            self.assertEqual(
                database._get_frozen_data_directory(),
                os.path.join(self.temp_dir.name, ".local", "share", "TimeTracker"),
            )


if __name__ == "__main__":
    unittest.main()
