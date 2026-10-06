"""Request/response contracts for the chat API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.retrieval import EvidenceTier


class SourceReference(BaseModel):
    citation: int = 0
    document_id: str
    filename: str
    title: str = ""
    section: str = ""
    section_path: str = ""
    section_id: str = ""
    heading: str = ""
    parent_section: str = ""
    chunk_id: str = ""
    score: float = 0.0
    vector_score: float | None = None
    keyword_score: float | None = None
    version: str = ""
    category: str = ""
    #: Where inside the file this came from — "صفحة 12", "Sheet: Summary — صفوف 12–18".
    #: Empty for Markdown, and every consumer treats empty as "show it the old way".
    locator: str = ""
    page: int | None = None
    tier: EvidenceTier = EvidenceTier.SUPPORTING
    origin: str = ""
    excerpt: str = ""


class DerivedValue(BaseModel):
    """A number this system calculated, kept apart from the ones it quoted.

    Returned so a reader can audit the arithmetic instead of trusting it: the operands
    are listed, not summarised. `holds` says whether the calculation agreed with the
    figure the document printed.
    """

    kind: str = "column_sum"
    label: str
    column: str = ""
    computed: str
    stated: str | None = None
    holds: bool = False
    difference: str = "0"
    operands: list[str] = Field(default_factory=list)
    citation: int = 0
    locator: str = ""


class WebSource(BaseModel):
    """An external page an answer rested on.

    A separate model from `SourceReference`, not a flag on it. The difference between a
    document this company owns and a page on the internet is the most important thing a
    reader of an answer can know, and a boolean on a shared shape is too easy to drop on
    the way to a screen.
    """

    citation: int = 0
    title: str = ""
    url: str = ""
    domain: str = ""
    snippet: str = ""
    #: Whether the domain is one a company could defend citing — a government body, a
    #: university, a standards organisation. Ranking only; nothing is excluded by it.
    authoritative: bool = False


class SourceFact(BaseModel):
    """A label/value pair taken verbatim from the document, with its citation."""

    label: str
    value: str
    kind: str = ""
    citation: int = 0
    primary: bool = False
    section_heading: str = ""


class KnowledgeReference(BaseModel):
    """An approved knowledge item that reached this answer.

    Reported separately from `sources` on purpose: a source is a passage from a document,
    this is a claim a colleague made and a reviewer approved. Collapsing the two would
    hide exactly the distinction the knowledge layer exists to keep.
    """

    item_id: str
    version_id: str = ""
    type: str = ""
    scope: str = ""
    content: str = ""
    source_text: str = ""
    source_document_id: str | None = None
    confidence: float = 0.0
    version_no: int = 1
    # offered | applied | stated — how far it got into the answer.
    influence: str = "offered"
    conflicted: bool = False
    # Similarity from the knowledge collection, or lexical overlap when the vector
    # index was unreachable. Reported so a weak match is visible rather than implied.
    score: float = 0.0
    retrieval: str = "lexical"  # semantic | lexical | directive
    answer_id: str = ""


class CoverageReport(BaseModel):
    """How much of the retrieved evidence actually reached the answer."""

    sections_retrieved: int = 0
    documents_retrieved: int = 0
    facts_extracted: int = 0
    facts_used: int = 0
    expected_entities: list[str] = Field(default_factory=list)
    answered_entities: list[str] = Field(default_factory=list)
    missing_entities: list[str] = Field(default_factory=list)
    completeness_score: float = 1.0
    passes: int = 1


class AnswerValidation(BaseModel):
    """Outcome of the post-answer completeness and grounding checks."""

    complete: bool = True
    cited_sources: list[int] = Field(default_factory=list)
    uncited_evidence: list[int] = Field(default_factory=list)
    unsupported_values: list[str] = Field(default_factory=list)
    unanswered_parts: list[str] = Field(default_factory=list)
    omitted_facts: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    expanded: bool = False


class QueryPlan(BaseModel):
    intent: str = ""
    language: str = ""
    keywords: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    multi_part: bool = False
    wide_retrieval: bool = False
    exhaustive: bool = False
    role_specific: bool = False
    high_risk: bool = False
    requirements: list[str] = Field(default_factory=list)
    #: A compound question's parts, as asked; each was searched on its own.
    parts: list[str] = Field(default_factory=list)
    #: Colloquial forms mapped and synonyms added for search, as "from→to" notes.
    rewrites: list[str] = Field(default_factory=list)
    #: The file the question named and was answered from alone — or, when the name
    #: fitted several, the candidates it was asked to choose between.
    scope: str = ""


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=60)
    category: str | None = None
    document_ids: list[str] | None = None
    #: The browser's conversation: a follow-up is read against the question before it.
    conversation_id: str | None = Field(default=None, max_length=64)
    #: Asked for again ("regenerate"): answered afresh, never from memory.
    fresh: bool = False
    #: "Deep thinking": the model reasons before it answers and before it reviews. Slower
    #: by minutes; chosen by the reader for the questions that matter.
    deep: bool = False
    #: Words read from a picture the reader attached, and from the part they marked on
    #: it (POST /api/chat/picture). The picture itself is never sent with the question.
    image_text: str | None = Field(default=None, max_length=8000)
    image_marked: str | None = Field(default=None, max_length=4000)
    #: What a vision model understood the picture to show.
    image_vision: str | None = Field(default=None, max_length=8000)


class ChatChoice(BaseModel):
    """A way to answer a clarification in one click: the question to send for it."""

    label: str
    question: str


class ChatFile(BaseModel):
    """A file made for the reader in this reply (GET /api/chat/files/{id})."""

    id: str
    name: str
    format: str
    size: int = 0


class ChatResponse(BaseModel):
    answer: str
    grounded: bool
    sources: list[SourceReference] = Field(default_factory=list)
    retrieved_chunks: int = 0
    model: str = ""
    plan: QueryPlan | None = None
    validation: AnswerValidation | None = None
    facts: list[SourceFact] = Field(default_factory=list)
    #: Values the system worked out, never presented as quoted evidence.
    derived: list[DerivedValue] = Field(default_factory=list)
    coverage: CoverageReport | None = None
    knowledge: list[KnowledgeReference] = Field(default_factory=list)
    #: External pages, kept in their own list so nothing can present one as internal.
    #: Empty on every answer the corpus could satisfy, which is almost all of them.
    web_sources: list[WebSource] = Field(default_factory=list)
    #: "internal" | "web" | "none" — where this answer actually came from.
    answer_source: str = "internal"
    #: When the reply is a question back — "which file do you mean?" — the answers it
    #: accepts, each a complete question to send. Empty for every ordinary answer.
    choices: list[ChatChoice] = Field(default_factory=list)
    #: Contact and access details masked because the question did not ask for them,
    #: as "kind:count" — "phone:1", "email:2". Empty when nothing was withheld.
    redacted: list[str] = Field(default_factory=list)
    #: Why the corpus could not answer, when it could not: "no-candidates",
    #: "model-read-evidence-and-refused" and the like. Empty on every answer.
    refusal_reason: str = ""
    # Identifies the stored trace behind this answer, for "why this answer?".
    answer_id: str = ""
    #: Files made for this reply — a PDF, Word or Excel asked for in the chat.
    files: list[ChatFile] = Field(default_factory=list)
    # Present when the question read as teaching rather than asking. An offer to
    # the person, never a change to anything.
    learning_signal: dict | None = None
    timings_ms: dict[str, int] = Field(default_factory=dict)


class AnswerExplanation(BaseModel):
    """What one answer was built from, for "why this answer?".

    Four separate lists on purpose. Merging document evidence with taught knowledge, or
    rules with facts, would hide the very distinction the reader needs in order to judge
    the answer — which part came from a filed source, and which from a colleague.
    """

    answer_id: str
    question: str = ""
    model: str = ""
    created_at: datetime | None = None

    document_evidence: list[dict] = Field(default_factory=list)
    knowledge_used: list[dict] = Field(default_factory=list)
    policies_applied: list[dict] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)

    conflict_count: int = 0
    unresolved_conflicts: int = 0
    knowledge_layer_enabled: bool = False
