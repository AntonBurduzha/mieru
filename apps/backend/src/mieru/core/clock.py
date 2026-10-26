from datetime import UTC, date, datetime


def now() -> datetime:
    """Current time in UTC. Timezone-aware."""
    return datetime.now(UTC)


def today() -> date:
    """Current date in UTC."""
    return now().date()
