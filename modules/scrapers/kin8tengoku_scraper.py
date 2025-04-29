#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import json
import logging
from urllib.parse import urljoin, urlencode, quote
from bs4 import BeautifulSoup

from modules.scrapers.base_scraper import BaseScraper


class Kin8tengokuScraper(BaseScraper):
    """Kin8tengoku网站爬虫类"""
    
    def __init__(self):
        """初始化Kin8tengoku爬虫类"""
        super().__init__()
        
        # 基础URL设置
        self.base_url = "https://www.kin8tengoku.com"
        self.search_url_template = "https://www.kin8tengoku.com/search/=/searchstr={}"
        
        # 详情页URL模板 - 使用movie_id构建
        self.detail_url_template = "https://www.kin8tengoku.com/moviepages/{}/index.html"
        
        # 设置HTTP请求头
        self.headers.update({
            'Referer': 'https://www.kin8tengoku.com/',
            'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8'
        })

    def clean_movie_id(self, movie_id):
        """
        清理并标准化电影ID
        例如: kin8-1522 -> 1522

        Args:
            movie_id (str): 原始电影ID

        Returns:
            str: 清理后的电影ID
        """
        self.logger.info(f"Cleaning movie ID: {movie_id}")
        
        # 匹配 kin8-xxxx 格式的ID
        match = re.search(r'kin8[^\d]*(\d+)', movie_id.lower())
        if match:
            cleaned_id = match.group(1)
            self.logger.info(f"Cleaned movie ID: {cleaned_id}")
            return cleaned_id
        else:
            # 如果没有匹配到特定格式，尝试提取数字
            match = re.search(r'(\d+)', movie_id)
            if match:
                cleaned_id = match.group(1)
                self.logger.info(f"Extracted numeric ID: {cleaned_id}")
                return cleaned_id
            
            # 如果无法提取有效ID，返回原始ID
            self.logger.warning(f"Could not clean movie ID: {movie_id}")
            return movie_id
    
    def get_movie_url(self, movie_id):
        """
        根据电影ID构建详情页URL

        Args:
            movie_id (str): 电影ID

        Returns:
            str: 详情页URL
        """
        cleaned_id = self.clean_movie_id(movie_id)
        url = self.detail_url_template.format(cleaned_id)
        self.logger.info(f"Generated movie URL: {url}")
        return url
    
    def search_movie(self, movie_id):
        """
        搜索电影并获取详情页URL

        Args:
            movie_id (str): 电影ID

        Returns:
            str: 详情页URL，如果找不到则返回None
        """
        # 首先尝试直接使用ID构建URL
        direct_url = self.get_movie_url(movie_id)
        self.logger.info(f"Trying direct URL: {direct_url}")
        
        # 尝试访问直接URL
        response = self.session.get(direct_url, headers=self.headers)
        if response.status_code == 200 and "金髪天國" in response.text:
            self.logger.info(f"Direct URL successful: {direct_url}")
            return direct_url
        
        # 如果直接访问失败，尝试搜索
        cleaned_id = self.clean_movie_id(movie_id)
        search_query = f"kin8-{cleaned_id}"
        search_url = self.search_url_template.format(quote(search_query))
        
        self.logger.info(f"Searching with URL: {search_url}")
        response = self.session.get(search_url, headers=self.headers)
        
        if response.status_code != 200:
            self.logger.error(f"Search request failed with status code: {response.status_code}")
            return None
        
        # 解析搜索结果
        soup = BeautifulSoup(response.text, 'html.parser')
        urls = self._extract_links_from_search_page(soup)
        
        if not urls:
            self.logger.warning(f"No results found for movie ID: {movie_id}")
            return None
        
        # 查找最佳匹配
        best_match = self._find_best_match(urls, cleaned_id)
        if best_match:
            self.logger.info(f"Found best match URL: {best_match}")
            return best_match
        
        # 如果没有找到最佳匹配，返回第一个结果
        self.logger.info(f"Using first result: {urls[0]}")
        return urls[0]
    
    def _extract_links_from_search_page(self, soup):
        """
        从搜索结果页面提取详情页链接

        Args:
            soup (BeautifulSoup): 搜索结果页面的BeautifulSoup对象

        Returns:
            list: 详情页URL列表
        """
        urls = []
        
        # 尝试多种选择器以处理不同的页面结构
        selectors = [
            '.thumb a',  # 缩略图链接
            '.title a',  # 标题链接
            '.sub_main a',  # 电影链接
            '.movie_left a',  # 电影左侧链接
            '.gallery_box a'  # 画廊链接
        ]
        
        for selector in selectors:
            elements = soup.select(selector)
            for element in elements:
                if 'href' in element.attrs:
                    url = urljoin(self.base_url, element['href'])
                    if '/moviepages/' in url and url not in urls:
                        urls.append(url)
        
        # 如果常规选择器没有找到结果，尝试从脚本标签中查找
        if not urls:
            script_tags = soup.find_all('script')
            for script in script_tags:
                if script.string and 'moviepages' in script.string:
                    matches = re.findall(r'//www\.kin8tengoku\.com/moviepages/(\d+)/index\.html', script.string)
                    for match in matches:
                        url = f"https://www.kin8tengoku.com/moviepages/{match}/index.html"
                        if url not in urls:
                            urls.append(url)
        
        self.logger.info(f"Extracted {len(urls)} links from search page")
        return urls
    
    def _find_best_match(self, urls, movie_id):
        """
        从URL列表中找出最匹配的URL

        Args:
            urls (list): URL列表
            movie_id (str): 电影ID

        Returns:
            str: 最匹配的URL，如果没有匹配则返回None
        """
        for url in urls:
            # 从URL中提取ID并比较
            match = re.search(r'/moviepages/(\d+)/index\.html', url)
            if match and match.group(1) == movie_id:
                return url
        return None
    
    def extract_info_from_page(self, soup, movie_id, url):
        """
        从详情页提取电影信息

        Args:
            soup (BeautifulSoup): 详情页的BeautifulSoup对象
            movie_id (str): 电影ID
            url (str): 详情页URL

        Returns:
            dict: 电影信息字典
        """
        # 清理ID并提取纯数字部分
        cleaned_id = self.clean_movie_id(movie_id)
        
        result = {
            'id': movie_id,
            'source': 'kin8tengoku',
            'actresses': [],
            'genres': [],
            'thumbnails': [],
            'samples': [],  # 添加samples字段，用于存储样本图片(字典格式)
            'url': url
        }
        
        if not soup:
            return result
        
        # 提取标题 - 修改为同时检查多个可能的标题元素
        title_elem = soup.select_one('.sub_title_vip') or soup.select_one('.sub_title')
        if title_elem:
            result['title'] = title_elem.text.strip()
        else:
            # 如果没有找到特定类，尝试其他可能的选择器
            title_candidates = [
                '#sub_main p.sub_title_vip',
                '#sub_main .sub_title',
                'p.sub_title',
                'h1.sub_title',
                'p.sub_title_vip'
            ]
            
            for selector in title_candidates:
                element = soup.select_one(selector)
                if element and element.text.strip():
                    result['title'] = element.text.strip()
                    self.logger.info(f"Found title using selector: {selector}")
                    break
        
        # 记录找到的标题
        if 'title' in result:
            self.logger.info(f"Extracted title: {result['title']}")
        else:
            self.logger.warning("Could not extract title from page")
        
        # 构建封面图片URL (格式: https://www.kin8tengoku.com/[ID]/pht/1.jpg)
        main_cover_url = f"https://www.kin8tengoku.com/{cleaned_id}/pht/1.jpg"
        result['img'] = main_cover_url
        
        # 添加主封面到缩略图列表中(字符串格式)
        result['thumbnails'].append(main_cover_url)
        
        # 将主封面添加到samples列表(字典格式)
        result['samples'].append({
            "src": main_cover_url,
            "thumbnail": main_cover_url,
            "alt": f"kin8-{cleaned_id} - Main Cover"
        })
        
        # 添加大图缩略图 (格式: https://www.kin8tengoku.com/[ID]/pht/2_lg.jpg 到 4_lg.jpg)
        for i in range(2, 5):
            img_url = f"https://www.kin8tengoku.com/{cleaned_id}/pht/{i}_lg.jpg"
            # 添加到缩略图列表(字符串格式)
            result['thumbnails'].append(img_url)
            # 添加到samples列表(字典格式)
            result['samples'].append({
                "src": img_url,
                "thumbnail": img_url,
                "alt": f"kin8-{cleaned_id} - Sample Image {i-1}"
            })
        
        # 添加小图缩略图 (格式: https://www.kin8tengoku.com/[ID]/pht/5.jpg 到 13.jpg)
        for i in range(5, 14):
            img_url = f"https://www.kin8tengoku.com/{cleaned_id}/pht/{i}.jpg"
            # 添加到缩略图列表(字符串格式)
            result['thumbnails'].append(img_url)
            # 添加到samples列表(字典格式)
            result['samples'].append({
                "src": img_url,
                "thumbnail": img_url,
                "alt": f"kin8-{cleaned_id} - Sample Image {i}"
            })
        
        # 提取演员信息
        actress_elem = soup.select('.movie_table_td2 .icon a')
        for elem in actress_elem:
            if '/actor_' in elem.get('href', ''):
                result['actresses'].append(elem.text.strip())
        
        # 提取类别/标签
        category_elems = soup.select('.movie_table_td2 .icon a')
        for elem in category_elems:
            if elem.get('href') and ('/listpages/' in elem.get('href')) and not '/actor_' in elem.get('href'):
                genre = elem.text.strip()
                # 从类别文本中提取纯文本（去除数字）
                genre = re.sub(r'\(\d+\)', '', genre).strip()
                if genre and genre not in result['genres']:
                    result['genres'].append(genre)
        
        # 提取发行日期
        date_elem = soup.select_one('.movie_table_td:-soup-contains("更新日")')
        if date_elem and date_elem.find_next('td'):
            result['release_date'] = date_elem.find_next('td').text.strip()
        
        # 提取持续时间 - 并转换为分钟
        duration_elem = soup.select_one('.movie_table_td:-soup-contains("再生時間")')
        if duration_elem and duration_elem.find_next('td'):
            duration_text = duration_elem.find_next('td').text.strip()
            # 转换 HH:MM:SS 格式为分钟
            try:
                parts = duration_text.split(':')
                if len(parts) == 3:
                    hours = int(parts[0])
                    minutes = int(parts[1])
                    seconds = int(parts[2])
                    total_minutes = hours * 60 + minutes
                    # 如果秒数超过30，向上取整
                    if seconds >= 30:
                        total_minutes += 1
                    result['duration'] = str(total_minutes)
                else:
                    result['duration'] = duration_text
            except Exception as e:
                self.logger.error(f"Error converting duration: {str(e)}")
                result['duration'] = duration_text
        
        # 提取描述 - 使用 summary 而不是 description
        description_elem = soup.select_one('#comment')
        if description_elem:
            result['summary'] = description_elem.text.strip()
        
        # 提取电影ID
        result['product_code'] = f"kin8-{cleaned_id}"
        
        return result 