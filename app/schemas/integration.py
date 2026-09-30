"""The ERP ↔ RAG wire contract, exactly as approved.

Two properties this module exists to hold.

`extra="forbid"` at every level. A field the server does not recognise is a
disagreement about the contract, and disagreements that are silently dropped surface
later as a security control that was never applied — the caller sent
`web_search_allowed`, the server read nothing, and both believed the other had it right.

Four citation lists that never merge. `sources` is a passage from a filed document,
`knowledge_sources` is a claim a colleague made and a reviewer approved, `web_sources`
is a page on the internet, and `erp_facts_used` is a live value the caller supplied.
A reader deciding whether to act on an answer needs that distinction before they open
anything, and a flag on a shared shape is too easy to lose on the way to a screen.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Caps that appear in the contract. Kept here so the schema and the documentation
# cannot drift apart: these constants are the only definition.
MAX_QUERY_CHARS = 4000
MIN_QUERY_CHARS = 2
MAX_ERP_FACTS = 50
MAX_ERP_FACT_LABEL = 128
MAX_ERP_FACT_VALUE = 500
MAX_DOCUMENT_IDS = 20


class _Strict(BaseModel):
    """Every model in this file refuses fields it does not know."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class EndUser(_Strict):
    """Who, in the calling system, is behind this question.

    Carried for audit and for nothing else. `role_hint` and `department` are recorded
    and never consulted: trusting a role asserted by another system is delegating
    authorisation to a party that does not hold it. The credential sets the ceiling.
    """

    external_user_id: str = Field(min_length=1, max_length=64)
    role_hint: str = Field(default="", max_length=64)
    department: str = Field(default="", max_length=64)
    display_name: str = Field(default="", max_length=128)
    #: Optional, and better left out. `external_user_id` identifies the caller for every
    #: audit purpose this integration has, and an address is personal data with a
    #: retention cost and no operational use here.
    email: str = Field(default="", max_length=320)


class RetrievalOptions(_Strict):
    top_k: int | None = Field(default=None, ge=1, le=60)
    category: str | None = Field(default=None, max_length=128)
    document_ids: list[str] = Field(default_factory=list, max_length=MAX_DOCUMENT_IDS)
    #: Narrows what this credential may already read. It can never widen it.
    knowledge_scopes: list[str] = Field(default_factory=list, max_length=8)


class ErpFact(_Strict):
    """One live value from the calling system, valid at a stated moment.

    `as_of` is required and not a formality. Comparing a policy against purchasing
    without knowing when the purchasing figures were read produces a verdict that will
    be quoted later as though it were still true.
    """

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=MAX_ERP_FACT_LABEL)
    value: str = Field(min_length=1, max_length=MAX_ERP_FACT_VALUE)
    entity_type: str = Field(min_length=1, max_length=64)
    #: ISO 8601, UTC.
    as_of: str = Field(min_length=4, max_length=40)
    #: Defaults to true. Treating an unmarked value as harmless is the wrong default
    #: when the cost of being wrong is a supplier's prices reaching a search engine.
    sensitive: bool = True


class RequestContext(_Strict):
    erp_facts: list[ErpFact] = Field(default_factory=list, max_length=MAX_ERP_FACTS)
    conversation_id: str = Field(default="", max_length=64)


class ErpQueryRequest(_Strict):
    request_id: str = Field(min_length=8, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=MIN_QUERY_CHARS, max_length=MAX_QUERY_CHARS)
    end_user: EndUser

    language: str | None = Field(default=None, pattern="^(ar|en)$")
    conversation_id: str = Field(default="", max_length=64)
    retrieval: RetrievalOptions = Field(default_factory=RetrievalOptions)
    context: RequestContext = Field(default_factory=RequestContext)
    #: Defaults to false, and is refused outright when ERP data is present.
    web_search_allowed: bool = False


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class DocumentSource(BaseModel):
    """A passage from a document this company filed."""

    citation: int = 0
    document_id: str = ""
    filename: str = ""
    title: str = ""
    section: str = ""
    section_path: str = ""
    #: Where inside the file — "صفحة 7", "Sheet: Summary — صفوف 12–18". Empty for
    #: Markdown, which has no location finer than its heading path.
    locator: str = ""
    page: int | None = None
    version: str = ""
    category: str = ""
    tier: str = "supporting"
    score: float = 0.0
    excerpt: str = ""


class KnowledgeSource(BaseModel):
    """A claim a colleague made and a reviewer approved."""

    item_id: str = ""
    version_id: str = ""
    version_no: int = 1
    type: str = ""
    scope: str = ""
    content: str = ""
    source_text: str = ""
    #: The author's own confidence, recorded rather than inferred. It is not, and must
    #: not be read as, the system's confidence in the answer.
    confidence: float = 0.0
    influence: str = "offered"
    conflicted: bool = False


class WebSourceRef(BaseModel):
    """A page on the internet."""

    citation: int = 0
    title: str = ""
    url: str = ""
    domain: str = ""
    snippet: str = ""
    authoritative: bool = False


class ErpFactRef(BaseModel):
    """A value the caller supplied, named so it can never be mistaken for evidence.

    `origin` is a constant. It is not derived from anything and cannot be set by a
    caller, because the one thing this field exists to guarantee is that a live figure
    is never read as a quotation from a filed document.
    """

    id: str = ""
    label: str = ""
    origin: str = "erp"
    as_of: str = ""
    influence: str = "offered"


class ComputedValue(BaseModel):
    """A number this system worked out, never presented as one it quoted."""

    kind: str = "column_sum"
    label: str = ""
    computed: str = ""
    stated: str | None = None
    holds: bool = False
    difference: str = "0"
    operands: list[str] = Field(default_factory=list)
    citation: int = 0
    locator: str = ""


class LatencyBreakdown(BaseModel):
    total: int = 0
    retrieval: int | None = None
    generation: int | None = None


class ErpQueryResponse(BaseModel):
    """What the caller receives. Refusal travels here too, not as an HTTP failure."""

    # -- always present ----------------------------------------------------
    request_id: str
    #: The stored trace's identifier. There is deliberately no second `trace_id`:
    #: two identifiers for one thing means correlating them by hand at the exact moment
    #: something has gone wrong.
    answer_id: str
    answered: bool
    answer: str
    #: internal | internal+erp | web | none
    answer_source: str
    grounded: bool
    language: str
    model: str
    latency_ms: LatencyBreakdown

    # -- four lists, never merged -----------------------------------------
    sources: list[DocumentSource] = Field(default_factory=list)
    knowledge_sources: list[KnowledgeSource] = Field(default_factory=list)
    web_sources: list[WebSourceRef] = Field(default_factory=list)
    erp_facts_used: list[ErpFactRef] = Field(default_factory=list)

    # -- optional; a caller must not fail on their absence ----------------
    computed_values: list[ComputedValue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    needs_live_data: bool = False
    suggested_source: str | None = None
    web_knowledge_candidate_id: str | None = None


class ErrorResponse(BaseModel):
    """The error envelope. `detail` is for a person; `error_code` is for a program."""

    error: str
    error_code: str
    detail: str
    request_id: str = ""
    retryable: bool = False
