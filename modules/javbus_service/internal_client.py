"""基于 javbus.com 网站爬虫的 JavBus 客户端实现。"""

from __future__ import annotations

import logging
import math
import time
from typing import Dict, Iterable, List, Optional, Tuple

from javbus_db import JavbusDatabase

from .base import JavbusClientProtocol, JsonDict
from .javbus_scraper import JavbusScraper


class InternalJavbusClient(JavbusClientProtocol):
    """直接爬取 javbus.com 网站的 JavBus 客户端。

    设计目标：
    - 直接从 javbus.com 爬取影片数据（搜索、详情、磁力）
    - 数据库仅作为缓存层，优先读缓存
    - 可选回退到外部 API（由配置控制）
    """

    def __init__(
        self,
        config: JsonDict,
        *,
        db: Optional[JavbusDatabase] = None,
        fallback_client: Optional[JavbusClientProtocol] = None,
        logger: Optional[logging.Logger] = None,
        base_url: str = "https://www.javbus.com",
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._db = db
        self._fallback = fallback_client

        internal_cfg = (config.get("internal") if isinstance(config, dict) else {}) or {}
        self._cache_ttl = max(60, int(internal_cfg.get("cache_ttl_seconds", 3600)))
        self._page_size = max(10, int(config.get("page_size", internal_cfg.get("page_size", 30))))
        
        # 初始化爬虫
        timeout = int(internal_cfg.get("timeout", 10))
        self._scraper = JavbusScraper(
            base_url=base_url,
            timeout=timeout,
            logger=self._logger.getChild("Scraper"),
        )

        self._movie_cache: Dict[str, Tuple[float, JsonDict]] = {}
        self._star_cache: Dict[str, Tuple[float, JsonDict]] = {}
        self._search_cache: Dict[Tuple, Tuple[float, JsonDict]] = {}

    # ------------------------------------------------------------------
    # 协议方法实现
    # ------------------------------------------------------------------
    def get_movie(self, movie_id: str, *, params: Optional[JsonDict] = None) -> Optional[JsonDict]:
        params = params or {}
        movie_type = params.get("type", "normal")

        # 1. 检查内存缓存
        cached = self._movie_cache.get(movie_id)
        if cached and not self._is_cache_expired(cached[0]):
            self._logger.info("[获取影片-缓存] %s", movie_id)
            return cached[1]

        # 2. 检查数据库缓存
        if self._db:
            movie_data = self._db.get_movie(movie_id, max_age=self._cache_ttl // 86400 or 1)
            if movie_data and self._is_movie_complete(movie_data):
                self._logger.info("[获取影片-数据库] %s", movie_id)
                self._movie_cache[movie_id] = (time.time(), movie_data)
                return movie_data
            if movie_data and not self._is_movie_complete(movie_data):
                self._logger.info("[获取影片-数据库] 数据不完整，重新爬取 %s", movie_id)

        # 3. 从 javbus.com 爬取详情
        self._logger.info("[获取影片-爬取] %s (type=%s)", movie_id, movie_type)
        scraped_movie = self._scraper.get_movie_detail(movie_id, movie_type=movie_type)
        
        if scraped_movie:
            # 如果有 gid，尝试获取磁力链接
            gid = scraped_movie.get("gid")
            uc = scraped_movie.get("uc", "0")
            detail_url = scraped_movie.get("_detail_url")
            ajax_img = scraped_movie.get("_ajax_img") or scraped_movie.get("img")

            if gid:
                try:
                    magnets = self._scraper.get_magnets(
                        movie_id,
                        gid,
                        uc,
                        img=ajax_img,
                        detail_url=detail_url,
                        movie_type=movie_type,
                    )
                    scraped_movie["magnets"] = magnets
                    self._logger.info("[获取影片-磁力] %s 找到 %d 个", movie_id, len(magnets))
                except Exception as exc:  # pylint: disable=broad-except
                    self._logger.error("[获取影片-磁力失败] %s - %s", movie_id, exc)
                    scraped_movie["magnets"] = []
            else:
                scraped_movie.setdefault("magnets", [])

            # 清理内部字段
            scraped_movie.pop("_detail_url", None)
            scraped_movie.pop("_ajax_img", None)
            scraped_movie.pop("_movie_type", None)

            # 保存到数据库
            if self._db:
                try:
                    self._db.save_movie(scraped_movie)
                    self._logger.info("[获取影片-已保存] %s", movie_id)
                except Exception as save_exc:  # pylint: disable=broad-except
                    self._logger.error("[获取影片-保存失败] %s - %s", movie_id, save_exc)
            
            self._movie_cache[movie_id] = (time.time(), scraped_movie)
            return scraped_movie

        # 4. 如果启用了外部 API 回退
        if self._fallback:
            self._logger.info("[获取影片-回退外部API] %s", movie_id)
            data = self._fallback.get_movie(movie_id, params=params)
            if data:
                if self._db:
                    try:
                        self._db.save_movie(data)
                    except Exception:  # pylint: disable=broad-except
                        pass
                self._movie_cache[movie_id] = (time.time(), data)
                return data

        self._logger.warning("[获取影片-失败] %s", movie_id)
        return None

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
        del magnet, extra_params  # 暂未使用的参数

        cache_key = (keyword or "", page, movie_type or "", filter_type or "", filter_value or "")
        cached = self._search_cache.get(cache_key)
        if cached and not self._is_cache_expired(cached[0]):
            self._logger.info(
                "[搜索-缓存] keyword=%s page=%s type=%s",
                keyword,
                page,
                movie_type,
            )
            return cached[1]

        normalized_type = movie_type or "normal"
        self._logger.info(
            "[搜索-开始] keyword=%s page=%s type=%s filter=%s:%s",
            keyword,
            page,
            normalized_type,
            filter_type,
            filter_value,
        )

        scrape_result: Optional[JsonDict] = None

        try:
            if keyword:
                self._logger.info(
                    "[搜索-爬取] keyword=%s page=%s type=%s",
                    keyword,
                    page,
                    normalized_type,
                )
                scrape_result = self._scraper.search_movies(
                    keyword=keyword,
                    page=page,
                    movie_type=normalized_type,
                )
                if scrape_result is not None:
                    scrape_result["keyword"] = keyword
            else:
                if filter_type == "star" and filter_value:
                    self._logger.info(
                        "[搜索-爬取演员] star=%s page=%s type=%s",
                        filter_value,
                        page,
                        normalized_type,
                    )
                    scrape_result = self._scraper.list_star_movies(
                        filter_value,
                        page=page,
                        movie_type=normalized_type,
                    )
                else:
                    self._logger.info(
                        "[搜索-爬取最新] page=%s type=%s",
                        page,
                        normalized_type,
                    )
                    scrape_result = self._scraper.list_latest_movies(
                        page=page,
                        movie_type=normalized_type,
                    )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.error("[搜索-爬取异常] %s", exc)
            scrape_result = None

        movies_list: list[JsonDict] = []
        pagination_data: Dict = {}
        payload: Dict = {}

        if scrape_result:
            movies_list = scrape_result.get("movies", []) or []
            pagination_data = scrape_result.get("pagination", {}) or {}
            if scrape_result.get("filter"):
                payload["filter"] = scrape_result["filter"]
            if scrape_result.get("keyword"):
                payload["keyword"] = scrape_result["keyword"]

            self._logger.info("[搜索-爬取完成] 找到 %d 部影片", len(movies_list))

            if self._db and movies_list:
                saved_count = 0
                for movie in movies_list:
                    if isinstance(movie, dict) and movie.get("id"):
                        try:
                            self._db.save_movie(movie)
                            saved_count += 1
                        except Exception:  # pylint: disable-broad-except
                            continue
                self._logger.info("[搜索-已缓存] %d/%d 部影片", saved_count, len(movies_list))

        if not movies_list and self._fallback:
            self._logger.info("[搜索-外部API] 回退")
            try:
                fallback_result = self._fallback.search_movies(
                    keyword=keyword,
                    page=page,
                    magnet="",
                    movie_type=normalized_type,
                    filter_type=filter_type,
                    filter_value=filter_value,
                )
                movies_list = fallback_result.get("movies", []) or []
                pagination_data = fallback_result.get("pagination", {}) or {}
                if fallback_result.get("filter"):
                    payload["filter"] = fallback_result["filter"]
                if fallback_result.get("keyword"):
                    payload["keyword"] = fallback_result["keyword"]

                self._logger.info("[搜索-外部API] 返回 %d 条", len(movies_list))

                if self._db and movies_list:
                    for movie in movies_list:
                        if isinstance(movie, dict) and movie.get("id"):
                            try:
                                self._db.save_movie(movie)
                            except Exception:  # pylint: disable-broad-except
                                continue
            except Exception as api_exc:  # pylint: disable-broad-except
                self._logger.error("[搜索-外部API失败] %s", api_exc)

        formatted_movies = [self._format_list_item(movie) for movie in movies_list]

        if pagination_data:
            pagination = {
                "currentPage": pagination_data.get("currentPage", page),
                "hasNextPage": pagination_data.get("hasNextPage", False),
                "nextPage": pagination_data.get("nextPage", page + 1),
                "pages": pagination_data.get("pages", [page]),
            }
        else:
            pagination = self._build_pagination(page, len(formatted_movies))

        result: Dict = {
            "movies": formatted_movies,
            "pagination": pagination,
        }
        if payload.get("filter"):
            result["filter"] = payload["filter"]
        if payload.get("keyword"):
            result["keyword"] = payload["keyword"]

        self._search_cache[cache_key] = (time.time(), result)
        self._logger.info("[搜索-完成] 返回 %d 条", len(formatted_movies))
        return result

    def get_star(self, star_id: str) -> Optional[JsonDict]:
        cached = self._star_cache.get(star_id)
        if cached and not self._is_cache_expired(cached[0]):
            return cached[1]

        star_data = None
        if self._db:
            star_data = self._db.get_star(star_id)

        if star_data:
            self._star_cache[star_id] = (time.time(), star_data)
            return star_data

        # 从 javbus.com 爬取演员详情
        self._logger.info("[获取演员-爬取] %s", star_id)
        scraped_star = self._scraper.get_star_detail(star_id)

        if scraped_star:
            # 保存到数据库
            if self._db:
                try:
                    self._db.save_star(scraped_star)
                    self._logger.info("[获取演员-已保存] %s", star_id)
                except Exception as save_exc:  # pylint: disable=broad-except
                    self._logger.error("[获取演员-保存失败] %s - %s", star_id, save_exc)

            self._star_cache[star_id] = (time.time(), scraped_star)
            return scraped_star

        # 尝试使用 fallback（外部 API）
        if self._fallback:
            star_data = self._fallback.get_star(star_id)
            if star_data and self._db:
                self._db.save_star(star_data)
            if star_data:
                self._star_cache[star_id] = (time.time(), star_data)
            return star_data

        return None

    def search_stars(self, keyword: str) -> Iterable[JsonDict]:
        results: list[JsonDict] = []
        if self._db:
            results = self._db.search_stars(keyword)

        if results:
            for star in results:
                star_id = star.get("id")
                if star_id:
                    self._star_cache[star_id] = (time.time(), star)
            return results

        if self._fallback:
            fallback_results = list(self._fallback.search_stars(keyword))
            if self._db:
                for star in fallback_results:
                    try:
                        self._db.save_star(star)
                    except Exception:  # pylint: disable=broad-except
                        continue
            return fallback_results

        return []

    def list_star_movies(self, star_id: str, *, page: int = 1, extra_params: Optional[JsonDict] = None) -> JsonDict:
        movie_type = "normal"
        if extra_params and isinstance(extra_params, dict):
            movie_type = extra_params.get("type", "normal") or "normal"

        try:
            scrape_result = self._scraper.list_star_movies(
                star_id,
                page=page,
                movie_type=movie_type,
            )
        except Exception as exc:  # pylint: disable-broad-except
            self._logger.error("[list_star_movies] 爬取失败: %s", exc)
            scrape_result = {"movies": [], "pagination": {}}

        movies_list = scrape_result.get("movies", []) or []
        pagination_data = scrape_result.get("pagination", {}) or {}

        if self._db and movies_list:
            for movie in movies_list:
                if isinstance(movie, dict) and movie.get("id"):
                    try:
                        self._db.save_movie(movie)
                    except Exception:  # pylint: disable-broad-except
                        continue

        formatted = [self._format_list_item(movie) for movie in movies_list]
        if pagination_data:
            pagination = {
                "currentPage": pagination_data.get("currentPage", page),
                "hasNextPage": pagination_data.get("hasNextPage", False),
                "nextPage": pagination_data.get("nextPage", page + 1),
                "pages": pagination_data.get("pages", [page]),
            }
        else:
            pagination = self._build_pagination(page, len(formatted))

        result = {"movies": formatted, "pagination": pagination}
        if scrape_result.get("filter"):
            result["filter"] = scrape_result["filter"]
        return result

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _is_cache_expired(self, timestamp: float) -> bool:
        return (time.time() - timestamp) > self._cache_ttl

    def _is_movie_complete(self, movie_data: JsonDict) -> bool:
        if not movie_data:
            return False

        img = movie_data.get("img") or ""
        if not img.startswith("http"):
            return False

        magnets = movie_data.get("magnets")
        if not magnets:
            return False

        return True

    def _format_list_item(self, movie: JsonDict) -> JsonDict:
        return {
            "id": movie.get("id", ""),
            "title": movie.get("title", ""),
            "img": movie.get("img", movie.get("image_url", "")),
            "date": movie.get("date", ""),
            "tags": movie.get("tags", []),
            "translated_title": movie.get("translated_title", ""),
        }

    def _build_pagination(self, current_page: int, total_items: int) -> JsonDict:
        current_page = max(1, current_page)
        total_pages = max(1, math.ceil(total_items / self._page_size)) if total_items else 1
        has_next = current_page < total_pages
        pages = list(range(1, min(total_pages, 10) + 1))
        if total_pages > 10 and current_page > 6:
            start = max(1, current_page - 4)
            end = min(total_pages, start + 9)
            pages = list(range(start, end + 1))

        return {
            "currentPage": current_page,
            "totalPages": total_pages,
            "hasNextPage": has_next,
            "nextPage": current_page + 1 if has_next else current_page,
            "pages": pages,
        }


