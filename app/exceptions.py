"""Domain exceptions mapped to HTTP responses in app.main."""

from __future__ import annotations


class RagError(Exception):
    """Base class for all application errors.

    `error_code` and `retryable` were added for the integration contract, where a caller
    is a program rather than a person. A program must not branch on an Arabic sentence:
    the wording is for whoever reads the reply, and the code is what the caller switches
    on. `retryable` is stated rather than inferred, because "may I try again?" is the one
    question a machine client always has and the status code answers it only ambiguously
    — a 502 is worth retrying and a 500 is not, though both are server errors.

    Existing errors keep their behaviour: the code defaults to the class name in
    UPPER_SNAKE, and nothing that already raises these needs to change.
    """

    status_code = 500
    #: Stable identifier for machine callers. Empty means "derive it from the class".
    error_code = ""
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "",
        retryable: bool | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_code:
            self.error_code = error_code
        if retryable is not None:
            self.retryable = retryable
        self.headers = headers or {}

    @property
    def code(self) -> str:
        """The stable code, derived from the class name when none was declared."""
        if self.error_code:
            return self.error_code
        name = type(self).__name__.removesuffix("Error")
        out: list[str] = []
        for index, char in enumerate(name):
            if char.isupper() and index and not name[index - 1].isupper():
                out.append("_")
            out.append(char)
        return "".join(out).upper() or "ERROR"


class ValidationError(RagError):
    status_code = 400


class UnsupportedFileTypeError(ValidationError):
    pass


class DocumentNotFoundError(RagError):
    status_code = 404


class DuplicateDocumentError(RagError):
    status_code = 409


class ParsingError(RagError):
    status_code = 422


class ExtractionError(ParsingError):
    """The file opened, but no usable text came out of it.

    Kept apart from ParsingError because the two send a person in different directions:
    a parsing failure is a malformed file, while this is a readable file that simply had
    nothing to give — an empty workbook, a PDF of blank pages.
    """


class OcrRequiredError(ExtractionError):
    """The pages are images, so there is nothing to extract without OCR.

    This is the one failure that must never be dressed up as a parsing error. Guessing at
    the contents of a scanned contract is the single worst thing this system could do, so
    it stops here and says plainly what the file needs.
    """



class EmbeddingError(RagError):
    status_code = 502


class VectorStoreError(RagError):
    status_code = 502


class LLMError(RagError):
    status_code = 502


class TooManyQuestionsError(RagError):
    """One person asking faster than they can be answered, while others wait."""

    status_code = 429


class AuthenticationError(RagError):
    """No valid session, or credentials that did not check out."""

    status_code = 401


class AuthorizationError(RagError):
    """A valid session that does not carry the capability this action needs."""

    status_code = 403


# ---------------------------------------------------------------------------
# Integration errors (ERP ↔ RAG, Phase 1)
#
# Separate classes rather than one class with a code, because the status code is part
# of the contract and a class is where a status code belongs. Every one of these carries
# an explicit `retryable`: the caller's retry policy is the contract's, not a guess.
# ---------------------------------------------------------------------------


class IntegrationDisabledError(RagError):
    """The integration surface is switched off on this instance."""

    status_code = 503
    error_code = "INTEGRATION_DISABLED"
    # Not retryable: this is a deliberate configuration, not a passing condition.
    retryable = False


class TransportError(RagError):
    """The request arrived over a transport this endpoint will not accept."""

    status_code = 403
    error_code = "HTTPS_REQUIRED"
    retryable = False


class IntegrationAuthError(RagError):
    """A credential, signature, timestamp or nonce that did not check out.

    One class for every failure, with the specific code in `error_code`, so the reply
    tells the operator what to fix without telling an attacker which half of the
    credential they got right.
    """

    status_code = 401
    error_code = "AUTH_INVALID"
    retryable = False


class TenantMismatchError(RagError):
    """The declared tenant is not the tenant this credential was issued for."""

    status_code = 403
    error_code = "TENANT_MISMATCH"
    retryable = False


class ScopeDeniedError(RagError):
    """A knowledge scope was requested that this credential may not read."""

    status_code = 403
    error_code = "SCOPE_DENIED"
    retryable = False


class PayloadTooLargeError(ValidationError):
    """The request exceeded a declared size limit."""

    error_code = "PAYLOAD_TOO_LARGE"
    retryable = False


class WebSearchNotPermittedError(ValidationError):
    """Web search was requested on a call carrying ERP data.

    Refused structurally rather than quietly switched off, because a caller that asked
    for something forbidden has a bug, and silently doing something else would leave it
    in place until the day the check is the only thing standing between a supplier's
    prices and a public search engine.
    """

    error_code = "WEB_SEARCH_NOT_PERMITTED_WITH_ERP_CONTEXT"
    retryable = False


class UnsupportedContextError(ValidationError):
    """Something in the request is contracted but not yet built."""

    error_code = "NOT_SUPPORTED_IN_THIS_PHASE"
    retryable = False


class IdempotencyConflictError(RagError):
    """The same idempotency key arrived with a different body, or is still running."""

    status_code = 409
    error_code = "IDEMPOTENCY_CONFLICT"
    retryable = False


class RagTimeoutError(RagError):
    """The answer did not finish inside the budget this endpoint allows."""

    status_code = 408
    error_code = "RAG_TIMEOUT"
    retryable = True


class RateLimitedError(RagError):
    """Too many requests, too many at once, or the day's quota is spent."""

    status_code = 429
    error_code = "RATE_LIMITED"
    retryable = True
