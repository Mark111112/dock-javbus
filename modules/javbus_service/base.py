"""JavBus service protocol definitions."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Protocol

JsonDict = Dict[str, Any]


class JavbusClientProtocol(Protocol):
    """Protocol that all JavBus clients should implement."""

    def get_movie(self, movie_id: str, *, params: Optional[JsonDict] = None) -> Optional[JsonDict]:
        """Return detailed movie information for the given ID."""

    def search_movies(
        self,
        *,
        keyword: str = "",
        page: int = 1,
        magnet: str = "",
        movie_type: str = "",
        filter_type: str = "",
        filter_value: str = "",
        extra_params: Optional[JsonDict] = None,
    ) -> JsonDict:
        """Search movies and return a dictionary with movies and pagination info."""

    def get_star(self, star_id: str) -> Optional[JsonDict]:
        """Return detailed information for the given star."""

    def search_stars(self, keyword: str) -> Iterable[JsonDict]:
        """Search stars by keyword."""

    def list_star_movies(
        self,
        star_id: str,
        *,
        page: int = 1,
        extra_params: Optional[JsonDict] = None,
    ) -> JsonDict:
        """Return paginated movies for a given star."""


__all__ = ["JavbusClientProtocol", "JsonDict"]






























