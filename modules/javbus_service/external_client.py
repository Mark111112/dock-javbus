"""基于现有远程 API 的 JavBus 客户端实现。"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

import requests

from .base import JavbusClientProtocol, JsonDict


class ExternalJavbusClient(JavbusClientProtocol):
    """通过 HTTP 调用既有 javbus-api 服务。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: int = 10,
        session: Optional[requests.Session] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session or requests.Session()
        self._logger = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # 协议方法实现
    # ------------------------------------------------------------------
    def get_movie(self, movie_id: str, *, params: Optional[JsonDict] = None) -> Optional[JsonDict]:
        endpoint = f"movies/{movie_id}"
        return self._request_json(endpoint, params=params)

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
        params: JsonDict = {"page": page}

        if keyword:
            params["keyword"] = keyword
        if magnet:
            params["magnet"] = magnet
        if movie_type:
            params["type"] = movie_type
        if filter_type and filter_value:
            params["filterType"] = filter_type
            params["filterValue"] = filter_value

        if extra_params:
            params.update(extra_params)

        endpoint = "movies/search" if keyword else "movies"
        data = self._request_json(endpoint, params=params) or {}
        return {
            "movies": data.get("movies", []),
            "pagination": data.get("pagination", {}),
        }

    def get_star(self, star_id: str) -> Optional[JsonDict]:
        endpoint = f"stars/{star_id}"
        return self._request_json(endpoint)

    def search_stars(self, keyword: str) -> Iterable[JsonDict]:
        data = self._request_json("stars/search", params={"keyword": keyword}) or {}
        stars = data.get("stars", [])
        if isinstance(stars, list):
            return stars
        return []

    def list_star_movies(self, star_id: str, *, page: int = 1, extra_params: Optional[JsonDict] = None) -> JsonDict:
        params: JsonDict = {
            "filterType": "star",
            "filterValue": star_id,
            "page": page,
            "magnet": "all",
        }
        if extra_params:
            params.update(extra_params)

        data = self._request_json("movies", params=params) or {}
        return {
            "movies": data.get("movies", []),
            "pagination": data.get("pagination", {}),
        }

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------
    def _request_json(self, endpoint: str, *, params: Optional[JsonDict] = None) -> Optional[JsonDict]:
        url = f"{self._base_url}/{endpoint.lstrip('/') }"
        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
            if response.status_code == 200:
                return response.json()

            self._logger.warning(
                "JavBus API 请求失败: %s %s (HTTP %s)",
                url,
                params or {},
                response.status_code,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.error("JavBus API 请求异常: %s", exc)
        return None
































