"""The Result that services return (Layer 3/4 rule: services never throw HTTP errors or pick HTTP codes)."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Result:
    ok: bool
    data: Any = None
    error_code: str | None = None
    message: str | None = None

    @staticmethod
    def success(data: Any = None) -> "Result":
        return Result(ok=True, data=data)

    @staticmethod
    def failure(error_code: str, message: str) -> "Result":
        return Result(ok=False, error_code=error_code, message=message)
