"""Deciding which of two disagreeing sources outranks the other — or admitting neither does.

The rule this file exists to enforce is narrow and deliberate: a winner is declared only
when explicit metadata says so. Nothing infers authority from wording, file name, length,
retrieval score or how confident a claim sounds. When the metadata is silent or equal,
the correct output is not a choice but a disagreement, reported with both values.

That asymmetry is the point. Picking wrongly puts a false figure in front of someone who
will act on it; reporting a conflict costs them a minute. Only an explicit, recorded fact
about the sources is allowed to break the tie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class DocumentAuthority(StrEnum):
    """Where a document stands, as an operator declared it — never as inferred."""

    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


#: Only an ACTIVE document may settle a disagreement. A draft is not in force, and a
#: superseded one has already been replaced; neither is allowed to overrule anything.
BINDING_STATUSES = frozenset({DocumentAuthority.ACTIVE})


class Basis(StrEnum):
    """Why one side won, recorded so the decision can be audited afterwards."""

    STATUS = "status"
    AUTHORITY_RANK = "authority_rank"
    EFFECTIVE_DATE = "effective_date"
    VERSION = "version"
    KNOWLEDGE_SCOPE = "knowledge_scope"
    DOCUMENT_OVER_KNOWLEDGE = "document_over_knowledge"
    UNRESOLVED = "unresolved"


@dataclass(slots=True, frozen=True)
class SourceAuthority:
    """The metadata a source carries into a tie-break, and nothing else.

    Deliberately small. Anything not on this record cannot influence the outcome, which
    is what stops "it reads more official" from quietly becoming a rule.
    """

    #: Stable identity for reporting — a citation number or a knowledge item id.
    ref: str
    kind: str = "document"  # document | knowledge
    status: str = ""
    #: Operator-assigned rank. Higher wins. Zero means "not declared".
    authority: int = 0
    effective_date: date | None = None
    version: str = ""
    #: For knowledge only: a company-wide item outranks a team one, which outranks personal.
    scope_rank: int = -1
    #: Free-text provenance, carried through so a report can name the source.
    label: str = ""

    @property
    def is_binding(self) -> bool:
        """A document with no declared status is treated as in force.

        Most of the corpus predates the field, and defaulting to "not binding" would
        silently strip authority from every existing document. A declared DRAFT or
        SUPERSEDED, by contrast, is an explicit statement that it is not.
        """
        if self.kind != "document":
            return True
        return not self.status or self.status == DocumentAuthority.ACTIVE


@dataclass(slots=True, frozen=True)
class AuthorityDecision:
    """The outcome of a tie-break, whether or not it produced a winner."""

    winner: SourceAuthority | None
    loser: SourceAuthority | None
    basis: Basis
    explanation: str

    @property
    def resolved(self) -> bool:
        return self.winner is not None


def _parse_version(raw: str) -> tuple[int, ...] | None:
    """A dotted numeric version, or None when it is not comparable as one."""
    cleaned = raw.strip().lstrip("vV")
    if not cleaned:
        return None
    parts = cleaned.split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        # "1.0-final" and the like are not orderable without guessing what the suffix
        # means, so they do not decide anything.
        return None


def _coerce_date(value: str | date | datetime | None) -> date | None:
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def resolve(left: SourceAuthority, right: SourceAuthority) -> AuthorityDecision:
    """Which source outranks the other, or an explicit "neither does".

    The tests are applied in order of how much they actually settle. Status comes first
    because a superseded document is not evidence for anything; an explicit rank comes
    next because an operator set it on purpose; date and version come last, and only
    between sources of the same kind, where "newer" genuinely means "replaces".
    """
    # 1. One side is not in force at all.
    if left.is_binding != right.is_binding:
        winner, loser = (left, right) if left.is_binding else (right, left)
        return AuthorityDecision(
            winner, loser, Basis.STATUS,
            f"«{loser.label or loser.ref}» بحالة «{loser.status}» وليست نافذة، "
            f"بينما «{winner.label or winner.ref}» نافذة.",
        )
    if not left.is_binding and not right.is_binding:
        return AuthorityDecision(
            None, None, Basis.UNRESOLVED,
            "كلا المصدرين غير نافذ، فلا يمكن ترجيح أحدهما.",
        )

    # 2. A document outranks a taught claim. This is the priority rule, applied as a
    #    comparison over metadata rather than as an instruction to the model.
    if left.kind != right.kind:
        winner, loser = (left, right) if left.kind == "document" else (right, left)
        return AuthorityDecision(
            winner, loser, Basis.DOCUMENT_OVER_KNOWLEDGE,
            f"دليل مستندي «{winner.label or winner.ref}» يُقدَّم على معرفة معتمدة "
            f"«{loser.label or loser.ref}».",
        )

    # 3. An explicitly declared rank.
    if left.authority != right.authority and (left.authority or right.authority):
        winner, loser = (left, right) if left.authority > right.authority else (right, left)
        return AuthorityDecision(
            winner, loser, Basis.AUTHORITY_RANK,
            f"«{winner.label or winner.ref}» مرتبتها {winner.authority} مقابل "
            f"{loser.authority} لـ«{loser.label or loser.ref}».",
        )

    # 4. Between knowledge items, a wider scope was approved by a wider authority.
    if left.kind == "knowledge" and left.scope_rank != right.scope_rank:
        winner, loser = (
            (left, right) if left.scope_rank > right.scope_rank else (right, left)
        )
        return AuthorityDecision(
            winner, loser, Basis.KNOWLEDGE_SCOPE,
            f"نطاق «{winner.label or winner.ref}» أوسع، واعتماده من سلطة أعلى.",
        )

    # 5. The later effective date, when both sides declare one.
    left_date, right_date = _coerce_date(left.effective_date), _coerce_date(right.effective_date)
    if left_date and right_date and left_date != right_date:
        winner, loser = (left, right) if left_date > right_date else (right, left)
        return AuthorityDecision(
            winner, loser, Basis.EFFECTIVE_DATE,
            f"«{winner.label or winner.ref}» سارية من {winner.effective_date} وهي أحدث من "
            f"{loser.effective_date}.",
        )

    # 6. The higher version, when both are comparable numbers.
    left_version, right_version = _parse_version(left.version), _parse_version(right.version)
    if left_version and right_version and left_version != right_version:
        winner, loser = (left, right) if left_version > right_version else (right, left)
        return AuthorityDecision(
            winner, loser, Basis.VERSION,
            f"«{winner.label or winner.ref}» إصدار {winner.version} مقابل {loser.version}.",
        )

    # Nothing in the metadata separates them. Reporting the disagreement is the answer.
    return AuthorityDecision(
        None, None, Basis.UNRESOLVED,
        "لا توجد بيانات وصفية تُرجّح أحد المصدرين على الآخر.",
    )
