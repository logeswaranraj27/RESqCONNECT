import os
import sqlite3
from urllib.parse import urlparse

import psycopg


class Database:
    """Portable database connection wrapper for SQLite and PostgreSQL."""

    def __init__(self, database_url):
        self.database_url = database_url

    @property
    def is_postgres(self):
        return self.database_url.startswith(("postgres://", "postgresql://"))

    def connect(self):
        if self.is_postgres:
            connection = psycopg.connect(self.database_url)
            connection.row_factory = psycopg.rows.dict_row
            return connection

        path = self.database_url.replace("sqlite:///", "", 1)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    def placeholder_sql(self, sql):
        return sql.replace("?", "%s") if self.is_postgres else sql

    def initialize(self):
        schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "schema.sql")
        with open(schema_path, "r", encoding="utf-8") as schema_file:
            schema = schema_file.read()
        connection = self.connect()
        try:
            if self.is_postgres:
                with connection.cursor() as cursor:
                    for statement in schema.split(";"):
                        if statement.strip():
                            cursor.execute(statement)
            else:
                connection.executescript(schema)
            connection.commit()
        finally:
            connection.close()

        from app.repositories.migrations import run_migrations
        run_migrations(self)
