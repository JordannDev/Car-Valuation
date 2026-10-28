"""Sample MySQL I/O and query plans without saving source values in the report."""

import json
import time

import pymysql

STATUS = (
    "Innodb_buffer_pool_read_requests",
    "Innodb_buffer_pool_reads",
    "Innodb_buffer_pool_pages_data",
    "Innodb_buffer_pool_pages_free",
    "Innodb_buffer_pool_pages_dirty",
    "Innodb_buffer_pool_wait_free",
    "Innodb_data_read",
    "Innodb_data_written",
    "Innodb_data_reads",
    "Innodb_data_writes",
    "Innodb_os_log_written",
    "Innodb_log_waits",
    "Bytes_sent",
    "Bytes_received",
    "Threads_running",
)
VARIABLES = (
    "version",
    "innodb_buffer_pool_size",
    "innodb_flush_log_at_trx_commit",
    "sync_binlog",
    "max_allowed_packet",
    "innodb_page_size",
    "innodb_redo_log_capacity",
    "foreign_key_checks",
)


def table_steps(plan):
    """Keep query access paths and estimates, but leave out SQL values and source VIN lists."""
    output = []

    def visit(value):
        if isinstance(value, dict):
            if "table_name" in value:
                output.append(
                    {
                        name: value.get(name)
                        for name in (
                            "table_name",
                            "access_type",
                            "key",
                            "used_key_parts",
                            "rows_examined_per_scan",
                            "rows_produced_per_join",
                            "filtered",
                            "using_join_buffer",
                        )
                    }
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(plan)
    return output


class Sampler:
    def __init__(self, connection):
        self.connection = connection
        self.diagnostic_seconds = 0.0
        self.plan_keys = set()
        self.plans = []

    def pairs(self, statement, names):
        started = time.perf_counter()
        try:
            placeholders = ",".join(["%s"] * len(names))
            with self.connection.cursor() as cursor:
                cursor.execute(
                    statement + f" WHERE Variable_name IN ({placeholders})", names
                )
                return {
                    key: int(value) if str(value).isdigit() else value
                    for key, value in cursor.fetchall()
                }
        finally:
            self.diagnostic_seconds += time.perf_counter() - started

    def status(self):
        return self.pairs("SHOW GLOBAL STATUS", STATUS)

    def variables(self):
        return self.pairs("SHOW GLOBAL VARIABLES", VARIABLES)

    def batch_counts(self, batch_id):
        started = time.perf_counter()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT accepted_rows,rejected_rows FROM import_batches WHERE id=%s",
                    (batch_id,),
                )
                accepted, rejected = cursor.fetchone()
                return int(accepted), int(rejected)
        finally:
            self.diagnostic_seconds += time.perf_counter() - started

    def batch_checkpoint(self, batch_id):
        """When measuring a resumed import, exclude rows saved by the previous run."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT last_processed_record FROM import_batches WHERE id=%s",
                (batch_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Unknown import batch")
            return int(row[0])

    def explain(self, cursor, statement, parameters, operation, seconds, records):
        if operation.startswith("orchestration"):
            return
        bucket = records // 250_000
        reason = "slow" if seconds >= 0.05 else "periodic"
        key = (bucket, operation, reason)
        if key in self.plan_keys:
            return
        self.plan_keys.add(key)
        started = time.perf_counter()
        try:
            with cursor.connection.cursor() as diagnostic:
                diagnostic.execute("EXPLAIN FORMAT=JSON " + statement, parameters)
                plan = json.loads(diagnostic.fetchone()[0])
            self.plans.append(
                {
                    "records_processed": records,
                    "operation": operation,
                    "observed_seconds": seconds,
                    "reason": reason,
                    "query_cost": plan.get("query_block", {})
                    .get("cost_info", {})
                    .get("query_cost"),
                    "tables": table_steps(plan),
                    "method": "EXPLAIN immediately after SELECT, same transaction and bound values",
                }
            )
        except (pymysql.MySQLError, json.JSONDecodeError, TypeError) as error:
            self.plans.append(
                {
                    "records_processed": records,
                    "operation": operation,
                    "diagnostic_error_type": type(error).__name__,
                }
            )
        finally:
            self.diagnostic_seconds += time.perf_counter() - started

    def statement_digests(self):
        """MySQL normalizes these query summaries, so they do not include the original row values."""
        with self.connection.cursor() as cursor:
            cursor.execute("""SELECT DIGEST_TEXT,COUNT_STAR,SUM_TIMER_WAIT/1000000000000,
                SUM_ROWS_EXAMINED,SUM_ROWS_SENT FROM performance_schema.events_statements_summary_by_digest
                WHERE SCHEMA_NAME=DATABASE() ORDER BY SUM_TIMER_WAIT DESC LIMIT 60""")
            return [
                {
                    "query_shape": row[0],
                    "calls": row[1],
                    "server_seconds": float(row[2]),
                    "rows_examined": row[3],
                    "rows_sent": row[4],
                }
                for row in cursor.fetchall()
            ]
