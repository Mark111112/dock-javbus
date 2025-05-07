import os
import time
import json
import logging
import re
import sqlite3
from jellyfin_apiclient_python import JellyfinClient
from modules.video_id_matcher import VideoIDMatcher

class JellyfinLibrary:
    """Jellyfin文件库管理类，用于管理从Jellyfin服务器导入的媒体文件信息"""
    
    def __init__(self, db_file="data/javbus.db", log_level=logging.INFO):
        """初始化数据库连接
        
        Args:
            db_file: 数据库文件路径
            log_level: 日志级别，默认为INFO
        """
        self.db_path = db_file
        self.client = None
        
        # 设置日志
        self.logger = logging.getLogger("jellyfin_library")
        self.logger.setLevel(log_level)
        
        # 如果没有处理器，添加一个控制台处理器
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        
        self.logger.info("初始化JellyfinLibrary，数据库路径: %s", self.db_path)
        
        self.video_id_matcher = VideoIDMatcher()
        self.ensure_database()
    
    def ensure_database(self):
        """确保数据库和表存在"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 创建Jellyfin电影库表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS jelmovie (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                jellyfin_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                video_id TEXT,
                library_name TEXT NOT NULL,
                library_id TEXT NOT NULL,
                play_url TEXT,
                path TEXT,
                cover_image TEXT,
                actors TEXT,
                date TEXT,
                date_added INTEGER,
                last_played INTEGER DEFAULT 0,
                play_count INTEGER DEFAULT 0,
                UNIQUE(item_id)
            )
            ''')
            
            conn.commit()
        except sqlite3.Error as e:
            self.logger.error(f"创建表错误: {e}")
        finally:
            conn.close()
    
    def connect_to_server(self, server_url, username=None, password=None, api_key=None):
        """连接到Jellyfin服务器
        
        Args:
            server_url: Jellyfin服务器URL
            username: 用户名，与password一起使用
            password: 密码，与username一起使用
            api_key: API密钥，可单独使用
        
        Returns:
            bool: 连接是否成功
        """
        try:
            self.client = JellyfinClient()
            self.client.config.app('BusPre', '1.0.0', 'BusPre', 'unique_device_id')
            self.client.config.data["auth.ssl"] = True
            
            # 使用API密钥认证
            if api_key:
                self.client.config.data["app.name"] = 'BusPre'
                self.client.config.data["app.version"] = '1.0.0'
                self.client.authenticate({
                    "Servers": [{
                        "AccessToken": api_key, 
                        "address": server_url
                    }]
                }, discover=False)
                self.logger.info(f"使用API密钥连接到服务器 {server_url} 成功")
                return True
            
            # 使用用户名和密码认证
            elif username and password:
                self.client.auth.connect_to_address(server_url)
                result = self.client.auth.login(server_url, username, password)
                if result:
                    self.logger.info(f"使用用户名/密码连接到服务器 {server_url} 成功")
                return bool(result)
            
            return False
        except Exception as e:
            self.logger.error(f"连接Jellyfin服务器错误: {e}")
            return False
    
    def get_libraries(self):
        """获取Jellyfin库列表"""
        if not self.client:
            self.logger.error("未连接到Jellyfin服务器")
            return []
        
        try:
            libraries = self.client.jellyfin.get_views()
            # 调整返回项目，确保获取到正确的计数
            self.logger.info(f"获取到 {len(libraries.get('Items', []))} 个媒体库")
            
            result = [
                {
                    "id": library.get("Id"),
                    "name": library.get("Name"),
                    "type": library.get("CollectionType", ""),
                    "item_count": library.get("ChildCount", 0)
                }
                for library in libraries.get("Items", [])
                # 不要过滤CollectionFolder类型，可能会错过某些库
            ]
            
            self.logger.debug(f"媒体库列表: {result}")
            return result
        except Exception as e:
            self.logger.error(f"获取Jellyfin库列表错误: {e}")
            return []
    
    def get_library_items(self, library_id, start_index=0, limit=100):
        """获取指定库中的项目列表"""
        if not self.client:
            self.logger.error("未连接到Jellyfin服务器")
            return {"items": [], "total_count": 0}
        
        try:
            self.logger.debug(f"尝试获取媒体库 {library_id} 的项目列表，startIndex={start_index}, limit={limit}")
            
            params = {
                "ParentId": library_id,
                "StartIndex": start_index,
                "Limit": limit,
                "Recursive": True,  # 确保递归获取所有子项目
                "Fields": "Path,Overview,PremiereDate,MediaSources,ProviderIds,MediaStreams",  # 添加ProviderIds获取更多元数据
                "IncludeItemTypes": "Movie,Episode,Video",  # 明确指定需要的项目类型
                "UserId": "{UserId}"  # 确保使用当前用户ID
            }
            
            # 使用items方法而不是get_items方法
            # 注意：items方法需要一个handler参数来指定API端点路径
            result = self.client.jellyfin.items(handler="", action="GET", params=params)
            
            items_count = len(result.get('Items', []))
            total_count = result.get('TotalRecordCount', 0)
            
            # 增加详细日志输出
            self.logger.info(f"Jellyfin库 {library_id} 获取到 {items_count} 个项目，总计: {total_count}")
            self.logger.debug(f"获取到的第一个项目: {result.get('Items', [])[0].get('Name') if items_count > 0 else 'None'}")
            
            # 输出所有项目的名称（仅在调试级别）
            if self.logger.isEnabledFor(logging.DEBUG) and items_count > 0:
                item_names = [item.get('Name', 'Unknown') for item in result.get('Items', [])]
                self.logger.debug(f"获取到的项目名称: {', '.join(item_names)}")
            
            return {
                "items": result.get("Items", []),
                "total_count": total_count
            }
        except Exception as e:
            self.logger.error(f"获取库项目列表错误: {e}")
            import traceback
            self.logger.error(traceback.format_exc())  # 输出完整的错误堆栈
            return {"items": [], "total_count": 0}
    
    def extract_video_id(self, title):
        """从标题中提取视频ID"""
        # 先使用基本的video_id_matcher
        video_id = self.video_id_matcher.extract_video_id(title)
        if video_id:
            return video_id
        
        # 如果基本提取失败，尝试从NFO风格的标题中提取
        # 例如：PPPD-561-性欲が凄すぎてサークル内に穴兄弟を増やしまくる巨乳ヲタサーの姫-JULIA
        try:
            match = re.match(r'^([A-Z]+-\d+)', title)
            if match:
                return match.group(1).upper()
        except:
            pass
        
        return ""
    
    def get_play_url(self, item_id):
        """获取播放URL
        
        Args:
            item_id: 项目ID
        
        Returns:
            str: 播放URL
        """
        if not self.client:
            self.logger.error("未连接到Jellyfin服务器")
            return ""
        
        try:
            url = self.client.jellyfin.video_url(item_id)
            self.logger.debug(f"获取到项目 {item_id} 的播放URL: {url}")
            return url
        except Exception as e:
            self.logger.error(f"获取播放URL错误: {e}")
            return ""
    
    def import_library(self, library_id, library_name):
        """导入Jellyfin库"""
        if not self.client:
            self.logger.error("未连接到Jellyfin服务器")
            return {"imported": 0, "failed": 0, "details": {"success": {}, "failed": []}}
        
        try:
            # 连接到数据库
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            imported_count = 0
            failed_count = 0
            success_dict = {}
            failed_list = []
            
            # 分批获取所有项目
            start_index = 0
            limit = 200  # 增加每批的数量
            total_processed = 0
            
            self.logger.info(f"开始导入Jellyfin库 {library_name} (ID: {library_id})")
            
            while True:
                result = self.get_library_items(library_id, start_index, limit)
                items = result.get("items", [])
                total_count = result.get("total_count", 0)
                
                if not items:
                    self.logger.info(f"没有更多项目，总计处理: {total_processed}/{total_count}")
                    break
                
                self.logger.info(f"正在处理批次: {start_index}-{start_index+len(items)}/{total_count}, 本批次项目数: {len(items)}")
                
                for item in items:
                    item_id = item.get("Id")
                    title = item.get("Name", "")
                    
                    try:
                        # 只过滤掉文件夹类型，接受所有媒体项目
                        if item.get("IsFolder", False):
                            self.logger.debug(f"跳过文件夹: {title}")
                            continue
                        
                        item_type = item.get('Type', '')
                        media_type = item.get('MediaType', '')
                        
                        # 调试输出项目类型
                        self.logger.debug(f"处理项目: {title}, 类型: {item_type}, MediaType: {media_type}")
                        
                        # 从标题中提取video_id
                        video_id = self.extract_video_id(title)
                        
                        # 如果从标题中提取失败，尝试从提供者ID中提取
                        if not video_id and item.get("ProviderIds"):
                            provider_ids = item.get("ProviderIds", {})
                            self.logger.debug(f"项目 {title} 的提供者ID: {provider_ids}")
                            
                            if provider_ids.get("Tmdb"):
                                video_id = f"TMDB-{provider_ids['Tmdb']}"
                            elif provider_ids.get("Imdb"):
                                video_id = f"IMDB-{provider_ids['Imdb']}"
                        
                        # 如果仍然无法提取，则记录警告
                        if not video_id:
                            self.logger.warning(f"无法为项目 {title} 提取视频ID")
                        
                        # 获取播放URL
                        play_url = self.get_play_url(item_id)
                        
                        # 获取路径
                        path = ""
                        if item.get("MediaSources") and len(item["MediaSources"]) > 0:
                            path = item["MediaSources"][0].get("Path", "")
                            self.logger.debug(f"项目 {title} 的路径: {path}")
                        
                        # 获取封面图
                        cover_image = ""
                        if item_id:
                            cover_image = f"{self.client.config.data['auth.server']}/Items/{item_id}/Images/Primary"
                        
                        # 获取演员
                        actors = []
                        if item.get("People"):
                            actors = [person.get("Name") for person in item["People"] if person.get("Type") == "Actor"]
                            if actors:
                                self.logger.debug(f"项目 {title} 的演员: {', '.join(actors)}")
                        
                        # 获取日期
                        date = item.get("PremiereDate", "")
                        if date:
                            date = date.split("T")[0]  # 只保留日期部分
                            self.logger.debug(f"项目 {title} 的日期: {date}")
                        
                        # 当前时间戳
                        now = int(time.time())
                        
                        # 插入或更新数据库
                        cursor.execute('''
                        INSERT OR REPLACE INTO jelmovie
                        (title, jellyfin_id, item_id, video_id, library_name, library_id, 
                        play_url, path, cover_image, actors, date, date_added)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            title,
                            self.client.config.data['auth.server'],
                            item_id,
                            video_id,
                            library_name,
                            library_id,
                            play_url,
                            path,
                            cover_image,
                            json.dumps(actors) if actors else "",
                            date,
                            now
                        ))
                        
                        imported_count += 1
                        success_dict[item_id] = {"title": title, "video_id": video_id}
                        self.logger.debug(f"成功导入: {title}, video_id: {video_id}")
                        
                    except Exception as e:
                        self.logger.error(f"导入项目错误: {e}, 项目: {title}")
                        import traceback
                        self.logger.error(traceback.format_exc())
                        failed_count += 1
                        failed_list.append(f"{title} (ID: {item_id})")
                
                # 更新起始索引
                total_processed += len(items)
                start_index += len(items)
                
                # 每批提交一次事务
                conn.commit()
                self.logger.info(f"已提交批次 {start_index-len(items)}-{start_index}，进度: {total_processed}/{total_count}")
                
                if start_index >= total_count:
                    self.logger.info(f"已处理所有项目: {total_processed}/{total_count}")
                    break
            
            conn.commit()
            conn.close()
            
            self.logger.info(f"导入Jellyfin库 {library_name} 完成，导入 {imported_count} 个项目，失败 {failed_count} 个")
            
            return {
                "imported": imported_count,
                "failed": failed_count,
                "details": {
                    "success": success_dict,
                    "failed": failed_list
                }
            }
        except Exception as e:
            self.logger.error(f"导入Jellyfin库错误: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {"imported": 0, "failed": 0, "details": {"success": {}, "failed": [str(e)]}}
    
    def get_imported_libraries(self):
        """获取已导入的库列表
        
        Returns:
            list: 库列表
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT DISTINCT library_id, library_name, jellyfin_id,
            COUNT(*) as item_count,
            MAX(date_added) as last_updated
            FROM jelmovie
            GROUP BY library_id
            ORDER BY last_updated DESC
            ''')
            
            libraries = []
            for row in cursor.fetchall():
                libraries.append({
                    "id": row["library_id"],
                    "name": row["library_name"],
                    "server": row["jellyfin_id"],
                    "item_count": row["item_count"],
                    "last_updated": row["last_updated"]
                })
            
            conn.close()
            self.logger.info(f"获取到 {len(libraries)} 个已导入的库")
            return libraries
        except Exception as e:
            self.logger.error(f"获取已导入库列表错误: {e}")
            return []
    
    def get_library_movies(self, library_id=None, start=0, limit=50, search_term=None):
        """获取库中的电影列表
        
        Args:
            library_id: 库ID，如果为None则获取所有库
            start: 起始索引
            limit: 每页数量
            search_term: 搜索关键词
        
        Returns:
            dict: 包含电影列表和总数的字典
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            query_params = []
            where_clauses = []
            
            if library_id:
                where_clauses.append("library_id = ?")
                query_params.append(library_id)
            
            if search_term:
                where_clauses.append("(title LIKE ? OR video_id LIKE ?)")
                query_params.extend([f"%{search_term}%", f"%{search_term}%"])
            
            where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            
            # 获取总数
            count_query = f"SELECT COUNT(*) as count FROM jelmovie {where_clause}"
            cursor.execute(count_query, query_params)
            total_count = cursor.fetchone()["count"]
            
            # 获取电影列表
            movie_query = f"""
            SELECT * FROM jelmovie
            {where_clause}
            ORDER BY date_added DESC
            LIMIT ? OFFSET ?
            """
            query_params.extend([limit, start])
            
            cursor.execute(movie_query, query_params)
            
            movies = []
            for row in cursor.fetchall():
                movie = dict(row)
                
                # 解析JSON字段
                if movie.get("actors") and movie["actors"]:
                    try:
                        movie["actors"] = json.loads(movie["actors"])
                    except:
                        movie["actors"] = []
                else:
                    movie["actors"] = []
                
                movies.append(movie)
            
            conn.close()
            
            self.logger.info(f"获取电影列表，库ID: {library_id}, 搜索词: {search_term}, 获取到 {len(movies)} 个项目，总计: {total_count}")
            return {
                "items": movies,
                "total_count": total_count
            }
        except Exception as e:
            self.logger.error(f"获取库电影列表错误: {e}")
            return {"items": [], "total_count": 0}
    
    def delete_library(self, library_id):
        """删除导入的库
        
        Args:
            library_id: 库ID
        
        Returns:
            int: 删除的记录数
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM jelmovie WHERE library_id = ?", (library_id,))
            deleted_count = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            self.logger.info(f"删除库 {library_id} 成功，删除了 {deleted_count} 条记录")
            return deleted_count
        except Exception as e:
            self.logger.error(f"删除库错误: {e}")
            return 0
    
    def update_play_count(self, item_id):
        """更新播放计数
        
        Args:
            item_id: 项目ID
        
        Returns:
            bool: 是否成功
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            now = int(time.time())
            
            cursor.execute('''
            UPDATE jelmovie
            SET play_count = play_count + 1, last_played = ?
            WHERE item_id = ?
            ''', (now, item_id))
            
            updated = cursor.rowcount > 0
            conn.commit()
            conn.close()
            
            if updated:
                self.logger.debug(f"更新项目 {item_id} 的播放计数成功")
            else:
                self.logger.warning(f"未找到项目 {item_id} 来更新播放计数")
                
            return updated
        except Exception as e:
            self.logger.error(f"更新播放计数错误: {e}")
            return False
            
    def find_files_by_movie_id(self, movie_id):
        """根据电影ID查找Jellyfin库中对应的文件
        
        Args:
            movie_id: 电影ID (video_id)
            
        Returns:
            list: 文件列表，每个文件包含id, title, path等信息
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # 查询匹配video_id的jellyfin文件
            cursor.execute('''
            SELECT id, title, item_id, path, play_url, cover_image, video_id
            FROM jelmovie
            WHERE video_id = ?
            ORDER BY title
            ''', (movie_id,))
            
            files = []
            for row in cursor.fetchall():
                file_data = dict(row)
                files.append(file_data)
            
            conn.close()
            
            self.logger.info(f"为电影ID {movie_id} 找到 {len(files)} 个Jellyfin文件")
            return files
        except Exception as e:
            self.logger.error(f"查找电影ID的Jellyfin文件错误: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return [] 