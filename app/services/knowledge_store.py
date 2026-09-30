"""Read/write access to the structured knowledge index."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.domain import Chunk, Entity, Section
from app.core.text import normalize, parse_date
from app.models.database import session_scope
from app.models.knowledge import ChunkRecord, EntityRecord, SectionRecord

logger = logging.getLogger(__name__)


class KnowledgeStore:
    def replace_document(
        self,
        document_id: str,
        sections: list[Section],
        entities: list[Entity],
        chunks: list[Chunk],
        category: str,
        version: str,
        language: str,
    ) -> None:
        with session_scope() as session:
            self.purge(session, document_id)

            session.add_all(
                SectionRecord(
                    id=f"{document_id}::{section.section_id}",
                    document_id=document_id,
                    section_id=section.section_id,
                    heading=section.heading,
                    path=section.breadcrumb,
                    level=section.level,
                    order_index=section.order,
                    parent_id=section.parent_id,
                    child_ids=list(section.child_ids),
                    summary=section.summary,
                    terms=list(section.terms),
                    has_table=section.has_table,
                    has_list=section.has_list,
                    has_code=section.has_code,
                    char_count=len(section.content),
                )
                for section in sections
                if section.section_id
            )

            session.add_all(
                EntityRecord(
                    document_id=document_id,
                    section_id=entity.section_id,
                    kind=entity.kind,
                    value=entity.value[:512],
                    label=entity.label[:256],
                    search_text=normalize(f"{entity.label} {entity.value}")[:1024],
                    context=entity.context,
                )
                for entity in entities
            )

            session.add_all(
                ChunkRecord(
                    chunk_id=chunk.chunk_id,
                    document_id=document_id,
                    section_id=chunk.section_id,
                    chunk_index=chunk.index,
                    filename=chunk.filename,
                    document_title=chunk.document_title,
                    heading=chunk.heading,
                    section=chunk.section,
                    parent_section=chunk.parent_section,
                    parent_section_id=chunk.parent_section_id,
                    content=chunk.content,
                    char_count=chunk.char_count,
                    has_table=chunk.has_table,
                    has_list=chunk.has_list,
                    has_code=chunk.has_code,
                    locator=chunk.locator,
                    page=chunk.page,
                    category=category,
                    version=version,
                    language=language,
                )
                for chunk in chunks
            )

        logger.info(
            "Knowledge index updated for %s: %s sections, %s entities, %s chunks",
            document_id, len(sections), len(entities), len(chunks),
        )

    def delete_document(self, document_id: str) -> None:
        with session_scope() as session:
            self.purge(session, document_id)

    @staticmethod
    def purge(session: Session, document_id: str) -> None:
        """Remove a document's knowledge rows using the caller's transaction."""
        for model in (SectionRecord, EntityRecord, ChunkRecord):
            session.execute(delete(model).where(model.document_id == document_id))

    # ---------------- retrieval support ----------------

    def chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, ChunkRecord]:
        if not chunk_ids:
            return {}
        with session_scope() as session:
            rows = session.scalars(
                select(ChunkRecord).where(ChunkRecord.chunk_id.in_(chunk_ids))
            )
            return {row.chunk_id: row for row in rows}

    def chunks_in_sections(
        self, document_id: str, section_ids: list[str], limit: int = 40
    ) -> list[ChunkRecord]:
        if not section_ids:
            return []
        with session_scope() as session:
            return list(
                session.scalars(
                    select(ChunkRecord)
                    .where(
                        ChunkRecord.document_id == document_id,
                        ChunkRecord.section_id.in_(section_ids),
                    )
                    .order_by(ChunkRecord.chunk_index)
                    .limit(limit)
                )
            )

    def related_section_ids(self, document_id: str, section_id: str) -> list[str]:
        """Parent, children and siblings — the sections most likely to complete an answer."""
        with session_scope() as session:
            section = session.scalar(
                select(SectionRecord).where(
                    SectionRecord.document_id == document_id,
                    SectionRecord.section_id == section_id,
                )
            )
            if section is None:
                return []

            related: list[str] = list(section.child_ids or [])
            if section.parent_id:
                related.append(section.parent_id)
                siblings = session.scalars(
                    select(SectionRecord.section_id).where(
                        SectionRecord.document_id == document_id,
                        SectionRecord.parent_id == section.parent_id,
                    )
                )
                related.extend(s for s in siblings if s != section_id)

            return list(dict.fromkeys(related))

    def sections_for_document(self, document_id: str) -> list[SectionRecord]:
        with session_scope() as session:
            return list(
                session.scalars(
                    select(SectionRecord)
                    .where(SectionRecord.document_id == document_id)
                    .order_by(SectionRecord.order_index)
                )
            )

    def dated_sections(
        self, document_ids: list[str], limit: int = 24
    ) -> list[tuple[str, str, tuple[int, int, int]]]:
        """Sections carrying a date, newest first.

        A timeline question must sweep the dated sections rather than whatever the
        question's wording happens to match: the update sections in these documents
        state their date in the heading, which no phrasing of "آخر تحديثات" hits.
        """
        if not document_ids:
            return []

        with session_scope() as session:
            rows = session.scalars(
                select(EntityRecord).where(
                    EntityRecord.document_id.in_(document_ids),
                    EntityRecord.kind == "date",
                )
            )
            latest: dict[tuple[str, str], tuple[int, int, int]] = {}
            for row in rows:
                parsed = parse_date(row.value) or parse_date(row.label)
                if parsed is None:
                    continue
                key = (row.document_id, row.section_id)
                if parsed > latest.get(key, (0, 0, 0)):
                    latest[key] = parsed

            headings = session.scalars(
                select(SectionRecord).where(SectionRecord.document_id.in_(document_ids))
            )
            for section in headings:
                parsed = parse_date(section.heading)
                if parsed is None:
                    continue
                key = (section.document_id, section.section_id)
                if parsed > latest.get(key, (0, 0, 0)):
                    latest[key] = parsed

        # Expiry dates (an ID valid until 2027, a licence renewal) sort above every real
        # event and would take every slot. An update cannot be in the future.
        today = date.today().timetuple()[:3]
        current = [item for item in latest.items() if item[1] <= today]
        ordered = sorted(current, key=lambda item: item[1], reverse=True)
        return [(doc, sec, when) for (doc, sec), when in ordered[:limit]]

    def merged_section_headings(
        self, pairs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], str]:
        """Sibling sections whose text the chunker may have folded into these chunks."""
        if not pairs:
            return {}

        with session_scope() as session:
            anchors = session.scalars(
                select(SectionRecord).where(
                    or_(*[
                        and_(
                            SectionRecord.document_id == document_id,
                            SectionRecord.section_id == section_id,
                        )
                        for document_id, section_id in pairs
                    ])
                )
            )
            parents = {(a.document_id, a.parent_id) for a in anchors if a.parent_id}
            if not parents:
                return {}

            siblings = session.scalars(
                select(SectionRecord).where(
                    or_(*[
                        and_(
                            SectionRecord.document_id == document_id,
                            SectionRecord.parent_id == parent_id,
                        )
                        for document_id, parent_id in parents
                    ])
                )
            )
            return {(s.document_id, s.section_id): s.heading for s in siblings}

    def entities_for_sections(
        self,
        pairs: list[tuple[str, str]],
        kinds: list[str] | None = None,
        limit: int = 200,
    ) -> list[EntityRecord]:
        """Facts extracted from specific (document_id, section_id) pairs.

        This is what turns retrieved sections into an explicit label/value list — the
        role-to-holder mapping the answer must preserve.
        """
        if not pairs:
            return []

        with session_scope() as session:
            conditions = [
                and_(
                    EntityRecord.document_id == document_id,
                    EntityRecord.section_id == section_id,
                )
                for document_id, section_id in pairs
            ]
            statement = select(EntityRecord).where(or_(*conditions))
            if kinds:
                statement = statement.where(EntityRecord.kind.in_(kinds))
            return list(session.scalars(statement.limit(limit)))

    def find_entities(
        self, terms: list[str], kinds: list[str] | None = None, limit: int = 40
    ) -> list[EntityRecord]:
        """Locate verbatim facts whose value or label matches the query wording."""
        cleaned = [normalize(t) for t in terms if len(normalize(t)) > 2]
        if not cleaned:
            return []

        with session_scope() as session:
            conditions = [
                EntityRecord.search_text.like(f"%{term}%") for term in cleaned[:12]
            ]

            statement = select(EntityRecord).where(or_(*conditions))
            if kinds:
                statement = statement.where(EntityRecord.kind.in_(kinds))
            return list(session.scalars(statement.limit(limit)))

    def stats(self) -> dict[str, int]:
        with session_scope() as session:
            return {
                "sections": session.scalar(select(func.count()).select_from(SectionRecord)) or 0,
                "entities": session.scalar(select(func.count()).select_from(EntityRecord)) or 0,
                "chunks": session.scalar(select(func.count()).select_from(ChunkRecord)) or 0,
            }
