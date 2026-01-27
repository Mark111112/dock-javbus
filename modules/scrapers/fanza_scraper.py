#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import json
import logging
import requests
import urllib3
from urllib.parse import urljoin, urlencode, quote, unquote
from bs4 import BeautifulSoup

from modules.scrapers.base_scraper import BaseScraper

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
        
        # 更新User-Agent为更现代的版本
        self.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none'
        })
        
    def create_session(self):
        """创建一个HTTP会话，针对DMM网站优化"""
        session = requests.Session()
        
        # 设置请求头
        session.headers.update(self.headers)
        
        # 设置cookies
        for key, value in self.cookies.items():
            session.cookies.set(key, value)
        
        # 设置适配器配置
        adapter = requests.adapters.HTTPAdapter(
            max_retries=requests.adapters.Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[500, 502, 503, 504, 520, 521, 522, 523, 524],
                allowed_methods=["HEAD", "GET", "OPTIONS"]
            )
        )
        
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        
        # SSL设置 - 处理DMM的SSL问题
        session.verify = False  # 禁用SSL验证
        
        return session
    
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
            
            # 如果是 video.dmm.co.jp 的客户端渲染页面，直接走 GraphQL
            if "video.dmm.co.jp" in url:
                content_id = self._extract_content_id_from_video_url(url)
                if content_id:
                    graph_info = self._fetch_video_dmm_content_by_content_id(content_id, movie_id)
                    if graph_info:
                        return graph_info
                # 无法解析或GraphQL失败则继续常规流程

            # 🎯 优化：检查是否已经在搜索过程中验证过此页面
            if hasattr(self, '_verified_page_cache') and url in self._verified_page_cache:
                self.logger.info(f"使用已验证的页面缓存")
                soup = self._verified_page_cache[url]
            else:
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
        
        # 3. 回退：尝试 video.dmm.co.jp 的 GraphQL 接口（客户端渲染）
        self.logger.info("尝试通过 video.dmm.co.jp GraphQL 接口获取详情")
        graph_info = self._fetch_video_dmm_content(movie_id)
        if graph_info:
            return graph_info

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
            f"{label}{number.zfill(5)}",       # 无连字符且零填充：ABC000123
            label + number                     # 无任何分隔：ABC123
        ]
        
        all_urls = []
        for term in search_terms:
            encoded_term = quote(term)
            search_url = self.search_url_template.format(encoded_term)
            
            self.logger.info(f"搜索URL: {search_url}")
            
            try:
                session = self.create_session()
                response = session.get(search_url, timeout=15)
                
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
                    
                    # 🎯 优化：找到结果后立即尝试获取详情页信息
                    self.logger.info(f"找到搜索结果，尝试验证最佳匹配的详情页...")
                    best_urls = self._find_best_match(urls, movie_id)
                    
                    if best_urls:
                        # 如果是 video.dmm.co.jp 链接，直接认为有效（后续用GraphQL获取）
                        test_url = best_urls[0]
                        if "video.dmm.co.jp" in test_url:
                            self.logger.info("检测到 video.dmm.co.jp 链接，跳过HTML验证，稍后用GraphQL获取详情")
                            return best_urls
                        
                        # 否则按原有方式验证HTML详情页
                        self.logger.info(f"验证详情页: {test_url}")
                        test_soup = self.get_page(test_url)
                        if test_soup and self._is_valid_detail_page(test_soup):
                            self.logger.info(f"验证成功，使用此搜索结果，跳过后续搜索")
                            if not hasattr(self, '_verified_page_cache'):
                                self._verified_page_cache = {}
                            self._verified_page_cache[test_url] = test_soup
                            return best_urls
                        else:
                            self.logger.warning(f"详情页验证失败，继续尝试其他搜索项")
                else:
                    self.logger.info(f"搜索 '{term}' 未找到结果")
                    
            except requests.exceptions.RequestException as e:
                self.logger.error(f"搜索请求异常: {str(e)}")
                continue
            except Exception as e:
                self.logger.error(f"搜索过程中出现未知错误: {str(e)}")
                continue
                
        # 如果有搜索结果但前面的验证都失败了，返回所有找到的URL
        if all_urls:
            return self._find_best_match(all_urls, movie_id)
        
        return []
    
    def _is_valid_detail_page(self, soup):
        """检查是否为有效的详情页
        
        Args:
            soup: BeautifulSoup对象
            
        Returns:
            bool: 是否为有效的详情页
        """
        if not soup:
            return False
            
        # 检查是否有影片标题
        title_tag = soup.find("h1", class_="item-name") or soup.find("h1", id="title")
        if not title_tag:
            return False
            
        # 检查是否有信息表格
        info_table = soup.find("table", class_="mg-b20") or soup.select_one("table.mg-b12")
        if not info_table:
            return False
            
        return True
    
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
            # 尝试通用方法：查找所有可能的详情链接
            all_links = soup.find_all("a", href=True)
            product_links = [
                link for link in all_links
                if ("cid=" in link.get("href") or 
                    "video.dmm.co.jp/av/content/?id=" in link.get("href") or
                    "video.dmm.co.jp/amateur/content/?id=" in link.get("href"))
            ]
        
        # 处理找到的链接
        if product_links:
            for link in product_links:
                href = link.get("href")
                if not href:
                    continue
                # 接受三类：cid= 详情页 或 video.dmm.co.jp 的 content 页面（av或amateur）
                if ("cid=" in href) or ("video.dmm.co.jp/av/content/?id=" in href) or ("video.dmm.co.jp/amateur/content/?id=" in href):
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
        label, number, clean_id = self.clean_movie_id(movie_id)
        label_part = label.lower() if label else ""
        number_part = number if number else ""
        
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
                
            # 5. video.dmm.co.jp 的内容页优先级最高（可直接GraphQL）
            if "video.dmm.co.jp/av/content" in url:
                priority += 1000
            elif "video.dmm.co.jp/amateur/content" in url:
                priority += 1000  # amateur内容也使用GraphQL，优先级相同
            
            # 5.5. 降低monthly链接的优先级（月额动画，通常不是我们想要的）
            if "monthly/" in url:
                priority -= 10000  # 大幅降低优先级，确保低于video.dmm.co.jp

            # 6. 检查URL中的cid匹配度
            cid_match = re.search(r'cid=([^/]+)', url, re.IGNORECASE)
            if cid_match:
                cid = cid_match.group(1).lower()
                
                # 6.1 完全匹配：cid与clean_id完全一致（忽略大小写）
                if cid == clean_id.lower():
                    priority += 10000  # 最高优先级
                    self.logger.info(f"找到完全匹配的cid: {cid} == {clean_id}")
                
                # 6.1.5 纯净匹配：优先选择没有后缀的CID（如ssni314而不是ssni314bod）
                elif label_part and number_part:
                    # 检查是否是纯净的label+number格式（没有额外后缀）
                    pure_pattern = f"^{label_part}{number_part}$"
                    if re.match(pure_pattern, cid):
                        priority += 8000  # 很高优先级，仅次于完全匹配
                        self.logger.info(f"找到纯净匹配的cid: {cid} == {label_part}{number_part}")
                
                # 6.2 精确匹配：检查label和number是否都匹配
                if label_part and number_part:
                    # 提取cid中的label和number部分
                    # 修改正则表达式以处理数字开头的CID（如1sdmf002）
                    cid_label_match = re.search(r'([a-z]+)', cid)
                    cid_number_match = re.search(r'[a-z]+(\d+)', cid)
                    
                    if cid_label_match and cid_number_match:
                        cid_label = cid_label_match.group(1)
                        cid_number = cid_number_match.group(1)
                        
                        # 调试信息
                        self.logger.info(f"分析CID: {cid}, 提取的label: {cid_label}, number: {cid_number}")
                        self.logger.info(f"目标label: {label_part}, number: {number_part}")
                        
                        # 如果label和number都匹配
                        if cid_label == label_part and cid_number == number_part:
                            priority += 5000  # 很高优先级
                            self.logger.info(f"找到精确匹配: {cid_label}{cid_number} == {label_part}{number_part}")
                        # 如果只有label匹配
                        elif cid_label == label_part:
                            priority += 1000
                            self.logger.info(f"找到label匹配: {cid_label} == {label_part}")
                        # 如果只有number匹配
                        elif cid_number == number_part:
                            priority += 500
                            self.logger.info(f"找到number匹配: {cid_number} == {number_part}")
                
                # 6.3 部分匹配：检查是否包含厂商代号
                if label_part and label_part in cid:
                    priority += 100
                    
            # 7. 检查video.dmm.co.jp的id参数匹配度
            video_id_match = re.search(r'[?&]id=([^&#]+)', url, re.IGNORECASE)
            if video_id_match:
                video_id = video_id_match.group(1).lower()
                
                # 7.1 完全匹配
                if video_id == clean_id.lower():
                    priority += 10000
                    self.logger.info(f"找到完全匹配的video id: {video_id} == {clean_id}")
                
                # 7.2 精确匹配
                elif label_part and number_part:
                    video_label_match = re.search(r'^([a-z]+)', video_id)
                    video_number_match = re.search(r'(\d+)', video_id)
                    
                    if video_label_match and video_number_match:
                        video_label = video_label_match.group(1)
                        video_number = video_number_match.group(1)
                        
                        if video_label == label_part and video_number == number_part:
                            priority += 5000
                            self.logger.info(f"找到精确匹配的video id: {video_label}{video_number} == {label_part}{number_part}")
                        elif video_label == label_part:
                            priority += 1000
                        elif video_number == number_part:
                            priority += 500
                    
            return priority
        
        # 按优先级排序
        sorted_urls = sorted(urls, key=get_priority, reverse=True)
        
        # 记录排序结果用于调试
        self.logger.info(f"搜索结果排序（前10个）:")
        for i, url in enumerate(sorted_urls[:10]):
            priority = get_priority(url)
            self.logger.info(f"  {i+1}. 优先级={priority}, URL={url}")
        
        # 特别检查是否包含目标CID
        target_cids = [f"cid={clean_id.lower()}", f"cid=1{label_part}{number_part}"]
        for target_cid in target_cids:
            matching_urls = [url for url in sorted_urls if target_cid in url.lower()]
            if matching_urls:
                self.logger.info(f"找到目标CID {target_cid} 的URL: {matching_urls[0]}")
                # 检查这个URL的优先级
                target_priority = get_priority(matching_urls[0])
                self.logger.info(f"目标URL优先级: {target_priority}")
            else:
                self.logger.info(f"未找到目标CID {target_cid} 的URL")
        
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
            
        URL模式说明：
        - 封面小图: https://pics.dmm.co.jp/mono/movie/adult/1start310v/1start310vps.jpg
        - 封面大图: https://pics.dmm.co.jp/mono/movie/adult/1start310v/1start310vpl.jpg
        - 预览小图: https://pics.dmm.co.jp/digital/video/1start310v/1start310v-1.jpg
        - 预览大图: https://pics.dmm.co.jp/digital/video/1start310v/1start310vjp-1.jpg
        """
        if not img_url:
            return None
        
        # 模式1: 封面图转换 (ps.jpg -> pl.jpg)
        if 'ps.jpg' in img_url:
            return img_url.replace('ps.jpg', 'pl.jpg')
        
        # 模式2: 封面图转换 (pt.jpg -> pl.jpg)
        if 'pt.jpg' in img_url:
            return img_url.replace('pt.jpg', 'pl.jpg')
        
        # 模式3: 预览图转换 (例如: 1start310v-1.jpg -> 1start310vjp-1.jpg)
        # 匹配模式: /video/番号/番号-数字.jpg
        import re
        match = re.search(r'/video/([^/]+)/\1-(\d+)\.jpg', img_url)
        if match:
            video_id = match.group(1)
            num = match.group(2)
            # 构建大图URL
            return img_url.replace(f'{video_id}-{num}.jpg', f'{video_id}jp-{num}.jpg')

        # 如果没有匹配到任何模式，返回原URL
        return img_url

    @staticmethod
    def _extract_text_with_line_breaks(element) -> str:
        if element is None:
            return ""

        if hasattr(element, "get_text"):
            raw_text = element.get_text(separator="\n")
        else:
            raw_text = str(element)

        if not raw_text:
            return ""

        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        raw_lines = [line.strip() for line in normalized.split("\n")]

        # 去掉首尾纯空行，避免前后多出来的空白段
        while raw_lines and raw_lines[0] == "":
            raw_lines.pop(0)
        while raw_lines and raw_lines[-1] == "":
            raw_lines.pop()

        # 规则：
        # - 普通情况下，由于 HTML 缩进，每一行文本后面会跟一个单独的空行，我们要去掉这些单空行；
        # - 但像 DMM 简介里那种连续 <br><br> 的地方，会形成 2 个以上连续空行，这里保留一个，
        #   作为“段落之间空一行”的效果。
        lines: list[str] = []
        blank_run = 0
        for line in raw_lines:
            if line == "":
                blank_run += 1
                continue
            # 处理之前累计的空行
            if blank_run >= 2 and lines:
                # 多个连续空行 => 段落分隔，保留一个空行
                lines.append("")
            # 如果 blank_run == 1，则认为是缩进噪声，直接丢弃
            blank_run = 0
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _remove_advertisement_from_text(text: str) -> str:
        """
        从简介文本中移除常见的广告/说明行，比如「コンビニ受取」「詳しくはこちら」等。
        只在整行包含这些关键词时才删除，尽量避免误伤正常内容。
        """
        if not text:
            return text

        ad_keywords = [
            "コンビニ受取",
            "詳しくはこちら",
            "対象商品です",
            "注文方法",
            "送料無料",
            "ポイント",
            "キャンペーン",
            "セット商品",
        ]

        lines = text.split("\n")
        filtered_lines = [
            line for line in lines
            if line.strip() and not any(k in line for k in ad_keywords)
        ]

        return "\n".join(filtered_lines).strip()
    
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
                # 演员（包含名称和头像）
                actresses = []
                actress_with_images = []
                actress_links = value_tag.find_all("a")
                if actress_links:
                    for link in actress_links:
                        name = link.text.strip()
                        if name and name != "：":
                            actresses.append(name)
                            # 尝试获取演员ID以构建头像URL
                            actress_id = None
                            href = link.get("href")
                            if href:
                                # 从URL中提取演员ID：/mono/person/-/id=9999/
                                id_match = re.search(r'/id=(\d+)/', href)
                                if id_match:
                                    actress_id = id_match.group(1)
                            
                            # 构建演员信息（包含头像URL）
                            actress_info = {"name": name}
                            if actress_id:
                                # DMM演员头像URL格式：https://pics.dmm.co.jp/mono/actjpgs/[actress_id].jpg
                                actress_info["id"] = actress_id
                                actress_info["avatar"] = f"https://pics.dmm.co.jp/mono/actjpgs/{actress_id}.jpg"
                            actress_with_images.append(actress_info)
                else:
                    # 无链接时直接获取文本
                    name = value_tag.text.strip()
                    if name and name != "：" and name != "----":
                        actresses.append(name)
                        actress_with_images.append({"name": name})
                        
                if actresses:
                    result["actresses"] = actresses
                    result["actress_details"] = actress_with_images
                    
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
        # 优先查找 #sample-image-block 中的图片
        thumbnail_links = soup.select("#sample-image-block img")
        if not thumbnail_links:
            thumbnail_links = soup.select(".position-relative.detail-cap a img")
        
        if thumbnail_links:
            for img in thumbnail_links:
                # 优先使用 data-lazy 属性（懒加载），其次是 data-src，最后是 src
                img_url = img.get("data-lazy") or img.get("data-src") or img.get("src")
                
                # 过滤掉占位图和无效图片
                if not img_url or img_url.endswith("noimage.jpg") or "dummy_ps.gif" in img_url:
                    continue
                
                # 转换小图为大图
                high_quality_url = self._convert_to_high_quality_image(img_url)
                if high_quality_url:
                    thumbnails.append(high_quality_url)
                    
        if thumbnails:
            result["thumbnails"] = thumbnails
            # 如果没有封面图，使用第一张预览图作为封面
            if not result.get("cover") and thumbnails:
                result["cover"] = thumbnails[0]
            
        # 6. 提取评分
        rating_element = soup.select_one(".d-review__average") or soup.select_one(".c-review__average") or soup.select_one(".c-rating-v2__average")
        if rating_element:
            rating_text = rating_element.text.strip()
            rating_match = re.search(r'([\d\.]+)', rating_text)
            if rating_match:
                result["rating"] = rating_match.group(1)
                
        # 7. 提取简介（更精准：优先提取正文段落，过滤广告/说明块）
        summary_text = None

        try:
            # 7.1 优先选择 page-detail 区域内的正文段落，过滤广告/说明
            detail_root = soup.select_one("div.page-detail")
            if detail_root:
                paragraph_candidates = detail_root.select("div.mg-b20.lh4 p, p.mg-b20")
            else:
                paragraph_candidates = soup.select("div.mg-b20.lh4 p, p.mg-b20")
            cleaned_candidates = []

            # 定义需过滤的关键词（常见广告/说明用语）
            ad_keywords = [
                "特典", "セット商品", "キャンペーン", "オフ", "セール", "詳しくはこちら", "コンビニ受取", "注文方法", "送料無料", "ポイント"
            ]

            for p in paragraph_candidates:
                text = self._extract_text_with_line_breaks(p)
                if not text:
                    continue
                # 过滤包含广告关键词或过短的段落
                if any(k in text for k in ad_keywords):
                    continue
                if len(text) < 50:
                    continue
                # 过滤处于广告说明容器内的段落（如 .d-boxother 或 .mg-t20）
                parent_classes = " ".join(p.parent.get("class", [])) if p.parent else ""
                if "d-boxother" in parent_classes or "mg-t20" in parent_classes:
                    continue
                cleaned_candidates.append(text)

            # 保留所有有效的段落，保持分段结构
            if cleaned_candidates:
                # 按长度排序，优先保留较长的段落，但保留所有有效段落
                cleaned_candidates.sort(key=len, reverse=True)
                # 如果只有一个段落，直接使用
                if len(cleaned_candidates) == 1:
                    summary_text = cleaned_candidates[0]
                else:
                    # 多个段落时，用双换行符分隔，保持分段
                    summary_text = "\n\n".join(cleaned_candidates)

            # 7.2 兜底：如果没拿到，退回旧的选择器
            if not summary_text:
                description_element = soup.select_one("#introduction-text") or soup.select_one(".mg-b20.lh4")
                if description_element:
                    paras = [self._extract_text_with_line_breaks(t) for t in description_element.select("p")]
                    paras = [t for t in paras if t and len(t) >= 50 and not any(k in t for k in ad_keywords)]
                    if paras:
                        # 保持分段结构
                        if len(paras) == 1:
                            summary_text = paras[0]
                        else:
                            summary_text = "\n\n".join(paras)
                    else:
                        summary_text = self._extract_text_with_line_breaks(description_element)
            # 7.3 再兜底：meta 描述
            if not summary_text:
                og_desc = soup.find("meta", attrs={"property": "og:description"})
                if og_desc and og_desc.get("content"):
                    summary_text = og_desc.get("content").strip()
            if not summary_text:
                meta_desc = soup.find("meta", attrs={"name": "description"})
                if meta_desc and meta_desc.get("content"):
                    summary_text = meta_desc.get("content").strip()
        except Exception:
            # 出错时退回最初策略，保证不影响整体抓取
            description_element = soup.select_one("#introduction-text") or soup.select_one(".mg-b20.lh4")
            if description_element:
                summary_text = self._extract_text_with_line_breaks(description_element)

        if summary_text:
            # 最后再做一层广告行过滤，防止兜底策略把「コンビニ受取」等说明文字带进来
            summary_text = self._remove_advertisement_from_text(summary_text)
            if summary_text:
                result["summary"] = summary_text

        # 8. 提取雑誌掲載コメント/AVライターコメント（作为摘要的补充段落）
        try:
            journal_comment = soup.select_one("div.journal-comment")
            if journal_comment:
                # 提取标题（dt）和内容（dd）
                dt = journal_comment.select_one("dt")
                dd = journal_comment.select_one("dd")
                if dt and dd:
                    title = dt.get_text().strip()
                    content = dd.get_text().strip()
                    if title and content:
                        # 将评价内容作为摘要的补充段落，保持分段结构
                        if "summary" in result:
                            # 如果已有摘要，添加换行符保持分段
                            result["summary"] += f"\n\n【{title}】\n{content}"
                        else:
                            # 如果没有摘要，直接使用评价内容
                            result["summary"] = f"【{title}】\n{content}"
        except Exception:
            # 出错时忽略，不影响整体抓取
            pass

        # 9. 提取用户评价（User Reviews）- 从专门的评价页面获取
        try:
            user_reviews = self._fetch_user_reviews_from_review_page(movie_id, result)
            if user_reviews:
                result["user_reviews"] = user_reviews
                
                # 同时将用户评价添加到摘要中（保持向后兼容）
                review_text = ""
                for review in user_reviews:
                    if review.get("title") and review.get("comment"):
                        review_text += f"\n\n【{review['title']}】\n{review['comment']}"
                    elif review.get("comment"):
                        review_text += f"\n\n【用户评价】\n{review['comment']}"
                
                if review_text and "summary" in result:
                    result["summary"] += review_text
                elif review_text:
                    result["summary"] = review_text.strip()
                    
        except Exception:
            # 出错时忽略，不影响整体抓取
            pass
    
        return result 

    def _fetch_user_reviews_from_review_page(self, movie_id, movie_data):
        """从专门的用户评价页面获取用户评价（支持多页爬取）"""
        try:
            # 从影片数据中提取cid
            cid = None
            if "url" in movie_data and "cid=" in movie_data["url"]:
                import re
                cid_match = re.search(r'cid=([^/&]+)', movie_data["url"])
                if cid_match:
                    cid = cid_match.group(1)
            
            # 如果没有cid，尝试从movie_id构造
            if not cid:
                # 尝试从movie_id构造cid（可能需要一些映射逻辑）
                cid = movie_id.lower()
            
            if not cid:
                self.logger.warning(f"无法获取cid来构建评价页面URL: {movie_id}")
                return []
            
            # 构建评价页面URL
            base_review_url = f"https://www.dmm.co.jp/mono/dvd/-/detail/review/=/cid={cid}/"
            self.logger.info(f"尝试获取用户评价: {base_review_url}")
            
            # 获取所有页面的评价
            all_user_reviews = []
            page = 1
            max_pages = 10  # 限制最大页数，避免无限循环
            session = self.create_session()
            
            while page <= max_pages:
                # 构建当前页面的URL
                if page == 1:
                    page_url = base_review_url
                else:
                    page_url = f"{base_review_url}?paging={page}&sort=value_desc#review_anchor"
                
                self.logger.info(f"获取第 {page} 页评价: {page_url}")
                
                # 获取当前页面
                response = session.get(page_url, timeout=15)
                if response.status_code != 200:
                    self.logger.warning(f"第 {page} 页请求失败，状态码: {response.status_code}")
                    break
                
                # 解析当前页面
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # 检查是否有评价内容
                review_units = soup.select("li.dcd-review__unit")
                if not review_units:
                    self.logger.info(f"第 {page} 页没有找到评价，停止爬取")
                    break
                
                self.logger.info(f"第 {page} 页找到 {len(review_units)} 条评价")
                
                # 处理当前页面的评价
                for unit in review_units:
                    try:
                        # 提取评价标题
                        title_elem = unit.select_one("span.dcd-review__unit__title")
                        title = title_elem.get_text().strip() if title_elem else ""
                        
                        # 提取评价内容 - 使用更精确的方法
                        comment_parts = []
                        
                        # 1. 首先提取所有可见的comment div（这是最可靠的方法）
                        comment_elems = unit.select("div.dcd-review__unit__comment")
                        for comment_elem in comment_elems:
                            # 处理HTML中的<br>标签，转换为换行符
                            comment_html = str(comment_elem)
                            # 将<br>和<br/>标签替换为换行符
                            import re
                            comment_html = re.sub(r'<br\s*/?>', '\n', comment_html)
                            # 然后提取文本内容
                            comment_text = BeautifulSoup(comment_html, 'html.parser').get_text().strip()
                            
                            # 只保留实际的评价内容，过滤掉警告和导航信息
                            if (comment_text and 
                                len(comment_text) > 20 and  # 内容足够长
                                not comment_text.startswith("※このレビューは作品の内容に関する記述が含まれています。") and
                                "レビューを表示する" not in comment_text and
                                "参考になりましたか" not in comment_text and
                                "違反を報告する" not in comment_text and
                                "投票しています" not in comment_text and
                                "このレビューは参考になりましたか" not in comment_text and
                                "不適切なレビューを報告する" not in comment_text and
                                "以下の内容に該当するレビューは報告できます" not in comment_text and
                                "個人情報の公開・漏洩" not in comment_text and
                                "特定の個人や企業等への嫌がらせ" not in comment_text and
                                "差別的な表現の使用" not in comment_text and
                                "無関係な宣伝スパム" not in comment_text and
                                "明らかに事実と異なる虚偽の主張" not in comment_text and
                                "報告後、内容を確認し" not in comment_text and
                                "より良いサービス環境のため" not in comment_text and
                                "キャンセル" not in comment_text and
                                "報告する" not in comment_text and
                                "エラーが発生しました" not in comment_text and
                                "再度時間をおいてお試しください" not in comment_text and
                                "購入・利用済み" not in comment_text and
                                "ビデオ(動画)" not in comment_text):
                                comment_parts.append(comment_text)
                        
                        # 2. 如果标准方法没有找到内容，尝试查找可能被折叠的内容
                        if not comment_parts:
                            # 查找可能包含评价内容的div，但排除已知的导航元素
                            excluded_classes = [
                                'dcd-review__unit__bottom',
                                'dcd-review__unit__voted', 
                                'dcd-review__unit__evaluate',
                                'dcd-review__unit__report',
                                'dcd-review__report-modal',
                                'dcd-review__modtogglelink-open'
                            ]
                            
                            # 查找所有div，但排除导航相关的
                            all_divs = unit.select("div")
                            for div in all_divs:
                                # 检查div的class是否在排除列表中
                                div_classes = div.get("class", [])
                                if any(excluded_class in div_classes for excluded_class in excluded_classes):
                                    continue
                                    
                                # 处理HTML中的<br>标签，转换为换行符
                                div_html = str(div)
                                div_html = re.sub(r'<br\s*/?>', '\n', div_html)
                                div_text = BeautifulSoup(div_html, 'html.parser').get_text().strip()
                                
                                if (div_text and 
                                    len(div_text) > 30 and  # 内容足够长
                                    "レビューを表示する" not in div_text and
                                    "参考になりましたか" not in div_text and
                                    "違反を報告する" not in div_text and
                                    "投票しています" not in div_text and
                                    "このレビューは参考になりましたか" not in div_text and
                                    "不適切なレビューを報告する" not in div_text and
                                    "以下の内容に該当するレビューは報告できます" not in div_text and
                                    "個人情報の公開・漏洩" not in div_text and
                                    "特定の個人や企業等への嫌がらせ" not in div_text and
                                    "差別的な表現の使用" not in div_text and
                                    "無関係な宣伝スパム" not in div_text and
                                    "明らかに事実と異なる虚偽の主張" not in div_text and
                                    "報告後、内容を確認し" not in div_text and
                                    "より良いサービス環境のため" not in div_text and
                                    "キャンセル" not in div_text and
                                    "報告する" not in div_text and
                                    "エラーが発生しました" not in div_text and
                                    "再度時間をおいてお試しください" not in div_text and
                                    "購入・利用済み" not in div_text and
                                    "ビデオ(動画)" not in div_text and
                                    not div_text.startswith("※") and
                                    div_text not in comment_parts):
                                    comment_parts.append(div_text)
                        
                        # 3. 如果还是没有找到内容，尝试从整个unit中智能提取
                        if not comment_parts:
                            unit_text = unit.get_text()
                            lines = unit_text.split('\n')
                            content_lines = []
                            in_content = False
                            
                            for line in lines:
                                line = line.strip()
                                if not line:
                                    continue
                                    
                                # 开始收集内容（在标题之后）
                                if title and title in line:
                                    in_content = True
                                    continue
                                    
                                # 停止收集内容（遇到评价者信息或导航元素）
                                if (reviewer and reviewer in line) or \
                                   "投票しています" in line or \
                                   "参考になりましたか" in line or \
                                   "違反を報告する" in line:
                                    break
                                    
                                # 收集内容行
                                if in_content and len(line) > 10:
                                    content_lines.append(line)
                            
                            if content_lines:
                                comment_parts.append('\n'.join(content_lines))
                        
                        # 合并所有评论内容，去重并保持顺序
                        seen = set()
                        unique_parts = []
                        for part in comment_parts:
                            if part not in seen and len(part.strip()) > 10:
                                seen.add(part)
                                unique_parts.append(part)
                        
                        comment = "\n\n".join(unique_parts) if unique_parts else ""
                        
                        # 提取评分
                        rating_elem = unit.select_one("span[class*='dcd-review-rating']")
                        rating = ""
                        if rating_elem:
                            class_name = " ".join(rating_elem.get("class", []))
                            rating_match = re.search(r'dcd-review-rating-(\d+)', class_name)
                            if rating_match:
                                rating_value = int(rating_match.group(1))
                                rating = f"{rating_value/10:.1f}" if rating_value > 0 else ""
                        
                        # 提取评价者信息
                        reviewer_elem = unit.select_one("span.dcd-review__unit__reviewer a")
                        reviewer = reviewer_elem.get_text().strip() if reviewer_elem else ""
                        
                        # 提取发布日期
                        date_elem = unit.select_one("span.dcd-review__unit__postdate")
                        post_date = date_elem.get_text().strip() if date_elem else ""
                        
                        # 只有当标题或内容存在时才添加
                        if title or comment:
                            review_data = {
                                "title": title,
                                "comment": comment,
                                "rating": rating,
                                "reviewer": reviewer,
                                "post_date": post_date
                            }
                            all_user_reviews.append(review_data)
                            
                    except Exception as e:
                        self.logger.warning(f"解析单个评价时出错: {str(e)}")
                        continue
                
                # 检查是否还有下一页
                # 查找分页链接，看是否有下一页
                next_page_link = soup.select_one("li a[href*='paging=']")
                if not next_page_link or page >= max_pages:
                    self.logger.info(f"没有更多页面或达到最大页数限制，停止爬取")
                    break
                
                page += 1
                # 添加延迟，避免请求过于频繁
                import time
                time.sleep(1)
            
            self.logger.info(f"成功获取 {len(all_user_reviews)} 条用户评价（共 {page-1} 页）")
            return all_user_reviews
            
        except Exception as e:
            self.logger.error(f"获取用户评价失败: {str(e)}")
            return []

    def _build_video_dmm_id(self, movie_id):
        """构造 video.dmm.co.jp 使用的ID（如 cosx00087）"""
        label, number, _ = self.clean_movie_id(movie_id)
        if not label:
            return None
        return f"{label.lower()}{number.zfill(5)}"

    def _video_dmm_url(self, content_id):
        """构造 video.dmm.co.jp 详情页 URL"""
        return f"https://video.dmm.co.jp/av/content/?id={content_id}"

    def _extract_content_id_from_video_url(self, url):
        """从 video.dmm.co.jp 链接中提取 id=XXXX 参数"""
        try:
            m = re.search(r'[?&]id=([^&#]+)', url, re.IGNORECASE)
            if m:
                return m.group(1)
            return None
        except Exception:
            return None

    def _fetch_video_dmm_content_by_content_id(self, content_id, movie_id=None):
        """使用已知 content_id（例如 1stcv00580、h_1732orecs00387）调用 GraphQL

        更新说明 (2026-01-27):
        FANZA 更新了 GraphQL API，旧的复杂查询已失效。
        现在使用简化的查询结构，直接查询所需字段。
        """
        try:
            if not content_id:
                return None

            graphql_url = "https://api.video.dmm.co.jp/graphql"

            # 简化的 GraphQL 查询 - 2026-01-27 更新
            query = """
query GetContent($id: ID!) {
  ppvContent(id: $id) {
    id
    floor
    title
    isExclusiveDelivery
    releaseStatus
    description
    deliveryStartDate
    makerReleasedAt
    duration
    contentType
    relatedWords
    packageImage {
      largeUrl
      mediumUrl
    }
    sampleImages {
      number
      imageUrl
      largeImageUrl
    }
    actresses {
      id
      name
      imageUrl
    }
    directors {
      id
      name
    }
    series {
      id
      name
    }
    maker {
      id
      name
    }
    label {
      id
      name
    }
    genres {
      id
      name
    }
    makerContentId
  }
  reviewSummary(contentId: $id) {
    average
    total
    withCommentTotal
  }
}
"""

            payload = {
                "operationName": "GetContent",
                "query": query,
                "variables": {"id": content_id}
            }

            session = self.create_session()
            session.headers.update({
                'Accept': 'application/graphql-response+json, application/json',
                'Content-Type': 'application/json',
                'Origin': 'https://video.dmm.co.jp',
                'Referer': self._video_dmm_url(content_id),
                'Sec-Fetch-Site': 'same-site',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Dest': 'empty',
                'Fanza-Device': 'BROWSER',
                'Accept-Language': session.headers.get('Accept-Language', 'ja,en-US;q=0.9,en;q=0.8'),
                'Accept-Encoding': 'gzip, deflate, br, zstd'
            })

            resp = session.post(graphql_url, data=json.dumps(payload), timeout=20)
            if resp.status_code != 200:
                self.logger.warning(f"GraphQL 请求失败，状态码: {resp.status_code}")
                return None

            data = resp.json()
            if isinstance(data, dict) and data.get('errors'):
                self.logger.warning(f"GraphQL返回错误: {data.get('errors')}")
                return None

            content = (data or {}).get('data', {}).get('ppvContent')
            if not content:
                self.logger.info("GraphQL 无内容返回")
                return None

            url = self._video_dmm_url(content_id)
            result = {
                "source": "fanza",
                "id": movie_id or content_id,
                "url": url,
                "title": content.get("title") or ""
            }

            maker_released_at = content.get("makerReleasedAt") or ""
            if maker_released_at:
                result["release_date"] = maker_released_at.split("T")[0].replace("/", "-")

            duration_sec = content.get("duration")
            if isinstance(duration_sec, int) and duration_sec > 0:
                minutes = str(int(round(duration_sec / 60)))
                result["duration"] = minutes + "分钟"

            # 提取演员信息（包含头像）
            performer_nodes = content.get("performers") or content.get("actresses") or []
            if performer_nodes:
                actresses = []
                actress_details = []
                for performer in performer_nodes:
                    if not isinstance(performer, dict):
                        continue
                    name = performer.get("name")
                    if name:
                        actresses.append(name)
                        performer_id = performer.get("id") or performer.get("performerId")
                        avatar_url = performer.get("imageUrl") or performer.get("avatarUrl")
                        actress_info = {
                            "name": name,
                            "id": performer_id or ""
                        }
                        if not avatar_url and performer_id:
                            avatar_url = f"https://pics.dmm.co.jp/mono/actjpgs/{performer_id}.jpg"
                        if avatar_url:
                            actress_info["avatar"] = avatar_url
                        actress_details.append(actress_info)
                if actresses:
                    result["actresses"] = actresses
                    result["actress_details"] = actress_details

            directors_data = content.get("directors") or []
            if directors_data:
                director_names = [
                    d.get("name") for d in directors_data if isinstance(d, dict) and d.get("name")
                ]
                if director_names:
                    if len(director_names) == 1:
                        result["director"] = director_names[0]
                    else:
                        result["director"] = director_names

            series_info = content.get("series")
            if isinstance(series_info, dict):
                series_name = series_info.get("name")
                if series_name:
                    result["series"] = series_name
            elif isinstance(series_info, list) and series_info:
                series_names = [s.get("name") for s in series_info if isinstance(s, dict) and s.get("name")]
                if series_names:
                    result["series"] = series_names[0] if len(series_names) == 1 else series_names

            content_floor = content.get("floor")
            if content_floor:
                result["floor"] = content_floor

            release_status = content.get("releaseStatus")
            if release_status:
                result["release_status"] = release_status

            delivery_start = content.get("deliveryStartDate")
            if delivery_start:
                result["delivery_start_date"] = delivery_start.split("T")[0].replace("/", "-")

            is_exclusive = content.get("isExclusiveDelivery")
            if is_exclusive is not None:
                result["is_exclusive_delivery"] = is_exclusive

            content_type = content.get("contentType")
            if content_type:
                result["content_type"] = content_type

            related_words = content.get("relatedWords")
            if related_words:
                result["related_words"] = related_words

            maker = (content.get("maker") or {}).get("name")
            if maker:
                result["maker"] = maker
            label_name = (content.get("label") or {}).get("name")
            if label_name:
                result["label"] = label_name

            maker_content_id = content.get("makerContentId")
            if maker_content_id:
                result["product_code"] = maker_content_id

            genres = [g.get("name") for g in (content.get("genres") or []) if g.get("name")]
            if genres:
                result["genres"] = genres

            package_image = content.get("packageImage") or {}
            cover = package_image.get("largeUrl") or package_image.get("mediumUrl")
            if cover:
                result["cover"] = cover

            thumbs = []
            for img in (content.get("sampleImages") or []):
                large = img.get("largeImageUrl") or img.get("imageUrl")
                if large:
                    thumbs.append(large)
            if thumbs:
                result["thumbnails"] = thumbs

            review = (data or {}).get('data', {}).get('reviewSummary') or {}
            average = review.get('average')
            if average is not None:
                result["rating"] = str(average)

            total_reviews = review.get('total')
            if total_reviews is not None:
                result["rating_count"] = total_reviews

            with_comment_total = review.get('withCommentTotal')
            if with_comment_total is not None:
                result["rating_with_comment_total"] = with_comment_total

            desc = content.get("description")
            if desc:
                result["summary"] = re.sub(r'<br\s*/?>', '\n', desc).strip()

            # 获取用户评价
            user_reviews = self._fetch_video_dmm_user_reviews(content_id)
            if user_reviews:
                result["user_reviews"] = user_reviews
                # 将用户评价也添加到summary中（向后兼容）
                review_texts = []
                for review in user_reviews:
                    review_text = f"【{review.get('title', '')}】\n{review.get('comment', '')}"
                    review_texts.append(review_text)

                if review_texts:
                    if result.get("summary"):
                        result["summary"] += "\n\n" + "\n\n".join(review_texts)
                    else:
                        result["summary"] = "\n\n".join(review_texts)

            self.logger.info("通过 GraphQL 成功获取详情（content_id）")
            return result

        except requests.exceptions.RequestException as e:
            self.logger.error(f"GraphQL 请求异常: {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"GraphQL 解析异常: {str(e)}")
            return None

    def _fetch_video_dmm_user_reviews(self, content_id):
        """从video.dmm.co.jp获取用户评价"""
        try:
            # 构建用户评价GraphQL查询
            query = """
            query UserReviews($id: ID!, $sort: ReviewSort!, $offset: Int!) {
                reviews(contentId: $id, sort: $sort, limit: 10, offset: $offset) {
                    items {
                        id
                        title
                        rating
                        reviewerId
                        nickname
                        isPurchased
                        comment
                        helpfulCount
                        service
                        isExposure
                        publishDate
                        __typename
                    }
                    __typename
                }
            }
            """
            
            variables = {
                "id": content_id,
                "offset": 0,
                "sort": "HELPFUL_COUNT_DESC"
            }
            
            payload = {
                "operationName": "UserReviews",
                "query": query,
                "variables": variables
            }
            
            # 发送GraphQL请求
            session = self.create_session()
            session.headers.update({
                'Accept': 'application/graphql-response+json, application/json',
                'Content-Type': 'application/json',
                'Origin': 'https://video.dmm.co.jp',
                'Referer': self._video_dmm_url(content_id)
            })
            
            response = session.post(
                "https://api.video.dmm.co.jp/graphql",
                data=json.dumps(payload),
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                if "data" in data and "reviews" in data["data"] and "items" in data["data"]["reviews"]:
                    reviews = data["data"]["reviews"]["items"]
                    self.logger.info(f"成功从video.dmm.co.jp获取 {len(reviews)} 条用户评价: {content_id}")
                    
                    # 转换数据格式以匹配现有结构
                    user_reviews = []
                    for review in reviews:
                        # 处理日期格式
                        publish_date = review.get("publishDate", "")
                        if publish_date:
                            # 转换ISO格式日期为简单格式
                            try:
                                from datetime import datetime
                                dt = datetime.fromisoformat(publish_date.replace('Z', '+00:00'))
                                publish_date = dt.strftime("%Y-%m-%d")
                            except:
                                publish_date = publish_date[:10] if len(publish_date) >= 10 else publish_date
                        
                        review_data = {
                            "title": review.get("title", ""),
                            "comment": review.get("comment", ""),
                            "rating": str(review.get("rating", 0)),
                            "reviewer": review.get("nickname", ""),
                            "post_date": publish_date,
                            "helpful_count": review.get("helpfulCount", 0),
                            "is_purchased": review.get("isPurchased", False),
                            "service": review.get("service", "")
                        }
                        user_reviews.append(review_data)
                    
                    return user_reviews
                else:
                    self.logger.warning(f"GraphQL响应中未找到reviews数据: {content_id}")
                    return []
            else:
                self.logger.warning(f"用户评价GraphQL请求失败，状态码: {response.status_code}")
                return []
                
        except Exception as e:
            self.logger.error(f"从video.dmm.co.jp获取用户评价失败: {str(e)}")
            return []

    def _fetch_video_dmm_content(self, movie_id):
        """基于 movie_id 推导 content_id 后调用 GraphQL"""
        content_id = self._build_video_dmm_id(movie_id)
        return self._fetch_video_dmm_content_by_content_id(content_id, movie_id)
