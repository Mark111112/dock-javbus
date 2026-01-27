#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import json
import logging
import argparse
import re
from importlib import import_module

# 获取项目根目录
root_dir = os.path.dirname(os.path.abspath(__file__))

# 添加项目根目录到搜索路径
sys.path.append(root_dir)

# 从模块目录导入爬虫
from modules.scrapers import base_scraper, fanza_scraper, dmm_scraper, heyzo_scraper, caribbean_scraper, pondo_scraper, musume_scraper, kin8tengoku_scraper, pacopacomama_scraper, tokyohot_scraper

# 设置基本日志格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 定义支持的爬虫类
SCRAPERS = {
    'fanza': {'module': 'modules.scrapers.fanza_scraper', 'class': 'FanzaScraper'},
    'dmm': {'module': 'modules.scrapers.dmm_scraper', 'class': 'DMMScraper'},
    'heyzo': {'module': 'modules.scrapers.heyzo_scraper', 'class': 'HeyzoScraper'},
    'caribbean': {'module': 'modules.scrapers.caribbean_scraper', 'class': 'CaribbeanScraper'},
    '1pondo': {'module': 'modules.scrapers.pondo_scraper', 'class': 'OnePondoScraper'},
    'musume': {'module': 'modules.scrapers.musume_scraper', 'class': 'MusumeScraper'},
    'kin8tengoku': {'module': 'modules.scrapers.kin8tengoku_scraper', 'class': 'Kin8tengokuScraper'},
    'pacopacomama': {'module': 'modules.scrapers.pacopacomama_scraper', 'class': 'PacopacomomaScraper'},
    'tokyohot': {'module': 'modules.scrapers.tokyohot_scraper', 'class': 'TokyoHotScraper'},
    # 未来可以在这里添加更多爬虫
    # 'fc2': {'module': 'modules.scrapers.fc2_scraper', 'class': 'FC2Scraper'},
}

# 定义爬虫类型：search_first类型需要先搜索，direct_access类型可以直接访问详情页
SCRAPER_TYPES = {
    'fanza': 'search_first',
    'dmm': 'search_first',
    'heyzo': 'direct_access',
    'caribbean': 'direct_access',
    '1pondo': 'direct_access',
    'musume': 'direct_access',
    'kin8tengoku': 'direct_access',
    'pacopacomama': 'direct_access',
    'tokyohot': 'search_first',
}

def load_scraper(scraper_name):
    """动态加载爬虫类
    
    Args:
        scraper_name: 爬虫名称
    
    Returns:
        爬虫类实例
    """
    if scraper_name not in SCRAPERS:
        raise ValueError(f"不支持的爬虫: {scraper_name}")
    
    scraper_info = SCRAPERS[scraper_name]
    try:
        # 动态导入模块
        module = import_module(scraper_info['module'])
        # 获取爬虫类
        scraper_class = getattr(module, scraper_info['class'])
        # 创建爬虫实例
        return scraper_class()
    except ImportError as e:
        logging.error(f"导入爬虫模块失败: {e}")
        raise
    except AttributeError as e:
        logging.error(f"获取爬虫类失败: {e}")
        raise

def print_movie_info(movie_info):
    """格式化打印影片信息
    
    Args:
        movie_info: 影片信息字典
    """
    if not movie_info:
        print("未找到影片信息")
        return
    
    # 定义要打印的字段和对应的描述
    field_groups = {
        "基本信息": [
            ('id', '影片ID'),
            ('title', '标题'),
            ('source', '数据源'),
            ('url', '详情页URL'),
        ],
        "制作信息": [
            ('release_date', '发行日期'),
            ('duration', '时长'),
            ('maker', '制作商'),
            ('label', '发行商'),
            ('series', '系列'),
            ('product_code', '品番'),
            ('director', '导演')
        ],
        "演员和类型": [
            ('actresses', '演员'),
            ('genres', '类型'),
            ('rating', '评分')
        ]
    }
    
    # 打印影片信息
    print("\n" + "="*60)
    for group_name, fields in field_groups.items():
        print(f"\n【{group_name}】")
        for field, desc in fields:
            if field in movie_info:
                value = movie_info[field]
                if isinstance(value, list):
                    print(f"{desc}: {', '.join(value)}")
                else:
                    print(f"{desc}: {value}")
    
    # 显示缩略图信息
    thumbnails = movie_info.get('thumbnails', [])
    if thumbnails:
        print(f"\n【媒体资源】")
        print(f"缩略图数量: {len(thumbnails)}")
        if len(thumbnails) > 0:
            print(f"第一张缩略图: {thumbnails[0]}")
        if len(thumbnails) > 1:
            print(f"最后一张缩略图: {thumbnails[-1]}")
    
    # 打印摘要信息（如果有）
    if 'summary' in movie_info and movie_info['summary']:
        print(f"\n【影片简介】")
        summary = movie_info['summary']
        if len(summary) > 300:
            print(f"{summary[:300]}...")
            print("...(完整内容请查看JSON输出)")
        else:
            print(summary)
    
    print("\n" + "="*60)

def save_to_json(movie_info, movie_id, scraper_name):
    """将影片信息保存为JSON文件
    
    Args:
        movie_info: 影片信息字典
        movie_id: 影片ID
        scraper_name: 爬虫名称
    """
    if not movie_info:
        return
    
    # 创建输出目录（如果不存在）
    output_dir = os.path.join("output", scraper_name)
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存JSON文件
    filename = os.path.join(output_dir, f"{movie_id}.json")
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(movie_info, f, ensure_ascii=False, indent=2)
    
    print(f"完整信息已保存至: {filename}")

def identify_scraper(movie_id):
    """根据影片ID自动识别应该使用哪个爬虫
    
    Args:
        movie_id: 影片ID
    
    Returns:
        str: 爬虫名称，如fanza, heyzo等
    """
    movie_id = movie_id.strip().lower()
    
    # Heyzo: heyzo-xxxx（4位数字）
    if re.match(r'^(?:heyzo[-_]?)?\d{4}$', movie_id) or re.match(r'^heyzo[-_]?\d{4}$', movie_id):
        return 'heyzo'
    
    # Caribbean: mmddyy-xxx（前面6位是日期，后面是3位数字）
    if re.match(r'^(?:\d{6})[-](?:\d{3})$', movie_id):
        return 'caribbean'
    
    # 10Musume: mmddyy-xx（前面6位是日期，后面是2位数字）
    if re.match(r'^(?:\d{6})[_](?:\d{2})$', movie_id):
        return 'musume'

    # 1pondo/pacopacomama: mmddyy_xxx（前面6位是日期，后面是3位数字）
    if re.match(r'^(?:\d{6})[_](?:\d{3})$', movie_id):
        # 由于1pondo和pacopacomama的ID格式相同，无法直接区分
        # 返回 '1pondo_or_paco' 特殊标识，表示需要测试两个爬虫
        return '1pondo_or_paco'
    
    # Tokyohot: nxxxx（字母n后是4位数字）
    if re.match(r'^[n,k]\d{4}$', movie_id):
        return 'tokyohot'
    
    # kin8tengoku: kin8-xxxx（固定的kin8-4位数字）
    if re.match(r'^kin8[-_]?\d{4}$', movie_id):
        return 'kin8tengoku'
    
    # Fanza默认: aaa（字母为2-6位）+xxx（数字为3位）
    if re.match(r'^[a-z]{2,6}[-_]?\d{3,}[a-z]?$', movie_id):
        return 'fanza'
    
    # 如果以上都不匹配，则默认为fanza
    logging.warning(f"无法识别影片ID格式: {movie_id}，默认使用fanza爬虫")
    return 'fanza'

def get_movie_summary(movie_id):
    """获取电影的摘要信息，封装了识别爬虫、获取信息的逻辑，方便外部调用
    
    Args:
        movie_id: 影片ID
    
    Returns:
        dict: 包含summary和其他信息的字典，如果失败则返回None
    """
    try:
        # 识别应该使用的爬虫
        scraper_name = identify_scraper(movie_id)
        logging.info(f"自动识别影片 {movie_id} 对应的爬虫: {scraper_name}")
        
        # 处理特殊情况：1pondo 和 pacopacomama 需要同时尝试
        if scraper_name == '1pondo_or_paco':
            # 依次尝试两个爬虫
            potential_scrapers = ['1pondo', 'pacopacomama']
            movie_info = None
            
            for potential_scraper in potential_scrapers:
                logging.info(f"尝试使用 {potential_scraper} 爬虫处理 ID: {movie_id}")
                try:
                    # 加载爬虫
                    scraper = load_scraper(potential_scraper)
                    
                    # 搜索影片
                    urls = scraper.search_movie(movie_id)
                    
                    if not urls:
                        logging.warning(f"{potential_scraper} 未找到影片: {movie_id}")
                        continue
                    
                    # 获取第一个结果的详情
                    url = urls[0]
                    soup = scraper.get_page(url)
                    
                    if not soup:
                        logging.warning(f"{potential_scraper} 无法获取页面: {url}")
                        continue
                    
                    # 提取影片信息
                    info = scraper.extract_info_from_page(soup, movie_id, url)
                    
                    if info and info.get('summary'):
                        logging.info(f"使用 {potential_scraper} 成功获取影片信息")
                        movie_info = info
                        # 设置正确的爬虫类型，标记来源
                        movie_info['source'] = potential_scraper
                        return movie_info
                        
                except Exception as e:
                    logging.warning(f"{potential_scraper} 处理出错: {str(e)}")
            
            # 所有尝试都失败
            logging.error(f"所有尝试都失败，未能获取影片: {movie_id} 的信息")
            return None
            
        # 常规爬虫处理
        try:
            # 加载爬虫
            scraper = load_scraper(scraper_name)

            # 统一通过爬虫的 get_movie_info 获取信息（内部自行处理直连/搜索/回退）
            movie_info = scraper.get_movie_info(movie_id)

            if not movie_info:
                logging.error(f"未找到影片: {movie_id}")
                return None

            # 标记来源（若爬虫内部已设置，以外部标记为准）
            movie_info['source'] = movie_info.get('source') or scraper_name

            return movie_info
            
        except Exception as e:
            logging.error(f"获取影片摘要时出错: {str(e)}")
            return None
            
    except Exception as e:
        logging.error(f"获取电影摘要过程中发生错误: {str(e)}")
        return None

def main():
    parser = argparse.ArgumentParser(description='测试爬虫脚本')
    parser.add_argument('--scraper', help='指定爬虫类型 (dmm, fanza, heyzo等)', required=False)
    parser.add_argument('--id', help='影片ID', required=True)
    parser.add_argument('--log', help='日志级别 (INFO, DEBUG)', default='INFO')
    parser.add_argument('--output', help='输出文件路径', default=None)
    
    args = parser.parse_args()
    
    # 设置日志级别
    log_level = getattr(logging, args.log.upper(), logging.INFO)
    logging.basicConfig(level=log_level)
    
    # 如果未指定爬虫类型，自动识别
    if not args.scraper:
        args.scraper = identify_scraper(args.id)
        logging.info(f"自动识别影片 {args.id} 对应的爬虫: {args.scraper}")
    
    # 处理特殊情况：1pondo 和 pacopacomama 需要同时尝试
    if args.scraper == '1pondo_or_paco':
        # 依次尝试两个爬虫
        potential_scrapers = ['1pondo', 'pacopacomama']
        movie_info = None
        
        for scraper_name in potential_scrapers:
            logging.info(f"尝试使用 {scraper_name} 爬虫处理 ID: {args.id}")
            try:
                # 加载爬虫
                scraper = load_scraper(scraper_name)
                
                # 搜索影片
                urls = scraper.search_movie(args.id)
                
                if not urls:
                    logging.warning(f"{scraper_name} 未找到影片: {args.id}")
                    continue
                
                # 获取第一个结果的详情
                url = urls[0]
                soup = scraper.get_page(url)
                
                if not soup:
                    logging.warning(f"{scraper_name} 无法获取页面: {url}")
                    continue
                
                # 提取影片信息
                info = scraper.extract_info_from_page(soup, args.id, url)
                
                if info:
                    logging.info(f"使用 {scraper_name} 成功获取影片信息")
                    movie_info = info
                    # 设置正确的爬虫类型，用于保存文件等操作
                    args.scraper = scraper_name
                    break
                    
            except Exception as e:
                logging.warning(f"{scraper_name} 处理出错: {str(e)}")
        
        if not movie_info:
            logging.error(f"所有尝试都失败，未能获取影片: {args.id} 的信息")
            sys.exit(1)
            
        # 输出结果
        formatted_json = json.dumps(movie_info, ensure_ascii=False, indent=2)
        
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(formatted_json)
            logging.info(f"结果已保存到: {args.output}")
        else:
            print(formatted_json)
            
        return
    
    # 检查爬虫类型是否支持
    if args.scraper not in SCRAPERS:
        logging.error(f"不支持的爬虫类型: {args.scraper}")
        sys.exit(1)
        
    # 从配置中获取爬虫类和模块
    scraper_info = SCRAPERS[args.scraper]
    module_name = scraper_info['module']
    class_name = scraper_info['class']
    
    try:
        # 动态导入爬虫模块
        module = import_module(module_name)
        scraper_class = getattr(module, class_name)
        
        # 实例化爬虫
        scraper = scraper_class()
        
        # 获取爬虫类型
        scraper_type = SCRAPER_TYPES.get(args.scraper, 'search_first')
        url = None
        
        if scraper_type == 'direct_access':
            # 直接访问类型: Heyzo, Caribbean等
            url = scraper.get_movie_url(args.id)
            if url:
                logging.info(f"直接访问详情页: {url}")
                urls = [url]
            else:
                # 如果直接构建URL失败，尝试搜索
                logging.warning(f"无法直接构建URL，尝试搜索模式")
                urls = scraper.search_movie(args.id)
        else:
            # 搜索优先类型: Fanza, DMM等
            urls = scraper.search_movie(args.id)
        
        if not urls:
            logging.error(f"未找到影片: {args.id}")
            sys.exit(1)
        
        # 获取第一个结果的详情
        url = urls[0]
        soup = scraper.get_page(url)
        
        if not soup:
            logging.error(f"无法获取页面: {url}")
            sys.exit(1)
        
        # 提取影片信息
        movie_info = scraper.extract_info_from_page(soup, args.id, url)
        
        if not movie_info:
            logging.error(f"无法提取影片信息: {url}")
            sys.exit(1)
        
        # 美化输出
        formatted_json = json.dumps(movie_info, ensure_ascii=False, indent=2)
        
        # 输出结果
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(formatted_json)
            logging.info(f"结果已保存到: {args.output}")
        else:
            print(formatted_json)
        
    except ImportError as e:
        logging.error(f"导入爬虫模块失败: {module_name} - {str(e)}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"执行爬虫时出错: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main() 