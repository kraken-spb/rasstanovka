"""Pooled PostgreSQL storage for the existing staffing query contract.

Translation is parsed, cached and explicit. Unsupported dialect features fail;
they never select another database. New workforce code can use native SQL.
"""
from functools import lru_cache
import os
import sqlite3
import threading
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from psycopg_pool import ConnectionPool
import sqlglot
from sqlglot import exp


class ConcurrentChange(Exception):
    """The transaction was rolled back because its snapshot became stale."""


class Record:
    """Both named and positional access, matching the retained sqlite Row API."""
    __slots__ = ('_values', '_names', '_lookup')

    def __init__(self, values, names):
        self._values, self._names = tuple(str(value) if isinstance(value, UUID) else value for value in values), names
        self._lookup = dict(zip(names, self._values))

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, (int, slice)) else self._lookup[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return self._names


class Cursor:
    def __init__(self, cursor, *, inserted_id=False):
        self._cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = None
        self._names = tuple(column.name for column in cursor.description or ())
        if inserted_id:
            row = cursor.fetchone()
            self.lastrowid = row[0] if row else None

    def fetchone(self):
        row = self._cursor.fetchone()
        return None if row is None else Record(row, self._names)

    def fetchall(self):
        return [Record(row, self._names) for row in self._cursor.fetchall()]

    def __iter__(self):
        for row in self._cursor:
            yield Record(row, self._names)


@lru_cache(maxsize=4096)
def translate(query, identity_tables=frozenset()):
    if 'sqlite_master' in query.lower():
        if query.strip() != "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_smu_access'":
            raise ValueError('SQLite schema introspection is unavailable in PostgreSQL runtime.')
        query = "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='user_smu_access'"
    trees = sqlglot.parse(query, read='sqlite', error_level=sqlglot.ErrorLevel.RAISE)
    if len(trees) != 1 or trees[0] is None:
        raise ValueError('Exactly one SQL statement is required.')
    tree = trees[0]
    if not isinstance(tree, (exp.Query, exp.Insert, exp.Update, exp.Delete)):
        raise ValueError('Schema changes require the offline migration command.')
    inserted_id = False
    if isinstance(tree, exp.Insert):
        alternative = tree.args.get('alternative')
        if alternative:
            if str(alternative).upper() != 'IGNORE':
                raise ValueError('Unsupported SQLite insert conflict mode.')
            tree.set('alternative', None)
            tree.set('conflict', sqlglot.parse_one('INSERT INTO x VALUES (1) ON CONFLICT DO NOTHING', read='postgres').args['conflict'])
        table = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
        if table.name in identity_tables and not tree.args.get('returning'):
            tree.set('returning', exp.Returning(expressions=[exp.column('id')]))
            inserted_id = True

    def transform(node):
        if isinstance(node, exp.Ordered) and isinstance(node.parent, exp.OnConflict):
            # SQLite parses conflict targets as ordered index columns. PostgreSQL
            # inference accepts their expressions, not NULLS FIRST/LAST clauses.
            return node.this
        if isinstance(node, exp.Literal) and not node.is_string:
            ancestor, child = node.parent, node
            while isinstance(ancestor, exp.Paren):
                child, ancestor = ancestor, ancestor.parent
            if isinstance(ancestor, (exp.Where, exp.Having, exp.And, exp.Or, exp.Not)):
                return exp.NEQ(this=node.copy(), expression=exp.Literal.number(0))
        if isinstance(node, exp.Collate):
            if node.expression.name.upper() != 'NOCASE':
                raise ValueError('Unreviewed SQLite collation.')
            return exp.Cast(this=node.this, to=exp.DataType.build('citext', dialect='postgres', udt=True))
        if isinstance(node, (exp.JSONExtract, exp.JSONExtractScalar)):
            node.set('this', exp.Cast(this=node.this, to=exp.DataType.build('json')))
        if isinstance(node, exp.Is) and not isinstance(node.expression, (exp.Null, exp.Boolean)):
            return exp.NullSafeEQ(this=node.this, expression=node.expression)
        if isinstance(node, exp.In) and not node.expressions and not node.args.get('query'):
            return exp.false()
        return node

    tree = tree.transform(transform)
    tree = tree.transform(lambda node: exp.Var(this='__CP_BOUND_PARAMETER__') if isinstance(node, exp.Placeholder) else node)
    rendered = tree.sql(dialect='postgres', unsupported_level=sqlglot.ErrorLevel.RAISE)
    rendered = rendered.replace('%', '%%').replace('__CP_BOUND_PARAMETER__', '%s')
    return rendered, isinstance(tree, (exp.Insert, exp.Update, exp.Delete)), inserted_id


_pool = None
_pool_pid = None
_identity_tables = frozenset()
_pool_lock = threading.Lock()


def get_pool():
    global _pool, _pool_pid, _identity_tables
    pid = os.getpid()
    if _pool is not None and _pool_pid == pid:
        return _pool
    with _pool_lock:
        if _pool is not None and _pool_pid == pid:
            return _pool
        dsn = os.environ.get('DATABASE_URL')
        if not dsn:
            raise RuntimeError('DATABASE_URL is required for the PostgreSQL backend.')
        pool = ConnectionPool(dsn, min_size=int(os.getenv('POOL_MIN_SIZE', '2')),
                              max_size=int(os.getenv('POOL_MAX_SIZE', '16')), timeout=5,
                              max_waiting=240, open=False,
                              kwargs={'autocommit': True, 'connect_timeout': 8,
                                      'options': '-c timezone=UTC -c search_path=public,pg_catalog'})
        pool.open()
        try:
            pool.wait(timeout=10)
            with pool.connection() as connection:
                version = connection.execute('SELECT MAX(version) FROM workforce_schema_versions').fetchone()[0]
                if version != 20:
                    raise RuntimeError('Apply the reviewed PostgreSQL migration before starting the app.')
                identities = connection.execute("SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='id' AND is_identity='YES'").fetchall()
                _identity_tables = frozenset(row[0] for row in identities)
        except BaseException:
            pool.close()
            raise
        _pool, _pool_pid = pool, pid
        return pool


class PostgresConnection:
    dialect = 'postgres'

    def __init__(self):
        self._pool = get_pool()
        self._connection = self._pool.getconn()
        self._history = None
        self._closed = False

    @property
    def in_transaction(self):
        return self._connection.info.transaction_status != TransactionStatus.IDLE

    def _run(self, query, parameters=()):
        try:
            return self._connection.execute(query, tuple(parameters))
        except (psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected) as error:
            raise ConcurrentChange('Concurrent transaction; reload current values.') from error
        except psycopg.IntegrityError as error:
            raise sqlite3.IntegrityError(str(error)) from error

    def execute(self, query, parameters=(), /):
        command = query.strip().upper()
        if command in ('BEGIN', 'BEGIN IMMEDIATE'):
            if self.in_transaction:
                raise RuntimeError('Cannot begin a transaction inside an active transaction.')
            self._permission_profiles = {}
            cursor = self._run('BEGIN ISOLATION LEVEL SERIALIZABLE' if command == 'BEGIN IMMEDIATE'
                               else 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            if command == 'BEGIN IMMEDIATE':
                from staffing_history import request_scope, capture, guards
                scope = request_scope(self)
                if scope is not None:
                    self._history = (scope, capture(self, scope), guards(self, scope))
            return Cursor(cursor)
        if command == 'COMMIT':
            return self.commit()
        if command == 'ROLLBACK':
            return self.rollback()
        statement, mutation, inserted_id = translate(query, _identity_tables)
        if mutation and not self.in_transaction:
            self._run('BEGIN ISOLATION LEVEL SERIALIZABLE')
        return Cursor(self._run(statement, parameters), inserted_id=inserted_id)

    def native(self, query, parameters=()):
        """Explicit PostgreSQL SQL; caller owns its transaction boundary."""
        return Cursor(self._run(query, parameters))

    def executemany(self, query, rows):
        count = 0
        cursor = None
        for row in rows:
            cursor = self.execute(query, row)
            count += max(0, cursor.rowcount)
        if cursor is not None:
            cursor.rowcount = count
        return cursor

    def commit(self):
        try:
            from staffing_history import HistoryConnection
            HistoryConnection.finish_history(self)
            return self._run('COMMIT')
        except BaseException:
            self.rollback()
            raise

    def rollback(self):
        self._history = None
        self._permission_profiles = {}
        return self._connection.execute('ROLLBACK')

    def close(self):
        if not self._closed:
            try:
                if self.in_transaction:
                    self.rollback()
            finally:
                self._closed = True
                self._pool.putconn(self._connection)

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        if error_type:
            self.rollback()
        elif self.in_transaction:
            self.commit()
        return False
