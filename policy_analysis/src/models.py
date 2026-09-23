from pydantic import BaseModel, Field


class ProposedPolicy(BaseModel):
    policy_name: str

    affected_groups: list[str] = Field(default_factory=list)

    current_policy: str | None = None

    proposed_change: str

    plain_english: str

    allowed: list[str] = Field(default_factory=list)

    restricted_or_prohibited: list[str] = Field(default_factory=list)

    exceptions: list[str] = Field(default_factory=list)

    time_limits_or_durations: list[str] = Field(default_factory=list)

    cfr_references: list[str] = Field(default_factory=list)

    evidence_quote: str

    source_page: int | None = None


class ChunkPolicyExtraction(BaseModel):
    policies: list[ProposedPolicy] = Field(default_factory=list)