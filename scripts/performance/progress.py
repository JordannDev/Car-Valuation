"""Record chunk speeds, server metrics, and occasional CPU samples as the import runs."""

import cProfile
import csv
import json
import os
import pstats
import resource
import subprocess
import time
from pathlib import Path


def peak_mib():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024**2 if os.uname().sysname == "Darwin" else 1024)


def current_mib():
    return (
        float(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())]))
        / 1024
    )


def sql_delta(current, previous):
    output = {}
    for key, item in current.items():
        before = previous.get(key, {})
        change = {
            name: value - before.get(name, 0)
            for name, value in item.items()
            if name != "max_seconds"
        }
        if change["calls"]:
            output[key] = change
    return output


class Progress:
    """The CSV only includes committed chunks. Window JSON also records how the growing tables affect the run."""

    def __init__(self, metrics, sampler, report_dir, artifact_dir, candidate_stats):
        self.metrics, self.sampler = metrics, sampler
        self.report_dir, self.artifact_dir = report_dir, artifact_dir
        self.candidate_stats = candidate_stats
        self.ingestion_started = 0.0
        self.previous_chunk = 0.0
        self.previous_sql = {}
        self.window_sql = {}
        self.window_started = 0.0
        self.previous_status = {}
        self.windows = []
        self.rows = []
        self.cpu_samples = []
        self.last_cpu_bucket = -1
        self.last_window = 0
        self.total_rows = 0
        self.initial_records = 0
        self.csv_file = (report_dir / "chunks.csv").open("w", newline="")
        self.writer = csv.DictWriter(
            self.csv_file,
            fieldnames=(
                "records_processed",
                "accepted_rows",
                "rejected_rows",
                "chunk_seconds",
                "records_per_second",
                "sql_seconds",
                "sql_calls",
                "write_rows",
                "returned_rows",
                "top_sql",
                "top_sql_seconds",
                "peak_process_mib",
            ),
        )
        self.writer.writeheader()

    def start_ingestion(self, resume_batch_id=None):
        if resume_batch_id is not None:
            self.initial_records = self.sampler.batch_checkpoint(resume_batch_id) - 1
            self.last_window = self.initial_records
            self.metrics.checkpoint = self.initial_records + 1
        self.ingestion_started = self.previous_chunk = self.window_started = (
            time.perf_counter()
        )
        self.previous_status = self.sampler.status()
        self.previous_sql = self.metrics.snapshot()["sql"]
        self.window_sql = self.previous_sql

    def wrap_process(self, original):
        def measured(connection, rows, batch_id, spec, **kwargs):
            first = rows[0].source_record_number if rows else 2
            bucket = (first - 2) // 500_000
            sample = bucket > self.last_cpu_bucket
            profile = cProfile.Profile() if sample else None
            if sample:
                self.last_cpu_bucket = bucket
                profile.enable()
            started = time.perf_counter()
            try:
                with self.metrics.stage("process_rows"):
                    return original(connection, rows, batch_id, spec, **kwargs)
            finally:
                if profile:
                    profile.disable()
                    self.save_cpu(profile, first, time.perf_counter() - started)

        return measured

    def save_cpu(self, profile, first, seconds):
        path = self.artifact_dir / f"cpu-record-{first}.prof"
        profile.dump_stats(path)
        stats = pstats.Stats(profile)
        functions = []
        for (filename, line, function), (
            primitive,
            calls,
            own,
            cumulative,
            callers,
        ) in stats.stats.items():
            functions.append(
                {
                    "file": Path(filename).name,
                    "line": line,
                    "function": function,
                    "calls": calls,
                    "self_seconds": own,
                    "cumulative_seconds": cumulative,
                }
            )
        self.cpu_samples.append(
            {
                "first_record": first,
                "profiled_chunk_seconds": seconds,
                "profile_path": str(path),
                "top_self": sorted(functions, key=lambda item: -item["self_seconds"])[
                    :25
                ],
                "top_cumulative": sorted(
                    functions, key=lambda item: -item["cumulative_seconds"]
                )[:25],
            }
        )
        (self.report_dir / "cpu-samples.json").write_text(
            json.dumps(self.cpu_samples, indent=2)
        )

    def on_chunk(self, progress):
        now = time.perf_counter()
        checkpoint = progress["checkpoint"]
        accepted, rejected = self.sampler.batch_counts(progress["batch_id"])
        current = self.metrics.snapshot()["sql"]
        change = sql_delta(current, self.previous_sql)
        seconds = now - self.previous_chunk
        previous_records = (
            self.rows[-1]["records_processed"] if self.rows else self.initial_records
        )
        processed = checkpoint - 1
        top_key, top = max(
            change.items(),
            key=lambda entry: entry[1]["seconds"],
            default=("", {"seconds": 0}),
        )
        row = {
            "records_processed": processed,
            "accepted_rows": accepted,
            "rejected_rows": rejected,
            "chunk_seconds": seconds,
            "records_per_second": (processed - previous_records) / seconds,
            "sql_seconds": sum(item["seconds"] for item in change.values()),
            "sql_calls": sum(item["calls"] for item in change.values()),
            "write_rows": sum(item["input_rows"] for item in change.values()),
            "returned_rows": sum(item["returned_rows"] for item in change.values()),
            "top_sql": top_key,
            "top_sql_seconds": top["seconds"],
            "peak_process_mib": peak_mib(),
        }
        self.rows.append(row)
        self.writer.writerow(row)
        self.csv_file.flush()
        self.total_rows = progress["total_rows"]
        self.metrics.checkpoint = checkpoint
        if processed - self.last_window >= 250_000 or processed == self.total_rows:
            self.save_window(processed, current, now)
        if len(self.rows) % 10 == 0 or processed == self.total_rows:
            print(
                json.dumps(
                    {
                        "type": "progress",
                        **row,
                        "elapsed_seconds": now - self.metrics.started,
                    }
                ),
                flush=True,
            )
        self.previous_sql = current
        self.previous_chunk = time.perf_counter()

    def save_window(self, processed, current, now):
        status = self.sampler.status()
        delta = sql_delta(current, self.window_sql)
        reads = status.get("Innodb_buffer_pool_reads", 0) - self.previous_status.get(
            "Innodb_buffer_pool_reads", 0
        )
        requests = status.get(
            "Innodb_buffer_pool_read_requests", 0
        ) - self.previous_status.get("Innodb_buffer_pool_read_requests", 0)
        record = {
            "records_processed": processed,
            "window_seconds": now - self.window_started,
            "records_per_second": (processed - self.last_window)
            / (now - self.window_started),
            "sql": delta,
            "mysql_status": status,
            "mysql_counter_delta": {
                key: value - self.previous_status.get(key, value)
                for key, value in status.items()
            },
            "buffer_pool_physical_read_ratio": reads / requests if requests else 0,
            "current_process_mib": current_mib(),
            "peak_process_mib": peak_mib(),
            "candidate_counters": self.candidate_stats(),
        }
        self.windows.append(record)
        (self.report_dir / "windows.json").write_text(
            json.dumps(self.windows, indent=2)
        )
        (self.report_dir / "query-plans.json").write_text(
            json.dumps(self.sampler.plans, indent=2)
        )
        (self.report_dir / "live-summary.json").write_text(
            json.dumps(
                {
                    "records_processed": processed,
                    "total_rows": self.total_rows,
                    "elapsed_seconds": now - self.metrics.started,
                    "timings": self.metrics.snapshot(),
                    "candidate_counters": self.candidate_stats(),
                },
                indent=2,
            )
        )
        self.previous_status, self.window_sql = status, current
        self.window_started, self.last_window = now, processed

    def close(self):
        self.csv_file.close()
