#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
AV-League 演员信息爬虫
从 av-league.com 获取演员详细信息，用于补充 javbus 数据
"""

import re
import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote, urljoin

# Playwright 用于绕过 Cloudflare
try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None
    Browser = None
    Page = None


class AVLeagueScraper:
    """AV-League 演员信息爬虫类"""

    BASE_URL = "https://www.av-league.com"
    SEARCH_URL = f"{BASE_URL}/search/search.php"
    ACTRESS_URL_TEMPLATE = f"{BASE_URL}/actress/{{id}}.html"

    # 日志
    logger = logging.getLogger('AVLeagueScraper')

    def __init__(self):
        """初始化爬虫"""
        # 直接在这里检查 Playwright
        try:
            from playwright.async_api import async_playwright, Browser, Page
            self._playwright_available = True
            self._async_playwright = async_playwright
            self._Browser = Browser
            self._Page = Page
        except ImportError:
            self._playwright_available = False
            raise ImportError("Playwright 未安装，请运行: pip install playwright")

        self._browser: Optional['Browser'] = None
        self._playwright = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _get_browser(self) -> 'Browser':
        """获取或创建浏览器实例"""
        if self._browser is None:
            self._playwright = await self._async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
        return self._browser

    async def close(self):
        """关闭浏览器"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def search_actress(self, name: str) -> Optional[Dict]:
        """搜索演员并获取详细信息

        Args:
            name: 演员名称（如 "森沢かな", "永瀬ゆい"）

        Returns:
            演员信息字典，如果未找到返回 None
        """
        self.logger.info(f"搜索演员: {name}")

        browser = await self._get_browser()
        page = await browser.new_page()

        try:
            # 访问搜索页面
            search_url = f"{self.SEARCH_URL}?k={quote(name)}"
            self.logger.info(f"搜索URL: {search_url}")

            await page.goto(search_url, wait_until='networkidle', timeout=30000)

            # 等待页面加载
            await asyncio.sleep(2)

            # 查找演员链接
            actress_info = await page.evaluate(r"""() => {
                // 查找演员链接
                const links = document.querySelectorAll('a[href*="/actress/"]');

                for (const link of links) {
                    const href = link.getAttribute('href');
                    const text = link.textContent.trim();

                    // 匹配演员ID
                    const match = href.match(/\/actress\/(\d+)\.html/);
                    if (match && text && text.length > 0) {
                        return {
                            id: match[1],
                            url: href,
                            name: text
                        };
                    }
                }

                return null;
            }""")

            if not actress_info:
                self.logger.warning(f"未找到演员: {name}")
                return None

            self.logger.info(f"找到演员: {actress_info['name']} (ID: {actress_info['id']})")

            # 获取演员详情
            actress_data = await self._fetch_actress_detail(page, actress_info['id'])

            return actress_data

        except Exception as e:
            self.logger.error(f"搜索演员时出错: {str(e)}")
            return None
        finally:
            await page.close()

    async def get_actress_by_id(self, actress_id: str) -> Optional[Dict]:
        """通过演员ID获取详细信息

        Args:
            actress_id: 演员ID（如 "9199"）

        Returns:
            演员信息字典，如果未找到返回 None
        """
        self.logger.info(f"获取演员信息 (ID: {actress_id})")

        browser = await self._get_browser()
        page = await browser.new_page()

        try:
            actress_data = await self._fetch_actress_detail(page, actress_id)
            return actress_data

        except Exception as e:
            self.logger.error(f"获取演员信息时出错: {str(e)}")
            return None
        finally:
            await page.close()

    async def _fetch_actress_detail(self, page: 'Page', actress_id: str) -> Optional[Dict]:
        """获取演员详情页面信息

        Args:
            page: Playwright Page 对象
            actress_id: 演员ID

        Returns:
            演员信息字典
        """
        url = self.ACTRESS_URL_TEMPLATE.format(id=actress_id)
        self.logger.info(f"访问演员页面: {url}")

        await page.goto(url, wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)

        # 提取演员信息
        data = await page.evaluate(r"""() => {
            const result = {
                source: 'av-league',
                url: window.location.href,
                actress_id: window.location.pathname.match(/\/actress\/(\d+)\.html/)?.[1] || null
            };

            // 1. 提取标题和别名
            const h1 = document.querySelector('h1');
            if (h1) {
                const fullText = h1.textContent.trim();
                // 格式: 森沢かな（もりさわかな） 別名 : 飯岡かなこ、飯島恭子...
                const nameMatch = fullText.match(/^(.+?)\uff08(.+?)\uff09/);  // （）
                if (nameMatch) {
                    result.name = nameMatch[1];
                    result.name_kana = nameMatch[2];
                } else {
                    result.name = fullText.split('\u5225\u540d')[0].trim();  // 別名
                }

                // 提取别名
                if (fullText.includes('\u5225\u540d :')) {  // 別名 :
                    const aliasPart = fullText.split('\u5225\u540d :')[1];
                    result.aliases = aliasPart.split('\u3001').map(a => a.trim()).filter(a => a);  // 、
                }
            }

            // 2. 提取资料表格
            const table = document.querySelector('table');
            if (table) {
                const rows = table.querySelectorAll('tr');
                rows.forEach(row => {
                    const th = row.querySelector('th');
                    const td = row.querySelector('td');
                    if (!th || !td) return;

                    const label = th.textContent.trim();
                    const value = td.textContent.trim();

                    switch (label) {
                        case '3\u30b5\u30a4\u30ba':  // 3サイズ
                            // B:82（E） / W:56 / H:86 - 使用 Unicode 转义
                            const sizeMatch = value.match(/B:(\d+)(?:\uff08([A-Z])\uff09)?\s*\/\s*W:(\d+)\s*\/\s*H:(\d+)/);
                            if (sizeMatch) {
                                result.bust = sizeMatch[1];
                                result.cup = sizeMatch[2] || '';
                                result.waist = sizeMatch[3];
                                result.hip = sizeMatch[4];
                            }
                            break;
                        case '\u8eab\u9577':  // 身長
                            const heightMatch = value.match(/(\d+)/);
                            if (heightMatch) result.height = heightMatch[1];
                            break;
                        case '\u8840\u6db2\u578b':  // 血液型
                            result.blood_type = value;
                            break;
                        case '\u751f\u5e74\u6708\u65e5':  // 生年月日
                            // 1992年5月9日（33歳）- 使用 Unicode 转义避免编码问题
                            const birthMatch = value.match(/(\d{4})\u5e74(\d{1,2})\u6708(\d{1,2})\u65e5(?:\uff08(\d+)\u6b73\uff09)?/);
                            if (birthMatch) {
                                result.birthday = `${birthMatch[1]}-${birthMatch[2].padStart(2, '0')}-${birthMatch[3].padStart(2, '0')}`;
                                if (birthMatch[4]) result.age = parseInt(birthMatch[4]);
                            }
                            break;
                        case '\u51fa\u8eab':  // 出身
                            result.birthplace = value;
                            break;
                        case '\u30c7\u30d3\u30e5\u30fc':  // デビュー
                            result.debut = value;
                            break;
                        case 'Twitter':
                            const twitterLink = td.querySelector('a');
                            if (twitterLink) {
                                const href = twitterLink.getAttribute('href');
                                if (href) {
                                    result.twitter = href;
                                    const screenNameMatch = href.match(/twitter\.com\/([^\/]+)/);
                                    if (screenNameMatch) result.twitter_screen_name = screenNameMatch[1];
                                }
                            }
                            break;
                        case '\u30a4\u30f3\u30b9\u30bf':  // インスタ
                            const instaLink = td.querySelector('a');
                            if (instaLink) {
                                const href = instaLink.getAttribute('href');
                                if (href) {
                                    result.instagram = href;
                                    const screenNameMatch = href.match(/instagram\.com\/([^\/]+)/);
                                    if (screenNameMatch) result.instagram_screen_name = screenNameMatch[1];
                                }
                            }
                            break;
                        case '\u51fa\u6f14\u6570':  // 出演数
                            const workCountMatch = value.match(/(\d+)/);
                            if (workCountMatch) result.work_count = parseInt(workCountMatch[1]);
                            break;
                        case '\u5358\u4f53\u672c\u6570':  // 単体本数
                            const soloCountMatch = value.match(/(\d+)/);
                            if (soloCountMatch) result.solo_count = parseInt(soloCountMatch[1]);
                            break;
                        case 'VR\u6709\u7121':  // VR有無
                            result.has_vr = (value === '\u3042\u308a');  // あり
                            break;
                        case '\u30bf\u30b0':  // タグ
                            result.tags = value.split('\u3001').map(t => t.trim()).filter(t => t);  // 、
                            break;
                    }
                });
            }

            // 3. 提取头像图片
            const avatarImg = document.querySelector('img[alt*="' + (result.name || '') + '"]');
            if (avatarImg && avatarImg.src && !avatarImg.src.includes('loading.gif')) {
                result.avatar_url = avatarImg.src;
            }

            return result;
        }""")

        if not data.get('name'):
            self.logger.warning(f"无法提取演员信息 (ID: {actress_id})")
            return None

        # 4. 提取评论
        data['comments'] = await self._extract_comments(page)

        # 5. 提取 Instagram 图片
        data['instagram_images'] = await self._extract_instagram_images(page)

        self.logger.info(f"成功获取演员信息: {data.get('name')} (生日: {data.get('birthday')}, 身高: {data.get('height')})")

        return data

    async def _extract_comments(self, page: 'Page') -> List[Dict]:
        """提取评论

        Args:
            page: Playwright Page 对象

        Returns:
            评论列表
        """
        comments = await page.evaluate(r"""() => {
            const comments = [];

            // 找到包含"コメント"的h2标题，然后获取其后的表格
            const h2s = Array.from(document.querySelectorAll('h2'));
            const commentsH2 = h2s.find(h => h.textContent.includes('\u30b3\u30e1\u30f3\u30c8'));  // コメント

            if (!commentsH2) return comments;

            // 从h2开始查找下面的table
            let current = commentsH2.nextElementSibling;
            let commentsTable = null;

            while (current) {
                if (current.tagName === 'TABLE') {
                    commentsTable = current;
                    break;
                }
                // 限制搜索深度
                if (current.children.length > 0) {
                    const nestedTable = current.querySelector('table');
                    if (nestedTable) {
                        commentsTable = nestedTable;
                        break;
                    }
                }
                current = current.nextElementSibling;
                if (current && current.tagName === 'H2') break;  // 遇到下一个h2就停止
            }

            if (!commentsTable) return comments;

            const rows = commentsTable.querySelectorAll('tr');
            rows.forEach(row => {
                const cell = row.querySelector('td');
                if (!cell) return;

                // 评论文本在第一个span中
                const commentSpan = cell.querySelector('span:not(.co-hist-list-td-s)');
                const commentText = commentSpan ? commentSpan.textContent.trim() : '';

                // 评论者信息在 p.co-hist-list-td-info 中
                const infoP = cell.querySelector('p.co-hist-list-td-info');
                let commenter = '\u540d\u7121\u3057';  // 名無し
                let date = null;
                let time = null;
                let user_id = null;

                if (infoP) {
                    const pText = infoP.textContent.trim();
                    // 匹配: (かわむらよしとさん　2026/1/8 14:38　ID:13306)
                    // 注意: 使用全角空格
                    const match = pText.match(/\((.+?)\u3000(\d{4})\/(\d{1,2})\/(\d{1,2})\s+(\d{2}):(\d{2})\s+ID:(\d+)\)$/);

                    if (match) {
                        commenter = match[1];
                        date = `${match[2]}-${match[3].padStart(2, '0')}-${match[4].padStart(2, '0')}`;
                        time = `${match[5]}:${match[6]}`;
                        user_id = match[7];
                    }
                }

                // 过滤垃圾广告
                if (commentText.length < 10 ||
                    commentText.includes('\u591c\u9b45\u9928') ||  // 夜魅館
                    commentText.includes('\u51fa\u5f35\u30b5\u30fc\u30d3\u30b9') ||  // 出張サービス
                    commentText.includes('TG\uff1a') ||  // TG：
                    commentText.includes('Gleezy\uff1a') ||
                    commentText.includes('LINE \uff1a') ||  // LINE ：
                    commentText.includes('\u5929\u4f7f\u306e\u7652\u3057')) {  // 天使の癒し
                    return;
                }

                if (commentText) {
                    comments.push({
                        comment: commentText,
                        commenter: commenter,
                        date: date,
                        time: time,
                        user_id: user_id
                    });
                }
            });

            return comments;
        }""")

        self.logger.info(f"提取到 {len(comments)} 条评论")
        return comments

    async def _extract_instagram_images(self, page: 'Page') -> List[Dict]:
        """提取 Instagram 图片

        Args:
            page: Playwright Page 对象

        Returns:
            Instagram 图片列表
        """
        # 滚动到 Instagram 区域触发懒加载
        await page.evaluate(r"""() => {
            const h2s = Array.from(document.querySelectorAll('h2'));
            const instaSection = h2s.find(h => h.textContent.includes('\u30a4\u30f3\u30b9\u30bf\u30b0\u30e9\u30e0\u753b\u50cf'));  // インスタグラム画像
            if (instaSection) {
                instaSection.scrollIntoView();
            }
        }""")

        await asyncio.sleep(2)

        images = await page.evaluate(r"""() => {
            const images = [];
            const imgs = document.querySelectorAll('img[alt*="\u30a4\u30f3\u30b9\u30bf\u30b0\u30e9\u30e0\u753b\u50cf"]');  // インスタグラム画像

            imgs.forEach(img => {
                const src = img.src;
                // 过滤 loading.gif
                if (src && !src.includes('loading.gif')) {
                    images.push({
                        url: src,
                        alt: img.alt
                    });
                }
            });

            return images;
        }""")

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

        # 基本映射
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

        # 别名
        if av_league_data.get('aliases'):
            normalized['aliases'] = av_league_data['aliases']

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
        normalized['data_source_url'] = av_league_data.get('url')

        return normalized


# 同步封装（方便调用）
class AVLeagueScraperSync:
    """AV-League 爬虫的同步封装"""

    def __init__(self):
        self._scraper = None

    def __enter__(self):
        self._scraper = AVLeagueScraper()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._scraper:
            # 使用 asyncio 运行关闭
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._scraper.close())
                else:
                    loop.run_until_complete(self._scraper.close())
            except:
                pass

    def search_actress(self, name: str) -> Optional[Dict]:
        """同步搜索演员"""
        return asyncio.run(self._scraper.search_actress(name))

    def get_actress_by_id(self, actress_id: str) -> Optional[Dict]:
        """同步获取演员信息"""
        return asyncio.run(self._scraper.get_actress_by_id(actress_id))

    def normalize_for_javbus(self, data: Dict) -> Dict:
        """转换数据格式"""
        return self._scraper.normalize_for_javbus(data)


# 便捷函数
async def search_actress_async(name: str) -> Optional[Dict]:
    """异步搜索演员"""
    async with AVLeagueScraper() as scraper:
        return await scraper.search_actress(name)


def search_actress(name: str) -> Optional[Dict]:
    """同步搜索演员（便捷函数）

    Args:
        name: 演员名称

    Returns:
        演员信息字典，如果未找到返回 None
    """
    with AVLeagueScraperSync() as scraper:
        return scraper.search_actress(name)


def normalize_for_javbus(av_league_data: Dict) -> Dict:
    """将 AV-League 数据转换为 javbus 格式（便捷函数）"""
    return AVLeagueScraper().normalize_for_javbus(av_league_data)
