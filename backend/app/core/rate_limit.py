"""Rate limiting (Layer 7, item 5): in-memory counters per key (an IP, an email, a user).

Sliding window: a key may have at most `limit` attempts in the last `window` seconds. Over the limit the
answer is 429 RATE_LIMITED with a "try again after" time. Entries older than every window are swept away,
so memory goes back to normal after a burst of many different keys.

In-memory works for one backend process; a shared store (like Redis) can come when several processes run.
hit() never awaits, so on the single event loop each call is atomic: counts are exact under concurrency.
"""

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi.responses import JSONResponse

from app.core import clock
from app.core.config import Settings
from app.core.errors import error_response


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window_seconds: float


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter:
    SWEEP_EVERY_SECONDS = 60.0

    def __init__(self, time_source: Callable[[], float] | None = None):
        self._time = time_source or (lambda: clock.monotonic())
        self._hits: dict[str, deque[float]] = {}
        self._windows: dict[str, float] = {}
        self._last_sweep = self._time()

    def hit(self, rule: Rule, key: str) -> RateLimitResult:
        """Counts one attempt for (rule, key) if it is allowed; refuses it (not counted) if over the limit."""
        now = self._time()
        self._maybe_sweep(now)
        full_key = f"{rule.name}:{key}"
        window_start = now - rule.window_seconds
        attempts = self._hits.get(full_key)
        if attempts is None:
            attempts = self._hits[full_key] = deque()
            self._windows[full_key] = rule.window_seconds
        while attempts and attempts[0] <= window_start:
            attempts.popleft()
        if len(attempts) >= rule.limit:
            retry_after = attempts[0] + rule.window_seconds - now
            return RateLimitResult(False, max(1, math.ceil(retry_after)))
        attempts.append(now)
        return RateLimitResult(True)

    def check_all(self, checks: list[tuple[Rule, str]]) -> RateLimitResult:
        """Several keys for one request (for example the IP and the email of a login). Refused if any key
        is over its limit; then nothing is counted."""
        now = self._time()
        worst = 0
        for rule, key in checks:
            attempts = self._hits.get(f"{rule.name}:{key}")
            if not attempts:
                continue
            recent = [t for t in attempts if t > now - rule.window_seconds]
            if len(recent) >= rule.limit:
                worst = max(worst, max(1, math.ceil(recent[0] + rule.window_seconds - now)))
        if worst:
            return RateLimitResult(False, worst)
        for rule, key in checks:
            self.hit(rule, key)
        return RateLimitResult(True)

    def key_count(self) -> int:
        return len(self._hits)

    def _maybe_sweep(self, now: float) -> None:
        if now - self._last_sweep < self.SWEEP_EVERY_SECONDS:
            return
        self._last_sweep = now
        for key in [k for k, attempts in self._hits.items()
                    if not attempts or attempts[-1] <= now - self._windows[k]]:
            del self._hits[key]
            del self._windows[key]


@dataclass(frozen=True)
class AuthRules:
    login: Rule
    register: Rule
    forgot_password: Rule
    resend_verification: Rule
    change_password: Rule


def auth_rules(settings: Settings) -> AuthRules:
    return AuthRules(
        login=Rule("login", settings.rate_limit_login, settings.rate_limit_login_window_seconds),
        register=Rule("register", settings.rate_limit_register, settings.rate_limit_register_window_seconds),
        forgot_password=Rule("forgot_password", settings.rate_limit_forgot_password,
                             settings.rate_limit_forgot_password_window_seconds),
        resend_verification=Rule("resend_verification", settings.rate_limit_resend_verification,
                                 settings.rate_limit_resend_verification_window_seconds),
        change_password=Rule("change_password", settings.rate_limit_change_password,
                             settings.rate_limit_change_password_window_seconds),
    )


def rate_limited_response(result: RateLimitResult) -> JSONResponse:
    return error_response(
        "RATE_LIMITED",
        f"Too many attempts. Try again in {result.retry_after_seconds} seconds.",
        headers={"Retry-After": str(result.retry_after_seconds)},
    )
