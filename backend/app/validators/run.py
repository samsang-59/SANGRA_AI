"""Validators for the run routes (Layer 2)."""

from typing import Annotated, Literal

from fastapi import Query
from pydantic import StringConstraints, model_validator

from app.validators.common import RequestModel

BugDescription = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]
Feedback = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]
HintText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]

# "active" means any of the active statuses (the frontend uses it to find the run that blocks a new start).
StatusFilter = Literal[
    "started", "searching", "waiting_for_hint", "waiting_for_approval", "applying", "testing",
    "succeeded", "failed", "stopped", "active",
]
MAX_ID = 2**63 - 1


class StartRunRequest(RequestModel):
    bug_description: BugDescription


class RunListQuery(RequestModel):
    project_id: int | None = None
    status: StatusFilter | None = None
    page: int = 1
    limit: int = 20


def run_list_query(
    project_id: Annotated[int | None, Query(gt=0, le=MAX_ID)] = None,
    status: Annotated[StatusFilter | None, Query()] = None,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RunListQuery:
    return RunListQuery(project_id=project_id, status=status, page=page, limit=limit)


class ApprovalRequest(RequestModel):
    attempt_id: int
    decision: Literal["accepted", "rejected"]
    feedback: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ApprovalRequest":
        if not 0 < self.attempt_id <= MAX_ID:
            raise ValueError("attempt_id must be a positive whole number")
        if self.decision == "rejected":
            text = (self.feedback or "").strip()
            if not 3 <= len(text) <= 2000:
                raise ValueError("feedback is required when rejecting (3 to 2000 characters)")
            self.feedback = text
        else:
            self.feedback = None
        return self


class HintRequest(RequestModel):
    text: HintText
