import re
import json
import requests
import os
from bs4 import BeautifulSoup
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 配置文件路径
CONFIG_FILE = "config/config.json"

class FanzaScraper:
    def __init__(self):
        self.base_url = "https://www.dmm.co.jp/"
        # FANZA使用的几种不同URL模板
        self.url_templates = [
            "https://www.dmm.co.jp/mono/dvd/-/detail/=/cid={}/",
            "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid={}/",
            "https://www.dmm.co.jp/digital/videoc/-/detail/=/cid={}/",
            "https://www.dmm.co.jp/digital/anime/-/detail/=/cid={}/",
            "https://www.dmm.co.jp/mono/anime/-/detail/=/cid={}/",
            "https://www.dmm.co.jp/digital/nikkatsu/-/detail/=/cid={}/"
        ]
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        # FANZA需要的cookie
        self.cookies = {'age_check_done': '1'}
        
        # 从配置文件加载前缀映射规则
        self.prefix_mappings = self.load_mappings_from_file()
        
        # 加载番号尾缀映射
        self.suffix_mappings = self.load_suffixes_from_file()
        
        logging.info(f"番号映射初始化完成，共加载 {len(self.prefix_mappings)} 条映射规则")
        logging.info(f"番号尾缀映射初始化完成，共加载 {len(self.suffix_mappings)} 条尾缀")
        
        # 输出一些映射样例作为调试信息
        if self.prefix_mappings:
            sample_keys = list(self.prefix_mappings.keys())[:3]  # 取前3个键作为样例
            sample_mappings = {k: self.prefix_mappings[k] for k in sample_keys}
            logging.info(f"映射样例: {sample_mappings}")
        else:
            logging.warning("没有加载到任何番号映射规则")
            
        # 输出一些尾缀映射样例
        if self.suffix_mappings:
            sample_keys = list(self.suffix_mappings.keys())[:3]  # 取前3个键作为样例
            sample_suffixes = {k: self.suffix_mappings[k] for k in sample_keys}
            logging.info(f"尾缀映射样例: {sample_suffixes}")
        
    def load_suffixes_from_file(self):
        """从配置文件加载番号尾缀映射"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 从整合的配置文件中获取fanza_suffixes字段
                    suffixes = config.get("fanza_suffixes", {})
                    logging.info(f"已从配置文件加载 {len(suffixes)} 条番号尾缀")
                    return suffixes
            return {}
        except Exception as e:
            logging.error(f"加载番号尾缀映射失败: {str(e)}")
            return {}
        
    def load_mappings_from_file(self):
        """从配置文件加载映射关系"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 从整合的配置文件中获取fanza_mappings字段
                    mappings = config.get("fanza_mappings", {})
                    logging.info(f"已从配置文件加载 {len(mappings)} 条番号映射")
                    # 输出所有映射作为调试信息
                    logging.info(f"完整映射: {mappings}")
                    return mappings
            elif os.path.exists('fanza_mappings.json'):
                # 兼容旧版本，如果有老的fanza_mappings.json文件
                with open('fanza_mappings.json', 'r', encoding='utf-8') as f:
                    mappings = json.load(f)
                    logging.info(f"从旧版映射文件加载 {len(mappings)} 条番号映射，将迁移到新配置文件")
                    # 迁移到新的配置文件
                    self.set_mappings(mappings)
                    return mappings
            else:
                # 如果配置文件不存在，使用默认的映射规则
                logging.warning("映射配置文件不存在，使用默认映射")
                return {}
        except Exception as e:
            logging.error(f"加载映射关系失败: {str(e)}")
            return {}
    
    def get_mappings(self):
        """获取所有番号映射关系"""
        return self.prefix_mappings
    
    def get_suffixes(self):
        """获取所有番号尾缀映射"""
        return self.suffix_mappings
    
    def set_mappings(self, mappings):
        """设置番号映射关系并保存到配置文件"""
        self.prefix_mappings = mappings
        
        try:
            # 读取现有配置
            config = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            
            # 按字母顺序排序映射
            sorted_mappings = {k: mappings[k] for k in sorted(mappings, key=str.lower)}
            
            # 更新配置
            config["fanza_mappings"] = sorted_mappings
            
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            
            logging.info(f"映射关系已保存到 {CONFIG_FILE}")
            return True
        except Exception as e:
            logging.error(f"保存映射关系失败: {str(e)}")
            return False
    
    def set_suffixes(self, suffixes):
        """设置番号尾缀映射并保存到配置文件"""
        self.suffix_mappings = suffixes
        
        try:
            # 读取现有配置
            config = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            
            # 按字母顺序排序映射
            sorted_suffixes = {k: suffixes[k] for k in sorted(suffixes, key=str.lower)}
            
            # 更新配置
            config["fanza_suffixes"] = sorted_suffixes
            
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            
            logging.info(f"尾缀映射已保存到 {CONFIG_FILE}")
            return True
        except Exception as e:
            logging.error(f"保存尾缀映射失败: {str(e)}")
            return False
    
    # 保持兼容性的方法
    def save_mappings(self, mappings):
        """保存番号映射关系到配置文件"""
        return self.set_mappings(mappings)
    
    def normalize_movie_id(self, movie_id):
        """将电影ID标准化为FANZA使用的格式"""
        # 1. 将ID转为小写
        id_lower = movie_id.lower()
        
        # 移除非字母数字字符(如连字符)，但对输入的ID进行清理，而不是映射结果
        cleaned_id = re.sub(r'[^a-z0-9]', '', id_lower)
        
        # 特殊处理: 首先检查是否是映射表中的已知前缀
        for prefix, mapped_prefix in self.prefix_mappings.items():
            if cleaned_id.startswith(prefix):
                # 提取数字部分
                prefix_removed = cleaned_id[len(prefix):]
                # 找到数字部分
                num_match = re.search(r'^(\d+)', prefix_removed)
                if num_match:
                    num = num_match.group(1).zfill(3)  # 数字部分补零到至少3位
                    logging.info(f"检测到已知前缀 {prefix}，映射到 {mapped_prefix}")
                    
                    # 检查是否有特定尾缀映射
                    suffix = ""
                    if prefix in self.suffix_mappings:
                        suffix = self.suffix_mappings[prefix]
                        logging.info(f"添加尾缀映射 {suffix}")
                    
                    # 重要：直接使用映射的前缀（包括下划线），不进行任何清理
                    result = f"{mapped_prefix}{num}{suffix}"
                    logging.info(f"特殊处理映射，最终ID: {result}")
                    return result
        
        # 从ID中提取前缀和数字部分 (标准格式处理)
        match = re.match(r'^([a-z]+)(\d+)$', cleaned_id)
        if match:
            prefix = match.group(1)
            number = match.group(2)
            
            # 2. 检查是否有映射，有映射直接替换
            if prefix in self.prefix_mappings:
                # 使用映射的前缀 - 保留前缀中的所有字符（包括下划线等）
                mapped_prefix = self.prefix_mappings[prefix]
                logging.info(f"映射前缀 {prefix} 到 {mapped_prefix}")
                
                # 确保数字部分至少为3位数，不足补0
                formatted_number = number.zfill(3)
                
                # 检查该前缀是否有特定尾缀映射
                if prefix in self.suffix_mappings:
                    suffix = self.suffix_mappings[prefix]
                    result = f"{mapped_prefix}{formatted_number}{suffix}"
                    logging.info(f"添加尾缀映射 {suffix}，最终ID: {result}")
                    return result
                else:
                    result = f"{mapped_prefix}{formatted_number}"
                    logging.info(f"最终ID: {result}")
                    return result
            
            # 3. 没有映射则在前缀和数字间加两个0
            formatted_number = number.zfill(3)
            
            # 检查该前缀是否有特定尾缀映射
            if prefix in self.suffix_mappings:
                suffix = self.suffix_mappings[prefix]
                result = f"{prefix}00{formatted_number}{suffix}"
                logging.info(f"无前缀映射但添加尾缀 {suffix}，最终ID: {result}")
                return result
            else:
                result = f"{prefix}00{formatted_number}"
                logging.info(f"无映射，使用默认格式，最终ID: {result}")
                return result
        
        # 如果不符合常规的 字母+数字 格式，返回原始ID（小写）
        logging.info(f"无法匹配常规格式，使用原始ID: {cleaned_id}")
        return cleaned_id
    
    def get_urls_by_id(self, movie_id):
        """返回所有可能的URL"""
        # 如果传入的ID已经标准化过并且包含下划线，直接使用它
        if '_' in movie_id:
            logging.info(f"使用已包含下划线的ID: {movie_id}")
            urls = [template.format(movie_id) for template in self.url_templates]
            for url in urls:
                logging.info(f"URL (保留下划线): {url}")
            return urls
        
        # 首先检查所有映射中的特殊前缀
        movie_id_lower = movie_id.lower()
        cleaned_id = re.sub(r'[^a-z0-9]', '', movie_id_lower)  # 清理ID，去除连字符等
        
        for prefix, mapped_prefix in self.prefix_mappings.items():
            if cleaned_id.startswith(prefix):
                # 去掉前缀部分，只保留数字
                prefix_removed = cleaned_id[len(prefix):]
                # 找到数字部分
                num_match = re.search(r'^(\d+)', prefix_removed)
                if num_match:
                    num = num_match.group(1).zfill(3)  # 数字部分补零到至少3位
                    
                    # 检查是否有尾缀
                    suffix = ""
                    if prefix in self.suffix_mappings:
                        suffix = self.suffix_mappings[prefix]
                    
                    # 保留映射前缀中的所有原始字符（包括下划线）
                    mapped_id = f"{mapped_prefix}{num}{suffix}"
                    logging.info(f"映射URL处理: {movie_id} -> {mapped_id}")
                    
                    urls = [template.format(mapped_id) for template in self.url_templates]
                    # 添加详细日志
                    for url in urls:
                        logging.info(f"映射前缀URL: {url}")
                    
                    return urls
        
        # 检查movie_id是否需要标准化
        if re.match(r'^[a-z]+\d{3,}[a-z]?$', movie_id.lower()):
            # 已经是标准化格式，直接使用
            normalized_id = movie_id.lower()
            logging.info(f"ID已是标准格式: {normalized_id}")
        else:
            # 需要标准化
            normalized_id = self.normalize_movie_id(movie_id)
            logging.info(f"标准化ID: {movie_id} -> {normalized_id}")
        
        urls = [template.format(normalized_id) for template in self.url_templates]
        
        # 如果ID符合特定模式，优先尝试digital videoa URL
        if re.match(r'(?i)[a-z]+00\d{3,}', normalized_id):
            urls[0], urls[1] = urls[1], urls[0]
            logging.info(f"交换URL优先级，首选: {urls[0]}")
        
        # 输出所有URL用于调试
        for url in urls:
            logging.info(f"URL: {url}")
            
        return urls
    
    def get_summary_from_json_ld(self, soup):
        """从JSON-LD脚本标签中提取摘要"""
        script_tag = soup.find('script', {'type': 'application/ld+json'})
        if not script_tag:
            return None
        
        try:
            data = json.loads(script_tag.string)
            if 'description' in data:
                return data['description']
        except (json.JSONDecodeError, AttributeError) as e:
            logging.warning(f"无法解析JSON-LD: {e}")
        
        return None
    
    def get_summary_from_html(self, soup):
        """从HTML内容中提取摘要"""
        # 尝试从class为"mg-b20 lh4"的div中提取
        summary_div = soup.select_one('div.mg-b20.lh4')
        if summary_div:
            # 先尝试从p.mg-b20获取
            p_tag = summary_div.select_one('p.mg-b20')
            if p_tag and p_tag.text.strip():
                return p_tag.text.strip()
            
            # 再尝试从任何p标签获取
            any_p = summary_div.select_one('p')
            if any_p and any_p.text.strip():
                return any_p.text.strip()
            
            # 最后获取整个div的文本
            if summary_div.text.strip():
                return summary_div.text.strip()
        
        # 尝试从 .txt.introduction p 获取
        intro_p = soup.select_one('.txt.introduction p')
        if intro_p and intro_p.text.strip():
            logging.info("从.txt.introduction p获取摘要")
            return intro_p.text.strip()
        
        # 尝试从 .nw-video-description 获取
        desc_div = soup.select_one('.nw-video-description')
        if desc_div and desc_div.text.strip():
            logging.info("从.nw-video-description获取摘要")
            return desc_div.text.strip()
        
        return None
    
    def get_summary_from_meta(self, soup):
        """从meta标签中提取摘要"""
        # 尝试从meta标签中的description属性获取
        meta_desc = soup.find('meta', {'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            content = meta_desc.get('content', '').strip()
            if content:
                return content
        
        # 尝试从open graph的description属性获取
        og_desc = soup.find('meta', {'property': 'og:description'})
        if og_desc and og_desc.get('content'):
            content = og_desc.get('content', '').strip()
            if content:
                return content
        
        return None
    
    def get_movie_summary(self, movie_id):
        """获取电影简介"""
        # 获取可能的URL列表
        normalized_id = self.normalize_movie_id(movie_id)
        logging.info(f"影片编号 {movie_id} 已被标准化为 {normalized_id}")
        
        # 使用标准化后的ID获取URL，这里直接使用normalized_id不再进行额外处理
        urls = self.get_urls_by_id(normalized_id)
        
        # 尝试使用标准化ID搜索
        for url in urls:
            logging.info(f"尝试URL: {url}")
            try:
                # 发送请求
                session = requests.Session()
                session.headers.update({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7'
                })
                
                # FANZA需要的cookie
                session.cookies.set('age_check_done', '1')
                
                response = session.get(url, timeout=10)
                
                # 检查响应状态
                if response.status_code == 200:
                    # 检查是否地区限制
                    if "not-available-in-your-region" in response.url:
                        logging.warning("该地区不可用")
                        continue
                        
                    # 解析HTML
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # 按优先级尝试不同的摘要获取方法
                    summary = self.get_summary_from_json_ld(soup)
                    if summary:
                        logging.info("从JSON-LD获取摘要")
                        return {
                            'movie_id': movie_id,
                            'fanza_id': normalized_id,
                            'url': url,
                            'summary': summary,
                            'source': 'json-ld'
                        }
                    
                    summary = self.get_summary_from_html(soup)
                    if summary:
                        logging.info("从HTML内容获取摘要")
                        return {
                            'movie_id': movie_id,
                            'fanza_id': normalized_id,
                            'url': url,
                            'summary': summary,
                            'source': 'html'
                        }
                    
                    summary = self.get_summary_from_meta(soup)
                    if summary:
                        logging.info("从Meta标签获取摘要")
                        return {
                            'movie_id': movie_id,
                            'fanza_id': normalized_id,
                            'url': url,
                            'summary': summary,
                            'source': 'meta'
                        }
                    
                    logging.warning("在页面中找不到摘要信息")
            except Exception as e:
                logging.error(f"获取电影 {movie_id} 的简介失败: {str(e)}")
                continue
        
        # 如果标准化ID搜索失败，尝试使用原始ID格式（去掉连字符）
        original_id = re.sub(r'[^a-z0-9]', '', movie_id.lower())
        if original_id != normalized_id:
            logging.info(f"标准化ID搜索失败，尝试使用原始ID格式: {original_id}")
            # 只尝试 DVD 版本的 URL
            url = f"https://www.dmm.co.jp/mono/dvd/-/detail/=/cid={original_id}/"
            logging.info(f"尝试原始ID URL: {url}")
            
            try:
                session = requests.Session()
                session.headers.update({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7'
                })
                
                # FANZA需要的cookie
                session.cookies.set('age_check_done', '1')
                
                response = session.get(url, timeout=10)
                
                # 检查响应状态
                if response.status_code == 200:
                    # 检查是否地区限制
                    if "not-available-in-your-region" in response.url:
                        logging.warning("该地区不可用")
                    else:
                        # 解析HTML
                        soup = BeautifulSoup(response.text, 'html.parser')
                        
                        # 按优先级尝试不同的摘要获取方法
                        summary = self.get_summary_from_json_ld(soup)
                        if summary:
                            logging.info("从JSON-LD获取摘要")
                            return {
                                'movie_id': movie_id,
                                'fanza_id': original_id,
                                'url': url,
                                'summary': summary,
                                'source': 'json-ld'
                            }
                        
                        summary = self.get_summary_from_html(soup)
                        if summary:
                            logging.info("从HTML内容获取摘要")
                            return {
                                'movie_id': movie_id,
                                'fanza_id': original_id,
                                'url': url,
                                'summary': summary,
                                'source': 'html'
                            }
                        
                        summary = self.get_summary_from_meta(soup)
                        if summary:
                            logging.info("从Meta标签获取摘要")
                            return {
                                'movie_id': movie_id,
                                'fanza_id': original_id,
                                'url': url,
                                'summary': summary,
                                'source': 'meta'
                            }
                        
                        logging.warning("在页面中找不到摘要信息")
            except Exception as e:
                logging.error(f"使用原始ID获取电影 {movie_id} 的简介失败: {str(e)}")
        
        logging.warning(f"未能获取到电影 {movie_id} 的简介")
        return None

    def test_mapping(self, test_id):
        """测试一个具体的ID映射，用于验证映射逻辑（特别是下划线处理）是否正确
        
        Args:
            test_id: 要测试的影片ID
            
        Returns:
            规范化后的ID和所有可能的URL
        """
        normalized_id = self.normalize_movie_id(test_id)
        urls = self.get_urls_by_id(test_id)
        
        # 详细日志
        logging.info(f"测试映射: {test_id} -> {normalized_id}")
        logging.info(f"生成URL: {urls[0] if urls else '无URL'}")
        
        return {
            "original_id": test_id,
            "normalized_id": normalized_id,
            "urls": urls
        }

# 使用示例
def main():
    """测试函数"""
    # 创建FanzaScraper实例
    scraper = FanzaScraper()

    # 测试特定的映射案例，特别是包含下划线的映射
    test_cases = [
        "NATR-747",  # 映射为 h_067natr747
        "HODV-21695",  # 如果有映射
        "ABW-123"  # 常规案例
    ]
    
    # 确保存在一些测试映射
    if "natr" not in scraper.prefix_mappings:
        print("添加测试映射: natr -> h_067natr")
        mappings = scraper.get_mappings()
        mappings["natr"] = "h_067natr"
        scraper.set_mappings(mappings)

    # 执行测试
    for test_id in test_cases:
        result = scraper.test_mapping(test_id)
        print(f"\nTest Case: {test_id}")
        print(f"Normalized ID: {result['normalized_id']}")
        print(f"First URL: {result['urls'][0] if result['urls'] else 'None'}")
    
    # 输出所有映射关系
    print("\n当前映射关系:")
    for prefix, mapped_prefix in scraper.prefix_mappings.items():
        print(f"{prefix} -> {mapped_prefix}")

if __name__ == "__main__":
    main() 