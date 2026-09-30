"""Request/response contracts for learning candidates."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.knowledge import KnowledgeScope, KnowledgeType


class LearningSignalResponse(BaseModel):
    """What detection noticed, returned alongside an answer as a suggestion.

    Present on a chat response only when the message read as teaching. It is an offer to
    the person, not a change to anything.
    """

    candidate_id: str = ""
    type: KnowledgeType
    signal: str
    suggested_content: str
    confidence: float
    proposed_scope: KnowledgeScope = KnowledgeScope.USER
    needs_approval: bool = True


class CandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    detected_type: KnowledgeType
    signal: str
    raw_text: str
    suggested_content: str
    confidence: float
    state: str
    proposed_scope: KnowledgeScope

    conversation_id: str = ""
    answer_id: str = ""
    corrects_item_id: str | None = None
    promoted_item_id: str | None = None

    user_id: str | None = None
    proposer_name: str = ""
    resolution_reason: str = ""
    resolved_by: str | None = None

    created_at: datetime
    resolved_at: datetime | None = None

    can_resolve: bool = False


class CandidateListResponse(BaseModel):
    total: int
    items: list[CandidateResponse]


class CandidateAcceptRequest(BaseModel):
    """The person may correct the wording, the type and the reach before accepting."""

    content: str | None = Field(default=None, max_length=4000)
    type: KnowledgeType | None = None
    scope: KnowledgeScope | None = None
    source_text: str = Field(default="", max_length=2000)
    explanation: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=12)


class CandidateDismissRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)
    rejected: bool = False


class CorrectAnswerRequest(BaseModel):
    """A correction raised against a specific answer.

    Carries the answer it disputes so the candidate can be read beside what it corrects.
    It creates a candidate — never an edit to the answer or to existing knowledge.
    """

    answer_id: str = Field(min_length=1, max_length=36)
    correction: str = Field(min_length=3, max_length=4000)
    source_text: str = Field(default="", max_length=2000)
    explanation: str = Field(default="", max_length=2000)
    corrects_item_id: str | None = None


class CandidateStatsResponse(BaseModel):
    total: int = 0
    by_state: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    open_for_me: int = 0
