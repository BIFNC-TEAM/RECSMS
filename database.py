"""PostgreSQL production connection; SQLite remains available for local development."""
import os
import re
import sqlite3
from contextlib import contextmanager


def production():
    return bool(os.environ.get('DATABASE_URL'))


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Cursor:
    def __init__(self, cursor, inserted=False):
        self.cursor = cursor
        self.lastrowid = None
        if inserted:
            row = cursor.fetchone()
            self.lastrowid = row[0] if row else None

    def fetchone(self):
        row = self.cursor.fetchone()
        return Row(zip((c.name for c in self.cursor.description), row)) if row is not None else None

    def fetchall(self):
        return list(self)

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


def postgres_sql(sql):
    # These are the small SQLite dialect features used by this application's queries.
    sql = re.sub(r'\bid INTEGER PRIMARY KEY\b', 'id SERIAL PRIMARY KEY', sql)
    sql = re.sub(r'\bBLOB\b', 'BYTEA', sql)
    sql = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', sql)
    ignore = bool(re.match(r'INSERT OR IGNORE\b', sql, re.I))
    sql = re.sub(r'^INSERT OR IGNORE\b', 'INSERT', sql, flags=re.I)
    if ignore:
        sql += ' ON CONFLICT DO NOTHING'
    # Only replace placeholders outside SQL string literals (including doubled quotes).
    parts = re.split(r"('(?:[^']|'')*')", sql)
    return ''.join(p.replace('%', '%%').replace('?', '%s') if i % 2 == 0 else p.replace('%', '%%') for i, p in enumerate(parts))


class Postgres:
    def __init__(self):
        import psycopg
        self.raw = psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=10)
        self.write_locked = False

    @property
    def in_transaction(self):
        return self.write_locked

    def write_lock(self):
        if not self.write_locked:
            # ponytail: preserve the pilot's serialized writes across instances; use row locks if write volume grows.
            self.raw.execute('SELECT pg_advisory_xact_lock(18274, 1)')
            self.write_locked = True

    def execute(self, sql, parameters=()):
        import psycopg
        sql = sql.strip().rstrip(';')
        if sql == 'BEGIN IMMEDIATE':
            self.write_lock()
            return Cursor(self.raw.execute('SELECT 1'))
        if match := re.fullmatch(r'PRAGMA table_info\((\w+)\)', sql):
            return Cursor(self.raw.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (match[1],)))
        if sql == "SELECT name FROM sqlite_master WHERE type='table'":
            return Cursor(self.raw.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=current_schema()'))
        if re.match(r'(INSERT|UPDATE|DELETE|CREATE|ALTER)\b', sql, re.I):
            self.write_lock()
        generated = {'users', 'directories', 'documents', 'versions', 'attachments', 'opinions', 'transitions', 'todos', 'grants', 'audit', 'evaluations', 'schedules', 'share_links', 'document_comments', 'review_issues', 'issue_events'}
        match = re.match(r'INSERT(?: OR IGNORE)? INTO (\w+)\b', sql, re.I)
        inserted = bool(match and match[1] in generated)
        statement = postgres_sql(sql)
        if inserted:
            statement += ' RETURNING id'
        try:
            if re.match(r'(INSERT|UPDATE|DELETE|CREATE|ALTER)\b', sql, re.I):
                # Match SQLite statement-failure semantics when callers handle a constraint error.
                with self.raw.transaction():
                    return Cursor(self.raw.execute(statement, parameters), inserted)
            return Cursor(self.raw.execute(statement, parameters), inserted)
        except psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError('PostgreSQL constraint violation') from exc

    def executescript(self, script):
        for statement in script.split(';'):
            if statement.strip():
                self.execute(statement)

    def commit(self):
        self.raw.commit()
        self.write_locked = False

    def rollback(self):
        self.raw.rollback()
        self.write_locked = False

    def close(self):
        self.raw.close()


@contextmanager
def connect(local_path):
    db = Postgres() if production() else sqlite3.connect(local_path, timeout=20)
    if not production():
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def job_lock(name):
    """Session advisory lock spans network calls without a long database transaction."""
    if not production():
        yield True
        return
    import psycopg
    with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True, connect_timeout=10) as db:
        key = {'autosave': 2}[name]
        locked = db.execute('SELECT pg_try_advisory_lock(18274, %s)', (key,)).fetchone()[0]
        try:
            yield locked
        finally:
            if locked:
                db.execute('SELECT pg_advisory_unlock(18274, %s)', (key,))
