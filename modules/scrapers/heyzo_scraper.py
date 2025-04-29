#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import json
import logging
from urllib.parse import urljoin, urlencode, quote, unquote
from bs4 import BeautifulSoup

from modules.scrapers.base_scraper import BaseScraper


class HeyzoScraper(BaseScraper):
    """Heyzo网站爬虫类"""
    
    def __init__(self):
        """初始化Heyzo爬虫类"""
        super().__init__()
        
        # 基础URL设置
        self.base_url = "https://www.heyzo.com"
        # 搜索URL模板 - Heyzo的搜索接口
        self.search_url_template = "https://www.heyzo.com/search/{}/1.html?sort=pop"
        
        # 详情页URL模板
        self.detail_url_template = "https://www.heyzo.com/moviepages/{}/index.html"
        
        # Heyzo特有设置
        self.cookies = {
            'age_auth': '1',  # 年龄确认
            'locale': 'ja'    # 使用日语
        }
        
        self.logger = logging.getLogger('HeyzoScraper')
    
    def clean_movie_id(self, movie_id, five_digit=False):
        """标准化影片ID
        
        Args:
            movie_id: 原始影片ID (例如 HEYZO-1112)
            five_digit: 不适用于Heyzo，保留参数以兼容接口
            
        Returns:
            tuple: (厂商代号, 数字部分, 完整ID)
        """
        # 清理ID，提取数字部分
        movie_id = movie_id.strip().upper()
        
        # 尝试提取数字部分
        match = re.search(r'(?:HEYZO[-_]?)?(\d+)', movie_id, re.IGNORECASE)
        
        if not match:
            self.logger.warning(f"无法解析影片ID: {movie_id}")
            return None, None, movie_id
        
        # 提取数字部分
        number = match.group(1)
        
        # 标准化ID格式
        clean_id = f"HEYZO-{number}"
        
        self.logger.debug(f"清理影片ID: {movie_id} -> {clean_id}")
        return "HEYZO", number, clean_id
    
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
        
        # Heyzo的URL格式比较简单，只需要数字部分
        number = number.zfill(4)  # 确保4位数字格式
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
        
        # Heyzo一般直接通过ID访问，不需要搜索
        # 但保留搜索功能以防直接访问失败
        direct_url = self.get_movie_url(movie_id)
        
        # 尝试一些可能的搜索关键词
        search_terms = [
            number,                # 只搜索数字部分
            f"heyzo {number}"      # 搜索"heyzo 数字"
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
        movie_links = soup.select('.movie a[href*="/moviepages/"]')
        for link in movie_links:
            href = link.get('href', '')
            if href and '/moviepages/' in href:
                full_url = urljoin(self.base_url, href)
                urls.append(full_url)
                self.logger.info(f"从搜索结果中找到链接: {full_url}")
        
        # 如果上面的选择器没有找到结果，尝试其他选择器
        if not urls:
            all_links = soup.select('a[href*="/moviepages/"]')
            for link in all_links:
                href = link.get('href', '')
                if href and '/moviepages/' in href and '/index.html' in href:
                    full_url = urljoin(self.base_url, href)
                    urls.append(full_url)
                    self.logger.info(f"使用备用选择器找到链接: {full_url}")
        
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
        
        # 对于Heyzo，通常直接比较数字部分即可
        for url in urls:
            # 检查URL中是否包含正确的数字ID
            url_match = re.search(r'/moviepages/(\d+)/index\.html', url)
            if url_match and url_match.group(1) == number.zfill(4):
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
            'source': 'heyzo'
        }
        
        try:
            # 从URL中提取Heyzo编号
            url_match = re.search(r'/moviepages/(\d+)/index\.html', url)
            if url_match:
                heyzo_id = url_match.group(1)
                info['heyzo_id'] = f"HEYZO-{heyzo_id}"
            
            # 提取标题 - Heyzo通常有一个h1或h2标题
            title_elem = soup.select_one('h1') or soup.select_one('h2')
            if title_elem:
                info['title'] = title_elem.text.strip()
            
            # 从movieInfo表格中提取详细信息
            info_table = soup.select_one('table.movieInfo')
            if info_table:
                # 提取发行日期
                release_row = info_table.select_one('.table-release-day')
                if release_row:
                    release_date = release_row.select_one('td:nth-of-type(2)')
                    if release_date:
                        info['release_date'] = release_date.text.strip()
                
                # 提取演员
                actor_row = info_table.select_one('.table-actor')
                if actor_row:
                    actors = []
                    for actor_link in actor_row.select('a'):
                        actors.append(actor_link.text.strip())
                    if actors:
                        info['actors'] = actors
                
                # 提取系列
                series_row = info_table.select_one('.table-series')
                if series_row:
                    series_link = series_row.select_one('a')
                    if series_link:
                        info['series'] = series_link.text.strip()
                
                # 提取评分
                rating_row = info_table.select_one('.table-estimate')
                if rating_row:
                    rating_value = rating_row.select_one('[itemprop="ratingValue"]')
                    if rating_value:
                        try:
                            info['rating'] = float(rating_value.text.strip())
                        except ValueError:
                            pass
                
                # 提取女优类型
                actor_type_row = info_table.select_one('.table-actor-type')
                if actor_type_row:
                    actress_types = []
                    for type_link in actor_type_row.select('a'):
                        actress_types.append(type_link.text.strip())
                    if actress_types:
                        info['actress_types'] = actress_types
                
                # 提取标签关键词
                tag_row = info_table.select_one('.table-tag-keyword-big')
                if tag_row:
                    tags = []
                    for tag_link in tag_row.select('a'):
                        tags.append(tag_link.text.strip())
                    if tags:
                        info['genres'] = tags
                
                # 提取影片简介
                memo_row = info_table.select_one('.table-memo')
                if memo_row:
                    memo_p = memo_row.select_one('p.memo')
                    if memo_p:
                        info['summary'] = memo_p.text.strip()
                        info['summary_source'] = 'html'
            
            # 提取封面图片 - 通常是页面上的主要图片
            thumb_url = self._get_preview_image_url(url)
            if thumb_url:
                info['cover_url'] = thumb_url
            
            # 提取缩略图 - 从图片库中提取
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
        # 从详情页URL提取影片ID
        match = re.search(r'/moviepages/(\d+)/index\.html', url)
        if not match:
            return None
        
        movie_id = match.group(1)
        
        # 构造预览图片URL - 修正为不带尺寸的链接
        preview_url = f"https://www.heyzo.com/contents/3000/{movie_id}/images/player_thumbnail.jpg"
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
        
        # 从URL提取影片ID
        match = re.search(r'/moviepages/(\d+)/index\.html', url)
        if not match:
            return thumbnails
        
        movie_id = match.group(1)
        
        # 修正：前5张图片使用高质量URL，第6-21张使用缩略图URL
        for i in range(1, 22):  # 尝试21张图片
            img_num = f"{i:03d}"  # 格式化为3位数 (001, 002, etc.)
            
            # 前5张使用大图链接，后面的使用缩略图链接
            if i <= 5:
                # 非会员可看到的高质量图片 (前5张)
                img_url = f"https://www.heyzo.com/contents/3000/{movie_id}/gallery/{img_num}.jpg"
            else:
                # 非会员只能看到的缩略图 (第6张开始)
                img_url = f"https://www.heyzo.com/contents/3000/{movie_id}/gallery/thumbnail_{img_num}.jpg"
            
            thumbnails.append(img_url)
        
        self.logger.info(f"构建了 {len(thumbnails)} 个缩略图URL（前5张为高质量图片，后续为缩略图）")
        
        return thumbnails 