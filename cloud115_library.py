import os
import re
import json
import time
import logging
import requests
from urllib.parse import urlparse, unquote

class Cloud115Library:
    """115云盘文件库管理类，用于管理115云盘文件和元数据"""
    
    def __init__(self, db, cloud115_dir="data/cloud115_library"):
        """初始化115云盘文件库
        
        Args:
            db: JavbusDatabase实例
            cloud115_dir: 115云盘文件存储目录
        """
        self.db = db
        self.cloud115_dir = cloud115_dir
        
        # 确保115云盘文件目录存在
        os.makedirs(self.cloud115_dir, exist_ok=True)
        for category in ['movies', 'tv', 'other']:
            os.makedirs(os.path.join(self.cloud115_dir, category), exist_ok=True)
            
        logging.info(f"115云盘文件库初始化完成，目录：{self.cloud115_dir}")
    
    def create_cloud115_file(self, title, url, category="movies", thumbnail=None, description=None):
        """创建115云盘文件
        
        Args:
            title: 影片标题
            url: 流媒体URL
            category: 分类 (movies, tv, other)
            thumbnail: 缩略图URL
            description: 描述信息
            
        Returns:
            dict: 创建的115云盘文件信息，包含文件路径
        """
        # 处理标题，移除不合法字符
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
        if not safe_title:
            safe_title = f"115cloud_{int(time.time())}"
            
        # 确保分类有效
        if category not in ['movies', 'tv', 'other']:
            category = 'other'
            
        # 创建文件路径
        filename = f"{safe_title}.115"
        category_dir = os.path.join(self.cloud115_dir, category)
        filepath = os.path.join(category_dir, filename)
        
        # 如果文件已存在，添加时间戳避免重复
        if os.path.exists(filepath):
            timestamp = int(time.time())
            filename = f"{safe_title}_{timestamp}.115"
            filepath = os.path.join(category_dir, filename)
        
        # 写入115云盘文件内容（只包含URL）
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(url)
                
            # 创建元数据记录
            cloud115_data = {
                'title': title,
                'filepath': filepath,
                'url': url,
                'thumbnail': thumbnail or '',
                'description': description or '',
                'category': category
            }
            
            # 保存到数据库
            if self.db.save_cloud115_file(cloud115_data):
                logging.info(f"创建115云盘文件成功: {filepath}")
                return cloud115_data
            else:
                logging.error(f"保存115云盘文件到数据库失败: {filepath}")
                # 失败时删除文件
                if os.path.exists(filepath):
                    os.remove(filepath)
                return None
                
        except Exception as e:
            logging.error(f"创建115云盘文件失败: {str(e)}")
            # 失败时删除文件
            if os.path.exists(filepath):
                os.remove(filepath)
            return None
    
    def delete_cloud115_file(self, file_id):
        """删除115云盘文件
        
        Args:
            file_id: 文件ID
            
        Returns:
            bool: 是否删除成功
        """
        # 获取文件信息
        file_info = self.db.get_cloud115_file(file_id)
        if not file_info:
            logging.error(f"要删除的115云盘文件不存在: {file_id}")
            return False
            
        filepath = file_info.get('filepath', '')
        
        # 删除物理文件
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                logging.info(f"已删除115云盘文件: {filepath}")
            
            # 从数据库删除记录
            if self.db.delete_cloud115_file(file_id):
                return True
            else:
                logging.error(f"从数据库删除115云盘文件记录失败: {file_id}")
                return False
                
        except Exception as e:
            logging.error(f"删除115云盘文件失败: {str(e)}")
            return False
    
    def import_cloud115_url(self, url, title=None, category="movies", thumbnail=None, description=None):
        """导入115云盘URL并创建文件
        
        Args:
            url: 115云盘URL
            title: 标题（如果为None，则从URL中提取）
            category: 分类
            thumbnail: 缩略图URL
            description: 描述
            
        Returns:
            dict: 创建的115云盘文件信息
        """
        if not url:
            logging.error("导入115云盘文件失败：URL不能为空")
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
                title = f"115Cloud_{int(time.time())}"
                
        # 创建115云盘文件
        return self.create_cloud115_file(title, url, category, thumbnail, description)
    
    def get_cloud115_play_url(self, file_id):
        """获取115云盘文件的播放URL，并记录播放
        
        Args:
            file_id: 文件ID
            
        Returns:
            str: 播放URL
        """
        # 获取文件信息
        file_info = self.db.get_cloud115_file(file_id)
        if not file_info:
            logging.error(f"115云盘文件不存在: {file_id}")
            return None
            
        # 更新播放计数
        self.db.update_cloud115_play_count(file_id)
        
        # 返回URL
        return file_info.get('url', '')
    
    def extract_video_ids(self, category=None, dictionary=None, only_missing=False):
        """从115云盘文件名中提取视频ID
        
        Args:
            category: 要处理的特定分类（可选）
            dictionary: 影片ID匹配字典（可选）
            only_missing: 是否只处理没有video_id的文件
            
        Returns:
            dict: 提取结果，包含成功和失败的提取
        """
        from modules.video_id_matcher import VideoIDMatcher
        
        # 获取文件列表
        files = self.db.get_cloud115_files(category=category)
        
        # 如果只处理没有video_id的文件
        if only_missing:
            files = [f for f in files if not f.get('video_id')]
            
        if not files:
            return {'success': {}, 'failed': []}
            
        # 创建匹配器
        matcher = VideoIDMatcher()
        if dictionary:
            matcher.load_dictionary_from_json(dictionary)
            
        # 处理文件
        processed_files = matcher.process_strm_files(files)
        
        # 准备更新结果
        success_dict = {}
        failed_list = []
        
        # 更新数据库中的视频ID和标题
        for file in processed_files:
            file_id = file.get('id')
            video_id = file.get('video_id')
            original_title = file.get('title', '')
            
            # 获取更新后的标题
            updated_title = matcher.update_strm_title(file, video_id)
            
            # 更新数据库
            if self.db.update_cloud115_video_id(file_id, video_id, updated_title):
                success_dict[str(file_id)] = video_id
            else:
                failed_list.append(f"{original_title} (ID: {file_id})")
        
        # 对于未处理的文件，添加到失败列表
        file_ids_processed = [str(file.get('id')) for file in processed_files]
        for file in files:
            if str(file.get('id')) not in file_ids_processed:
                failed_list.append(f"{file.get('title')} (ID: {file.get('id')})")
        
        return {'success': success_dict, 'failed': failed_list}
    
    def get_default_dictionary(self):
        """获取默认过滤字典
        
        Returns:
            list: 字典项列表
        """
        dict_path = "config/cloud115_filter_dictionary.txt"
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
            dict_path = "config/cloud115_filter_dictionary.txt"
            with open(dict_path, 'w', encoding='utf-8') as f:
                for item in dictionary_list:
                    f.write(f"{item}\n")
            
            logging.info(f"成功更新115云盘过滤字典，保存了 {len(dictionary_list)} 个项目")
            return True
        except Exception as e:
            logging.error(f"更新115云盘过滤字典时出错: {str(e)}")
            return False
    
    def delete_all_files(self):
        """删除所有115云盘文件
        
        Returns:
            int: 删除的文件数量
        """
        files = self.db.get_cloud115_files()
        count = 0
        
        for file in files:
            if self.delete_cloud115_file(file['id']):
                count += 1
                
        logging.info(f"已删除所有115云盘文件，共 {count} 个")
        return count
    
    def delete_files_by_category(self, category):
        """删除指定分类的所有115云盘文件
        
        Args:
            category: 要删除的文件分类
            
        Returns:
            int: 删除的文件数量
        """
        files = self.db.get_cloud115_files(category=category)
        count = 0
        
        for file in files:
            if self.delete_cloud115_file(file['id']):
                count += 1
                
        logging.info(f"已删除分类 '{category}' 的115云盘文件，共 {count} 个")
        return count
    
    def import_from_115_directory(self, directory_path):
        """从115云盘目录导入影片（待完善）
        
        Args:
            directory_path: 115云盘目录路径
            
        Returns:
            int: 导入的文件数量
        """
        # 这里只是一个占位符，需要后续完善115云盘API的集成
        logging.info(f"115云盘目录导入功能待开发: {directory_path}")
        return 0
        
    def sync_cloud115_movie_info(self, category=None):
        """同步115云盘文件的影片详情与电影数据库
        
        从API获取影片详情并更新数据库中的影片信息，每次处理10个请求
        
        Args:
            category: 可选，指定要同步的分类
            
        Returns:
            dict: 包含更新结果的字典，格式为 {"success": int, "failed": int}
        """
        try:
            # 获取所有带有video_id的115云盘文件（可选按分类过滤）
            cloud115_files = self.db.get_cloud115_files(category=category)
            cloud115_files = [file for file in cloud115_files if file.get('video_id')]
            
            total_files = len(cloud115_files)
            if total_files == 0:
                logging.info(f"115云盘中没有找到带有video_id的文件")
                return {"success": 0, "failed": 0}
                
            success_count = 0
            failed_count = 0
            
            # 导入get_movie_data函数
            from webserver import get_movie_data, format_movie_data, download_image
            
            # 批量处理，每批10个
            batch_size = 10
            for i in range(0, total_files, batch_size):
                batch = cloud115_files[i:i+batch_size]
                logging.info(f"处理第 {i+1}-{min(i+batch_size, total_files)}/{total_files} 批影片信息")
                
                # 处理当前批次
                batch_success = 0
                for file in batch:
                    video_id = file.get('video_id')
                    if not video_id:
                        continue
                        
                    try:
                        # 从API获取影片详情
                        logging.info(f"从API获取影片 {video_id} 的详情")
                        movie_data = get_movie_data(video_id)
                        
                        if movie_data:
                            # 格式化影片数据并保存到数据库
                            formatted_data = format_movie_data(movie_data)
                            self.db.save_movie(movie_data)
                            
                            # 下载封面图片
                            cover_url = formatted_data.get('image_url')
                            if cover_url:
                                cover_path = os.path.join("buspic", "covers", f"{video_id}.jpg")
                                download_image(cover_url, cover_path)
                            
                            # 准备演员数据
                            actors_data = []
                            for actor in formatted_data.get('actors', []):
                                actors_data.append({
                                    "id": actor.get('id', ''),
                                    "name": actor.get('name', ''),
                                    "image_url": actor.get('image_url', '')
                                })
                            
                            # 将演员数据序列化为JSON字符串
                            actors_json = json.dumps(actors_data)
                            
                            # 更新115云盘文件的元数据
                            file_id = file.get('id')
                            self.db.update_cloud115_metadata(
                                file_id,
                                video_id=video_id,
                                cover_image=formatted_data.get('image_url', ''),
                                actors=actors_json
                            )
                            
                            # 更新115云盘文件的标题和日期
                            self.db.update_cloud115_movie_info(
                                file_id,
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
            logging.info(f"115云盘文件影片详情同步完成，成功: {success_count}，失败: {failed_count}，分类: {category_info}")
            return {"success": success_count, "failed": failed_count}
        except Exception as e:
            logging.error(f"同步115云盘文件影片详情失败: {str(e)}")
            return {"success": 0, "failed": 0} 