#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import json
import logging
from urllib.parse import urljoin, urlencode, quote, unquote
from bs4 import BeautifulSoup

from modules.scrapers.base_scraper import BaseScraper


class CaribbeanScraper(BaseScraper):
    """Caribbean网站爬虫类"""
    
    def __init__(self):
        """初始化Caribbean爬虫类"""
        super().__init__()
        
        # 基础URL设置
        self.base_url = "https://www.caribbeancom.com"
        # 搜索URL模板
        self.search_url_template = "https://www.caribbeancom.com/moviepages/search.html?keyword={}"
        
        # 详情页URL模板
        self.detail_url_template = "https://www.caribbeancom.com/moviepages/{}/index.html"
        
        # Cookies设置
        self.cookies = {
            'age_check_done': '1',  # 年龄确认
            'lang': 'ja'            # 使用日语
        }
        
        self.logger = logging.getLogger('CaribbeanScraper')
    
    def clean_movie_id(self, movie_id, five_digit=False):
        """标准化影片ID
        
        Args:
            movie_id: 原始影片ID (例如 Carib-123456-789, 123456-789, caribbeancom-123456-789)
            five_digit: 不适用于Caribbean，保留参数以兼容接口
            
        Returns:
            tuple: (厂商代号, 数字部分, 完整ID)
        """
        # 清理ID，提取数字部分
        movie_id = movie_id.strip().upper()
        
        # 尝试提取数字部分，Caribbean的格式通常是6位数-3位数 (例如 123456-789)
        match = re.search(r'(?:CARIBBEANCOM[-_]?|CARIBBEAN[-_]?|CARIB[-_]?)?(\d{6})[-_]?(\d{3})', movie_id, re.IGNORECASE)
        
        if not match:
            self.logger.warning(f"无法解析影片ID: {movie_id}")
            return None, None, movie_id
        
        # 提取数字部分
        number_part1 = match.group(1)
        number_part2 = match.group(2)
        number = f"{number_part1}-{number_part2}"
        
        # 标准化ID格式
        clean_id = f"CARIBBEANCOM-{number}"
        
        self.logger.debug(f"清理影片ID: {movie_id} -> {clean_id}")
        return "CARIBBEANCOM", number, clean_id
    
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
        
        # Caribbean的URL格式需要保留连字符
        # 例如：123456-789 格式的ID对应的URL是 /moviepages/123456-789/index.html
        url = self.detail_url_template.format(number)
        
        self.logger.info(f"构建URL: {movie_id} -> {url}")
        return url
    
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
        
        # 先尝试直接通过ID访问
        direct_url = self.get_movie_url(movie_id)
        
        # 准备搜索关键词
        search_terms = [
            number,               # 完整编号(带连字符) 例如 123456-789
            number.replace('-', '') # 不带连字符的编号 例如 123456789
        ]
        
        all_urls = []
        if direct_url:
            all_urls.append(direct_url)
        
        for term in search_terms:
            encoded_term = quote(term)
            search_url = self.search_url_template.format(encoded_term)
            
            self.logger.info(f"搜索URL: {search_url}")
            soup = self.get_page(search_url)
            
            if not soup:
                continue
                
            # 提取搜索结果中的详情页链接
            urls = self._extract_links_from_search_page(soup, number)
            
            if urls:
                self.logger.info(f"搜索 '{term}' 找到 {len(urls)} 个结果")
                all_urls.extend(urls)
            else:
                self.logger.info(f"搜索 '{term}' 未找到结果")
        
        # 移除重复URL并找出最匹配的URL
        unique_urls = list(dict.fromkeys(all_urls))
        
        if unique_urls:
            best_url = self._find_best_match(unique_urls, movie_id)
            if best_url:
                return [best_url] + [url for url in unique_urls if url != best_url]
        
        return unique_urls
    
    def _extract_links_from_search_page(self, soup, movie_id):
        """从搜索结果页提取详情页链接
        
        Args:
            soup: 搜索页面BeautifulSoup对象
            movie_id: 影片ID
            
        Returns:
            list: 详情页URL列表
        """
        urls = []
        
        # 查找所有影片链接
        movie_links = soup.select('a[href*="/moviepages/"]')
        for link in movie_links:
            href = link.get('href', '')
            if href and '/moviepages/' in href and '/index.html' in href:
                full_url = urljoin(self.base_url, href)
                urls.append(full_url)
                self.logger.info(f"从搜索结果中找到链接: {full_url}")
        
        # 去重
        unique_urls = list(dict.fromkeys(urls))
        self.logger.info(f"共找到 {len(unique_urls)} 个不重复的详情页链接")
        return unique_urls
    
    def _find_best_match(self, urls, movie_id):
        """从URL列表中找出最匹配的URL
        
        Args:
            urls: URL列表
            movie_id: 影片ID
            
        Returns:
            str: 最匹配的URL或None
        """
        if not urls:
            return None
            
        # 标准化影片ID
        label, number, clean_id = self.clean_movie_id(movie_id)
        if not label:
            return urls[0]
        
        # 对于Caribbean，直接比较包含连字符的数字部分
        for url in urls:
            # 检查URL中是否包含正确的数字ID
            url_match = re.search(r'/moviepages/([\d-]+)/index\.html', url)
            if url_match and url_match.group(1) == number:
                self.logger.info(f"找到精确匹配URL: {url}")
                return url
        
        # 如果没有找到精确匹配，返回第一个URL
        return urls[0]
    
    def extract_info_from_page(self, soup, movie_id, url):
        """从详情页提取影片信息
        
        Args:
            soup: 详情页BeautifulSoup对象
            movie_id: 原始影片ID
            url: 详情页URL
            
        Returns:
            dict: 影片信息字典
        """
        if not soup:
            return None
        
        # 初始化结果字典
        info = {
            'movie_id': movie_id,
            'url': url,
            'source': 'caribbean'
        }
        
        try:
            # 从URL中提取Caribbean编号，优先使用带连字符的格式
            url_match = re.search(r'/moviepages/([\d-]+)/index\.html', url)
            if url_match:
                carib_id = url_match.group(1)
                
                # 如果URL中已包含连字符，直接使用
                if '-' in carib_id:
                    info['carib_id'] = carib_id
                else:
                    # 否则将数字格式化为6位数-3位数格式
                    formatted_id = f"{carib_id[:6]}-{carib_id[6:]}"
                    info['carib_id'] = formatted_id
            
            # 提取标题 - 通常在h1标签中
            title_elem = soup.select_one('h1.heading') or soup.select_one('h1')
            if title_elem:
                info['title'] = title_elem.text.strip()
            
            # 提取各种详细信息
            # 发行日期
            date_elem = soup.select_one('li.movie-spec:contains("配信日") span') or soup.select_one('.movie-info span[itemprop="datePublished"]')
            if date_elem:
                info['release_date'] = date_elem.text.strip()
            
            # 演员
            actors = []
            actor_elems = soup.select('span[itemprop="actors"] a') or soup.select('.movie-info a[itemprop="actor"]')
            for actor in actor_elems:
                actors.append(actor.text.strip())
            if actors:
                info['actors'] = actors
            
            # 系列
            series_elem = soup.select_one('li.movie-spec:contains("シリーズ") span a') or soup.select_one('.movie-info a[href*="series"]')
            if series_elem:
                info['series'] = series_elem.text.strip()
            
            # 类别/标签
            genres = []
            genre_elems = soup.select('li.movie-spec:contains("タグ") span a') or soup.select('.movie-info a[href*="genres"]')
            for genre in genre_elems:
                genres.append(genre.text.strip())
            if genres:
                info['genres'] = genres
            
            # 影片时长
            duration_elem = soup.select_one('li.movie-spec:contains("再生時間") span') or soup.select_one('.movie-info span[itemprop="duration"]')
            if duration_elem:
                duration_text = duration_elem.text.strip()
                # 提取分钟数
                duration_match = re.search(r'(\d+)分', duration_text)
                if duration_match:
                    info['duration'] = int(duration_match.group(1))
            
            # 影片简介
            summary_elem = soup.select_one('.movie-comment') or soup.select_one('.movie-info p[itemprop="description"]')
            if summary_elem:
                info['summary'] = summary_elem.text.strip()
                info['summary_source'] = 'html'
            
            # 封面图片
            cover_url = self._get_preview_image_url(url)
            if cover_url:
                info['cover_url'] = cover_url
            
            # 缩略图
            thumbnails = self._extract_thumbnails(soup, url)
            if thumbnails:
                info['thumbnails'] = thumbnails
            
            return info
            
        except Exception as e:
            self.logger.error(f"提取信息时出错: {str(e)}")
            return None
    
    def _get_preview_image_url(self, url):
        """获取预览图片URL
        
        Args:
            url: 详情页URL
            
        Returns:
            str: 预览图片URL
        """
        # 从详情页URL提取影片ID，保留连字符
        match = re.search(r'/moviepages/([\d-]+)/index\.html', url)
        if not match:
            return None
        
        movie_id = match.group(1)
        
        # 构造预览图片URL
        preview_url = f"https://www.caribbeancom.com/moviepages/{movie_id}/images/l_l.jpg"
        return preview_url
    
    def _extract_thumbnails(self, soup, url):
        """提取缩略图
        
        Args:
            soup: 详情页BeautifulSoup对象
            url: 详情页URL
            
        Returns:
            list: 缩略图URL列表
        """
        thumbnails = []
        
        # 从URL提取影片ID，保留连字符
        match = re.search(r'/moviepages/([\d-]+)/index\.html', url)
        if not match:
            return thumbnails
        
        movie_id = match.group(1)
        
        # Caribbean缩略图格式: 001.jpg到005.jpg
        for i in range(1, 6):  # 仅提取5张图片
            # 缩略图格式为 "l/00X.jpg"
            img_url = f"https://www.caribbeancom.com/moviepages/{movie_id}/images/l/{i:03d}.jpg"
            thumbnails.append(img_url)
        
        self.logger.info(f"构建了 {len(thumbnails)} 个缩略图URL")
        
        return thumbnails