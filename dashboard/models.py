"""Bounded public API inputs; credentials are never browser inputs."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=240)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Range(Input):
    preset: Literal["4w", "12w", "all", "custom"] = "12w"
    start: date | None = None
    end: date | None = None


class Filters(Input):
    split_ids_or_values: list[Label] = Field(default_factory=list, max_length=64)
    set_types: list[Label] = Field(default_factory=list, max_length=64)
    exercise_ids: list[Identifier] = Field(default_factory=list, max_length=128)


class DetailScope(Input):
    exercise_id: Identifier | None = None
    condition_key: Label | None = None
    fixed_load: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pullup_mode: Literal["bodyweight", "added", "assisted", "unknown"] | None = None


class Baseline(Input):
    exercise_id: Identifier
    condition_key: Label
    metric: Literal["e1rm", "reps"]
    source_set_id: Identifier
    source_fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    value: float = Field(gt=0, allow_inf_nan=False)
    version: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class AnalysisRequest(Input):
    snapshot_id: Identifier
    range: Range = Field(default_factory=Range)
    filters: Filters = Field(default_factory=Filters)
    core_exercise_ids: list[Identifier] = Field(default_factory=list, max_length=64)
    detail_scope: DetailScope = Field(default_factory=DetailScope)
    baselines: list[Baseline] = Field(default_factory=list, max_length=128)
    recent_limit: int = Field(default=3, ge=1, le=1000)


class ExportRequest(AnalysisRequest):
    kind: Literal["sets", "sessions"] = "sets"
