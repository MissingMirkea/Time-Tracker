import os
import shutil
import sys
import sqlite3
from datetime import datetime

if getattr(sys, "frozen", False):
    _data_directory = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "TimeTracker",
    )
    DATABASE_NAME = os.path.join(_data_directory, "my_database.db")
    _legacy_database_name = os.path.join(
        os.path.dirname(sys.executable), "my_database.db"
    )
else:
    DATABASE_NAME = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "my_database.db"
    )
    _legacy_database_name = ""

def create_database():
    """Create a new SQLite database if it doesn't exist."""
    database_directory = os.path.dirname(os.path.abspath(DATABASE_NAME))
    os.makedirs(database_directory, exist_ok=True)
    if (
        _legacy_database_name
        and os.path.normcase(os.path.abspath(_legacy_database_name))
        != os.path.normcase(os.path.abspath(DATABASE_NAME))
        and not os.path.exists(DATABASE_NAME)
        and os.path.isfile(_legacy_database_name)
    ):
        shutil.copy2(_legacy_database_name, DATABASE_NAME)

    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()

    # Create the table if it doesn't exist
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            application_name TEXT NOT NULL,
            window_title TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            executable_path TEXT NOT NULL DEFAULT '')
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    cursor.execute("PRAGMA table_info(activities)")
    columns = {row[1] for row in cursor.fetchall()}
    if "executable_path" not in columns:
        cursor.execute(
            "ALTER TABLE activities ADD COLUMN executable_path TEXT NOT NULL DEFAULT ''"
        )

    connection.commit()
    connection.close()

def log_activity(
    application_name: str,
    window_title: str,
    duration_seconds: float,
    executable_path: str = "",
):
    """Log an activity to the database."""
    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()

    timestamp_now = datetime.now().isoformat()  # Get the current timestamp in ISO format

    cursor.execute(
        """
        INSERT INTO activities (
            timestamp, application_name, window_title, duration_seconds,
            executable_path
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            timestamp_now,
            application_name,
            window_title,
            round(duration_seconds, 2),
            executable_path,
        )
    )

    connection.commit()
    connection.close()

def get_summary():
    """Retrieve a summary of activities from the database."""
    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT application_name, window_title,
               SUM(duration_seconds) as total_duration,
               MAX(executable_path) as executable_path
        FROM activities
        GROUP BY application_name, window_title
        ORDER BY total_duration DESC
        """
    )

    summary = cursor.fetchall()
    connection.close()
    return summary


def get_summary_for_period(start_date: str, end_date: str):
    """Retrieve app and window totals in the half-open date range."""
    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT application_name, window_title,
               SUM(duration_seconds) as total_duration,
               MAX(executable_path) as executable_path
        FROM activities
        WHERE timestamp >= ? AND timestamp < ?
        GROUP BY application_name, window_title
        ORDER BY total_duration DESC
        """,
        (start_date, end_date),
    )
    summary = cursor.fetchall()
    connection.close()
    return summary


def get_daily_totals(start_date: str, end_date: str):
    """Retrieve total tracked seconds grouped by local calendar date."""
    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT substr(timestamp, 1, 10), SUM(duration_seconds)
        FROM activities
        WHERE timestamp >= ? AND timestamp < ?
        GROUP BY substr(timestamp, 1, 10)
        ORDER BY substr(timestamp, 1, 10)
        """,
        (start_date, end_date),
    )
    totals = cursor.fetchall()
    connection.close()
    return totals


def get_activities_for_period(start_date: str, end_date: str):
    """Retrieve individual activity records in the half-open date range."""
    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT timestamp, application_name, window_title, duration_seconds
        FROM activities
        WHERE timestamp >= ? AND timestamp < ?
        ORDER BY timestamp
        """,
        (start_date, end_date),
    )
    activities = cursor.fetchall()
    connection.close()
    return activities


def get_setting(key: str, default: str = "") -> str:
    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    connection.close()
    return row[0] if row else default


def set_setting(key: str, value: str):
    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    connection.commit()
    connection.close()


def delete_activities_for_period(start_date: str, end_date: str) -> int:
    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()
    cursor.execute(
        "DELETE FROM activities WHERE timestamp >= ? AND timestamp < ?",
        (start_date, end_date),
    )
    deleted_count = cursor.rowcount
    connection.commit()
    connection.close()
    return deleted_count


def delete_all_activities() -> int:
    connection = sqlite3.connect(DATABASE_NAME)
    cursor = connection.cursor()
    cursor.execute("DELETE FROM activities")
    deleted_count = cursor.rowcount
    connection.commit()
    connection.close()
    return deleted_count


if __name__ == "__main__":
    create_database()
    # Example usage: log an activity
    print("Database created successfully.")