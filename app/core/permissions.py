"""Roles and the capabilities each one carries.

Handlers ask for a capability, never for a role. A role is an administrative label that
changes over time; a capability is what the code actually depends on, so adding a role
later means adding a row to this table and nothing else.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"
    CONTRIBUTOR = "contributor"
    KNOWLEDGE_MANAGER = "knowledge_manager"
    ADMIN = "admin"
    # Non-human. Used by the evaluation harness and the test suites, which need to ask
    # questions without being able to change anything.
    SERVICE = "service"


class Permission(StrEnum):
    CHAT_ASK = "chat.ask"

    DOCUMENT_READ = "document.read"
    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_DELETE = "document.delete"

    KNOWLEDGE_READ = "knowledge.read"
    KNOWLEDGE_PROPOSE = "knowledge.propose"
    KNOWLEDGE_APPROVE = "knowledge.approve"
    # Editing your own proposal is a different right from editing anyone's: a
    # contributor may correct what they wrote, not what a colleague wrote.
    KNOWLEDGE_EDIT_OWN = "knowledge.edit_own"
    KNOWLEDGE_EDIT = "knowledge.edit"
    KNOWLEDGE_ARCHIVE = "knowledge.archive"

    USER_MANAGE = "user.manage"
    SYSTEM_READ = "system.read"
    #: Request rates, latency, refusal reasons and backup state. Administrators only:
    #: the refusal list and the slowest answers point at what people are asking.
    SYSTEM_MONITOR = "system.monitor"


_VIEWER = frozenset({
    Permission.CHAT_ASK,
    Permission.DOCUMENT_READ,
    Permission.KNOWLEDGE_READ,
    Permission.SYSTEM_READ,
})

_CONTRIBUTOR = _VIEWER | {
    Permission.DOCUMENT_UPLOAD,
    Permission.KNOWLEDGE_PROPOSE,
    Permission.KNOWLEDGE_EDIT_OWN,
}

_KNOWLEDGE_MANAGER = _CONTRIBUTOR | {
    Permission.KNOWLEDGE_APPROVE,
    Permission.KNOWLEDGE_EDIT,
    Permission.KNOWLEDGE_ARCHIVE,
}

_ADMIN = _KNOWLEDGE_MANAGER | {
    Permission.DOCUMENT_DELETE,
    Permission.USER_MANAGE,
    Permission.SYSTEM_MONITOR,
}

# Deliberately minimal: a leaked service credential must not be able to teach the
# system anything, delete a document, or read who else exists.
_SERVICE = frozenset({
    Permission.CHAT_ASK,
    Permission.DOCUMENT_READ,
    Permission.SYSTEM_READ,
})

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset(_VIEWER),
    Role.CONTRIBUTOR: frozenset(_CONTRIBUTOR),
    Role.KNOWLEDGE_MANAGER: frozenset(_KNOWLEDGE_MANAGER),
    Role.ADMIN: frozenset(_ADMIN),
    Role.SERVICE: frozenset(_SERVICE),
}


def permissions_for(role: str) -> frozenset[Permission]:
    """Every capability a role carries. An unknown role carries none."""
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return frozenset()


def has_permission(role: str, permission: Permission) -> bool:
    return permission in permissions_for(role)
