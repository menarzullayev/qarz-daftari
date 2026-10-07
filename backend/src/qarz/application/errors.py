"""Application errors. Codes are part of the API contract (technical specification, error handling)."""

from qarz.domain.access import Role


class AppError(Exception):
    code = "ERROR"

    def __init__(self, fields: dict[str, str] | None = None) -> None:
        super().__init__(self.code)
        self.fields = fields or {}


class Unauthenticated(AppError):
    code = "UNAUTHENTICATED"


class NotFound(AppError):
    """Also raised when the caller has no right to know the thing exists, so probing reveals nothing."""

    code = "NOT_FOUND"


class ForbiddenRole(AppError):
    """The caller is a member of the shop but their role does not include the operation."""

    code = "FORBIDDEN_ROLE"

    def __init__(self, needed: Role) -> None:
        super().__init__({"needed_role": needed.value})
        self.needed = needed


class ValidationFailed(AppError):
    code = "VALIDATION"


class StorageTimeout(AppError):
    """A statement ran longer than the application allows and was cancelled; nothing was saved.

    One slow query must not hold a database core while every other shop waits (S19.1 load test).
    """

    code = "TIMEOUT"


class AlreadyMember(AppError):
    """The person is already a member of the shop they were invited to."""

    code = "ALREADY_MEMBER"
