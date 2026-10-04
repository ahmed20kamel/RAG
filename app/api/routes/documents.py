"""Document management endpoints."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, File, Form, Query, UploadFile, status

from app.api.deps import CurrentUserDep, DocumentServiceDep, SessionDep, require
from app.core.permissions import Permission, has_permission
from app.exceptions import AuthorizationError, DocumentNotFoundError
from app.services import access
from app.models.auth import User
from app.models.document import Document
from app.core.domain import DocumentStatus
from app.schemas.document import (
    DeleteResponse,
    DocumentChunkPage,
    DocumentChunkPreview,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentMetadataInput,
    DocumentRaw,
    DocumentResponse,
    EntityResponse,
    LibraryStats,
    SectionResponse,
)

logger = logging.getLogger(__name__)

#: Characters a file name picks up on its way through messaging apps and copy-paste —
#: the replacement character left by a broken encoding, direction marks, zero-width
#: joiners — that no one typed and every list then shows as "��".
_NAME_NOISE = re.compile("[�​-‏‪-‮⁦-⁩﻿]")


def clean_filename(name: str) -> str:
    """The file name without invisible or broken characters, and without stray spaces."""
    cleaned = _NAME_NOISE.sub("", name)
    return re.sub(r"\s+", " ", cleaned).strip()

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _visible(session, service, user, document_id: str) -> Document:
    """The document if this reader may see it. Someone else's reads as not found:
    a different answer would tell a stranger which identifiers exist."""
    document = service.get(session, document_id)
    if not access.can_see(user, document):
        raise DocumentNotFoundError(f"المستند '{document_id}' غير موجود.")
    return document


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    session: SessionDep,
    service: DocumentServiceDep,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    category: str | None = Form(default=None),
    source: str | None = Form(default=None),
    version: str | None = Form(default=None),
    date: str | None = Form(default=None),
    language: str | None = Form(default=None),
    project: str | None = Form(default=None),
    folder: str | None = Form(default=None),
    uploader: User = require(Permission.DOCUMENT_UPLOAD),
) -> DocumentResponse:
    """Accept a Markdown file and start background processing."""
    content = await file.read()
    metadata = DocumentMetadataInput(
        title=title or None,
        category=category or None,
        source=source or None,
        version=version or None,
        date=date or None,
        language=language or None,
        project=clean_filename(project or "") or None,
        folder=clean_filename(folder or "") or None,
    )
    filename = clean_filename(file.filename or "") or "untitled.md"
    logger.info("Upload by %s: %s", uploader.email, filename)
    document = service.upload(
        session=session,
        filename=filename,
        content=content,
        metadata=metadata,
        owner_id=uploader.id,
    )
    return DocumentResponse.model_validate(document)


@router.get("", response_model=DocumentListResponse)
def list_documents(
    session: SessionDep,
    service: DocumentServiceDep,
    user: CurrentUserDep,
    document_status: DocumentStatus | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    search: str | None = Query(default=None),
    project: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> DocumentListResponse:
    total, items = service.list_documents(
        session=session,
        status=document_status,
        category=category,
        search=search,
        limit=limit,
        offset=offset,
        project=project,
        owner_id=access.owner_filter(user),
    )
    return DocumentListResponse(
        total=total, items=[DocumentResponse.model_validate(item) for item in items]
    )


@router.get("/categories", response_model=list[str])
def list_categories(
    session: SessionDep, service: DocumentServiceDep, user: CurrentUserDep
) -> list[str]:
    return service.categories(session, access.owner_filter(user))


@router.get("/projects", response_model=list[str])
def list_projects(
    session: SessionDep, service: DocumentServiceDep, user: CurrentUserDep
) -> list[str]:
    return service.projects(session, access.owner_filter(user))


@router.get("/stats", response_model=LibraryStats)
def library_stats(
    session: SessionDep, service: DocumentServiceDep, user: CurrentUserDep
) -> LibraryStats:
    """Totals for the overview page, aggregated in the database rather than in the client."""
    return service.library_stats(session, access.owner_filter(user))


@router.get("/{document_id}/chunks", response_model=DocumentChunkPage)
def get_document_chunks(
    document_id: str,
    session: SessionDep,
    service: DocumentServiceDep,
    user: CurrentUserDep,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DocumentChunkPage:
    """One page of chunks, so opening a large document does not transfer all of it."""
    _visible(session, service, user, document_id)
    chunks = service.get_chunks(document_id)
    window = chunks[offset : offset + limit]
    return DocumentChunkPage(
        total=len(chunks),
        offset=offset,
        items=[
            DocumentChunkPreview(
                chunk_id=str(chunk.get("chunk_id", "")),
                index=int(chunk.get("chunk_index", 0)),
                heading=str(chunk.get("heading", "")),
                section=str(chunk.get("section", "")),
                char_count=int(chunk.get("char_count", 0)),
                content=str(chunk.get("content", "")),
            )
            for chunk in window
        ],
    )


@router.get("/{document_id}/raw", response_model=DocumentRaw)
def get_document_raw(
    document_id: str,
    session: SessionDep,
    service: DocumentServiceDep,
    user: CurrentUserDep,
) -> DocumentRaw:
    """The uploaded file itself, so a citation can be opened at its source."""
    document = _visible(session, service, user, document_id)
    content = service.read_stored_file(document)
    return DocumentRaw(
        id=document.id,
        filename=document.filename,
        content=content,
        char_count=len(content),
    )


@router.get("/{document_id}/sections", response_model=list[SectionResponse])
def get_document_sections(
    document_id: str, session: SessionDep, service: DocumentServiceDep, user: CurrentUserDep
) -> list[SectionResponse]:
    """The section tree built at ingestion, with extractive summaries and key terms."""
    _visible(session, service, user, document_id)
    return [
        SectionResponse(
            section_id=row.section_id,
            heading=row.heading,
            path=row.path,
            level=row.level,
            parent_id=row.parent_id,
            child_ids=list(row.child_ids or []),
            summary=row.summary,
            terms=list(row.terms or []),
            has_table=row.has_table,
            has_list=row.has_list,
            has_code=row.has_code,
            char_count=row.char_count,
        )
        for row in service.knowledge.sections_for_document(document_id)
    ]


@router.get("/{document_id}/entities", response_model=list[EntityResponse])
def get_document_entities(
    document_id: str,
    session: SessionDep,
    service: DocumentServiceDep,
    user: CurrentUserDep,
    kind: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[EntityResponse]:
    """Facts extracted verbatim from the document — nothing here is model generated."""
    _visible(session, service, user, document_id)
    return [
        EntityResponse(
            kind=row.kind,
            value=row.value,
            label=row.label,
            section_id=row.section_id,
            context=row.context,
        )
        for row in service.entities(document_id, kind=kind, limit=limit)
    ]


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: str,
    session: SessionDep,
    service: DocumentServiceDep,
    user: CurrentUserDep,
    include_chunks: bool = Query(default=True),
) -> DocumentDetailResponse:
    document = _visible(session, service, user, document_id)
    payload = DocumentDetailResponse.model_validate(document)

    if include_chunks and document.status == DocumentStatus.COMPLETED:
        payload.chunks = [
            DocumentChunkPreview(
                chunk_id=str(chunk.get("chunk_id", "")),
                index=int(chunk.get("chunk_index", 0)),
                heading=str(chunk.get("heading", "")),
                section=str(chunk.get("section", "")),
                char_count=int(chunk.get("char_count", 0)),
                content=str(chunk.get("content", "")),
            )
            for chunk in service.get_chunks(document_id)
        ]
    return payload


@router.post("/{document_id}/reindex", response_model=DocumentResponse, status_code=status.HTTP_202_ACCEPTED)
def reindex_document(
    document_id: str,
    session: SessionDep,
    service: DocumentServiceDep,
    user: User = require(Permission.DOCUMENT_UPLOAD),
) -> DocumentResponse:
    _visible(session, service, user, document_id)
    document = service.reindex(session, document_id)
    return DocumentResponse.model_validate(document)


@router.delete("/{document_id}", response_model=DeleteResponse)
def delete_document(
    document_id: str,
    session: SessionDep,
    service: DocumentServiceDep,
    user: User = require(Permission.DOCUMENT_UPLOAD),
) -> DeleteResponse:
    # Anyone who may upload may delete what they uploaded; deleting someone else's
    # document takes the delete permission.
    owned = _visible(session, service, user, document_id)
    if owned.owner_id != user.id and not has_permission(user.role, Permission.DOCUMENT_DELETE):
        raise AuthorizationError("لا يمكنك حذف مستند لم ترفعه.")
    # Who deleted what is recorded before the row is gone. The access log names only
    # an address; a deletion is the one action here that cannot be undone from the
    # interface, and the first question after one is always who did it.
    document = session.get(Document, document_id)
    logger.info(
        "Delete by %s: %s (%s)", user.email,
        document.filename if document else "(already gone)", document_id,
    )
    service.delete(session, document_id)
    return DeleteResponse(id=document_id, deleted=True, message="تم حذف المستند ومتجهاته.")
