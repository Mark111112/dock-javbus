#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
AV-League 演员信息爬虫 (快速版)
使用 requests + BeautifulSoup，无需 Playwright
"""

import re
import json
import logging
import time
import random
from typing import Dict, List, Optional
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup


class AVLeagueScraperFast:
    """AV-League 演员信息爬虫类 (快速版)"""

    BASE_URL = "https://www.av-league.com"
    SEARCH_URL = f"{BASE_URL}/search/search.php"
    ACTRESS_URL_TEMPLATE = f"{BASE_URL}/actress/{{id}}.html"

    # 日志
    logger = logging.getLogger('AVLeagueScraperFast')

    def __init__(self):
        """初始化爬虫"""
        self.base_url = self.BASE_URL

        # 请求头
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
            'Connection': 'keep-alive',
        }

        # Cookie 设置
        self.cookies = {
            'age_check_done': '1',  # 年龄确认
        }

        # Session 复用连接
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def get_page(self, url: str, retry: int = 3, delay: float = 0) -> Optional[BeautifulSoup]:
        """获取并解析页面

        Args:
            url: 页面URL
            retry: 重试次数
            delay: 请求延迟（秒）

        Returns:
            BeautifulSoup: 解析后的页面 或 None（如果获取失败）
        """
        for attempt in range(retry):
            try:
                start_time = time.time()

                # 添加小延迟防止请求过快
                if delay > 0:
                    time.sleep(delay)

                response = self.session.get(
                    url,
                    cookies=self.cookies,
                    timeout=30,
                    allow_redirects=True
                )

                elapsed = time.time() - start_time

                # 检查响应状态
                if response.status_code == 200:
                    self.logger.info(f"页面获取成功 ({response.status_code}) - 耗时: {elapsed:.2f}秒")
                    return BeautifulSoup(response.text, 'html.parser')
                elif response.status_code == 404:
                    self.logger.warning(f"页面不存在 (404): {url}")
                    return None
                else:
                    self.logger.warning(f"HTTP {response.status_code}: {url}")

            except requests.RequestException as e:
                self.logger.error(f"请求失败 (尝试 {attempt+1}/{retry}): {e}")

        self.logger.error(f"无法获取页面: {url}")
        return None

    def search_actress(self, name: str) -> Optional[Dict]:
        """搜索演员并获取详细信息

        Args:
            name: 演员名称（如 "森沢かな", "もりさわかな"）
                   支持带别名的格式如 "森沢かな（飯岡かなこ）" 会自动去除括号部分

        Returns:
            演员信息字典，如果未找到返回 None
        """
        import time
        timings = {}

        # 去除括号中的别名（如 "森沢かな（飯岡かなこ）" -> "森沢かな"）
        clean_name = re.sub(r'（.+?）', '', name)
        clean_name = re.sub(r'\(.+?\)', '', clean_name).strip()

        self.logger.info(f"搜索演员: {name} -> 清理后: {clean_name}")

        # 访问搜索页面
        search_url = f"{self.SEARCH_URL}?k={quote(clean_name)}"

        start = time.time()
        soup = self.get_page(search_url)
        timings['search_page'] = time.time() - start
        self.logger.info(f"[计时] 搜索页面获取: {timings['search_page']:.2f}秒")

        if not soup:
            self.logger.warning(f"搜索页面获取失败: {name}")
            return None

        # 解析搜索结果
        start = time.time()
        actress_info = self._parse_search_result(soup, clean_name)
        timings['parse_search'] = time.time() - start
        self.logger.info(f"[计时] 搜索结果解析: {timings['parse_search']:.4f}秒")

        if not actress_info:
            self.logger.warning(f"未找到演员: {name}")
            return None

        self.logger.info(f"找到演员: {actress_info['name']} (ID: {actress_info['id']})")

        # 获取演员详情
        start = time.time()
        actress_data = self.get_actress_by_id(actress_info['id'])
        timings['detail_page'] = time.time() - start
        self.logger.info(f"[计时] 演员详情页获取: {timings['detail_page']:.2f}秒")

        # 汇总
        total = sum(timings.values())
        self.logger.info(f"[计时] 总耗时: {total:.2f}秒 (搜索页: {timings['search_page']:.2f}s + 解析: {timings['parse_search']:.4f}s + 详情页: {timings['detail_page']:.2f}s)")

        return actress_data

    def get_actress_by_id(self, actress_id: str, delay: float = 0.1) -> Optional[Dict]:
        """通过演员ID获取详细信息

        Args:
            actress_id: 演员ID（如 "9199"）
            delay: 请求延迟（秒），默认0.1秒

        Returns:
            演员信息字典，如果未找到返回 None
        """
        self.logger.info(f"获取演员信息 (ID: {actress_id})")

        url = self.ACTRESS_URL_TEMPLATE.format(id=actress_id)
        soup = self.get_page(url, delay=delay)

        if not soup:
            self.logger.warning(f"演员页面获取失败 (ID: {actress_id})")
            return None

        return self._parse_actress_detail(soup, actress_id)

    def _parse_search_result(self, soup: BeautifulSoup, name: str) -> Optional[Dict]:
        """解析搜索结果页面

        Args:
            soup: BeautifulSoup 对象
            name: 搜索的演员名称（已清理，无括号别名）

        Returns:
            包含 id, name 的字典，或 None
        """
        # 查找所有演员链接
        links = soup.find_all('a', href=re.compile(r'/actress/\d+\.html'))

        for link in links:
            href = link.get('href')
            text = link.get_text(strip=True)

            if text and len(text) > 0:
                # 检查是否匹配（支持部分匹配，因为搜索结果可能包含别名）
                if name in text or text in name:
                    # 提取演员ID
                    match = re.search(r'/actress/(\d+)\.html', href)
                    if match:
                        return {
                            'id': match.group(1),
                            'name': text,
                            'url': urljoin(self.BASE_URL, href)
                        }

        return None

    def _parse_actress_detail(self, soup: BeautifulSoup, actress_id: str) -> Optional[Dict]:
        """解析演员详情页面

        Args:
            soup: BeautifulSoup 对象
            actress_id: 演员ID

        Returns:
            演员信息字典
        """
        result = {
            'source': 'av-league',
            'url': self.ACTRESS_URL_TEMPLATE.format(id=actress_id),
            'actress_id': actress_id
        }

        # 1. 提取标题和别名
        h1 = soup.find('h1')
        if h1:
            full_text = h1.get_text(strip=True)
            # 格式: 森沢かな（もりさわかな） 別名 : 飯岡かなこ、飯島恭子...
            name_match = re.search(r'^(.+?)（(.+?)）', full_text)
            if name_match:
                result['name'] = name_match.group(1)
                result['name_kana'] = name_match.group(2)
            else:
                result['name'] = full_text.split('別名')[0].strip()

            # 提取别名
            if '別名 :' in full_text:
                alias_part = full_text.split('別名 :')[1]
                result['aliases'] = [a.strip() for a in alias_part.split('、') if a.strip()]

        # 2. 提取资料表格
        table = soup.find('table')
        if table:
            rows = table.find_all('tr')
            for row in rows:
                th = row.find('th')
                td = row.find('td')
                if not th or not td:
                    continue

                label = th.get_text(strip=True)
                value = td.get_text(strip=True)

                if label == '3サイズ':
                    # B:82（E） / W:56 / H:86
                    size_match = re.search(r'B:(\d+)(?:（([A-Z])）)?\s*/\s*W:(\d+)\s*/\s*H:(\d+)', value)
                    if size_match:
                        result['bust'] = size_match.group(1)
                        result['cup'] = size_match.group(2) or ''
                        result['waist'] = size_match.group(3)
                        result['hip'] = size_match.group(4)

                elif label == '身長':
                    height_match = re.search(r'(\d+)', value)
                    if height_match:
                        result['height'] = height_match.group(1)

                elif label == '血液型':
                    result['blood_type'] = value

                elif label == '生年月日':
                    # 1992年5月9日（33歳）
                    birth_match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日(?:（(\d+)歳）)?', value)
                    if birth_match:
                        result['birthday'] = f"{birth_match.group(1)}-{birth_match.group(2).zfill(2)}-{birth_match.group(3).zfill(2)}"
                        if birth_match.group(4):
                            result['age'] = int(birth_match.group(4))

                elif label == '出身':
                    result['birthplace'] = value

                elif label == 'デビュー':
                    result['debut'] = value

                elif label == 'Twitter':
                    twitter_link = td.find('a')
                    if twitter_link:
                        href = twitter_link.get('href')
                        if href:
                            result['twitter'] = href
                            screen_match = re.search(r'twitter\.com/([^/]+)', href)
                            if screen_match:
                                result['twitter_screen_name'] = screen_match.group(1)

                elif label == 'インスタ':
                    insta_link = td.find('a')
                    if insta_link:
                        href = insta_link.get('href')
                        if href:
                            result['instagram'] = href
                            screen_match = re.search(r'instagram\.com/([^/]+)', href)
                            if screen_match:
                                result['instagram_screen_name'] = screen_match.group(1)

                elif label == '出演数':
                    work_match = re.search(r'(\d+)', value)
                    if work_match:
                        result['work_count'] = int(work_match.group(1))

                elif label == '単体本数':
                    solo_match = re.search(r'(\d+)', value)
                    if solo_match:
                        result['solo_count'] = int(solo_match.group(1))

                elif label == 'VR有無':
                    result['has_vr'] = (value == 'あり')

                elif label == 'タグ':
                    result['tags'] = [t.strip() for t in value.split('、') if t.strip()]

        # 3. 提取头像图片
        img = soup.find('img', alt=re.compile(re.escape(result.get('name', ''))))
        if img and img.get('src') and 'loading.gif' not in img.get('src', ''):
            avatar_src = img.get('src')
            # 如果是相对路径，转换为绝对路径
            if avatar_src.startswith('/'):
                avatar_src = urljoin(self.BASE_URL, avatar_src)
            result['avatar_url'] = avatar_src

        if not result.get('name'):
            self.logger.warning(f"无法提取演员信息 (ID: {actress_id})")
            return None

        # 4. 提取评论
        result['comments'] = self._parse_comments(soup)

        # 5. 提取 Instagram 图片
        result['instagram_images'] = self._parse_instagram_images(soup)

        self.logger.info(f"成功获取演员信息: {result.get('name')} (生日: {result.get('birthday')}, 身高: {result.get('height')})")

        return result

    def _parse_comments(self, soup: BeautifulSoup) -> List[Dict]:
        """解析评论

        Args:
            soup: BeautifulSoup 对象

        Returns:
            评论列表
        """
        comments = []

        # 找到包含"コメント"的h2标题
        h2s = soup.find_all('h2')
        comments_h2 = None
        for h2 in h2s:
            if 'コメント' in h2.get_text():
                comments_h2 = h2
                break

        if not comments_h2:
            return comments

        # 找到评论表格
        comments_table = None
        current = comments_h2.find_next_sibling()
        while current:
            if current.name == 'table':
                comments_table = current
                break
            nested = current.find('table')
            if nested:
                comments_table = nested
                break
            current = current.find_next_sibling()
            if current and current.name == 'h2':
                break

        if not comments_table:
            return comments

        rows = comments_table.find_all('tr')
        for row in rows:
            # 查找所有 td，跳过只有 th 的行
            tds = row.find_all('td')
            if not tds:
                continue

            # 使用第一个 td
            td = tds[0]

            # 评论文本在第一个span中
            comment_span = td.find('span', class_=lambda c: c != 'co-hist-list-td-s')
            comment_text = comment_span.get_text(strip=True) if comment_span else ''

            # 评论者信息在 p.co-hist-list-td-info 中
            info_p = td.find('p', class_='co-hist-list-td-info')
            commenter = '名無し'
            date = None
            time = None
            user_id = None

            if info_p:
                p_text = info_p.get_text(strip=True)
                # 匹配: (かわむらよしとさん　2026/1/8 14:38　ID:13306)
                match = re.search(r'\((.+?)\u3000(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{2}):(\d{2})\s+ID:(\d+)\)$', p_text)
                if match:
                    commenter = match.group(1)
                    date = f"{match.group(2)}-{match.group(3).zfill(2)}-{match.group(4).zfill(2)}"
                    time = f"{match.group(5)}:{match.group(6)}"
                    user_id = match.group(7)

            # 过滤垃圾广告
            spam_keywords = ['夜魅館', '出張サービス', 'TG：', 'Gleezy：', 'LINE ：', '天使の癒し']
            if len(comment_text) < 10 or any(kw in comment_text for kw in spam_keywords):
                continue

            if comment_text:
                comments.append({
                    'comment': comment_text,
                    'commenter': commenter,
                    'date': date,
                    'time': time,
                    'user_id': user_id
                })

        self.logger.info(f"提取到 {len(comments)} 条评论")
        return comments

    def _parse_instagram_images(self, soup: BeautifulSoup) -> List[Dict]:
        """解析 Instagram 图片

        Args:
            soup: BeautifulSoup 对象

        Returns:
            Instagram 图片列表
        """
        images = []

        # 找到所有Instagram图片（使用 data-layzr 属性，因为 src 是 loading.gif）
        imgs = soup.find_all('img', alt=re.compile(r'インスタグラム画像'))

        for img in imgs:
            # 优先使用 data-layzr 属性（懒加载的实际图片URL）
            img_url = img.get('data-layzr') or img.get('data-src') or img.get('src')
            if img_url and 'loading.gif' not in img_url:
                # 如果是相对路径，转换为绝对路径
                if img_url.startswith('/'):
                    img_url = urljoin(self.BASE_URL, img_url)
                images.append({
                    'url': img_url,
                    'alt': img.get('alt', '')
                })

        self.logger.info(f"提取到 {len(images)} 张 Instagram 图片")
        return images

    def normalize_for_javbus(self, av_league_data: Dict) -> Dict:
        """将 AV-League 数据转换为 javbus 格式

        Args:
            av_league_data: 从 AV-League 获取的原始数据

        Returns:
            javbus 格式的数据
        """
        if not av_league_data:
            return {}

        normalized = {}

        # 基本字段映射
        if av_league_data.get('birthday'):
            normalized['birthday'] = av_league_data['birthday']
        if av_league_data.get('age'):
            normalized['age'] = av_league_data['age']
        if av_league_data.get('height'):
            normalized['height'] = av_league_data['height']
        if av_league_data.get('bust'):
            normalized['bust'] = av_league_data['bust']
        if av_league_data.get('waist'):
            normalized['waistline'] = av_league_data['waist']
        if av_league_data.get('hip'):
            normalized['hipline'] = av_league_data['hip']
        if av_league_data.get('birthplace'):
            normalized['birthplace'] = av_league_data['birthplace']
        if av_league_data.get('cup'):
            normalized['cup'] = av_league_data['cup']

        # av-league 特有字段
        if av_league_data.get('aliases'):
            normalized['aliases'] = av_league_data['aliases']

        if av_league_data.get('avatar_url'):
            normalized['av_league_avatar'] = av_league_data['avatar_url']

        if av_league_data.get('debut'):
            normalized['debut_date'] = av_league_data['debut']

        if av_league_data.get('work_count'):
            normalized['video_count'] = av_league_data['work_count']

        if av_league_data.get('solo_count') is not None:
            normalized['solo_count'] = av_league_data['solo_count']

        if av_league_data.get('tags'):
            normalized['tags'] = av_league_data['tags']

        # 社交媒体
        if av_league_data.get('twitter'):
            normalized['twitter'] = av_league_data['twitter']
        if av_league_data.get('instagram'):
            normalized['instagram'] = av_league_data['instagram']

        # Instagram 图片
        if av_league_data.get('instagram_images'):
            normalized['instagram_images'] = av_league_data['instagram_images']

        # 评论
        if av_league_data.get('comments'):
            normalized['av_league_comments'] = av_league_data['comments']

        # 数据源标识
        normalized['data_source'] = 'av-league'
        normalized['av_league_updated'] = int(time.time())
        normalized['data_source_url'] = av_league_data.get('url')

        return normalized


# 便捷函数
def search_actress(name: str) -> Optional[Dict]:
    """同步搜索演员（便捷函数）

    Args:
        name: 演员名称

    Returns:
        演员信息字典，如果未找到返回 None
    """
    scraper = AVLeagueScraperFast()
    return scraper.search_actress(name)


def get_actress_by_id(actress_id: str) -> Optional[Dict]:
    """通过ID获取演员信息（便捷函数）

    Args:
        actress_id: 演员ID

    Returns:
        演员信息字典，如果未找到返回 None
    """
    scraper = AVLeagueScraperFast()
    return scraper.get_actress_by_id(actress_id)


def normalize_for_javbus(av_league_data: Dict) -> Dict:
    """将 AV-League 数据转换为 javbus 格式（便捷函数）"""
    scraper = AVLeagueScraperFast()
    return scraper.normalize_for_javbus(av_league_data)
