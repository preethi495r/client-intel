from dataclasses import dataclass, field
from datetime import date
import sqlite3
from typing import Any

AS_OF = date(2026, 11, 1)
USAGE_MONTHS = 3
SUPPORT_DAYS = 90


@dataclass(frozen=True)
class Source:
    table: str
    key: str
    rowid: int
    raw: dict[str, Any]


@dataclass(frozen=True)
class Metric:
    name: str
    value: str | int | None
    unit: str
    display: str
    period: str = ""
    product_id: str | None = None


@dataclass(frozen=True)
class Evidence:
    key: str
    analysis: str
    text: str
    sources: tuple[Source, ...] = ()
    metrics: tuple[Metric, ...] = ()
    product_id: str | None = None
    period: str = ""
    caveats: tuple[str, ...] = ()
    priority: int = 50
    # Only decision-relevant canonical facts should be sent to the writer.
    model_visible: bool = True


@dataclass(frozen=True)
class ToolContext:
    db: sqlite3.Connection  # sqlite3.Row row_factory
    client: dict[str, Any]  # clients columns, plus rowid
    as_of: date = AS_OF

    @property
    def client_id(self) -> str:
        return self.client["client_id"]


@dataclass
class ToolResult:
    evidence: list[Evidence] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok, qualified, empty, unavailable
    inputs: dict[str, Any] = field(default_factory=dict)


def source(table: str, id_column: str, row: sqlite3.Row | dict) -> Source:
    fields = dict(row)
    occurrence = fields.pop("rowid")
    return Source(table, fields[id_column], occurrence, fields)
