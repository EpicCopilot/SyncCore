class AniListError(Exception):
    """Base exception for AniList integration failures."""


class AuthenticationError(AniListError):
    """OAuth or token failure."""


class ApiError(AniListError):
    """HTTP or GraphQL API failure."""


class DatabaseError(Exception):
    """Persistence failure."""
