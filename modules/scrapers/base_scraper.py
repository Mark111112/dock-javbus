#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import time
import random
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from abc import ABC, abstractmethod


class BaseScraper(ABC):
    """爬虫基类，提供通用功能"""
    
    def __init__(self):
        """初始化爬虫基类"""
        # 设置日志
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        
        # 基本设置
        self.base_url = None
        self.search_url = None
        self.cookies = {}
        
        # 请求头
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
    
    def get_page(self, url, retry=3):
        """获取并解析页面
        
        Args:
            url: 页面URL
            retry: 重试次数
            
        Returns:
            BeautifulSoup: 解析后的页面 或 None（如果获取失败）
        """
        for attempt in range(retry):
            try:
                self.logger.info(f"获取页面: {url}")
                
                # 添加随机延迟，防止请求过快
                sleep_time = random.uniform(1.0, 3.0)
                self.logger.debug(f"延迟 {sleep_time:.2f} 秒")
                time.sleep(sleep_time)
                
                # 发起请求
                response = requests.get(
                    url, 
                    headers=self.headers,
                    cookies=self.cookies,
                    timeout=10
                )
                
                # 检查响应状态
                if response.status_code == 200:
                    self.logger.info(f"页面获取成功 ({response.status_code})")
                    # 解析HTML
                    soup = BeautifulSoup(response.text, 'html.parser')
                    return soup
                elif response.status_code == 404:
                    self.logger.warning(f"页面不存在 ({response.status_code}): {url}")
                    return None
                else:
                    self.logger.warning(f"请求失败 ({response.status_code}): {url}")
            
            except requests.RequestException as e:
                self.logger.error(f"请求异常 (尝试 {attempt+1}/{retry}): {str(e)}")
                
                # 如果不是最后一次尝试，则等待一段时间后重试
                if attempt < retry - 1:
                    wait_time = (attempt + 1) * 2
                    self.logger.info(f"等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
        
        self.logger.error(f"获取页面失败，已达到最大重试次数 ({retry})")
        return None
    
    def is_valid_url(self, url):
        """检查URL是否有效
        
        Args:
            url: 要检查的URL
            
        Returns:
            bool: URL是否有效
        """
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False
    
    def get_movie_info(self, movie_id):
        """获取影片信息的主函数
        
        Args:
            movie_id: 影片ID
            
        Returns:
            dict: 影片信息字典 或 None（如果找不到影片）
        """
        self.logger.info(f"获取影片信息: {movie_id}")
        
        # 1. 尝试直接构建URL
        url = self.get_movie_url(movie_id)
        
        if url and self.is_valid_url(url):
            self.logger.info(f"尝试直接访问URL: {url}")
            soup = self.get_page(url)
            
            if soup:
                self.logger.info(f"直接访问成功，提取信息")
                info = self.extract_info_from_page(soup, movie_id, url)
                if info:
                    return info
        
        # 2. 直接URL失败，尝试搜索
        self.logger.info(f"直接URL访问失败，尝试搜索: {movie_id}")
        url_list = self.search_movie(movie_id)
        
        if not url_list:
            self.logger.warning(f"未找到影片: {movie_id}")
            return None
        
        # 获取第一个URL（通常是最匹配的结果）
        url = url_list[0]
        self.logger.info(f"获取到详情页URL: {url}")
        
        # 获取详情页内容
        soup = self.get_page(url)
        if not soup:
            self.logger.warning(f"无法获取详情页: {url}")
            return None
        
        # 提取信息
        info = self.extract_info_from_page(soup, movie_id, url)
        return info
    
    @abstractmethod
    def get_movie_url(self, movie_id):
        """直接构建影片详情页URL"""
        pass
    
    @abstractmethod
    def search_movie(self, movie_id):
        """搜索影片，获取详情页URL"""
        pass
    
    @abstractmethod
    def extract_info_from_page(self, soup, movie_id, url):
        """从页面提取影片信息"""
        pass

    def create_session(self):
        """创建一个HTTP会话"""
        session = requests.Session()
        session.headers.update(self.headers)
        for key, value in self.cookies.items():
            session.cookies.set(key, value)
        return session
    
    def save_debug_file(self, content, filename):
        """保存调试内容到文件
        
        Args:
            content: 要保存的内容
            filename: 文件名
        """
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)
            self.logger.debug(f"已保存调试内容到: {filename}")
        except Exception as e:
            self.logger.error(f"保存调试文件失败: {str(e)}")
    
    @abstractmethod
    def clean_movie_id(self, movie_id):
        """清理并标准化影片ID
        
        Args:
            movie_id: 原始影片ID
            
        Returns:
            str: 标准化后的影片ID
        """
        pass 