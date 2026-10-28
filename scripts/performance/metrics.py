"""Measure import stages and SQL calls with minimal timing overhead."""

import heapq
import re
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import wraps

import pymysql
from sqlalchemy import event
from sqlalchemy.engine import Engine


@dataclass
class SQLMetric:
    seconds: float = 0.0
    calls: int = 0
    input_rows: int = 0
    returned_rows: int = 0
    returned_values: int = 0
    max_seconds: float = 0.0


class Timings:
    """Stage times include nested work. Count each SQL execution once in the SQL totals."""

    def __init__(self):
        self.active = False
        self.started = 0.0
        self.stack = []
        self.stages = defaultdict(float)
        self.stage_calls = defaultdict(int)
        self.sql = defaultdict(SQLMetric)
        self.driver = defaultdict(float)
        self.driver_calls = defaultdict(int)
        self.slow_queries = []
        self.query_number = 0
        self.checkpoint = 1
        self.plan_callback = None

    def start(self):
        self.active = True
        self.started = time.perf_counter()

    @contextmanager
    def stage(self, name):
        started = time.perf_counter()
        self.stack.append(name)
        try:
            yield
        finally:
            self.stack.pop()
            if self.active:
                self.stages[name] += time.perf_counter() - started
                self.stage_calls[name] += 1

    def wrap(self, owner, name, label=None):
        original = getattr(owner, name)

        @wraps(original)
        def measured(*args, **kwargs):
            with self.stage(label or name):
                return original(*args, **kwargs)

        setattr(owner, name, measured)

    @staticmethod
    def category(statement):
        verb = statement.lstrip().split(None, 1)[0].upper()
        pattern = (
            r"\bFROM\s+`?(\w+)" if verb == "SELECT" else r"\b(?:INTO|UPDATE)\s+`?(\w+)"
        )
        match = re.search(pattern, statement, re.IGNORECASE)
        return f"{verb} {match.group(1) if match else 'connection/setup'}"

    def before(self, conn, cursor, statement, parameters, context, executemany):
        if not self.active:
            return
        context._import_profile_key = f"{self.stack[-1] if self.stack else 'orchestration'} | {self.category(statement)}"
        context._import_profile_started = time.perf_counter()

    def after(self, conn, cursor, statement, parameters, context, executemany):
        if not self.active or not hasattr(context, "_import_profile_started"):
            return
        elapsed = time.perf_counter() - context._import_profile_started
        key = context._import_profile_key
        metric = self.sql[key]
        metric.seconds += elapsed
        metric.calls += 1
        metric.max_seconds = max(metric.max_seconds, elapsed)
        if cursor.description:
            metric.returned_rows += max(0, cursor.rowcount)
            metric.returned_values += max(0, cursor.rowcount) * len(cursor.description)
        elif statement.lstrip().upper().startswith(("INSERT", "UPDATE")):
            metric.input_rows += len(parameters) if executemany else 1
        self.query_number += 1
        entry = (
            elapsed,
            self.query_number,
            {
                "seconds": elapsed,
                "operation": key,
                "records_processed": self.checkpoint - 1,
            },
        )
        if len(self.slow_queries) < 50:
            heapq.heappush(self.slow_queries, entry)
        elif elapsed > self.slow_queries[0][0]:
            heapq.heapreplace(self.slow_queries, entry)
        if self.plan_callback and cursor.description:
            self.plan_callback(cursor, statement, parameters, key, elapsed)

    def wrap_driver(self, name):
        original = getattr(pymysql.connections.Connection, name)

        @wraps(original)
        def measured(connection, *args, **kwargs):
            started = time.perf_counter()
            try:
                return original(connection, *args, **kwargs)
            finally:
                if self.active:
                    self.driver[name] += time.perf_counter() - started
                    self.driver_calls[name] += 1

        setattr(pymysql.connections.Connection, name, measured)

    def install(self):
        event.listen(Engine, "before_cursor_execute", self.before)
        event.listen(Engine, "after_cursor_execute", self.after)
        self.wrap_driver("commit")
        self.wrap_driver("rollback")

    def snapshot(self):
        return {
            "sql": {key: asdict(value) for key, value in self.sql.items()},
            "inclusive_stage_seconds": dict(self.stages),
            "stage_calls": dict(self.stage_calls),
            "driver_seconds": dict(self.driver),
            "driver_calls": dict(self.driver_calls),
            "slowest_queries": [
                entry[2] for entry in sorted(self.slow_queries, reverse=True)
            ],
        }
