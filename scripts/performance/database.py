"""Connect to the selected local Compose database without printing credentials."""

import json
import os
import subprocess
from dataclasses import dataclass, field

import pymysql
from sqlalchemy import URL


@dataclass(frozen=True)
class LocalDatabase:
    """Keep credentials in memory and include only non-secret connection details in reports."""

    container: str
    values: dict[str, str] = field(repr=False)
    host: str = "127.0.0.1"
    port: int = 3306

    @classmethod
    def inspect(cls, container: str) -> "LocalDatabase":
        info = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
        labels = info["Config"].get("Labels", {})
        if labels.get("com.docker.compose.project") != "valueapi":
            raise ValueError("Profiler requires the local ValueApi Compose project")
        binding = info["NetworkSettings"]["Ports"].get("3306/tcp", [])
        if len(binding) != 1 or binding[0]["HostIp"] != "127.0.0.1":
            raise ValueError("Profiler requires one localhost-only MySQL port binding")
        values = dict(
            item.split("=", 1) for item in info["Config"]["Env"] if "=" in item
        )
        if values.get("MYSQL_DATABASE") != "valueapi":
            raise ValueError("Unexpected Compose database name")
        return cls(container, values, port=int(binding[0]["HostPort"]))

    def configure_application(self, database: str) -> URL:
        """Reuse the existing credentials in memory rather than creating another .env file."""
        if database not in ("valueapi", "valueapi_profile_check"):
            raise ValueError("Unexpected profiler database target")
        username = self.values.get("MYSQL_USER", "valueapi")
        password = self.values.get("MYSQL_PASSWORD")
        if database == "valueapi_profile_check":
            username, password = "root", self.values.get("MYSQL_ROOT_PASSWORD")
        url = URL.create(
            "mysql+pymysql",
            username=username,
            password=password,
            host=self.host,
            port=self.port,
            database=database,
            query={"charset": "utf8mb4"},
        )
        os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
        return url

    def diagnostics(self, database: str):
        """Use a separate read-only connection for diagnostics so those queries do not affect import SQL timings."""
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user="root",
            password=self.values.get("MYSQL_ROOT_PASSWORD") or "",
            database=database,
            autocommit=True,
            connect_timeout=5,
            read_timeout=30,
        )
