"""JavBus 网站爬虫实现。

参考 javbus-api 项目实现，直接爬取 javbus.com 网站数据。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup


class JavbusScraper:
    """JavBus 网站爬虫，负责抓取和解析 javbus.com 的数据。"""

    def __init__(
        self,
        base_url: str = "https://www.javbus.com",
        *,
        timeout: int = 10,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._logger = logger or logging.getLogger(__name__)
        
        # 模拟浏览器请求头，参考 javbus-api
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": self._base_url,
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        
        self._session = requests.Session()
        self._session.headers.update(self._headers)
        
        # 请求间隔，避免过快请求
        self._last_request_time = 0.0
        self._min_interval = 1.0  # 最小请求间隔（秒）

    def _rate_limit(self) -> None:
        """限制请求频率。"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def _get_page(self, url: str) -> Optional[BeautifulSoup]:
        """获取页面并解析为 BeautifulSoup 对象。"""
        self._rate_limit()
        
        try:
            self._logger.debug("[爬虫-请求] %s", url)
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
            self._logger.debug(
                "[爬虫-响应] status=%s length=%s", response.status_code, len(response.text)
            )
            self._logger.debug(
                "[爬虫-响应片段] %s",
                (response.text[:200] + "...") if len(response.text) > 200 else response.text
            )
            
            soup = BeautifulSoup(response.text, "html.parser")
            return soup
        except requests.RequestException as exc:
            self._logger.error("[爬虫-失败] %s - %s", url, exc)
            return None

    def _absolute_url(self, value: str) -> str:
        """将相对路径转换为绝对 URL。"""
        if not value:
            return value
        if value.startswith("http://") or value.startswith("https://"):
            return value
        # javbus 有些图片使用 // 开头
        if value.startswith("//"):
            parsed_base = urlparse(self._base_url)
            return f"{parsed_base.scheme}:{value}"
        return urljoin(self._base_url + "/", value.lstrip("/"))

    def search_movies(
        self,
        keyword: str,
        *,
        page: int = 1,
        movie_type: str = "normal",
    ) -> Dict:
        """搜索影片。
        
        Args:
            keyword: 搜索关键词
            page: 页码
            movie_type: 影片类型 (normal/uncensored)
        
        Returns:
            包含 movies 和 pagination 的字典
        """
        # 构建搜索 URL
        if movie_type == "uncensored":
            search_url = f"{self._base_url}/uncensored/search/{keyword}/{page}"
        else:
            search_url = f"{self._base_url}/search/{keyword}/{page}"
        
        self._logger.info("[爬虫-搜索] keyword=%s page=%s type=%s", keyword, page, movie_type)
        
        soup = self._get_page(search_url)
        if not soup:
            return {"movies": [], "pagination": {}}
        
        # 解析影片列表
        movies = self._parse_movie_list(soup)
        
        # 解析分页信息
        pagination = self._parse_pagination(soup, page)
        
        self._logger.info("[爬虫-搜索完成] 找到 %d 部影片", len(movies))
        return {"movies": movies, "pagination": pagination}

    def list_latest_movies(
        self,
        *,
        page: int = 1,
        movie_type: str = "normal",
    ) -> Dict:
        """获取最新影片列表。"""

        if movie_type == "uncensored":
            if page <= 1:
                list_url = f"{self._base_url}/uncensored"
            else:
                list_url = f"{self._base_url}/uncensored/page/{page}"
        else:
            if page <= 1:
                list_url = self._base_url
            else:
                list_url = f"{self._base_url}/page/{page}"

        self._logger.info("[爬虫-最新] page=%s type=%s", page, movie_type)
        soup = self._get_page(list_url)
        if not soup:
            return {"movies": [], "pagination": {}}

        movies = self._parse_movie_list(soup)
        pagination = self._parse_pagination(soup, page)

        self._logger.info("[爬虫-最新完成] 找到 %d 部影片", len(movies))
        return {"movies": movies, "pagination": pagination}

    def list_star_movies(
        self,
        star_id: str,
        *,
        page: int = 1,
        movie_type: str = "normal",
    ) -> Dict:
        """获取演员影片列表。"""

        if movie_type == "uncensored":
            prefix = f"{self._base_url}/uncensored"
        else:
            prefix = self._base_url

        if page <= 1:
            list_url = f"{prefix}/star/{star_id}"
        else:
            list_url = f"{prefix}/star/{star_id}/{page}"

        self._logger.info("[爬虫-演员] star=%s page=%s type=%s", star_id, page, movie_type)
        soup = self._get_page(list_url)
        if not soup:
            return {"movies": [], "pagination": {}}

        movies = self._parse_movie_list(soup)
        pagination = self._parse_pagination(soup, page)

        star_name = ""
        title_tag = soup.find("title")
        if title_tag:
            star_name = title_tag.get_text(strip=True).split("|")[0].strip()
        if not star_name:
            name_candidate = soup.select_one(".photo-info h3") or soup.select_one(".star-name a")
            if name_candidate:
                star_name = name_candidate.get_text(strip=True)

        filter_info = {
            "type": "star",
            "value": star_id,
            "name": star_name,
        }

        self._logger.info("[爬虫-演员完成] star=%s 找到 %d 部影片", star_id, len(movies))
        return {"movies": movies, "pagination": pagination, "filter": filter_info}

    def get_movie_detail(self, movie_id: str, *, movie_type: str = "normal") -> Optional[Dict]:
        """获取影片详情。
        
        Args:
            movie_id: 影片番号
            movie_type: 影片类型 (normal/uncensored)
        
        Returns:
            影片详情字典
        """
        # 注意：无码影片的详情页URL也是 https://www.javbus.com/{movie_id}
        # 不需要添加 /uncensored/ 路径，只有搜索和列表页面需要
        detail_url = f"{self._base_url}/{movie_id}"
        
        self._logger.info("[爬虫-详情] movie_id=%s type=%s", movie_id, movie_type)
        
        soup = self._get_page(detail_url)
        if not soup:
            return None
        
        # 解析影片详情
        movie = self._parse_movie_detail(soup, movie_id)
        
        if movie:
            self._logger.info("[爬虫-详情完成] %s", movie_id)
            movie.setdefault("_detail_url", detail_url)
            movie.setdefault("_movie_type", movie_type)
        else:
            self._logger.warning("[爬虫-详情失败] %s", movie_id)
        
        return movie

    def get_magnets(
        self,
        movie_id: str,
        gid: str,
        uc: str = "0",
        *,
        img: Optional[str] = None,
        detail_url: Optional[str] = None,
        movie_type: str = "normal",
    ) -> List[Dict]:
        """获取影片磁力链接。
        
        Args:
            movie_id: 影片番号
            gid: 从详情页获取的 gid
            uc: 从详情页获取的 uc
        
        Returns:
            磁力链接列表
        """
        ajax_url = f"{self._base_url}/ajax/uncledatoolsbyajax.php"

        self._rate_limit()

        try:
            params = {
                "gid": gid,
                "lang": "zh",
                "img": img or movie_id,
                "uc": uc,
                "floor": str(int(time.time() * 1000)),
            }

            headers = {
                "Referer": detail_url or f"{self._base_url}/{movie_id}",
                "X-Requested-With": "XMLHttpRequest",
            }

            self._logger.info(
                "[爬虫-磁力] movie_id=%s gid=%s img=%s",
                movie_id,
                gid,
                params.get("img"),
            )

            response = self._session.get(
                ajax_url,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            self._logger.debug(
                "[爬虫-磁力响应] status=%s length=%s",
                response.status_code,
                len(response.text),
            )

            soup = BeautifulSoup(response.text, "html.parser")
            magnets = self._parse_magnets(soup)

            self._logger.info("[爬虫-磁力完成] 找到 %d 个链接", len(magnets))
            return magnets

        except requests.RequestException as exc:
            self._logger.error("[爬虫-磁力失败] %s - %s", movie_id, exc)
            return []

    def _parse_movie_list(self, soup: BeautifulSoup) -> List[Dict]:
        """解析影片列表页面。"""
        movies = []
        
        # 查找所有影片项
        movie_items = soup.select("#waterfall #waterfall .item")
        if not movie_items:
            movie_items = soup.select("#waterfall .item")

        for item in movie_items:
            try:
                link_tag = item.select_one("a.movie-box") or item.find("a")
                if not link_tag:
                    continue

                # 解析封面图
                img_tag = item.select_one(".photo-frame img")
                img_url = ""
                if img_tag:
                    for attr in ("data-src", "data-original", "data-echo", "src"):
                        candidate = (img_tag.get(attr) or "").strip()
                        if not candidate:
                            continue
                        if "loading" in candidate or "blank" in candidate:
                            continue
                        img_url = candidate
                        break
                img_url = self._absolute_url(img_url)

                # 解析影片编号与日期
                code = ""
                release_date = ""
                info_dates = item.select(".photo-info date")
                if info_dates:
                    code = info_dates[0].get_text(strip=True)
                    if len(info_dates) > 1:
                        release_date = info_dates[1].get_text(strip=True)

                movie_url = link_tag.get("href", "").strip()
                fallback_id = movie_url.rstrip("/").split("/")[-1]
                movie_id = code or fallback_id

                # 解析标题
                title = (img_tag.get("title") if img_tag else "") or ""
                if not title:
                    title = item.select_one(".photo-info span")
                    if title:
                        title = title.get_text(strip=True)
                if not title:
                    title = movie_id

                # 解析标签
                tags = [btn.get_text(strip=True) for btn in item.select(".item-tag button") if btn.get_text(strip=True)]

                movies.append({
                    "id": movie_id,
                    "title": title,
                    "img": img_url,
                    "date": release_date,
                    "tags": tags,
                })

            except Exception as exc:  # pylint: disable=broad-except
                self._logger.debug("[爬虫-解析影片项失败] %s", exc)
                continue
        
        return movies

    def _parse_pagination(self, soup: BeautifulSoup, current_page: int) -> Dict:
        """解析分页信息。"""
        pagination = {
            "currentPage": current_page,
            "hasNextPage": False,
            "nextPage": current_page,
            "pages": [current_page],
        }
        
        try:
            # 查找分页元素
            page_selector = soup.find("ul", class_="pagination")
            if not page_selector:
                return pagination
            
            page_links = page_selector.find_all("a")
            pages = []
            
            for link in page_links:
                href = link.get("href", "")
                # 提取页码
                match = re.search(r"/(\d+)(?:\?|$)", href)
                if match:
                    page_num = int(match.group(1))
                    if page_num not in pages:
                        pages.append(page_num)
            
            if pages:
                pages.sort()
                pagination["pages"] = pages
                
                # 检查是否有下一页
                if current_page < max(pages):
                    pagination["hasNextPage"] = True
                    pagination["nextPage"] = current_page + 1
        
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.debug("[爬虫-解析分页失败] %s", exc)
        
        return pagination

    def _parse_movie_detail(self, soup: BeautifulSoup, movie_id: str) -> Optional[Dict]:
        """解析影片详情页面。"""
        try:
            movie: Dict[str, any] = {"id": movie_id}
            
            # 标题
            title_tag = soup.find("h3")
            raw_title = title_tag.get_text(strip=True) if title_tag else ""
            if raw_title and not raw_title.upper().startswith(movie_id.upper()):
                movie["title"] = f"{movie_id} {raw_title}"
            else:
                movie["title"] = raw_title or movie_id
            
            # 封面大图
            cover_tag = soup.find("a", class_="bigImage")
            if cover_tag:
                cover_url = cover_tag.get("href", "")
                movie["img"] = self._absolute_url(cover_url)
                img_tag = cover_tag.find("img")
                if img_tag:
                    width = img_tag.get("width")
                    height = img_tag.get("height")
                    if width and height:
                        try:
                            movie["imageSize"] = {
                                "width": int(width),
                                "height": int(height),
                            }
                        except ValueError:
                            pass
            
            # 基本信息（在 .info 区域）
            info_section = soup.find("div", class_="info")
            if info_section:
                # 发行日期
                for p_tag in info_section.find_all("p"):
                    p_text = p_tag.get_text()
                    if "發行日期:" in p_text:
                        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", p_text)
                        if date_match:
                            movie["date"] = date_match.group(1)
                        break
                
                # 时长
                for p_tag in info_section.find_all("p"):
                    p_text = p_tag.get_text()
                    if "長度:" in p_text:
                        length_match = re.search(r"(\d+)", p_text)
                        if length_match:
                            movie["videoLength"] = int(length_match.group(1))
                        break
                
                # 导演
                movie["director"] = None
                for p_tag in info_section.find_all("p"):
                    if "導演:" in p_tag.get_text():
                        director_link = p_tag.find("a")
                        if director_link:
                            movie["director"] = {
                                "id": director_link.get("href", "").split("/")[-1],
                                "name": director_link.get_text(strip=True),
                            }
                        break
                
                # 制作商
                movie["producer"] = None
                for p_tag in info_section.find_all("p"):
                    if "製作商:" in p_tag.get_text():
                        maker_link = p_tag.find("a")
                        if maker_link:
                            movie["producer"] = {
                                "id": maker_link.get("href", "").split("/")[-1],
                                "name": maker_link.get_text(strip=True),
                            }
                        break
                
                # 发行商
                movie["publisher"] = None
                for p_tag in info_section.find_all("p"):
                    if "發行商:" in p_tag.get_text():
                        publisher_link = p_tag.find("a")
                        if publisher_link:
                            movie["publisher"] = {
                                "id": publisher_link.get("href", "").split("/")[-1],
                                "name": publisher_link.get_text(strip=True),
                            }
                        break
                
                # 系列
                movie["series"] = None
                for p_tag in info_section.find_all("p"):
                    if "系列:" in p_tag.get_text():
                        series_link = p_tag.find("a")
                        if series_link:
                            movie["series"] = {
                                "id": series_link.get("href", "").split("/")[-1],
                                "name": series_link.get_text(strip=True),
                            }
                        break
                
                # 类别（genres）
                genre_tags = info_section.find_all("span", class_="genre")
                genres = []
                for genre_tag in genre_tags:
                    genre_link = genre_tag.find("a")
                    if genre_link:
                        genres.append({
                            "id": genre_link.get("href", "").split("/")[-1],
                            "name": genre_link.get_text(strip=True),
                        })
                movie["genres"] = genres
                
                # 演员
                star_tags = info_section.find_all("div", class_="star-name")
                stars = []
                for star_tag in star_tags:
                    star_link = star_tag.find("a")
                    if star_link:
                        stars.append({
                            "id": star_link.get("href", "").split("/")[-1],
                            "name": star_link.get_text(strip=True),
                        })
                movie["stars"] = stars
            else:
                movie["genres"] = []
                movie["stars"] = []
            
            # 预览图
            samples = []
            # 查找所有 sample-box 元素（包括 <a> 和 <span>）
            sample_images = soup.select("#sample-waterfall .sample-box")
            for idx, sample_box in enumerate(sample_images, 1):
                # 获取大图URL：从 <a href> 获取（可能是 DMM 大图或 javbus bigsample）
                # 如果是 <span> 包裹的，则没有 href，大图URL为 null
                big_src_url = None
                if sample_box.name == "a":
                    href = sample_box.get("href", "")
                    if href:
                        # 可能是 DMM 大图（绝对URL）或 javbus bigsample（相对路径）
                        big_src_url = href
                
                # 获取缩略图URL：从 <img src> 获取
                img_tag = sample_box.find("img")
                javbus_thumb_url = ""
                if img_tag:
                    javbus_thumb_url = img_tag.get("src") or img_tag.get("data-src") or ""
                
                # 从缩略图路径提取 id (如 1z2f_1 或 brvw_1)
                sample_id = ""
                if javbus_thumb_url:
                    # URL 类似 /pics/sample/brvw_1.jpg 或 /imgs/sample/1z2f_1.jpg
                    parts = javbus_thumb_url.rstrip("/").split("/")
                    if parts:
                        filename = parts[-1]
                        sample_id = filename.rsplit(".", 1)[0] if "." in filename else filename
                
                if not sample_id:
                    sample_id = f"{movie_id}_{idx}"
                
                # 转换URL为绝对URL
                thumbnail_url = self._absolute_url(javbus_thumb_url) if javbus_thumb_url else ""
                src_url = self._absolute_url(big_src_url) if big_src_url else None
                
                samples.append({
                    "id": sample_id,
                    "thumbnail": thumbnail_url,
                    "src": src_url,  # 如果没有大图则为 null
                    "alt": f"{movie['title']} - 樣品圖像 - {idx}",
                })
            movie["samples"] = samples
            
            # 获取 gid 和 uc（用于获取磁力链接）
            script_tags = soup.find_all("script")
            for script in script_tags:
                script_text = script.string or ""

                gid_match = re.search(r"var\s+gid\s*=\s*(\d+)", script_text)
                if gid_match:
                    movie["gid"] = gid_match.group(1)

                uc_match = re.search(r"var\s+uc\s*=\s*(\d+)", script_text)
                if uc_match:
                    movie["uc"] = uc_match.group(1)

                img_match = re.search(r"var\s+img\s*=\s*['\"]([^'\"]+)['\"]", script_text)
                if img_match:
                    movie.setdefault("_ajax_img", img_match.group(1))

            movie.setdefault("magnets", [])

            return movie
            
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.error("[爬虫-解析详情失败] %s - %s", movie_id, exc)
            return None

    def _parse_magnets(self, soup: BeautifulSoup) -> List[Dict]:
        """解析磁力链接。"""
        magnets = []
        
        try:
            rows = soup.find_all("tr")
            
            for row in rows:
                try:
                    # 获取链接
                    link_tag = row.find("a", href=re.compile(r"^magnet:"))
                    if not link_tag:
                        continue
                    
                    magnet_link = link_tag.get("href", "")
                    
                    # 提取 btih
                    btih_match = re.search(r"btih:([A-F0-9]+)", magnet_link, re.IGNORECASE)
                    magnet_id = btih_match.group(1).lower() if btih_match else ""
                    
                    # 从链接中提取标题（通常是番号）
                    title = ""
                    dn_match = re.search(r"[&?]dn=([^&]+)", magnet_link)
                    if dn_match:
                        title = unquote(dn_match.group(1))
                    
                    # 判断是否高清 / 字幕 标记
                    td_tags = row.find_all("td")
                    is_hd = False
                    has_subtitle = False
                    for td in td_tags:
                        td_text = td.get_text(strip=True)
                        if "高清" in td_text or "HD" in td_text.upper():
                            is_hd = True
                        if "字幕" in td_text or "中文" in td_text:
                            has_subtitle = True
                        icon_hd = td.find("span", class_=re.compile(r"hd", re.IGNORECASE))
                        icon_sub = td.find("span", class_=re.compile(r"sub", re.IGNORECASE))
                        if icon_hd:
                            is_hd = True
                        if icon_sub:
                            has_subtitle = True
                    
                    # 获取大小和日期
                    size = ""
                    share_date = ""
                    if len(td_tags) >= 2:
                        size = td_tags[1].get_text(strip=True)
                        if not size:
                            size_link = td_tags[1].find("a", title="檔案大小")
                            size = size_link.get_text(strip=True) if size_link else ""
                    if len(td_tags) >= 3:
                        share_date = td_tags[2].get_text(strip=True)
                        if not share_date:
                            date_link = td_tags[2].find("a", title="上傳日期")
                            share_date = date_link.get_text(strip=True) if date_link else ""
                    
                    # 解析 numberSize（字节数）
                    number_size = 0
                    if size:
                        try:
                            size_match = re.match(r"([\d.]+)\s*([KMGT]?B)", size, re.IGNORECASE)
                            if not size_match:
                                size_match = re.match(r"([\d.]+)\s*([KMGT]iB)", size, re.IGNORECASE)
                            if size_match:
                                num = float(size_match.group(1))
                                unit = size_match.group(2).upper().replace("IB", "B")
                                multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
                                number_size = int(num * multipliers.get(unit, 1))
                        except (ValueError, KeyError):
                            number_size = 0
                    
                    # 统一日期格式（优先提取 YYYY-MM-DD）
                    if share_date:
                        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", share_date)
                        share_date = date_match.group(1) if date_match else share_date
                     
                    magnets.append({
                        "id": magnet_id,
                        "link": magnet_link,
                        "isHD": is_hd,
                        "title": title,
                        "size": size,
                        "numberSize": number_size,
                        "shareDate": share_date,
                        "hasSubtitle": has_subtitle,
                    })
                    
                except Exception as exc:  # pylint: disable=broad-except
                    self._logger.debug("[爬虫-解析磁力项失败] %s", exc)
                    continue
        
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.error("[爬虫-解析磁力列表失败] %s", exc)

        return magnets

    def get_star_detail(self, star_id: str) -> Optional[Dict]:
        """获取演员详情。

        Args:
            star_id: 演员 ID

        Returns:
            演员详情字典，包含 id, name, avatar, birthday, age, height, bust, waistline, hipline, birthplace, hobby
        """
        # 构建演员详情页 URL
        detail_url = f"{self._base_url}/star/{star_id}"

        self._logger.info("[爬虫-演员详情] star_id=%s", star_id)

        soup = self._get_page(detail_url)
        if not soup:
            self._logger.warning("[爬虫-演员详情失败] %s - 页面获取失败", star_id)
            return None

        try:
            star_data: Dict[str, any] = {"id": star_id}

            # 查找演员信息容器
            avatar_box = soup.select_one(".avatar-box") or soup.select_one(".photo-frame")
            if not avatar_box:
                self._logger.warning("[爬虫-演员详情失败] %s - 未找到演员信息容器", star_id)
                return None

            # 获取头像 URL
            # 格式: https://www.javbus.com/pics/actress/{star_id}_a.jpg
            avatar_url = f"{self._base_url}/pics/actress/{star_id}_a.jpg"
            star_data["avatar"] = avatar_url

            # 获取姓名
            name_tag = soup.select_one(".photo-info span.pb10")
            if name_tag:
                star_data["name"] = name_tag.get_text(strip=True)
            else:
                # 尝试从头像的 title 属性获取
                img_tag = soup.select_one(".photo-frame img")
                if img_tag:
                    star_data["name"] = img_tag.get("title", "").strip()
                else:
                    star_data["name"] = star_id

            # 解析个人信息
            info_lines = soup.select(".photo-info p")

            # 字段映射：中文关键词 -> 数据字段
            field_mapping = {
                "生日": "birthday",
                "年齡": "age",
                "身高": "height",
                "罩杯": "cup",
                "胸圍": "bust",
                "腰圍": "waistline",
                "臀圍": "hipline",
                "出生地": "birthplace",
                "愛好": "hobby",
            }

            for p_tag in info_lines:
                text = p_tag.get_text(strip=True)
                if not text or ":" not in text:
                    continue

                # 分割键值对
                parts = text.split(":", 1)
                if len(parts) != 2:
                    continue

                key = parts[0].strip()
                value = parts[1].strip()

                # 查找对应的字段
                for chinese_key, field_name in field_mapping.items():
                    if key == chinese_key or key in chinese_key:
                        star_data[field_name] = value
                        break

            self._logger.info("[爬虫-演员详情完成] %s - %s", star_id, star_data.get("name", star_id))
            return star_data

        except Exception as exc:
            self._logger.error("[爬虫-演员详情失败] %s - %s", star_id, exc)
            return None

