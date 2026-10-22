"""The app clock. Tests replace these functions with a fake clock (expiry and rate-limit tests).

Note: "is this email token expired?" uses the DATABASE clock (repository rule 7), not this one.
"""

import time
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def monotonic() -> float:
    return time.monotonic()
