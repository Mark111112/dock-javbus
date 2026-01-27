#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import json
import logging
from urllib.parse import urljoin, urlencode, quote
from bs4 import BeautifulSoup

from modules.scrapers.base_scraper import BaseScraper


class DMMScraper(BaseScraper):
    """DMM网站爬虫类"""
    
    def __init__(self):
        """初始化DMM爬虫类"""
        super().__init__()
        
        # 基础URL设置
        self.base_url = "https://www.dmm.com"
        self.search_url_template = "https://www.dmm.com/search/=/searchstr={}"
        
        # 详情页URL模板
        self.detail_url_template = "https://www.dmm.com/mono/dvd/-/detail/=/cid={}/"
        
        # DMM特有设置
        self.cookies = {
            'age_check_done': '1',  # 年龄确认
            'locale': 'ja'          # 使用日语
        }
        
        self.logger = logging.getLogger('DMMScraper')
    
    def clean_movie_id(self, movie_id, five_digit=False):
        """标准化影片ID
        
        Args:
            movie_id: 原始影片ID
            five_digit: 是否格式化为5位数字
            
        Returns:
            tuple: (厂商代号, 数字部分, 完整ID)
        """
        # 清理ID，提取字母和数字部分
        movie_id = movie_id.upper().strip()
        match = re.search(r'([a-zA-Z]+)[-_]?(\d+)', movie_id)
        
        if not match:
            self.logger.warning(f"无法解析影片ID: {movie_id}")
            return None, None, movie_id
        
        # 提取厂商代号和数字部分
        label, number = match.groups()
        
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
        
        # 构造商品代码(cid)
        # DMM的cid通常是: 厂商代号_数字部分
        cid = f"{label.lower()}{number}"
        
        # 构建URL
        url = self.detail_url_template.format(cid)
        
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
        
        # 尝试两种格式进行搜索
        search_terms = [
            f"{label}-{number}",  # 基本3位数格式
        ]
        
        # 添加5位数格式
        _, number_5d, clean_id_5d = self.clean_movie_id(movie_id, five_digit=True)
        if clean_id != clean_id_5d:
            search_terms.append(f"{label}-{number_5d}")
        
        all_urls = []
        for term in search_terms:
            encoded_term = quote(term)
            search_url = self.search_url_template.format(encoded_term)
            
            self.logger.info(f"搜索URL: {search_url}")
            soup = self.get_page(search_url)
            
            if not soup:
                continue
                
            # 提取搜索结果中的详情页链接
            urls = self._extract_links_from_search_page(soup, clean_id)
            
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
        
        # 尝试多种元素选择器，DMM页面结构可能不同
        selectors = [
            '.tmb a',  # 缩略图链接
            '.title a',  # 标题链接
            '.d-item a',  # 商品项目
            '[data-pid] a',  # 带有pid属性的链接
            '.productList a'  # 产品列表链接
        ]
        
        for selector in selectors:
            elements = soup.select(selector)
            for element in elements:
                if 'href' in element.attrs:
                    url = urljoin(self.base_url, element['href'])
                    if '/mono/dvd/-/detail/' in url or '/digital/videoa/-/detail/' in url:
                        urls.append(url)
        
        # 尝试从JavaScript数据中提取链接
        script_tags = soup.find_all('script')
        for script in script_tags:
            script_text = script.string
            if not script_text:
                continue
                
            # 尝试匹配包含商品信息的JSON数据
            json_match = re.search(r'var\s+params\s+=\s+(\{.*?\});', str(script_text), re.DOTALL)
            if json_match:
                try:
                    json_data = json.loads(json_match.group(1))
                    if 'items' in json_data:
                        for item in json_data['items']:
                            if 'url' in item:
                                urls.append(urljoin(self.base_url, item['url']))
                except Exception as e:
                    self.logger.debug(f"解析JSON数据失败: {str(e)}")
        
        # 去重
        urls = list(dict.fromkeys(urls))
        self.logger.debug(f"提取到 {len(urls)} 个链接")
        return urls
    
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
        _, _, clean_id = self.clean_movie_id(movie_id)
        
        # 首先尝试在URL中精确匹配商品ID
        for url in urls:
            # 提取URL中的cid参数
            cid_match = re.search(r'/cid=([^/]+)/', url)
            if not cid_match:
                continue
                
            cid = cid_match.group(1)
            
            # 如果CID包含清理后的ID，优先返回
            if clean_id.lower().replace('-', '') in cid.lower():
                self.logger.info(f"找到最佳匹配URL: {url}")
                return url
        
        # 如果没有精确匹配，返回第一个URL
        self.logger.info(f"未找到精确匹配，使用第一个URL: {urls[0]}")
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
            'id': movie_id,
            'source': 'dmm',
            'url': url
        }
        
        try:
            # 提取标题
            title_elem = soup.select_one('h1#title') or soup.select_one('h1.item_name')
            if title_elem:
                info['title'] = title_elem.text.strip()
            
            # 提取封面图片
            cover_elem = soup.select_one('#package-image img') or soup.select_one('.productPreview img')
            if cover_elem and 'src' in cover_elem.attrs:
                info['cover'] = cover_elem['src']
                # 尝试获取高分辨率图片
                if 'data-src' in cover_elem.attrs:
                    info['cover'] = cover_elem['data-src']
                # 有些图片URL是相对路径
                if not info['cover'].startswith('http'):
                    info['cover'] = urljoin(self.base_url, info['cover'])
            
            # 提取详情表格数据
            info_tables = soup.select('.m-productInformation table') or soup.select('.informationTable')
            
            for table in info_tables:
                rows = table.select('tr')
                for row in rows:
                    header = row.select_one('th') or row.select_one('td.nw')
                    data = row.select_one('td') or row.select_one('td[width="100%"]')
                    
                    if not header or not data:
                        continue
                    
                    header_text = header.text.strip()
                    data_text = data.text.strip()
                    
                    # 提取各种元数据
                    if 'メーカー' in header_text:  # 制造商
                        info['maker'] = data_text
                    elif 'レーベル' in header_text:  # 标签
                        info['label'] = data_text
                    elif '品番' in header_text or '品番：' in header_text:  # 产品编号
                        info['product_code'] = data_text
                    elif '発売日' in header_text or '商品発売日' in header_text:  # 发售日期
                        info['release_date'] = data_text
                    elif '出演者' in header_text:  # 演员
                        actresses = []
                        for a in data.select('a'):
                            actresses.append(a.text.strip())
                        if actresses:
                            info['actresses'] = actresses
                    elif 'ジャンル' in header_text:  # 类型
                        genres = []
                        for a in data.select('a'):
                            genres.append(a.text.strip())
                        if genres:
                            info['genres'] = genres
                    elif '収録時間' in header_text:  # 时长
                        time_match = re.search(r'(\d+)分', data_text)
                        if time_match:
                            info['duration'] = int(time_match.group(1))
                    elif 'シリーズ' in header_text:  # 系列
                        info['series'] = data_text
                    elif '監督' in header_text:  # 导演
                        info['director'] = data_text
            
            # 提取简介
            summary_elem = soup.select_one('.m-productInformation .m-ratioText') or soup.select_one('#introduction .mg-b20')
            if summary_elem:
                info['summary'] = summary_elem.text.strip()
            
            # 提取缩略图
            thumbnails = []
            thumb_containers = soup.select('#sample-image-block a') or soup.select('#sample-image li a')
            for thumb in thumb_containers:
                img = thumb.select_one('img')
                if img and 'src' in img.attrs:
                    thumb_url = img['src']
                    # 获取原图URL
                    if 'data-src' in img.attrs:
                        thumb_url = img['data-src']
                    # 处理相对路径
                    if not thumb_url.startswith('http'):
                        thumb_url = urljoin(self.base_url, thumb_url)
                    # 处理DMM缩略图URL，转换为大图
                    thumb_url = thumb_url.replace('-', 'jp-')
                    thumbnails.append(thumb_url)
            
            if thumbnails:
                info['thumbnails'] = thumbnails
            
            # 提取评分
            rating_elem = soup.select_one('.d-review__average') or soup.select_one('.m-productEvaluation span')
            if rating_elem:
                try:
                    info['rating'] = float(rating_elem.text.strip())
                except ValueError:
                    pass
            
            return info
            
        except Exception as e:
            self.logger.error(f"提取信息时出错: {str(e)}")
            return None 