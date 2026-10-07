"""Wall-clock time for domain timestamps. Lease timing uses database time instead."""

from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
