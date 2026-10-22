"""Validators for the project routes (Layer 2)."""

from typing import Annotated, Literal

from fastapi import Path
from pydantic import AfterValidator, BaseModel, BeforeValidator, StringConstraints, model_validator

from app.validators.project_rules import check_branch, check_reference

ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
TestCommand = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Language = Literal["java", "python"]
SourceType = Literal["github", "local_path", "zip"]


def _empty_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


# Optional branch: empty means the repo's default branch (never assumed to be "main").
Branch = Annotated[str | None, BeforeValidator(_empty_to_none), AfterValidator(lambda v: v if v is None else check_branch(v))]
Reference = Annotated[str | None, BeforeValidator(_empty_to_none)]

# Every {id} in a URL: a positive whole number that fits the database (bigint).
EntityId = Annotated[int, Path(gt=0, le=2**63 - 1)]


class ProjectCreateRequest(BaseModel):
    name: ProjectName
    language: Language
    source_type: SourceType
    source_reference: Reference = None
    branch: Branch = None
    test_command: TestCommand

    @model_validator(mode="after")
    def _reference_matches_type(self) -> "ProjectCreateRequest":
        # Required unless zip; a zip project keeps no reference (its code comes with each run).
        try:
            self.source_reference = check_reference(self.source_type, self.source_reference)
        except ValueError as exc:
            raise ValueError(f"source_reference {exc}" if "source_reference" not in str(exc) else str(exc)) from None
        return self


class ProjectUpdateRequest(BaseModel):
    """PATCH: only the fields sent are changed. Fields that are not editable (id, user_id, created_at) are
    ignored. Whether the merged project still makes sense is the service's job (INVALID_PROJECT_CONFIG)."""

    name: ProjectName | None = None
    language: Language | None = None
    source_type: SourceType | None = None
    source_reference: Reference = None
    branch: Branch = None
    test_command: TestCommand | None = None

    @model_validator(mode="after")
    def _required_fields_not_null(self) -> "ProjectUpdateRequest":
        for field in ("name", "language", "source_type", "test_command"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be empty")
        return self

    def changes(self) -> dict:
        return {field: getattr(self, field) for field in self.model_fields_set}
