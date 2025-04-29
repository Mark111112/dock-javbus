#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin


class TokyoHotScraper:
    """TokyoHot网站爬虫类"""
    
    def __init__(self):
        """初始化TokyoHot爬虫类"""
        # 基础URL设置
        self.base_url = "https://my.tokyo-hot.com"
        
        # 搜索URL
        self.search_url = "https://my.tokyo-hot.com/product/"
        
        # 详情页URL模板
        self.detail_url_template = "https://my.tokyo-hot.com/product/{}/?lang=ja"
        
        # 设置User-Agent
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        
        self.logger = logging.getLogger('TokyoHotScraper')
    
    def clean_movie_id(self, movie_id):
        """标准化影片ID
        
        Args:
            movie_id: 原始影片ID
            
        Returns:
            tuple: (厂商代号, 数字部分, 完整ID)
        """
        # 清理ID，提取数字部分
        movie_id = movie_id.strip().upper()
        
        # Tokyo Hot格式通常是字母+数字，例如 n1568, k1234
        match = re.search(r'([A-Z]+)(\d+)', movie_id, re.IGNORECASE)
        
        if not match:
            self.logger.warning(f"无法解析影片ID: {movie_id}")
            return None, None, movie_id
        
        # 提取字母部分和数字部分
        letter_part = match.group(1).upper()
        number_part = match.group(2)
        
        # 标准化ID格式
        clean_id = f"{letter_part}{number_part}"
        
        self.logger.debug(f"清理影片ID: {movie_id} -> {clean_id}")
        return "TOKYOHOT", clean_id, clean_id
    
    def get_page(self, url, params=None):
        """获取页面内容
        
        Args:
            url: 页面URL
            params: 请求参数
            
        Returns:
            BeautifulSoup: 页面解析后的BeautifulSoup对象
        """
        try:
            if params:
                self.logger.info(f"开始请求URL: {url}，参数: {params}")
            else:
                self.logger.info(f"开始请求URL: {url}")
            
            # 发送HTTP请求
            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=10,
                verify=True
            )
            
            # 记录最终URL
            self.logger.info(f"最终请求URL: {response.url}")
            
            # 检查状态码
            if response.status_code != 200:
                self.logger.error(f"HTTP错误: {response.status_code}")
                return None
            
            # 检查内容长度
            if len(response.text) < 1000:
                self.logger.warning(f"页面内容过短，可能是错误页面: {len(response.text)}")
            
            # 解析HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            self.logger.info(f"成功获取页面: {url}")
            return soup
            
        except Exception as e:
            self.logger.error(f"请求页面时出错: {str(e)}")
            return None
    
    def search_movie(self, movie_id):
        """搜索影片
        
        Args:
            movie_id: 影片ID
            
        Returns:
            list: 详情页URL列表
        """
        # 标准化影片ID
        label, clean_id, full_id = self.clean_movie_id(movie_id)
        if not label:
            return []
        
        self.logger.info(f"开始搜索影片: {movie_id}")
        
        # 构建搜索参数
        search_params = {
            'q': clean_id.lower(),
            'x': '0',
            'y': '0'
        }
        
        # 发送搜索请求
        search_soup = self.get_page(self.search_url, params=search_params)
        if not search_soup:
            self.logger.error(f"搜索页面获取失败: {movie_id}")
            return []
        
        # 查找搜索结果
        result_links = []
        product_list = search_soup.select('ul.list.slider.cf li.detail a.rm')
        
        for product_link in product_list:
            # 获取影片链接
            href = product_link.get('href')
            if not href:
                continue
                
            # 从链接中提取产品ID - 支持数字或字母数字组合（如n1970或sky-133）
            product_id_match = re.search(r'/product/([a-zA-Z0-9\-]+)/', href)
            if not product_id_match:
                continue
            
            product_id = product_id_match.group(1)
            self.logger.info(f"找到产品ID: {product_id}")
            
            # 获取影片编号（从描述中）
            description = product_link.select_one('.actor')
            if description:
                self.logger.debug(f"描述文本: {description.text}")
                product_number_match = re.search(r'作品番号:\s*([a-zA-Z0-9\-]+)', description.text)
                if product_number_match:
                    found_number = product_number_match.group(1).lower()
                    self.logger.info(f"找到作品编号: {found_number}")
                    
                    # 检查是否匹配搜索的ID
                    if found_number == clean_id.lower():
                        # 构建详情页URL
                        full_url = urljoin(self.base_url, href)
                        # 添加日语参数
                        if '?' not in full_url:
                            full_url += '?lang=ja'
                        elif 'lang=' not in full_url:
                            full_url += '&lang=ja'
                        
                        result_links.append(full_url)
                        self.logger.info(f"找到匹配结果: {full_url}")
                        break
            
            # 如果没有找到编号或没有匹配上，尝试从标题/描述中判断
            title_element = product_link.select_one('.title')
            if title_element:
                title_text = title_element.text.strip()
                self.logger.debug(f"标题文本: {title_text}")
                
                # 在标题中查找影片编号 (支持如 n1970 或 sky-133 格式)
                title_number_match = re.search(r'([a-zA-Z]+[\-]?\d+)', title_text, re.IGNORECASE)
                if title_number_match:
                    title_number = title_number_match.group(1).lower()
                    self.logger.info(f"从标题中找到编号: {title_number}")
                    
                    if title_number == clean_id.lower():
                        # 构建详情页URL
                        full_url = urljoin(self.base_url, href)
                        # 添加日语参数
                        if '?' not in full_url:
                            full_url += '?lang=ja'
                        elif 'lang=' not in full_url:
                            full_url += '&lang=ja'
                        
                        result_links.append(full_url)
                        self.logger.info(f"从标题匹配找到结果: {full_url}")
                        break
            
            # 如果上面都没找到，就检查图片alt文本
            img_element = product_link.select_one('img')
            if img_element:
                alt_text = img_element.get('alt', '')
                title_text = img_element.get('title', '')
                self.logger.debug(f"图片alt文本: {alt_text}, title文本: {title_text}")
                
                # 查找编号 (支持如 n1970 或 sky-133 格式)
                for text in [alt_text, title_text]:
                    if not text:
                        continue
                    
                    alt_match = re.search(r'([a-zA-Z]+[\-]?\d+)', text, re.IGNORECASE)
                    if alt_match:
                        alt_number = alt_match.group(1).lower()
                        self.logger.info(f"从图片属性中找到编号: {alt_number}")
                        
                        if alt_number == clean_id.lower():
                            # 构建详情页URL
                            full_url = urljoin(self.base_url, href)
                            # 添加日语参数
                            if '?' not in full_url:
                                full_url += '?lang=ja'
                            elif 'lang=' not in full_url:
                                full_url += '&lang=ja'
                            
                            result_links.append(full_url)
                            self.logger.info(f"从图片属性匹配找到结果: {full_url}")
                            break
        
        if not result_links:
            self.logger.warning(f"未找到匹配的影片链接: {movie_id}")
        
        return result_links
    
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
            'source': 'tokyohot'
        }
        
        try:
            # 提取标题
            title_tag = soup.select_one('#main .contents h2')
            if title_tag:
                info['title'] = title_tag.text.strip()
            
            # 提取简介
            sentence_tag = soup.select_one('#main .contents .sentence')
            if sentence_tag:
                # 保留换行符，但移除HTML标签
                info['summary'] = sentence_tag.get_text(separator='\n').strip()
                info['summary_source'] = 'page'
            
            # 提取信息区域
            info_wrapper = soup.select_one('#main .contents .infowrapper')
            if info_wrapper:
                # 提取演员列表
                actors_dt = info_wrapper.find('dt', text='出演者')
                if actors_dt:
                    actors_dd = actors_dt.find_next('dd')
                    if actors_dd:
                        actors = [a.text.strip() for a in actors_dd.find_all('a')]
                        if actors:
                            info['actors'] = actors
                
                # 提取玩法类型（プレイ内容）
                play_dt = info_wrapper.find('dt', text='プレイ内容')
                if play_dt:
                    play_dd = play_dt.find_next('dd')
                    if play_dd:
                        plays = [a.text.strip() for a in play_dd.find_all('a')]
                        if plays:
                            info['genres'] = plays
                
                # 提取系列
                series_dt = info_wrapper.find('dt', text='シリーズ')
                if series_dt:
                    series_dd = series_dt.find_next('dd')
                    if series_dd:
                        series = [a.text.strip() for a in series_dd.find_all('a')]
                        if series:
                            info['series'] = series[0] if len(series) == 1 else series
                
                # 提取发行商（レーベル）
                label_dt = info_wrapper.find('dt', text='レーベル')
                if label_dt:
                    label_dd = label_dt.find_next('dd')
                    if label_dd and label_dd.find('a'):
                        info['label'] = label_dd.find('a').text.strip()
                
                # 提取发布日期
                date_dt = info_wrapper.find('dt', text='配信開始日')
                if date_dt:
                    date_dd = date_dt.find_next('dd')
                    if date_dd:
                        info['release_date'] = date_dd.text.strip()
                
                # 提取时长
                duration_dt = info_wrapper.find('dt', text='収録時間')
                if duration_dt:
                    duration_dd = duration_dt.find_next('dd')
                    if duration_dd:
                        # 将时长转换为分钟
                        duration_text = duration_dd.text.strip()
                        duration_match = re.search(r'(\d{2}):(\d{2}):(\d{2})', duration_text)
                        if duration_match:
                            hours = int(duration_match.group(1))
                            minutes = int(duration_match.group(2))
                            seconds = int(duration_match.group(3))
                            total_minutes = hours * 60 + minutes + (1 if seconds >= 30 else 0)
                            info['duration'] = total_minutes
                
                # 提取作品编号
                number_dt = info_wrapper.find('dt', text='作品番号')
                if number_dt:
                    number_dd = number_dt.find_next('dd')
                    if number_dd:
                        info['product_number'] = number_dd.text.strip()
            
            # 提取缩略图
            vcap_div = soup.select_one('#main .contents .vcap')
            if vcap_div:
                thumbnails = []
                for img_link in vcap_div.select('a[rel="cap"]'):
                    # 使用完整尺寸图片链接（而不是缩略图）
                    href = img_link.get('href')
                    if href:
                        thumbnails.append(href)
                
                if thumbnails:
                    info['thumbnails'] = thumbnails
            
            # 设置封面图片（优先使用海报图）
            # 查找movie区域中的封面图
            movie_div = soup.select_one('.movie.cf')
            if movie_div:
                # 尝试获取jacket图片链接（优先级最高）
                jacket_link = movie_div.select_one('.package a[href*="/jacket/"]')
                if jacket_link and jacket_link.get('href'):
                    info['cover_url'] = jacket_link.get('href')
                # 如果没有jacket，尝试获取poster图片
                else:
                    poster_img = movie_div.select_one('video[poster]')
                    if poster_img and poster_img.get('poster'):
                        info['cover_url'] = poster_img.get('poster')
            
            # 如果上面没有找到封面图，则使用第一个缩略图作为封面
            if 'cover_url' not in info and 'thumbnails' in info and info['thumbnails']:
                info['cover_url'] = info['thumbnails'][0]
            
            # 提取预览视频URL
            if movie_div:
                video_source = movie_div.select_one('video source[src]')
                if video_source and video_source.get('src'):
                    info['sample_video_url'] = video_source.get('src')
            
            return info
            
        except Exception as e:
            self.logger.error(f"提取信息时出错: {str(e)}")
            return None
    
    def scrape(self, movie_id):
        """抓取影片信息
        
        Args:
            movie_id: 影片ID
            
        Returns:
            dict: 影片信息字典
        """
        # 搜索影片
        urls = self.search_movie(movie_id)
        if not urls:
            self.logger.warning(f"未找到影片: {movie_id}")
            return None
        
        # 获取详情页
        url = urls[0]
        soup = self.get_page(url)
        if not soup:
            self.logger.error(f"获取详情页失败: {url}")
            return None
        
        # 提取信息
        info = self.extract_info_from_page(soup, movie_id, url)
        
        return info

    def set_cover_url(self, info, cover_url):
        """手动设置封面图URL
        
        Args:
            info: 影片信息字典
            cover_url: 封面图URL
            
        Returns:
            dict: 更新后的影片信息字典
        """
        if not info:
            return info
            
        self.logger.info(f"手动设置封面图: {cover_url}")
        info['cover_url'] = cover_url
        return info 