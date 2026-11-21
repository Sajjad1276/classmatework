class ApplicationError(Exception):
    """Base class for expected application-level failures."""


class AccessDenied(ApplicationError):
    """The actor does not have the permission required for this use case."""


class ResourceConflict(ApplicationError):
    """The requested resource conflicts with existing state."""


class ResourceNotFound(ApplicationError):
    """The requested resource does not exist in the actor's scope."""


class InvalidApplicationInput(ApplicationError):
    """The supplied command violates application/domain validation."""
