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


class ForbiddenPermission(ForbiddenRole):
    """The caller is a member of the shop but does not hold the permission the operation needs.

    Raised in the place of ForbiddenRole while the per-member permissions are on: the role alone no
    longer says what is missing. It is a ForbiddenRole, so whatever refuses by role refuses this too.
    """

    code = "FORBIDDEN_PERMISSION"

    def __init__(self, permission: str, needed: Role) -> None:
        AppError.__init__(self, {"permission": permission})
        self.needed = needed
        self.permission = permission


class BeyondOwnPermissions(AppError):
    """A member who manages staff tried to act on someone, or give something, above their own rights."""

    code = "BEYOND_OWN_PERMISSIONS"


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
