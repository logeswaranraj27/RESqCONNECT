import uuid
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix):
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class BaseRepository:
    def __init__(self, database):
        self.database = database

    def fetchone(self, sql, params=()):
        connection = self.database.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(self.database.placeholder_sql(sql), params)
            row = cursor.fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def fetchall(self, sql, params=()):
        connection = self.database.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(self.database.placeholder_sql(sql), params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def execute(self, sql, params=()):
        connection = self.database.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(self.database.placeholder_sql(sql), params)
            connection.commit()
            return cursor.rowcount
        finally:
            connection.close()
