#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import json
import logging
from urllib.parse import urljoin, quote
import requests
from bs4 import BeautifulSoup

from modules.scrapers.base_scraper import BaseScraper


class PacopacomomaScraper(BaseScraper):
    """Pacopacomama网站爬虫类"""
    
    def __init__(self):
        """初始化Pacopacomama爬虫类"""
        super().__init__()
        
        # 基础URL设置
        self.base_url = "https://www.pacopacomama.com"
        
        # 详情页URL模板
        self.detail_url_template = "https://www.pacopacomama.com/movies/{}"
        
        # API URL模板用于获取详细信息
        self.api_url_template = "https://www.pacopacomama.com/dyn/phpauto/movie_details/movie_id/{}.json"
        
        # Cookies设置
        self.cookies = {
            'ageCheck': '1'  # 年龄确认
        }
        
        # 设置User-Agent
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json;charset=utf-8',
            'Referer': 'https://www.pacopacomama.com/movies/'
        }
        
        self.logger = logging.getLogger('PacopacomomaScraper')
    
    def clean_movie_id(self, movie_id, five_digit=False):
        """标准化影片ID
        
        Args:
            movie_id: 原始影片ID (例如 123456_001, pacopacomama-123456_001)
            five_digit: 不适用于Pacopacomama，保留参数以兼容接口
            
        Returns:
            tuple: (厂商代号, 数字部分, 完整ID)
        """
        # 清理ID，提取数字部分
        movie_id = movie_id.strip().upper()
        
        # 尝试提取数字部分，Pacopacomama的格式通常是6位数_3位数 (例如 123456_001)
        match = re.search(r'(?:PACOPACOMAMA[-_]?|PACO[-_]?)?(\d{6})(?:[-_])(\d{3})', movie_id, re.IGNORECASE)
        
        if not match:
            self.logger.warning(f"无法解析影片ID: {movie_id}")
            return None, None, movie_id
        
        # 提取数字部分
        number_part1 = match.group(1)
        number_part2 = match.group(2)
        number = f"{number_part1}_{number_part2}"
        
        # 标准化ID格式
        clean_id = f"PACOPACOMAMA-{number}"
        
        self.logger.debug(f"清理影片ID: {movie_id} -> {clean_id}")
        return "PACOPACOMAMA", number, clean_id
    
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
        
        # Pacopacomama URL使用下划线格式
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
        # 对于Pacopacomama，直接构建URL而不是搜索
        url = self.get_movie_url(movie_id)
        if url:
            return [url]
        return []
    
    def get_page(self, url, encoding=None):
        """获取页面内容
        
        Args:
            url: 页面URL
            encoding: 页面编码(可选)
            
        Returns:
            BeautifulSoup: 页面解析后的BeautifulSoup对象
        """
        try:
            self.logger.info(f"开始请求URL: {url}")
            
            # 发送HTTP请求
            response = requests.get(
                url,
                headers=self.headers,
                cookies=self.cookies,
                timeout=10,
                verify=True
            )
            
            # 检查状态码
            if response.status_code != 200:
                self.logger.error(f"HTTP错误: {response.status_code}")
                return None
            
            # 设置编码
            if encoding:
                response.encoding = encoding
            
            # 解析HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            self.logger.info(f"成功获取页面: {url}")
            return soup
            
        except Exception as e:
            self.logger.error(f"请求页面时出错: {str(e)}")
            return None
    
    def get_json_data(self, movie_id):
        """从API获取影片JSON数据
        
        Args:
            movie_id: 影片ID
            
        Returns:
            dict: 影片JSON数据
        """
        # 标准化影片ID
        label, number, clean_id = self.clean_movie_id(movie_id)
        if not label:
            return None
        
        # 构建API URL
        api_url = self.api_url_template.format(number)
        
        try:
            self.logger.info(f"开始请求API: {api_url}")
            
            # 发送HTTP请求
            response = requests.get(
                api_url,
                headers=self.headers,
                cookies=self.cookies,
                timeout=10,
                verify=True
            )
            
            # 检查状态码
            if response.status_code != 200:
                self.logger.error(f"API请求错误: {response.status_code}")
                return None
            
            # 解析JSON数据
            data = response.json()
            self.logger.info(f"成功获取API数据: {api_url}")
            return data
            
        except Exception as e:
            self.logger.error(f"请求API时出错: {str(e)}")
            return None
    
    def extract_info_from_page(self, soup, movie_id, url):
        """从详情页提取影片信息
        
        Args:
            soup: 详情页BeautifulSoup对象 (不使用，直接使用API)
            movie_id: 原始影片ID
            url: 详情页URL
            
        Returns:
            dict: 影片信息字典
        """
        # 获取JSON数据
        json_data = self.get_json_data(movie_id)
        
        if not json_data:
            return None
        
        # 初始化结果字典
        info = {
            'movie_id': movie_id,
            'url': url,
            'source': 'pacopacomama'
        }
        
        try:
            # 从JSON提取各种信息
            if 'MovieID' in json_data:
                info['paco_id'] = json_data['MovieID']
            
            if 'Title' in json_data:
                info['title'] = json_data['Title']
            
            if 'TitleEn' in json_data and json_data['TitleEn']:
                info['title_en'] = json_data['TitleEn']
            
            if 'Release' in json_data:
                info['release_date'] = json_data['Release']
            
            # 提取演员信息
            actresses = []
            if 'ActressesJa' in json_data and json_data['ActressesJa']:
                actresses = json_data['ActressesJa']
            elif 'Actor' in json_data and json_data['Actor']:
                actresses = [json_data['Actor']]
            
            if actresses:
                info['actors'] = actresses
            
            # 提取英文演员名称
            actresses_en = []
            if 'ActressesEn' in json_data and json_data['ActressesEn']:
                actresses_en = json_data['ActressesEn']
            
            if actresses_en:
                info['actors_en'] = actresses_en
            
            # 提取系列信息
            if 'Series' in json_data and json_data['Series']:
                info['series'] = json_data['Series']
            
            if 'SeriesEn' in json_data and json_data['SeriesEn']:
                info['series_en'] = json_data['SeriesEn']
            
            # 提取标签/类别
            genres = []
            if 'UCNAME' in json_data and json_data['UCNAME']:
                genres = json_data['UCNAME']
            
            if genres:
                info['genres'] = genres
            
            # 提取英文标签/类别
            genres_en = []
            if 'UCNAMEEn' in json_data and json_data['UCNAMEEn']:
                genres_en = json_data['UCNAMEEn']
            
            if genres_en:
                info['genres_en'] = genres_en
            
            # 提取影片时长
            if 'Duration' in json_data:
                # 将秒转换为分钟
                duration_seconds = json_data['Duration']
                duration_minutes = duration_seconds // 60
                info['duration'] = int(duration_minutes)
            
            # 提取简介
            if 'Desc' in json_data and json_data['Desc']:
                info['summary'] = json_data['Desc']
                info['summary_source'] = 'api'
            
            # 提取封面图片
            cover_url = None
            if 'ThumbHigh' in json_data and json_data['ThumbHigh']:
                cover_url = json_data['ThumbHigh']
            elif 'MovieThumb' in json_data and json_data['MovieThumb']:
                cover_url = json_data['MovieThumb']
            
            if cover_url:
                info['cover_url'] = cover_url
            
            # 提取缩略图
            thumbnails = self._extract_thumbnails(json_data, url)
            if thumbnails:
                info['thumbnails'] = thumbnails
            
            # 提取视频文件信息
            if 'SampleFiles' in json_data and json_data['SampleFiles']:
                sample_files = []
                for sample in json_data['SampleFiles']:
                    if 'URL' in sample:
                        sample_files.append(sample['URL'])
                
                if sample_files:
                    info['sample_files'] = sample_files
            
            return info
            
        except Exception as e:
            self.logger.error(f"提取信息时出错: {str(e)}")
            return None
    
    def _extract_thumbnails(self, json_data, url):
        """提取缩略图
        
        Args:
            json_data: 影片JSON数据
            url: 详情页URL
            
        Returns:
            list: 缩略图URL列表
        """
        thumbnails = []
        
        try:
            # 从URL或JSON中提取影片ID
            movie_id = None
            if 'MovieID' in json_data:
                movie_id = json_data['MovieID']
            else:
                match = re.search(r'/movies/([^/]+)', url)
                if match:
                    movie_id = match.group(1)
            
            if not movie_id:
                return thumbnails
            
            # 构建缩略图URL，Pacopacomama通常有5个缩略图
            for i in range(1, 6):
                img_url = f"https://www.pacopacomama.com/moviepages/{movie_id}/images/popu/{i}.jpg"
                thumbnails.append(img_url)
            
            self.logger.info(f"构建了 {len(thumbnails)} 个缩略图URL")
            
        except Exception as e:
            self.logger.error(f"提取缩略图时出错: {str(e)}")
        
        return thumbnails 