#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import json
import logging
from urllib.parse import urljoin, urlencode, quote, unquote
from bs4 import BeautifulSoup

from modules.scrapers.base_scraper import BaseScraper


class FanzaScraper(BaseScraper):
    """FANZA网站爬虫类"""
    
    def __init__(self):
        """初始化FANZA爬虫类"""
        super().__init__()
        
        # 基础URL设置
        self.base_url = "https://www.dmm.co.jp"
        # 添加analyze参数，提高搜索精确度
        self.search_url_template = "https://www.dmm.co.jp/search/=/searchstr={}/analyze=V1EBAwoQAQcGXQ0OXw4C/"
        
        # 详情页URL模板 - 不同类型的商品有不同的URL路径
        self.detail_url_templates = [
            "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid={}/",  # 数字版
            "https://www.dmm.co.jp/mono/dvd/-/detail/=/cid={}/",        # DVD版
            "https://www.dmm.co.jp/digital/videoc/-/detail/=/cid={}/",  # 成人动画
            "https://www.dmm.co.jp/rental/ppr/-/detail/=/cid={}/"       # 租赁版
        ]
        
        # FANZA特有设置
        self.cookies = {
            'age_check_done': '1',  # 年龄确认
            'locale': 'ja'          # 使用日语
        }
        
        self.logger = logging.getLogger('FanzaScraper')
    
    def clean_movie_id(self, movie_id, five_digit=False):
        """标准化影片ID
        
        Args:
            movie_id: 原始影片ID
            five_digit: 是否格式化为5位数字
            
        Returns:
            tuple: (厂商代号, 数字部分, 完整ID)
        """
        # 清理ID，提取字母和数字部分
        movie_id = movie_id.strip()
        match = re.search(r'([a-zA-Z]+)[-_]?(\d+)', movie_id, re.IGNORECASE)
        
        if not match:
            self.logger.warning(f"无法解析影片ID: {movie_id}")
            return None, None, movie_id
        
        # 提取厂商代号和数字部分
        label = match.group(1).upper()
        number = match.group(2)
        
        # 格式化数字部分
        if five_digit and len(number) < 5:
            number = number.zfill(5)  # 填充为5位数字
        
        # 返回标准化结果
        clean_id = f"{label}-{number}"
        
        self.logger.debug(f"清理影片ID: {movie_id} -> {clean_id}")
        return label, number, clean_id
    
    def get_movie_url(self, movie_id):
        """构建影片详情页URL
        
        Args:
            movie_id: 影片ID
            
        Returns:
            str: 详情页URL
        """
        # 标准化影片ID
        label, number, clean_id = self.clean_movie_id(movie_id)
        if not label:
            return None
        
        # 构造可能的商品代码(cid)格式
        possible_cids = [
            f"{label.lower()}00{number}",                 # 标准格式：abc00123
            f"{label.lower()}{number}",                   # 简单格式：abc123
            f"3{label.lower()}{number}",                  # DVD格式：3abc123
            f"33{label.lower()}{number}",                 # 租赁格式：33abc123
            f"33{label.lower()}{number}dod",              # DOD格式：33abc123dod
            f"{label.lower()}{number.zfill(5)}"           # 五位数字格式：abc00123
        ]
        
        # 尝试所有可能的URL组合
        all_urls = []
        for cid in possible_cids:
            for template in self.detail_url_templates:
                all_urls.append(template.format(cid))
        
        self.logger.info(f"构建URL: {movie_id} -> {all_urls[0]}")
        return all_urls[0]  # 返回第一个URL供直接访问尝试
    
    def get_movie_info(self, movie_id):
        """获取影片信息的主函数 - 覆盖基类方法，先搜索，再尝试直接URL
        
        Args:
            movie_id: 影片ID
            
        Returns:
            dict: 影片信息字典 或 None（如果找不到影片）
        """
        self.logger.info(f"获取影片信息: {movie_id}")
        
        # 1. 首先尝试搜索
        self.logger.info(f"尝试搜索: {movie_id}")
        url_list = self.search_movie(movie_id)
        
        if url_list:
            # 获取第一个URL（通常是最匹配的结果）
            url = url_list[0]
            self.logger.info(f"搜索找到详情页URL: {url}")
            
            # 获取详情页内容
            soup = self.get_page(url)
            if soup:
                self.logger.info(f"成功获取详情页，提取信息")
                info = self.extract_info_from_page(soup, movie_id, url)
                if info:
                    return info
        
        # 2. 搜索失败，尝试直接构建URL
        self.logger.info(f"搜索未找到结果，尝试直接访问URL")
        url = self.get_movie_url(movie_id)
        
        if url and self.is_valid_url(url):
            self.logger.info(f"尝试直接访问URL: {url}")
            soup = self.get_page(url)
            
            if soup:
                self.logger.info(f"直接访问成功，提取信息")
                info = self.extract_info_from_page(soup, movie_id, url)
                if info:
                    return info
        
        self.logger.warning(f"未找到影片: {movie_id}")
        return None
    
    def search_movie(self, movie_id):
        """搜索影片
        
        Args:
            movie_id: 影片ID
            
        Returns:
            list: 详情页URL列表
        """
        # 获取清理后的ID
        label, number, clean_id = self.clean_movie_id(movie_id)
        if not label:
            return []
        
        # 尝试多种搜索格式
        search_terms = [
            clean_id,                          # 标准格式：ABC-123
            f"{label}-{number.zfill(5)}",      # 5位数字格式：ABC-00123
            f"{label}{number}",                # 无连字符格式：ABC123
            label + number                     # 无任何分隔：ABC123
        ]
        
        all_urls = []
        for term in search_terms:
            encoded_term = quote(term)
            search_url = self.search_url_template.format(encoded_term)
            
            self.logger.info(f"搜索URL: {search_url}")
            response = self.create_session().get(search_url)
            
            if response.status_code != 200:
                self.logger.warning(f"搜索请求失败，状态码: {response.status_code}")
                continue
                
            # 获取页面内容
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 调试信息
            page_title = soup.title.text if soup.title else "无标题"
            self.logger.info(f"页面标题: {page_title}")
            
            # 提取搜索结果中的详情页链接
            urls = self._extract_links_from_search_page(soup, clean_id)
            
            if urls:
                self.logger.info(f"搜索 '{term}' 找到 {len(urls)} 个结果")
                all_urls.extend(urls)
            else:
                self.logger.info(f"搜索 '{term}' 未找到结果")
                
        # 选择最匹配的URL
        if all_urls:
            return self._find_best_match(all_urls, movie_id)
        
        return []
    
    def _extract_links_from_search_page(self, soup, movie_id):
        """从搜索结果页提取详情页链接
        
        Args:
            soup: BeautifulSoup对象
            movie_id: 影片ID
            
        Returns:
            list: URL列表
        """
        urls = []
        
        # 检查是否直接跳转到详情页
        if "detail" in soup.title.text.lower():
            self.logger.info("搜索已直接跳转到详情页")
            current_url = soup.find("link", rel="canonical")
            if current_url and current_url.get("href"):
                urls.append(current_url.get("href"))
                return urls
            return []
        
        # 查找搜索结果中的所有链接
        product_links = soup.select("p.tmb a")
        if not product_links:
            # 尝试不同的CSS选择器
            product_links = soup.select("div.box-image a")
        
        if not product_links:
            # 尝试通用方法：查找所有包含"cid="的链接
            all_links = soup.find_all("a", href=True)
            product_links = [link for link in all_links if "cid=" in link.get("href")]
        
        # 处理找到的链接
        if product_links:
            for link in product_links:
                href = link.get("href")
                if href and "cid=" in href:
                    # 标准化URL
                    url = href
                    if not url.startswith("http"):
                        url = urljoin(self.base_url, url)
                    urls.append(url)
        
        return urls
    
    def _find_best_match(self, urls, movie_id):
        """从URL列表中找出最匹配的结果
        
        Args:
            urls: URL列表
            movie_id: 影片ID
            
        Returns:
            list: 排序后的URL列表
        """
        # 获取标准化的影片ID
        _, _, clean_id = self.clean_movie_id(movie_id)
        label_part = clean_id.split('-')[0].lower()
        
        # 定义优先级计算函数
        def get_priority(url):
            priority = 0
            
            # 1. 优先考虑数字版（digital/videoa）
            if "digital/videoa" in url:
                priority += 100
            # 2. 其次考虑DVD版
            elif "mono/dvd" in url:
                priority += 50
            # 3. 再次考虑动画
            elif "digital/videoc" in url:
                priority += 30
            # 4. 最后考虑租赁
            elif "rental" in url:
                priority += 10
                
            # 5. 检查URL中是否包含完整影片代号
            cid_match = re.search(r'cid=([^/]+)', url, re.IGNORECASE)
            if cid_match:
                cid = cid_match.group(1)
                # 如果cid包含厂商代号，提高优先级
                if label_part in cid.lower():
                    priority += 5
                    
            return priority
        
        # 按优先级排序
        sorted_urls = sorted(urls, key=get_priority, reverse=True)
        
        # 去除重复URL
        unique_urls = []
        for url in sorted_urls:
            if url not in unique_urls:
                unique_urls.append(url)
                
        return unique_urls
    
    def _convert_to_high_quality_image(self, img_url):
        """将缩略图URL转换为高质量大图URL
        
        Args:
            img_url: 缩略图URL
            
        Returns:
            str: 高质量图片URL
        """
        if not img_url:
            return None
            
        # DMM的图片URL模式：
        # 缩略图: https://pics.dmm.co.jp/digital/video/abc00123/abc00123pt.jpg
        # 封面图: https://pics.dmm.co.jp/digital/video/abc00123/abc00123pl.jpg
        
        # 尝试转换为高质量图片
        high_quality_url = img_url.replace('pt.jpg', 'pl.jpg')
        
        # 如果URL不变，可能有其他格式
        if high_quality_url == img_url:
            # 尝试其他常见格式
            high_quality_url = img_url.replace('ps.jpg', 'pl.jpg')
            
            # 还是没变化，可能是预览图
            if high_quality_url == img_url and 'jp-' in img_url:
                # 预览图转换尝试
                parts = img_url.split('jp-')
                if len(parts) == 2:
                    high_quality_url = parts[0] + 'pl.jpg'
        
        return high_quality_url
    
    def extract_info_from_page(self, soup, movie_id, url):
        """从页面提取影片信息
        
        Args:
            soup: BeautifulSoup对象
            movie_id: 影片ID
            url: 页面URL
            
        Returns:
            dict: 影片信息字典
        """
        if not soup:
            return None
        
        # 检查是否是有效的详情页
        title_tag = soup.find("h1", class_="item-name") or soup.find("h1", id="title")
        if not title_tag:
            self.logger.warning("页面不是有效的影片详情页")
            return None
            
        # 1. 初始化结果字典
        result = {
            "source": "fanza",
            "id": movie_id,
            "url": url
        }
        
        # 2. 提取标题
        title = title_tag.text.strip()
        result["title"] = title
        
        # 3. 提取详情信息表格
        info_table = soup.find("table", class_="mg-b20") or soup.select_one("table.mg-b12")
        
        if not info_table:
            self.logger.warning("未找到信息表格")
            # 保存页面以供调试
            self.save_debug_file(str(soup), f"debug_fanza_{movie_id}.html")
            return None
        
        # 提取表格中的信息
        info_rows = info_table.find_all("tr")
        
        for row in info_rows:
            # 获取标签和值
            label_tag = row.find("td", class_="nw") or row.find("td", width="100")
            if not label_tag:
                continue
                
            label = label_tag.text.strip()
            value_tag = label_tag.find_next("td")
            if not value_tag:
                continue
                
            # 根据标签解析不同类型的信息
            if "商品発売日" in label or "配信開始日" in label or "発売日" in label:
                # 发行日期
                date_text = value_tag.text.strip().replace("/", "-")
                result["release_date"] = date_text
                
            elif "収録時間" in label or "時間" in label:
                # 时长
                duration_text = value_tag.text.strip()
                duration_match = re.search(r'(\d+)', duration_text)
                if duration_match:
                    result["duration"] = duration_match.group(1) + "分钟"
                    
            elif "出演者" in label or "女優" in label:
                # 演员
                actresses = []
                actress_links = value_tag.find_all("a")
                if actress_links:
                    for link in actress_links:
                        name = link.text.strip()
                        if name and name != "：":
                            actresses.append(name)
                else:
                    # 无链接时直接获取文本
                    name = value_tag.text.strip()
                    if name and name != "：" and name != "----":
                        actresses.append(name)
                        
                if actresses:
                    result["actresses"] = actresses
                    
            elif "監督" in label:
                # 导演
                director = value_tag.text.strip()
                if director and director != "----":
                    result["director"] = director
                    
            elif "シリーズ" in label:
                # 系列
                series = value_tag.text.strip()
                if series and series != "----":
                    result["series"] = series
                    
            elif "メーカー" in label:
                # 制作商
                maker = value_tag.text.strip()
                if maker and maker != "----":
                    result["maker"] = maker
                    
            elif "レーベル" in label:
                # 发行商
                label_text = value_tag.text.strip()
                if label_text and label_text != "----":
                    result["label"] = label_text
                    
            elif "品番" in label or "品番：" in label:
                # 品番（产品代码）
                product_code = value_tag.text.strip()
                if product_code and product_code != "----":
                    result["product_code"] = product_code
                    
            elif "ジャンル" in label or "カテゴリ" in label:
                # 类型/标签
                genres = []
                genre_links = value_tag.find_all("a")
                if genre_links:
                    for link in genre_links:
                        genre = link.text.strip()
                        if genre and genre != "：":
                            genres.append(genre)
                else:
                    # 无链接时直接获取文本
                    genre_text = value_tag.text.strip()
                    if genre_text and genre_text != "：" and genre_text != "----":
                        genres = [g.strip() for g in re.split(r'[、,、]', genre_text) if g.strip()]
                        
                if genres:
                    result["genres"] = genres
        
        # 4. 提取封面图
        cover_img = soup.select_one("#sample-video img") or soup.select_one(".item-image img")
        if cover_img:
            img_url = cover_img.get("src") or cover_img.get("data-src")
            if img_url:
                # 转换为高质量图片
                high_quality_url = self._convert_to_high_quality_image(img_url)
                result["cover"] = high_quality_url
                
        # 5. 提取预览图
        thumbnails = []
        thumbnail_links = soup.select("#sample-image-block img") or soup.select(".position-relative.detail-cap a img")
        
        if thumbnail_links:
            for img in thumbnail_links:
                img_url = img.get("src") or img.get("data-src")
                if img_url and not img_url.endswith("noimage.jpg"):
                    # 转换缩略图为大图
                    high_quality_url = self._convert_to_high_quality_image(img_url)
                    thumbnails.append(high_quality_url)
                    
        if thumbnails:
            result["thumbnails"] = thumbnails
            
        # 6. 提取评分
        rating_element = soup.select_one(".d-review__average") or soup.select_one(".c-review__average") or soup.select_one(".c-rating-v2__average")
        if rating_element:
            rating_text = rating_element.text.strip()
            rating_match = re.search(r'([\d\.]+)', rating_text)
            if rating_match:
                result["rating"] = rating_match.group(1)
                
        # 7. 提取简介
        description_element = soup.select_one("#introduction-text") or soup.select_one(".mg-b20.lh4")
        if description_element:
            summary = description_element.text.strip()
            if summary:
                result["summary"] = summary
    
        return result 