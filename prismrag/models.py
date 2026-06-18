"""PrismRAG — Pydantic request/response models."""
from __future__ import annotations

import re
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ── Enums ─────────────────────────────────────────────────────────────────────

class SourceType(str, Enum):
    sql    = "sql"
    file   = "file"
    api    = "api"
    chunk  = "chunk"     # re-map existing pgvector chunk store
    inline = "inline"    # records embedded directly in the job request


class StrategyType(str, Enum):
    rules        = "rules"         # Tier 1: explicit word→category table
    mlp          = "mlp"           # Tier 2: MLP trained on rules
    cluster      = "cluster"       # auto-cluster (K-means)
    external_api = "external_api"  # delegate to client's own model


class JobStatus(str, Enum):
    queued    = "queued"
    running   = "running"
    completed = "completed"
    failed    = "failed"
    stale     = "stale"


# ── Source configs ─────────────────────────────────────────────────────────────

class SQLSourceConfig(BaseModel):
    connection_string: str = Field(..., description="PostgreSQL DSN or SQLAlchemy URL")
    query: str             = Field(..., description="SELECT query returning (word, text) rows")
    word_column: str       = Field("word",  description="Column name for the word/token")
    text_column:  str      = Field("text",  description="Column name for the full chunk text")
    page_size: int         = Field(1000, ge=1, le=50000)


class FileSourceConfig(BaseModel):
    filename: str          = Field(..., description="Original filename (for logging)")
    word_column: str       = Field("word")
    text_column:  str      = Field("text")
    category_column: str | None = Field(None, description="Optional pre-assigned category column")


class APISourceConfig(BaseModel):
    url: str               = Field(..., description="Paginated REST endpoint URL")
    headers: dict[str, str] = Field(default_factory=dict)
    word_field: str        = Field("word")
    text_field:  str       = Field("text")
    page_param:  str       = Field("page")
    page_size:   int       = Field(500)


class ChunkSourceConfig(BaseModel):
    source_dsn: str        = Field(..., description="DSN of the existing pgvector database")
    source_table: str      = Field(..., description="Fully qualified table name e.g. public.chunks")
    text_column: str       = Field("content")
    ref_column:  str       = Field("id")
    where_clause: str      = Field("", description="Optional WHERE clause (no WHERE keyword)")


class InlineRecordIn(BaseModel):
    word:          str = Field(..., min_length=1, max_length=500)
    text:          str | None = Field(None, description="Chunk text; defaults to word")
    category_hint: str | None = Field(None, description="Optional category slug hint per row")


class InlineSourceConfig(BaseModel):
    records: list[InlineRecordIn] = Field(
        ...,
        min_length=1,
        max_length=50_000,
        description="Inline word/text records to ingest",
    )


# ── Mapping / category ────────────────────────────────────────────────────────

class CategoryIn(BaseModel):
    slug:  str = Field(..., min_length=1, max_length=100)
    label: str = Field(..., min_length=1, max_length=200)
    sort_order: int = Field(0)

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v: str) -> str:
        v = v.strip().lower()
        if not _SLUG_RE.match(v):
            raise ValueError(
                "category slug must start with a letter and contain only "
                "lowercase letters, digits, and underscores (e.g. risk, lab_results)"
            )
        return v


class MappingRuleIn(BaseModel):
    word:          str   = Field(..., min_length=1, max_length=500)
    category_slug: str   = Field(..., min_length=1, max_length=100)
    weight:        float = Field(1.0, ge=0.0, le=10.0)

    @field_validator("word")
    @classmethod
    def normalise_word(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("rule word cannot be empty")
        return v

    @field_validator("category_slug")
    @classmethod
    def normalise_category_slug(cls, v: str) -> str:
        return v.strip().lower()


class MappingConfigIn(BaseModel):
    categories: list[CategoryIn]
    rules:      list[MappingRuleIn]

    @model_validator(mode="after")
    def validate_mapping_consistency(self) -> "MappingConfigIn":
        if not self.categories:
            raise ValueError("mapping.categories must contain at least one category")
        if not self.rules:
            raise ValueError(
                "mapping.rules must contain at least one word→category rule"
            )

        slugs = [c.slug for c in self.categories]
        if len(slugs) != len(set(slugs)):
            dupes = sorted({s for s in slugs if slugs.count(s) > 1})
            raise ValueError(f"mapping.categories contains duplicate slugs: {dupes}")

        slug_set = set(slugs)
        unknown = sorted({r.category_slug for r in self.rules if r.category_slug not in slug_set})
        if unknown:
            raise ValueError(
                f"rules reference unknown category_slug values: {unknown}. "
                f"Valid slugs: {sorted(slug_set)}"
            )

        words = [r.word for r in self.rules]
        if len(words) != len(set(words)):
            dupes = sorted({w for w in words if words.count(w) > 1})
            raise ValueError(f"mapping.rules contains duplicate words: {dupes}")

        return self


# ── Job request ───────────────────────────────────────────────────────────────

class JobRequest(BaseModel):
    tenant_id:   UUID
    source_type: SourceType
    strategy:    StrategyType = StrategyType.rules

    sql_config:    SQLSourceConfig    | None = None
    file_config:   FileSourceConfig   | None = None
    api_config:    APISourceConfig    | None = None
    chunk_config:  ChunkSourceConfig  | None = None
    inline_config: InlineSourceConfig | None = None

    # Tier-1 mapping (required for rules/mlp strategies)
    mapping: MappingConfigIn | None = None

    # Tier-2 MLP options (strategy='mlp')
    mlp_epochs: int | None = Field(None, ge=1, le=1000)
    mlp_recall_target: float | None = Field(None, ge=0.0, le=1.0)

    # Quality / fallback
    ml_fallback: str = Field(
        "auto",
        description=(
            "When to apply ML-assisted reclassification for low-quality chunks. "
            "'auto' (default): apply when quality_score < 0.45. "
            "'always': apply to all chunks. "
            "'never': skip ML fallback entirely."
        ),
        pattern="^(auto|always|never)$",
    )

    webhook_url: str | None = Field(None, description="Called when job completes")

    @model_validator(mode="after")
    def validate_source_and_mapping(self) -> "JobRequest":
        needs_mapping = self.strategy in (
            StrategyType.rules,
            StrategyType.mlp,
            StrategyType.cluster,
        )
        if needs_mapping and not self.mapping:
            raise ValueError(
                f"mapping is required when strategy is '{self.strategy.value}'"
            )

        required_field = {
            SourceType.sql:    "sql_config",
            SourceType.api:    "api_config",
            SourceType.chunk:  "chunk_config",
            SourceType.inline: "inline_config",
            SourceType.file:   "file_config",
        }[self.source_type]

        if getattr(self, required_field) is None:
            raise ValueError(
                f"{required_field} is required when source_type is '{self.source_type.value}'"
            )

        # Disallow cross-config pollution
        config_fields = (
            ("sql_config",    SourceType.sql),
            ("file_config",   SourceType.file),
            ("api_config",    SourceType.api),
            ("chunk_config",  SourceType.chunk),
            ("inline_config", SourceType.inline),
        )
        for field_name, expected_type in config_fields:
            if getattr(self, field_name) is not None and self.source_type != expected_type:
                raise ValueError(
                    f"{field_name} is only allowed when source_type is '{expected_type.value}'"
                )

        if self.api_config and not self.api_config.url.strip():
            raise ValueError("api_config.url cannot be empty")

        if self.sql_config:
            if not self.sql_config.connection_string.strip():
                raise ValueError("sql_config.connection_string cannot be empty")
            if not self.sql_config.query.strip():
                raise ValueError("sql_config.query cannot be empty")

        if self.inline_config and not self.inline_config.records:
            raise ValueError("inline_config.records must contain at least one record")

        return self


class JobResponse(BaseModel):
    job_id:     str
    tenant_id:  str
    status:     JobStatus
    status_url: str
    sync:       bool = Field(False, description="True if completed inline (small dataset)")


class JobStatusResponse(BaseModel):
    job_id:          str
    tenant_id:       str
    status:          JobStatus
    records_total:   int | None
    records_written: int
    progress_pct:    int
    error_message:   str | None
    started_at:      str | None
    finished_at:     str | None


# ── Search / serve ────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    tenant_id:  UUID
    mapping_id: UUID | None = None     # defaults to latest active mapping
    query:      str  = Field(..., min_length=1)
    top_k:      int  = Field(10, ge=1, le=100)
    category_filter: str | None = None
    wait:       bool = Field(
        False,
        description="If true, block until results are ready. Default false returns a task_id to poll.",
    )


class SearchTaskSubmitResponse(BaseModel):
    task_id:    str
    tenant_id:  str
    status:     str = "pending"
    status_url: str
    async_mode: bool = True


class SearchTaskStatusResponse(BaseModel):
    task_id:       str
    tenant_id:     str
    status:        str
    request:       dict[str, Any] = Field(default_factory=dict)
    result:        SearchResponse | None = None
    error_message: str | None = None
    latency_ms:    int | None = None
    created_at:    str | None = None
    finished_at:   str | None = None


class SearchHit(BaseModel):
    chunk_text:    str
    chunk_ref:     str | None
    category_slug: str | None
    community_id:  int | None
    community_label: str | None
    score:         float
    metadata:      dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query:        str
    tenant_id:    str
    mapping_id:   str
    retrieval_mode: str          # 'graph_rag' | 'direct'
    hits:         list[SearchHit]
    communities:  list[dict[str, Any]] = Field(default_factory=list)


# ── Bridge vector (AP001.2) ───────────────────────────────────────────────────

class BridgeRequest(BaseModel):
    tenant_id:    UUID
    mapping_id:   UUID
    community_a:  int = Field(..., description="Source community ID")
    community_b:  int = Field(..., description="Target community ID")
    label:        str | None = Field(None, description="Override auto-generated label")


class BridgeResponse(BaseModel):
    bridge_id:    int
    tenant_id:    str
    mapping_id:   str
    community_a:  int
    community_b:  int
    label:        str
