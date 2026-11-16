"""A PostgreSQL schema for tests, used only when TEST_DATABASE_URL is set.

The PostgreSQL tests are skipped unless TEST_DATABASE_URL is set and psycopg is installed,
so make test needs no database. Each test class gets its own schema with a random name,
dropped when the class ends, so the tests never touch other tables in that database.
"""

import importlib.util
import os
import unittest
from uuid import uuid4

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
HAVE_PSYCOPG = importlib.util.find_spec("psycopg") is not None
needs_postgres = unittest.skipUnless(
    TEST_DATABASE_URL and HAVE_PSYCOPG,
    "Set TEST_DATABASE_URL and install psycopg to run the PostgreSQL tests.",
)


class PostgresTestCase(unittest.TestCase):
    """Make a schema for the class and empty its tables before each test."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        from noteworthy_app.database import Database

        schema = f"test_{uuid4().hex}"
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        cls.addClassCleanup(cls.drop_schema, schema)
        # The schema comes first on the search path, so the app makes its tables there.
        cls.database_url = make_conninfo(TEST_DATABASE_URL, options=f"-c search_path={schema}")
        cls.database = Database(cls.database_url)

    @staticmethod
    def drop_schema(schema):
        import psycopg
        from psycopg import sql

        with psycopg.connect(TEST_DATABASE_URL) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

    def setUp(self):
        super().setUp()
        self.execute("TRUNCATE accounts, sessions, quizzes, attempts")

    def execute(self, query, params=()):
        """Run one statement in the test schema; return its rows, if it has any."""
        with self.database.connect() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchall() if cursor.description else None
