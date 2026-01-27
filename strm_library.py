#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import logging
import requests
from urllib.parse import urlparse, unquote

class StrmLibrary:
    """STRM文件库管理类，用于管理STRM文件和元数据"""
    
    def __init__(self, db, strm_dir="data/strm_library"):
        """初始化STRM文件库
        
        Args:
            db: JavbusDatabase实例
            strm_dir: STRM文件存储目录
        """
        self.db = db
        self.strm_dir = strm_dir
        
        # 确保STRM文件目录存在
        os.makedirs(self.strm_dir, exist_ok=True)
        for category in ['movies', 'tv', 'other']:
            os.makedirs(os.path.join(self.strm_dir, category), exist_ok=True)
            
        logging.info(f"STRM文件库初始化完成，目录：{self.strm_dir}")
    
    def create_strm_file(self, title, url, category="movies", thumbnail=None, description=None):
        """创建STRM文件
        
        Args:
            title: 影片标题
            url: 流媒体URL
            category: 分类 (movies, tv, other)
            thumbnail: 缩略图URL
            description: 描述信息
            
        Returns:
            dict: 创建的STRM文件信息，包含文件路径
        """
        # 处理标题，移除不合法字符
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
        if not safe_title:
            safe_title = f"stream_{int(time.time())}"
            
        # 确保分类有效
        if category not in ['movies', 'tv', 'other']:
            category = 'other'
            
        # 创建文件路径
        filename = f"{safe_title}.strm"
        category_dir = os.path.join(self.strm_dir, category)
        filepath = os.path.join(category_dir, filename)
        
        # 如果文件已存在，添加时间戳避免重复
        if os.path.exists(filepath):
            timestamp = int(time.time())
            filename = f"{safe_title}_{timestamp}.strm"
            filepath = os.path.join(category_dir, filename)
        
        # 写入STRM文件内容（只包含URL）
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(url)
                
            # 创建元数据记录
            strm_data = {
                'title': title,
                'filepath': filepath,
                'url': url,
                'thumbnail': thumbnail or '',
                'description': description or '',
                'category': category
            }
            
            # 保存到数据库
            if self.db.save_strm_file(strm_data):
                logging.info(f"创建STRM文件成功: {filepath}")
                return strm_data
            else:
                logging.error(f"保存STRM文件到数据库失败: {filepath}")
                # 失败时删除文件
                if os.path.exists(filepath):
                    os.remove(filepath)
                return None
                
        except Exception as e:
            logging.error(f"创建STRM文件失败: {str(e)}")
            # 失败时删除文件
            if os.path.exists(filepath):
                os.remove(filepath)
            return None
    
    def delete_strm_file(self, file_id):
        """删除STRM文件
        
        Args:
            file_id: 文件ID
            
        Returns:
            bool: 是否删除成功
        """
        # 获取文件信息
        file_info = self.db.get_strm_file(file_id)
        if not file_info:
            logging.error(f"要删除的STRM文件不存在: {file_id}")
            return False
            
        filepath = file_info.get('filepath', '')
        
        # 删除物理文件
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                logging.info(f"已删除STRM文件: {filepath}")
            
            # 从数据库删除记录
            if self.db.delete_strm_file(file_id):
                return True
            else:
                logging.error(f"从数据库删除STRM文件记录失败: {file_id}")
                return False
                
        except Exception as e:
            logging.error(f"删除STRM文件失败: {str(e)}")
            return False
    
    def scan_directory(self, directory=None):
        """扫描目录下的所有STRM文件，并添加到数据库
        
        Args:
            directory: 要扫描的目录，默认为STRM文件库根目录
            
        Returns:
            int: 添加的文件数量
        """
        directory = directory or self.strm_dir
        count = 0
        
        try:
            # 遍历指定目录
            for root, _, files in os.walk(directory):
                # 确定分类
                if 'movies' in root:
                    category = 'movies'
                elif 'tv' in root:
                    category = 'tv'
                else:
                    category = 'other'
                    
                # 处理.strm文件
                for file in files:
                    if file.endswith('.strm'):
                        filepath = os.path.join(root, file)
                        
                        # 读取文件内容获取URL
                        try:
                            with open(filepath, 'r', encoding='utf-8') as f:
                                url = f.read().strip()
                                
                            if url:
                                # 从文件名获取标题
                                title = os.path.splitext(file)[0]
                                
                                # 创建元数据记录
                                strm_data = {
                                    'title': title,
                                    'filepath': filepath,
                                    'url': url,
                                    'thumbnail': '',
                                    'description': '',
                                    'category': category
                                }
                                
                                # 保存到数据库
                                if self.db.save_strm_file(strm_data):
                                    count += 1
                                    logging.info(f"添加STRM文件: {filepath}")
                                    
                        except Exception as e:
                            logging.error(f"处理STRM文件失败 {filepath}: {str(e)}")
                            
            logging.info(f"STRM目录扫描完成，共添加 {count} 个文件")
            return count
            
        except Exception as e:
            logging.error(f"扫描STRM目录失败: {str(e)}")
            return 0
    
    def import_strm_url(self, url, title=None, category="movies", thumbnail=None, description=None):
        """导入流媒体URL并创建STRM文件
        
        Args:
            url: 流媒体URL
            title: 标题（如果为None，则从URL中提取）
            category: 分类
            thumbnail: 缩略图URL
            description: 描述
            
        Returns:
            dict: 创建的STRM文件信息
        """
        if not url:
            logging.error("导入STRM失败：URL不能为空")
            return None
            
        # 如果未提供标题，尝试从URL中提取
        if not title:
            try:
                parsed_url = urlparse(url)
                path = unquote(parsed_url.path)
                # 获取路径的最后一部分作为文件名
                filename = os.path.basename(path)
                # 移除扩展名
                title = os.path.splitext(filename)[0]
                # 使用-和_替换为空格，美化标题
                title = title.replace('-', ' ').replace('_', ' ')
                # 如果还是空的，使用域名+时间戳
                if not title:
                    title = f"{parsed_url.netloc}_{int(time.time())}"
            except:
                # 如果出错，使用默认标题
                title = f"Stream_{int(time.time())}"
                
        # 创建STRM文件
        return self.create_strm_file(title, url, category, thumbnail, description)
    
    def get_strm_play_url(self, file_id):
        """获取STRM文件的播放URL，并记录播放
        
        Args:
            file_id: 文件ID
            
        Returns:
            str: 播放URL
        """
        # 获取文件信息
        file_info = self.db.get_strm_file(file_id)
        if not file_info:
            logging.error(f"STRM文件不存在: {file_id}")
            return None
            
        # 更新播放计数
        self.db.update_strm_play_count(file_id)
        
        # 返回URL
        return file_info.get('url', '')

    def extract_video_ids(self, category=None, dictionary=None, only_missing=False):
        """从STRM文件中提取视频ID
        
        Args:
            category: 特定分类，默认为所有分类
            dictionary: 过滤字典，可以是文件路径或列表
            only_missing: 是否仅处理没有 video_id 的条目
            
        Returns:
            tuple: (更新数量, 提取结果)
        """
        try:
            # 确保数据库中存在video_id列
            self.db.add_video_id_column_if_not_exists()
            
            # 获取所有STRM文件
            strm_files = self.db.get_strm_files(category=category)
            # 如果只处理没有 video_id 的条目
            if only_missing:
                strm_files = [f for f in strm_files if not f.get('video_id')]
            
            if not strm_files:
                logging.info(f"未找到STRM文件进行处理")
                return 0, []
            
            # 导入模块
            try:
                from modules.video_id_matcher import VideoIDMatcher
            except ImportError:
                # 处理导入错误，尝试直接从当前目录导入
                import sys
                import os
                script_dir = os.path.dirname(os.path.abspath(__file__))
                modules_dir = os.path.join(script_dir, 'modules')
                
                if os.path.exists(modules_dir):
                    sys.path.insert(0, script_dir)
                    logging.info(f"添加目录到Python路径: {script_dir}")
                    try:
                        from modules.video_id_matcher import VideoIDMatcher
                    except ImportError as e:
                        logging.error(f"无法导入VideoIDMatcher: {e}")
                        # 尝试创建一个VideoIDMatcher的简单版本
                        class VideoIDMatcher:
                            def __init__(self, dictionary_path=None):
                                self.dictionary = []
                            
                            def load_dictionary(self, dict_file):
                                try:
                                    with open(dict_file, 'r', encoding='utf-8') as f:
                                        self.dictionary = [line.strip() for line in f if line.strip()]
                                    return len(self.dictionary)
                                except Exception as ex:
                                    logging.error(f"Error loading dictionary: {ex}")
                                    return 0
                                
                            def load_dictionary_from_json(self, json_dictionary):
                                if isinstance(json_dictionary, list):
                                    self.dictionary = json_dictionary
                                    return len(json_dictionary)
                                return 0
                            
                            def extract_video_id(self, filename):
                                # 简单的提取ID逻辑
                                import re
                                # 从文件名中提取ID-数字模式
                                match = re.search(r'([A-Za-z]+-\d+)', filename)
                                if match:
                                    return match.group(1).upper()
                                return ""
                            
                            def process_strm_files(self, strm_files):
                                results = []
                                for file in strm_files:
                                    filename = os.path.basename(file.get('filepath', ''))
                                    video_id = self.extract_video_id(filename)
                                    if video_id:
                                        results.append({
                                            'id': file.get('id'),
                                            'filepath': file.get('filepath', ''),
                                            'title': file.get('title', ''),
                                            'filename': filename,
                                            'video_id': video_id,
                                            'original_id': file.get('video_id', '')
                                        })
                                return results
                            
                            def update_strm_title(self, file, video_id):
                                title = file.get('title', '')
                                if video_id in title:
                                    return title
                                return video_id
                else:
                    logging.error(f"模块目录不存在: {modules_dir}")
                    return 0, []
            
            matcher = VideoIDMatcher()
            
            # 加载字典
            dict_count = 0
            if dictionary:
                if isinstance(dictionary, list):
                    dict_count = matcher.load_dictionary_from_json(dictionary)
                elif isinstance(dictionary, str) and os.path.exists(dictionary):
                    dict_count = matcher.load_dictionary(dictionary)
                logging.info(f"已加载 {dict_count} 个字典项")
            
            # 处理STRM文件
            processed_files = matcher.process_strm_files(strm_files)
            
            if not processed_files:
                logging.info(f"未能从任何STRM文件中提取出视频ID")
                return 0, []
            
            # 准备更新
            updates = []
            for file in processed_files:
                file_id = file.get('id')
                video_id = file.get('video_id')
                original_title = file.get('title', '')
                
                # 更新标题，在开头添加视频ID（如果不存在）
                updated_title = matcher.update_strm_title(file, video_id)
                
                updates.append({
                    'id': file_id,
                    'video_id': video_id,
                    'title': updated_title,
                    'original_title': original_title
                })
            
            # 批量更新数据库
            update_count = self.db.batch_update_strm_video_ids([{
                'id': update['id'],
                'video_id': update['video_id'],
                'title': update['title']
            } for update in updates])
            
            logging.info(f"成功更新 {update_count} 个STRM文件的视频ID")
            
            return update_count, updates
            
        except Exception as e:
            logging.error(f"视频ID提取过程出错: {str(e)}")
            import traceback
            logging.error(traceback.format_exc())
            return 0, []
    
    def update_default_dictionary(self, dictionary_list):
        """更新默认过滤字典
        
        Args:
            dictionary_list: 字典项列表
            
        Returns:
            bool: 成功返回True，否则返回False
        """
        try:
            # 确保配置目录存在
            os.makedirs("config", exist_ok=True)
            
            # 保存字典到文件
            dict_path = "config/filter_dictionary.txt"
            with open(dict_path, 'w', encoding='utf-8') as f:
                for item in dictionary_list:
                    f.write(f"{item}\n")
            
            logging.info(f"成功更新过滤字典，保存了 {len(dictionary_list)} 个项目")
            return True
        except Exception as e:
            logging.error(f"更新过滤字典时出错: {str(e)}")
            return False
    
    def get_default_dictionary(self):
        """获取默认过滤字典
        
        Returns:
            list: 字典项列表
        """
        dict_path = "config/filter_dictionary.txt"
        dictionary = []
        
        try:
            if os.path.exists(dict_path):
                # 尝试导入chardet进行编码检测
                try:
                    import chardet
                    
                    # 检测文件编码
                    with open(dict_path, 'rb') as f:
                        raw_data = f.read(4096)
                        result = chardet.detect(raw_data)
                        encoding = result['encoding'] or 'utf-8'
                except ImportError:
                    # 如果chardet不可用，使用默认编码
                    logging.warning("无法导入chardet模块，使用默认UTF-8编码")
                    encoding = 'utf-8'
                
                # 读取字典文件
                try:
                    with open(dict_path, 'r', encoding=encoding) as f:
                        dictionary = [line.strip() for line in f if line.strip()]
                except UnicodeDecodeError:
                    # 如果使用检测到的编码失败，尝试常见编码
                    for fallback_encoding in ['utf-8', 'latin-1', 'gbk', 'cp932', 'euc-jp', 'euc-kr']:
                        try:
                            with open(dict_path, 'r', encoding=fallback_encoding) as f:
                                dictionary = [line.strip() for line in f if line.strip()]
                            logging.info(f"使用备用编码 {fallback_encoding} 成功读取字典")
                            break
                        except UnicodeDecodeError:
                            continue
            
            return dictionary
        except Exception as e:
            logging.error(f"获取过滤字典时出错: {str(e)}")
            return []
    
    def sync_strm_movie_info(self, category=None):
        """同步STRM文件的影片详情与电影数据库
        
        从API获取影片详情并更新数据库中的影片信息，每次处理10个请求
        
        Args:
            category: 可选，指定要同步的分类
            
        Returns:
            dict: 包含更新结果的字典，格式为 {"success": int, "failed": int}
        """
        try:
            # 确保数据库中存在必要的列
            self.db.add_date_column_if_not_exists()
            
            # 获取所有带有video_id的STRM文件（可选按分类过滤）
            strm_files = self.db.get_strm_files(category=category)
            strm_files = [file for file in strm_files if file.get('video_id')]
            
            total_files = len(strm_files)
            if total_files == 0:
                logging.info(f"STRM库中没有找到带有video_id的文件")
                return {"success": 0, "failed": 0}
                
            success_count = 0
            failed_count = 0
            
            # 导入功能函数
            from webserver import get_movie_data, format_movie_data, download_image
            import time
            
            # 批量处理，每批10个
            batch_size = 10
            for i in range(0, total_files, batch_size):
                batch = strm_files[i:i+batch_size]
                logging.info(f"处理第 {i+1}-{min(i+batch_size, total_files)}/{total_files} 批影片信息")
                
                # 处理当前批次
                batch_success = 0
                for file in batch:
                    video_id = file.get('video_id')
                    if not video_id:
                        logging.debug(f"跳过无video_id的文件: {file.get('id')} - {file.get('title')}")
                        continue
                
                try:
                    # 从API获取影片详情
                    logging.info(f"从API获取影片 {video_id} 的详情")
                    movie_data = get_movie_data(video_id)
                    
                    if movie_data:
                        # 格式化影片数据并保存到数据库
                        formatted_data = format_movie_data(movie_data)
                        self.db.save_movie(formatted_data)
                        
                        # 下载封面图片
                        cover_url = formatted_data.get('img')
                        if cover_url:
                            cover_path = os.path.join("buspic", "covers", f"{video_id}.jpg")
                            download_image(cover_url, cover_path)
                        
                        # 准备演员数据
                        actors_data = []
                        for actor in formatted_data.get('stars', []):
                            actors_data.append({
                                "id": actor.get('id', ''),
                                "name": actor.get('name', ''),
                                "image_url": actor.get('image', '')
                            })
                        
                        # 将演员数据序列化为JSON字符串
                        actors_json = json.dumps(actors_data)
                        
                        # 更新STRM文件的元数据
                        file_id = file.get('id')
                        self.db.update_strm_metadata(
                            file_id,
                            video_id=video_id,
                            cover_image=formatted_data.get('img', ''),
                            actors=actors_json
                        )
                        
                        # 更新STRM文件的标题和日期
                        self.db.update_strm_movie_info(
                            file_id=file.get('id'),
                            title=formatted_data.get('title', ''),
                            date=formatted_data.get('date', '')
                        )
                        
                        success_count += 1
                        batch_success += 1
                        logging.info(f"成功更新影片 {video_id} 的详情")
                    else:
                        failed_count += 1
                        logging.warning(f"无法获取影片 {video_id} 的详情")
                except Exception as e:
                    failed_count += 1
                    logging.error(f"更新影片 {video_id} 详情时出错: {str(e)}")
                
                # 如果处理了多个条目并且还有下一批，休息2秒钟避免API压力过大
                if batch_success > 0 and i + batch_size < total_files:
                    logging.info(f"批次处理完成，休息2秒后继续处理下一批")
                    time.sleep(2)
            
            category_info = f"「{category}」分类的" if category else ""
            logging.info(f"STRM文件影片详情同步完成，成功: {success_count}，失败: {failed_count}，分类: {category_info}")
            return {"success": success_count, "failed": failed_count}
            
        except Exception as e:
            logging.error(f"同步STRM文件影片详情失败: {str(e)}")
            return {"success": 0, "failed": 0} 