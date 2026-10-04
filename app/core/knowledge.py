"""What a piece of taught knowledge is, and what may happen to it.

Kept apart from the document pipeline on purpose. A document is evidence because someone
wrote it down and filed it; an item here is a claim a colleague made, and it earns the
right to reach an answer only by passing through the workflow below. The two never merge
into one pool — that separation is the whole point of this layer.
"""

from __future__ import annotations

from enum import StrEnum


class KnowledgeType(StrEnum):
    """What the item asserts, which decides how an answer may use it.

    The split matters more than it looks. A FACT can be checked against a document and
    can contradict one. A RULE changes how an answer is assembled and can never be
    quoted as a fact. A PREFERENCE touches wording only. Collapsing these into one
    "knowledge" bucket is how a style note ends up overruling a contract figure.
    """

    FACT = "fact"
    RULE = "rule"
    CORRECTION = "correction"
    PROCEDURE = "procedure"
    TERMINOLOGY = "terminology"
    PREFERENCE = "preference"


#: Types that assert something about the world, so they may be weighed against document
#: evidence and may conflict with it.
FACTUAL_TYPES = frozenset({
    KnowledgeType.FACT,
    KnowledgeType.CORRECTION,
    KnowledgeType.PROCEDURE,
    KnowledgeType.TERMINOLOGY,
})

#: Types that shape how an answer is produced rather than what it states. These are
#: never presented as evidence and never enter a conflict with a document.
BEHAVIOURAL_TYPES = frozenset({KnowledgeType.RULE, KnowledgeType.PREFERENCE})


class KnowledgeScope(StrEnum):
    USER = "user"
    TEAM = "team"
    DEPARTMENT = "department"
    GLOBAL = "global"


#: Widening order. An item is visible to a reader whose own scope reaches at least this
#: far, which is what keeps one person's note out of everyone else's answers.
SCOPE_RANK: dict[KnowledgeScope, int] = {
    KnowledgeScope.USER: 0,
    KnowledgeScope.TEAM: 1,
    KnowledgeScope.DEPARTMENT: 2,
    KnowledgeScope.GLOBAL: 3,
}


class KnowledgeStatus(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    ACTIVE = "active"
    REJECTED = "rejected"
    ARCHIVED = "archived"


#: The only statuses retrieval will ever read. Everything else — a draft, something
#: waiting on review, something rejected or archived — is invisible to an answer.
#: Expressed as a set the query filters on, not as a check after the rows come back,
#: so a forgotten filter fails loudly rather than leaking an unapproved claim.
RETRIEVABLE_STATUSES = frozenset({KnowledgeStatus.ACTIVE})

#: Statuses an item can still be edited from without creating a new review cycle.
EDITABLE_STATUSES = frozenset({
    KnowledgeStatus.DRAFT,
    KnowledgeStatus.PENDING,
    KnowledgeStatus.IN_REVIEW,
    KnowledgeStatus.REJECTED,
})

#: Allowed transitions. Anything absent here is refused, so a new path has to be added
#: deliberately rather than appearing because some handler happened to set a field.
TRANSITIONS: dict[KnowledgeStatus, frozenset[KnowledgeStatus]] = {
    KnowledgeStatus.DRAFT: frozenset({KnowledgeStatus.PENDING, KnowledgeStatus.ARCHIVED}),
    KnowledgeStatus.PENDING: frozenset({
        KnowledgeStatus.IN_REVIEW,
        KnowledgeStatus.APPROVED,
        KnowledgeStatus.REJECTED,
        KnowledgeStatus.ARCHIVED,
    }),
    KnowledgeStatus.IN_REVIEW: frozenset({
        KnowledgeStatus.APPROVED,
        KnowledgeStatus.REJECTED,
        KnowledgeStatus.PENDING,
        KnowledgeStatus.ARCHIVED,
    }),
    KnowledgeStatus.APPROVED: frozenset({KnowledgeStatus.ACTIVE, KnowledgeStatus.ARCHIVED}),
    KnowledgeStatus.ACTIVE: frozenset({KnowledgeStatus.ARCHIVED}),
    # A rejection is not the end of the item: it can be revised and offered again.
    KnowledgeStatus.REJECTED: frozenset({KnowledgeStatus.PENDING, KnowledgeStatus.ARCHIVED}),
    KnowledgeStatus.ARCHIVED: frozenset({KnowledgeStatus.PENDING}),
}


def can_transition(current: KnowledgeStatus, target: KnowledgeStatus) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def is_self_activating(item_type: KnowledgeType, scope: KnowledgeScope) -> bool:
    """Anything personal needs nobody's approval; anything shared does.

    A personal item reaches one person's answers and no one else's: their own preference,
    or a correction to an answer from their own documents. Making them wait for a
    reviewer only meant the system kept repeating a mistake its reader had already
    pointed out. What another person can read — team, department, everyone — still
    goes through review, because one wrong correction there would spread.
    """
    return scope is KnowledgeScope.USER


class ReviewDecision(StrEnum):
    SUBMITTED = "submitted"
    STARTED_REVIEW = "started_review"
    APPROVED = "approved"
    ACTIVATED = "activated"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    RESTORED = "restored"
    EDITED = "edited"
