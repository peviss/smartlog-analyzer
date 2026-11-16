"""Analysis request, the LLM output contract and the stored analysis."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from api.schemas.logs import LogFilters

MAX_ANALYSIS_LOG_IDS = 500

# ---------------------------------------------------------------- analysis


class AnalysisFilters(LogFilters):
    limit: int = Field(
        default=200, ge=1, le=MAX_ANALYSIS_LOG_IDS, description="Max logs to include (most recent first)"
    )


class AnalyzeRequest(BaseModel):
    """Either an explicit list of log ids or a filter set, never both."""

    model_config = ConfigDict(extra="forbid")

    log_ids: list[int] | None = Field(default=None, min_length=1, max_length=MAX_ANALYSIS_LOG_IDS)
    filters: AnalysisFilters | None = None

    @field_validator("log_ids")
    @classmethod
    def ids_positive_unique(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if any(i <= 0 for i in v):
            raise ValueError("log ids must be positive integers")
        return list(dict.fromkeys(v))  # dedupe, keep order

    @model_validator(mode="after")
    def exactly_one_source(self):
        if (self.log_ids is None) == (self.filters is None):
            raise ValueError("provide exactly one of 'log_ids' or 'filters'")
        return self


Severity = Literal["low", "medium", "high", "critical"]
Priority = Literal["low", "medium", "high"]


class Category(BaseModel):
    """A group of related problems found in the logs."""

    name: str = Field(min_length=1, max_length=100, description='Short label, e.g. "Database connectivity".')
    severity: Severity
    description: str = Field(min_length=1, description="What is going wrong, based only on the data.")
    occurrences: int = Field(ge=0, description="Sum of the pattern counts in this category, as given.")
    services: list[str] = Field(default_factory=list, description="service.name values affected.")
    example_log_ids: list[int] = Field(
        default_factory=list, description="Ids taken from the patterns' sample_log_ids."
    )


class Suggestion(BaseModel):
    """A concrete, actionable next step for the team."""

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    priority: Priority
    category: str | None = Field(default=None, description="Name of the category it addresses, if any.")


class AnalysisResult(BaseModel):
    """The contract the LLM must fulfil.

    Pydantic AI sends this schema to the model as its structured-output tool and validates
    the answer against it; anything that does not match is rejected.
    """

    summary: str = Field(min_length=1, description="3-6 sentences, most important first.")
    categories: list[Category] = Field(description="Empty if there are no problems.")
    suggestions: list[Suggestion] = Field(description="Empty if there are no problems.")


class AnalysisErrorInfo(BaseModel):
    code: str
    message: str


class AnalysisRead(BaseModel):
    id: uuid.UUID
    status: Literal["pending", "completed", "failed"]
    created_at: datetime
    completed_at: datetime | None
    request: dict[str, Any]
    log_count: int
    pattern_count: int
    stats: dict[str, Any]
    provider: str
    model: str
    summary: str | None
    categories: list[Category] | None
    suggestions: list[Suggestion] | None
    error: AnalysisErrorInfo | None
    duration_ms: int | None
