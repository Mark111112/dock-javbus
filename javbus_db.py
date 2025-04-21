import os
import json
import sqlite3
import time
import threading
from datetime import datetime, timedelta
import re

class JavbusDatabase:
    """JavBus数据库类，用于存储和检索演员和影片信息"""
    
    def __init__(self, db_file="data/javbus.db"):
        """初始化数据库连接"""
        self.db_path = db_file
        self.local = threading.local()  # 使用线程本地存储
        print(f"Initializing JavbusDatabase with database file: {self.db_path}")
        self.connect()
        self.create_tables()
        self.upgrade_schema()  # Ensure schema is up-to-date
    
    def connect(self):
        """连接到数据库，每个线程使用独立的连接"""
        try:
            # 确保数据库目录存在
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            if not hasattr(self.local, 'conn') or self.local.conn is None:
                self.local.conn = sqlite3.connect(self.db_path)
                self.local.conn.row_factory = sqlite3.Row  # 使查询结果可以通过列名访问
                self.local.cursor = self.local.conn.cursor()
        except sqlite3.Error as e:
            print(f"数据库连接错误: {e}")
    
    def close(self):
        """关闭数据库连接"""
        if hasattr(self.local, 'conn') and self.local.conn:
            self.local.conn.close()
            self.local.conn = None
            self.local.cursor = None
    
    def ensure_connection(self):
        """确保当前线程有可用的数据库连接"""
        if not hasattr(self.local, 'conn') or self.local.conn is None:
            self.connect()
    
    def create_tables(self):
        """创建必要的数据表"""
        self.ensure_connection()
        try:
            # 创建电影表
            self.local.cursor.execute('''
            CREATE TABLE IF NOT EXISTS movies (
                id TEXT PRIMARY KEY,
                data TEXT,
                title TEXT,
                cover TEXT,
                date TEXT,
                publisher TEXT,
                last_updated INTEGER
            )
            ''')
            
            # 创建演员表
            self.local.cursor.execute('''
            CREATE TABLE IF NOT EXISTS stars (
                id TEXT PRIMARY KEY,
                data TEXT,
                name TEXT,
                avatar TEXT,
                birthday TEXT,
                age TEXT,
                height TEXT,
                bust TEXT,
                waistline TEXT,
                hipline TEXT,
                birthplace TEXT,
                hobby TEXT,
                last_updated INTEGER
            )
            ''')
            
            # 创建演员-电影关系表
            self.local.cursor.execute('''
            CREATE TABLE IF NOT EXISTS star_movie (
                star_id TEXT,
                movie_id TEXT,
                PRIMARY KEY (star_id, movie_id),
                FOREIGN KEY (star_id) REFERENCES stars (id) ON DELETE CASCADE,
                FOREIGN KEY (movie_id) REFERENCES movies (id) ON DELETE CASCADE
            )
            ''')
            
            # 创建搜索历史表
            self.local.cursor.execute('''
            CREATE TABLE IF NOT EXISTS search_history (
                keyword TEXT PRIMARY KEY,
                search_time INTEGER
            )
            ''')
            
            # 创建STRM文件库表
            self.local.cursor.execute('''
            CREATE TABLE IF NOT EXISTS strm_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                filepath TEXT NOT NULL,
                url TEXT NOT NULL,
                thumbnail TEXT,
                description TEXT,
                category TEXT,
                date_added INTEGER,
                last_played INTEGER DEFAULT 0,
                play_count INTEGER DEFAULT 0,
                video_id TEXT,
                cover_image TEXT,
                actors TEXT,
                date TEXT,
                UNIQUE(filepath)
            )
            ''')
            
            # 创建115网盘文件库表
            self.local.cursor.execute('''
            CREATE TABLE IF NOT EXISTS cloud115_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                filepath TEXT NOT NULL,
                url TEXT NOT NULL,
                thumbnail TEXT,
                description TEXT,
                category TEXT,
                date_added INTEGER,
                last_played INTEGER DEFAULT 0,
                play_count INTEGER DEFAULT 0,
                video_id TEXT,
                cover_image TEXT,
                actors TEXT,
                date TEXT,
                file_id TEXT,
                pickcode TEXT,
                UNIQUE(filepath)
            )
            ''')
            
            self.local.conn.commit()
        except sqlite3.Error as e:
            print(f"创建表错误: {e}")
    
    def save_star(self, star_data):
        """保存演员信息到数据库"""
        self.ensure_connection()
        try:
            star_id = star_data.get('id')
            if not star_id:
                return False
            
            # 将完整数据转换为JSON字符串
            data_json = json.dumps(star_data, ensure_ascii=False)
            
            # 准备插入或更新的数据
            now = int(time.time())
            self.local.cursor.execute('''
            INSERT OR REPLACE INTO stars 
            (id, name, avatar, birthday, age, height, bust, waistline, hipline, birthplace, hobby, last_updated, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                star_id,
                star_data.get('name', ''),
                star_data.get('avatar', ''),
                star_data.get('birthday', ''),
                star_data.get('age', ''),
                star_data.get('height', ''),
                star_data.get('bust', ''),
                star_data.get('waistline', ''),
                star_data.get('hipline', ''),
                star_data.get('birthplace', ''),
                star_data.get('hobby', ''),
                now,
                data_json
            ))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"保存演员信息错误: {e}")
            return False
    
    def save_movie(self, movie_data):
        """保存影片信息到数据库"""
        self.ensure_connection()
        try:
            movie_id = movie_data.get('id')
            if not movie_id:
                return False
            
            # 将完整数据转换为JSON字符串
            data_json = json.dumps(movie_data, ensure_ascii=False)
            
            # 准备插入或更新的数据
            now = int(time.time())
            self.local.cursor.execute('''
            INSERT OR REPLACE INTO movies 
            (id, title, cover, date, publisher, last_updated, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                movie_id,
                movie_data.get('title', ''),
                movie_data.get('img', ''),
                movie_data.get('date', ''),
                movie_data.get('publisher', {}).get('name', '') if isinstance(movie_data.get('publisher'), dict) else movie_data.get('publisher', ''),
                now,
                data_json
            ))
            
            # 保存演员关联
            stars = movie_data.get('stars', [])
            if stars:
                # 先删除旧的关联
                self.local.cursor.execute('DELETE FROM star_movie WHERE movie_id = ?', (movie_id,))
                
                # 添加新的关联
                for star in stars:
                    star_id = star.get('id')
                    if star_id:
                        self.local.cursor.execute('''
                        INSERT OR IGNORE INTO star_movie (star_id, movie_id)
                        VALUES (?, ?)
                        ''', (star_id, movie_id))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"保存影片信息错误: {e}")
            return False
    
    def get_star(self, star_id, max_age=7):
        """获取演员信息，如果数据过期则返回None"""
        self.ensure_connection()
        try:
            # 计算过期时间（默认7天）
            expire_time = int(time.time()) - (max_age * 24 * 60 * 60)
            
            self.local.cursor.execute('''
            SELECT data FROM stars 
            WHERE id = ? AND last_updated > ?
            ''', (star_id, expire_time))
            
            result = self.local.cursor.fetchone()
            if result:
                return json.loads(result['data'])
            return None
        except sqlite3.Error as e:
            print(f"获取演员信息错误: {e}")
            return None
    
    def get_movie(self, movie_id, max_age=30):
        """获取影片信息，如果数据过期则返回None"""
        self.ensure_connection()
        try:
            # 计算过期时间（默认30天）
            expire_time = int(time.time()) - (max_age * 24 * 60 * 60)
            
            self.local.cursor.execute('''
            SELECT data FROM movies 
            WHERE id = ? AND last_updated > ?
            ''', (movie_id, expire_time))
            
            result = self.local.cursor.fetchone()
            if result:
                return json.loads(result['data'])
            return None
        except sqlite3.Error as e:
            print(f"获取影片信息错误: {e}")
            return None
    
    def search_stars(self, keyword, max_age=7):
        """搜索演员，返回匹配的演员列表"""
        self.ensure_connection()
        try:
            # 计算过期时间（默认7天）
            expire_time = int(time.time()) - (max_age * 24 * 60 * 60)
            
            # 使用LIKE进行模糊匹配
            search_term = f"%{keyword}%"
            self.local.cursor.execute('''
            SELECT data FROM stars 
            WHERE name LIKE ? AND last_updated > ?
            ''', (search_term, expire_time))
            
            results = self.local.cursor.fetchall()
            return [json.loads(row['data']) for row in results]
        except sqlite3.Error as e:
            print(f"搜索演员错误: {e}")
            return []
    
    def get_star_movies(self, star_id, max_age=30):
        """获取演员的所有影片"""
        self.ensure_connection()
        try:
            # 计算过期时间（默认30天）
            expire_time = int(time.time()) - (max_age * 24 * 60 * 60)
            
            self.local.cursor.execute('''
            SELECT m.data FROM movies m
            JOIN star_movie sm ON m.id = sm.movie_id
            WHERE sm.star_id = ? AND m.last_updated > ?
            ''', (star_id, expire_time))
            
            results = self.local.cursor.fetchall()
            return [json.loads(row['data']) for row in results]
        except sqlite3.Error as e:
            print(f"获取演员影片错误: {e}")
            return []
    
    def save_search_history(self, keyword):
        """保存搜索历史"""
        self.ensure_connection()
        try:
            now = int(time.time())
            self.local.cursor.execute('''
            INSERT OR REPLACE INTO search_history (keyword, search_time)
            VALUES (?, ?)
            ''', (keyword, now))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"保存搜索历史错误: {e}")
            return False
    
    def get_search_history(self, limit=10):
        """获取最近的搜索历史"""
        self.ensure_connection()
        try:
            self.local.cursor.execute('''
            SELECT keyword FROM search_history
            ORDER BY search_time DESC
            LIMIT ?
            ''', (limit,))
            
            results = self.local.cursor.fetchall()
            return [row['keyword'] for row in results]
        except sqlite3.Error as e:
            print(f"获取搜索历史错误: {e}")
            return []
    
    def clear_expired_data(self, star_max_age=30, movie_max_age=90):
        """清理过期数据"""
        self.ensure_connection()
        try:
            # 计算过期时间
            star_expire_time = int(time.time()) - (star_max_age * 24 * 60 * 60)
            movie_expire_time = int(time.time()) - (movie_max_age * 24 * 60 * 60)
            
            # 删除过期的演员数据
            self.local.cursor.execute('DELETE FROM stars WHERE last_updated < ?', (star_expire_time,))
            
            # 删除过期的影片数据
            self.local.cursor.execute('DELETE FROM movies WHERE last_updated < ?', (movie_expire_time,))
            
            # 清理不再存在的关联
            self.local.cursor.execute('''
            DELETE FROM star_movie 
            WHERE star_id NOT IN (SELECT id FROM stars) 
            OR movie_id NOT IN (SELECT id FROM movies)
            ''')
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"清理过期数据错误: {e}")
            return False
    
    def clear_star_data(self, star_id):
        """清除特定演员的所有数据，包括演员信息和相关影片"""
        self.ensure_connection()
        try:
            # 获取与该演员相关的所有影片ID
            self.local.cursor.execute('''
            SELECT movie_id FROM star_movie 
            WHERE star_id = ?
            ''', (star_id,))
            
            movie_ids = [row['movie_id'] for row in self.local.cursor.fetchall()]
            
            # 删除演员-影片关联
            self.local.cursor.execute('''
            DELETE FROM star_movie 
            WHERE star_id = ?
            ''', (star_id,))
            
            # 删除演员信息
            self.local.cursor.execute('''
            DELETE FROM stars 
            WHERE id = ?
            ''', (star_id,))
            
            # 删除只与该演员相关的影片
            for movie_id in movie_ids:
                # 检查该影片是否还与其他演员相关
                self.local.cursor.execute('''
                SELECT COUNT(*) as count FROM star_movie 
                WHERE movie_id = ?
                ''', (movie_id,))
                
                result = self.local.cursor.fetchone()
                if result and result['count'] == 0:
                    # 如果没有其他演员与该影片相关，则删除影片
                    self.local.cursor.execute('''
                    DELETE FROM movies 
                    WHERE id = ?
                    ''', (movie_id,))
            
            self.local.conn.commit()
            return True, len(movie_ids)
        except sqlite3.Error as e:
            print(f"清除演员数据错误: {e}")
            return False, 0
    
    def get_recent_movies(self, limit=4):
        """获取最近更新的电影列表"""
        self.ensure_connection()
        movies = []
        try:
            self.local.cursor.execute('''
            SELECT data FROM movies 
            ORDER BY last_updated DESC
            LIMIT ?
            ''', (limit,))
            
            results = self.local.cursor.fetchall()
            if results:
                for row in results:
                    try:
                        movie_data = json.loads(row['data'])
                        movies.append(movie_data)
                    except:
                        pass
        except sqlite3.Error as e:
            print(f"获取最近电影错误: {e}")
        
        return movies
    
    # Add new methods for STRM library management
    def save_strm_file(self, strm_data):
        """保存STRM文件信息到数据库"""
        self.ensure_connection()
        try:
            now = int(time.time())
            
            # 检查必要字段
            if not all(key in strm_data for key in ['title', 'filepath', 'url']):
                return False
                
            # 确保数据库中存在必要的列
            self.add_video_id_column_if_not_exists()  
            self.add_cover_and_actors_columns_if_not_exists()
            self.add_date_column_if_not_exists()
                
            # 准备插入或更新的数据
            self.local.cursor.execute('''
            INSERT OR REPLACE INTO strm_library 
            (title, filepath, url, thumbnail, description, category, date_added, video_id, cover_image, actors, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                strm_data.get('title', ''),
                strm_data.get('filepath', ''),
                strm_data.get('url', ''),
                strm_data.get('thumbnail', ''),
                strm_data.get('description', ''),
                strm_data.get('category', ''),
                now,
                strm_data.get('video_id', ''),
                strm_data.get('cover_image', ''),
                strm_data.get('actors', ''),
                strm_data.get('date', '')
            ))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"保存STRM文件信息错误: {e}")
            return False
    
    def get_strm_files(self, category=None, limit=None, offset=0, sort_by='date_added', sort_order='desc'):
        """获取STRM文件列表，可按分类筛选并排序
        
        Args:
            category: 可选的分类筛选
            limit: 结果数量限制
            offset: 分页偏移量
            sort_by: 排序字段，可选值：date_added, video_id, title, date
            sort_order: 排序方向，可选值：asc, desc, random
            
        Returns:
            list: STRM文件列表
        """
        self.ensure_connection()
        try:
            # 处理排序参数
            order_clause = self._get_order_clause(sort_by, sort_order)
            
            if category:
                query = f'''
                SELECT * FROM strm_library
                WHERE category = ?
                {order_clause}
                '''
                params = (category,)
            else:
                query = f'''
                SELECT * FROM strm_library
                {order_clause}
                '''
                params = ()
                
            # 添加分页
            if limit:
                query += ' LIMIT ? OFFSET ?'
                params = params + (limit, offset)
                
            self.local.cursor.execute(query, params)
            
            results = self.local.cursor.fetchall()
            # 将sqlite3.Row转换为字典
            return [dict(row) for row in results]
        except sqlite3.Error as e:
            print(f"获取STRM文件列表错误: {e}")
            return []
    
    def get_strm_file(self, file_id):
        """根据ID获取单个STRM文件信息"""
        self.ensure_connection()
        try:
            self.local.cursor.execute('''
            SELECT * FROM strm_library WHERE id = ?
            ''', (file_id,))
            
            result = self.local.cursor.fetchone()
            if result:
                return dict(result)
            return None
        except sqlite3.Error as e:
            print(f"获取STRM文件信息错误: {e}")
            return None
    
    def delete_strm_file(self, file_id):
        """删除STRM文件记录"""
        self.ensure_connection()
        try:
            self.local.cursor.execute('''
            DELETE FROM strm_library WHERE id = ?
            ''', (file_id,))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"删除STRM文件错误: {e}")
            return False
    
    def update_strm_play_count(self, file_id):
        """更新STRM文件播放次数和最后播放时间"""
        self.ensure_connection()
        try:
            now = int(time.time())
            self.local.cursor.execute('''
            UPDATE strm_library
            SET play_count = play_count + 1, last_played = ?
            WHERE id = ?
            ''', (now, file_id))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"更新STRM文件播放信息错误: {e}")
            return False
    
    def get_strm_categories(self):
        """获取所有STRM分类
        
        Returns:
            list: 分类列表
        """
        self.ensure_connection()
        try:
            self.local.cursor.execute('''
            SELECT DISTINCT category FROM strm_library
            ''')
            
            result = self.local.cursor.fetchall()
            return [row['category'] for row in result]
        except sqlite3.Error as e:
            print(f"获取STRM分类错误: {e}")
            return []
            
    def add_video_id_column_if_not_exists(self):
        """检查并添加video_id列到strm_library表（如果不存在）
        
        Returns:
            bool: 成功返回True，否则返回False
        """
        self.ensure_connection()
        try:
            # 检查video_id列是否存在
            self.local.cursor.execute('''
            PRAGMA table_info(strm_library)
            ''')
            
            columns = self.local.cursor.fetchall()
            column_names = [column['name'] for column in columns]
            
            # 如果video_id列不存在，添加它
            if 'video_id' not in column_names:
                self.local.cursor.execute('''
                ALTER TABLE strm_library ADD COLUMN video_id TEXT
                ''')
                self.local.conn.commit()
                print("已添加video_id列到strm_library表")
            
            return True
        except sqlite3.Error as e:
            print(f"添加video_id列错误: {e}")
            return False
    
    def update_strm_video_id(self, file_id, video_id):
        """更新STRM文件的视频ID
        
        Args:
            file_id: STRM文件ID
            video_id: 提取的视频ID
            
        Returns:
            bool: 成功返回True，否则返回False
        """
        self.ensure_connection()
        try:
            self.local.cursor.execute('''
            UPDATE strm_library
            SET video_id = ?
            WHERE id = ?
            ''', (video_id, file_id))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"更新STRM视频ID错误: {e}")
            return False
    
    def update_strm_title(self, file_id, title):
        """更新STRM文件的标题
        
        Args:
            file_id: STRM文件ID
            title: 新标题
            
        Returns:
            bool: 成功返回True，否则返回False
        """
        self.ensure_connection()
        try:
            self.local.cursor.execute('''
            UPDATE strm_library
            SET title = ?
            WHERE id = ?
            ''', (title, file_id))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"更新STRM标题错误: {e}")
            return False
            
    def batch_update_strm_video_ids(self, updates):
        """批量更新STRM文件的视频ID和标题
        
        Args:
            updates: 包含id, video_id, title的字典列表
            
        Returns:
            int: 成功更新的记录数
        """
        self.ensure_connection()
        success_count = 0
        
        try:
            for update in updates:
                file_id = update.get('id')
                video_id = update.get('video_id')
                title = update.get('title')
                
                if file_id and (video_id or title):
                    # 更新video_id（如果提供）
                    if video_id:
                        self.local.cursor.execute('''
                        UPDATE strm_library
                        SET video_id = ?
                        WHERE id = ?
                        ''', (video_id, file_id))
                    
                    # 更新title（如果提供）
                    if title:
                        self.local.cursor.execute('''
                        UPDATE strm_library
                        SET title = ?
                        WHERE id = ?
                        ''', (title, file_id))
                    
                    success_count += 1
            
            self.local.conn.commit()
            return success_count
        except sqlite3.Error as e:
            print(f"批量更新STRM视频ID错误: {e}")
            self.local.conn.rollback()
            return 0
            
    def get_all_strm_video_ids(self):
        """获取所有STRM文件的ID和视频ID
        
        Returns:
            list: 元组 (file_id, video_id) 的列表
        """
        self.ensure_connection()
        try:
            # 确保video_id列存在
            self.add_video_id_column_if_not_exists()
            
            self.local.cursor.execute('''
            SELECT id, video_id FROM strm_library
            WHERE video_id IS NOT NULL AND video_id != ""
            ''')
            
            result = self.local.cursor.fetchall()
            return [(row['id'], row['video_id']) for row in result]
        except sqlite3.Error as e:
            print(f"获取STRM视频ID错误: {e}")
            return []
    
    def add_cover_and_actors_columns_if_not_exists(self):
        """检查并添加cover_image和actors列到strm_library表（如果不存在）
        
        Returns:
            bool: 成功返回True，否则返回False
        """
        self.ensure_connection()
        try:
            # 检查列是否存在
            self.local.cursor.execute('''
            PRAGMA table_info(strm_library)
            ''')
            
            columns = self.local.cursor.fetchall()
            column_names = [column['name'] for column in columns]
            
            # 如果cover_image列不存在，添加它
            if 'cover_image' not in column_names:
                self.local.cursor.execute('''
                ALTER TABLE strm_library ADD COLUMN cover_image TEXT
                ''')
                print("已添加cover_image列到strm_library表")
            
            # 如果actors列不存在，添加它
            if 'actors' not in column_names:
                self.local.cursor.execute('''
                ALTER TABLE strm_library ADD COLUMN actors TEXT
                ''')
                print("已添加actors列到strm_library表")
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"添加新列错误: {e}")
            return False
            
    def update_strm_metadata(self, file_id, video_id=None, cover_image=None, actors=None):
        """更新STRM文件的元数据
        
        Args:
            file_id: STRM文件ID
            video_id: 视频ID
            cover_image: 封面图片URL
            actors: 演员信息JSON字符串
            
        Returns:
            bool: 成功返回True，否则返回False
        """
        self.ensure_connection()
        try:
            # 确保表中存在必要的列
            self.add_video_id_column_if_not_exists()
            self.add_cover_and_actors_columns_if_not_exists()
            
            # 构建更新SQL
            update_fields = []
            params = []
            
            if video_id is not None:
                update_fields.append("video_id = ?")
                params.append(video_id)
                
            if cover_image is not None:
                update_fields.append("cover_image = ?")
                params.append(cover_image)
                
            if actors is not None:
                update_fields.append("actors = ?")
                params.append(actors)
                
            if not update_fields:
                return True  # 没有需要更新的字段
                
            # 追加file_id参数
            params.append(file_id)
            
            # 执行更新
            self.local.cursor.execute(f'''
            UPDATE strm_library
            SET {", ".join(update_fields)}
            WHERE id = ?
            ''', params)
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"更新STRM元数据错误: {e}")
            return False
    
    def search_strm_files(self, query, category=None, limit=None, offset=0, sort_by='date_added', sort_order='desc'):
        """搜索STRM文件，在标题和视频ID中搜索关键词
        
        Args:
            query: 搜索关键词
            category: 可选的分类筛选
            limit: 结果数量限制
            offset: 分页偏移量
            sort_by: 排序字段，可选值：date_added, video_id, title, date
            sort_order: 排序方向，可选值：asc, desc, random
            
        Returns:
            list: 符合条件的STRM文件列表
        """
        self.ensure_connection()
        try:
            # 处理排序参数
            order_clause = self._get_order_clause(sort_by, sort_order)
            
            # 构建搜索条件
            search_term = f"%{query}%"
            
            if category:
                base_query = f'''
                SELECT * FROM strm_library
                WHERE category = ? AND (
                    title LIKE ? OR 
                    video_id LIKE ? OR
                    actors LIKE ?
                )
                {order_clause}
                '''
                params = (category, search_term, search_term, search_term)
            else:
                base_query = f'''
                SELECT * FROM strm_library
                WHERE title LIKE ? OR 
                      video_id LIKE ? OR
                      actors LIKE ?
                {order_clause}
                '''
                params = (search_term, search_term, search_term)
                
            # 添加分页
            if limit:
                base_query += ' LIMIT ? OFFSET ?'
                params = params + (limit, offset)
                
            self.local.cursor.execute(base_query, params)
            
            results = self.local.cursor.fetchall()
            # 将sqlite3.Row转换为字典
            return [dict(row) for row in results]
        except sqlite3.Error as e:
            print(f"搜索STRM文件错误: {e}")
            return []
    
    def add_date_column_if_not_exists(self):
        """检查并添加date列到strm_library表（如果不存在）
        
        Returns:
            bool: 成功返回True，否则返回False
        """
        self.ensure_connection()
        try:
            # 检查date列是否存在
            self.local.cursor.execute('''
            PRAGMA table_info(strm_library)
            ''')
            
            columns = self.local.cursor.fetchall()
            column_names = [column['name'] for column in columns]
            
            # 如果date列不存在，添加它
            if 'date' not in column_names:
                self.local.cursor.execute('''
                ALTER TABLE strm_library ADD COLUMN date TEXT
                ''')
                self.local.conn.commit()
                print("已添加date列到strm_library表")
            
            return True
        except sqlite3.Error as e:
            print(f"添加date列错误: {e}")
            return False
            
    def update_strm_movie_info(self, file_id, title=None, date=None):
        """更新STRM文件的电影信息（从movies表同步）
        
        Args:
            file_id: STRM文件ID
            title: 电影标题
            date: 发布日期
            
        Returns:
            bool: 成功返回True，否则返回False
        """
        self.ensure_connection()
        try:
            # 确保表中存在必要的列
            self.add_date_column_if_not_exists()
            
            # 构建更新SQL
            update_fields = []
            params = []
            
            if title is not None:
                update_fields.append("title = ?")
                params.append(title)
                
            if date is not None:
                update_fields.append("date = ?")
                params.append(date)
                
            if not update_fields:
                return True  # 没有需要更新的字段
                
            # 追加file_id参数
            params.append(file_id)
            
            # 执行更新
            self.local.cursor.execute(f'''
            UPDATE strm_library
            SET {", ".join(update_fields)}
            WHERE id = ?
            ''', params)
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"更新STRM电影信息错误: {e}")
            return False
    
    def upgrade_schema(self):
        """升级数据库架构"""
        self.ensure_connection()
        try:
            # 检查是否需要添加新的列到movies表
            self.local.cursor.execute('''
            PRAGMA table_info(movies)
            ''')
            
            columns = self.local.cursor.fetchall()
            column_names = [column['name'] for column in columns]
            
            # 确保电影表有所有需要的列
            required_columns = {
                'title': 'TEXT',
                'cover': 'TEXT',
                'date': 'TEXT',
                'publisher': 'TEXT'
            }
            
            for col_name, col_type in required_columns.items():
                if col_name not in column_names:
                    print(f"升级电影表: 添加 {col_name} 列")
                    self.local.cursor.execute(f'''
                    ALTER TABLE movies ADD COLUMN {col_name} {col_type}
                    ''')
            
            # 检查是否需要添加新的列到stars表
            self.local.cursor.execute('''
            PRAGMA table_info(stars)
            ''')
            
            columns = self.local.cursor.fetchall()
            column_names = [column['name'] for column in columns]
            
            # 确保演员表有所有需要的列
            required_columns = {
                'name': 'TEXT',
                'avatar': 'TEXT',
                'birthday': 'TEXT',
                'age': 'TEXT',
                'height': 'TEXT',
                'bust': 'TEXT',
                'waistline': 'TEXT',
                'hipline': 'TEXT',
                'birthplace': 'TEXT',
                'hobby': 'TEXT'
            }
            
            for col_name, col_type in required_columns.items():
                if col_name not in column_names:
                    print(f"升级演员表: 添加 {col_name} 列")
                    self.local.cursor.execute(f'''
                    ALTER TABLE stars ADD COLUMN {col_name} {col_type}
                    ''')
            
            # 检查是否需要创建star_movie表
            self.local.cursor.execute('''
            SELECT name FROM sqlite_master WHERE type='table' AND name='star_movie'
            ''')
            
            if not self.local.cursor.fetchone():
                print("创建演员-电影关系表")
                self.local.cursor.execute('''
                CREATE TABLE star_movie (
                    star_id TEXT,
                    movie_id TEXT,
                    PRIMARY KEY (star_id, movie_id),
                    FOREIGN KEY (star_id) REFERENCES stars (id) ON DELETE CASCADE,
                    FOREIGN KEY (movie_id) REFERENCES movies (id) ON DELETE CASCADE
                )
                ''')
            
            # 检查是否需要创建search_history表
            self.local.cursor.execute('''
            SELECT name FROM sqlite_master WHERE type='table' AND name='search_history'
            ''')
            
            if not self.local.cursor.fetchone():
                print("创建搜索历史表")
                self.local.cursor.execute('''
                CREATE TABLE search_history (
                    keyword TEXT PRIMARY KEY,
                    search_time INTEGER
                )
                ''')
            
            # 检查STRM表的date列
            self.local.cursor.execute('''
            PRAGMA table_info(strm_library)
            ''')
            
            columns = self.local.cursor.fetchall()
            column_names = [column['name'] for column in columns]
            
            # 添加date列（如果不存在）
            if 'date' not in column_names:
                print("升级STRM表：添加date列")
                self.local.cursor.execute('''
                ALTER TABLE strm_library ADD COLUMN date TEXT
                ''')
            
            # 检查cloud115_library表的file_id和pickcode列
            self.local.cursor.execute('''
            PRAGMA table_info(cloud115_library)
            ''')
            
            columns = self.local.cursor.fetchall()
            column_names = [column['name'] for column in columns]
            
            # 添加file_id列（如果不存在）
            if 'file_id' not in column_names:
                print("升级cloud115_library表：添加file_id列")
                self.local.cursor.execute('''
                ALTER TABLE cloud115_library ADD COLUMN file_id TEXT
                ''')
                
            # 添加pickcode列（如果不存在）
            if 'pickcode' not in column_names:
                print("升级cloud115_library表：添加pickcode列")
                self.local.cursor.execute('''
                ALTER TABLE cloud115_library ADD COLUMN pickcode TEXT
                ''')
                
                # 如果添加了pickcode列，从URL提取已有记录的pickcode
                self.local.cursor.execute('''
                SELECT id, url FROM cloud115_library
                ''')
                
                records = self.local.cursor.fetchall()
                for record in records:
                    record_id = record['id']
                    url = record['url']
                    
                    # 从URL中提取pickcode
                    pick_code_match = re.search(r'pickcode=([^&]+)', url)
                    if pick_code_match:
                        pick_code = pick_code_match.group(1)
                        
                        # 更新记录
                        self.local.cursor.execute('''
                        UPDATE cloud115_library SET pickcode = ? WHERE id = ?
                        ''', (pick_code, record_id))
            
            self.local.conn.commit()
            print("数据库架构升级完成")
        except sqlite3.Error as e:
            print(f"升级数据库架构错误: {e}")
    
    def _get_order_clause(self, sort_by, sort_order):
        """根据排序字段和排序方向生成SQL ORDER BY子句
        
        Args:
            sort_by: 排序字段
            sort_order: 排序方向
            
        Returns:
            str: SQL ORDER BY子句
        """
        # 随机排序特殊处理
        if sort_order == 'random':
            return 'ORDER BY RANDOM()'
            
        # 验证排序字段
        valid_fields = {
            'added_time': 'date_added',
            'date_added': 'date_added',
            'video_id': 'video_id',
            'title': 'title',
            'date': 'date',
            'filepath': 'filepath',
            'last_played': 'last_played',
            'play_count': 'play_count',
            'category': 'category'
        }

        db_field = valid_fields.get(sort_by, 'date_added')
        
        # 对于可能为NULL的字段进行特殊处理
        if db_field in ['video_id', 'date', 'title']:
            # 将NULL值排在最后
            null_handling = f"CASE WHEN {db_field} IS NULL OR {db_field} = '' THEN 1 ELSE 0 END,"
            order_direction = 'ASC' if sort_order == 'asc' else 'DESC'
            return f"ORDER BY {null_handling} {db_field} COLLATE NOCASE {order_direction}"
        else:
            order_direction = 'ASC' if sort_order == 'asc' else 'DESC'
            return f"ORDER BY {db_field} {order_direction}"
        
    # 115云盘文件库方法
    def save_cloud115_file(self, file_data):
        """保存115云盘文件信息到数据库"""
        self.ensure_connection()
        try:
            now = int(time.time())
            
            # 检查文件是否已存在
            self.local.cursor.execute('''
            SELECT id FROM cloud115_library WHERE filepath = ?
            ''', (file_data.get('filepath'),))
            
            result = self.local.cursor.fetchone()
            
            # 从URL中提取pickcode（如果有）
            pickcode = None
            url = file_data.get('url', '')
            if url:
                pickcode_match = re.search(r'pickcode=([^&]+)', url)
                if pickcode_match:
                    pickcode = pickcode_match.group(1)
            
            # 如果没有从URL中提取到pickcode，使用file_id作为pickcode
            if not pickcode and 'file_id' in file_data:
                pickcode = file_data.get('file_id')
            
            if result:
                # 更新现有记录
                self.local.cursor.execute('''
                UPDATE cloud115_library SET 
                    title = ?,
                    url = ?,
                    thumbnail = ?,
                    description = ?,
                    category = ?,
                    file_id = ?,
                    pickcode = ?
                WHERE id = ?
                ''', (
                    file_data.get('title', ''),
                    file_data.get('url', ''),
                    file_data.get('thumbnail', ''),
                    file_data.get('description', ''),
                    file_data.get('category', 'other'),
                    file_data.get('file_id', ''),
                    pickcode,
                    result['id']
                ))
                file_id = result['id']
            else:
                # 插入新记录
                self.local.cursor.execute('''
                INSERT INTO cloud115_library 
                (title, filepath, url, thumbnail, description, category, date_added, file_id, pickcode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    file_data.get('title', ''),
                    file_data.get('filepath', ''),
                    file_data.get('url', ''),
                    file_data.get('thumbnail', ''),
                    file_data.get('description', ''),
                    file_data.get('category', 'other'),
                    now,
                    file_data.get('file_id', ''),
                    pickcode
                ))
                file_id = self.local.cursor.lastrowid
            
            self.local.conn.commit()
            return file_id
        except sqlite3.Error as e:
            print(f"保存115云盘文件信息错误: {e}")
            return None
            
    def get_cloud115_files(self, category=None, limit=None, offset=0, sort_by='date_added', sort_order='desc'):
        """获取115云盘文件列表"""
        self.ensure_connection()
        try:
            # 构建查询语句
            query = "SELECT * FROM cloud115_library"
            params = []
            
            # 添加分类过滤
            if category:
                query += " WHERE category = ?"
                params.append(category)
            
            # 添加排序
            query += " " + self._get_order_clause(sort_by, sort_order)
            
            # 添加分页
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            
            # 执行查询
            self.local.cursor.execute(query, params)
            
            return [dict(row) for row in self.local.cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"获取115云盘文件列表错误: {e}")
            return []
            
    def get_cloud115_file(self, file_id):
        """获取单个115云盘文件信息"""
        self.ensure_connection()
        try:
            self.local.cursor.execute('''
            SELECT * FROM cloud115_library WHERE id = ?
            ''', (file_id,))
            
            result = self.local.cursor.fetchone()
            if result:
                result_dict = dict(result)
                
                # 如果pickcode字段为None但file_id字段存在，则使用file_id作为pickcode
                if (not result_dict.get('pickcode')) and result_dict.get('file_id'):
                    result_dict['pickcode'] = result_dict['file_id']
                    
                # 如果仍未设置pickcode，尝试从URL中提取
                if not result_dict.get('pickcode') and result_dict.get('url'):
                    import re
                    url = result_dict.get('url', '')
                    pick_code_match = re.search(r'pickcode=([^&]+)', url)
                    if pick_code_match:
                        result_dict['pickcode'] = pick_code_match.group(1)
                        
                        # 更新数据库
                        self.local.cursor.execute('''
                        UPDATE cloud115_library SET pickcode = ? WHERE id = ?
                        ''', (result_dict['pickcode'], file_id))
                        self.local.conn.commit()
                        
                return result_dict
            return None
        except sqlite3.Error as e:
            print(f"获取115云盘文件信息错误: {e}")
            return None
            
    def delete_cloud115_file(self, file_id):
        """删除115云盘文件记录"""
        self.ensure_connection()
        try:
            self.local.cursor.execute('''
            DELETE FROM cloud115_library WHERE id = ?
            ''', (file_id,))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"删除115云盘文件错误: {e}")
            return False
            
    def update_cloud115_play_count(self, file_id):
        """更新115云盘文件播放次数"""
        self.ensure_connection()
        try:
            now = int(time.time())
            
            self.local.cursor.execute('''
            UPDATE cloud115_library SET 
                play_count = play_count + 1,
                last_played = ?
            WHERE id = ?
            ''', (now, file_id))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"更新115云盘文件播放次数错误: {e}")
            return False
            
    def get_cloud115_categories(self):
        """获取所有115云盘文件分类"""
        self.ensure_connection()
        try:
            self.local.cursor.execute('''
            SELECT DISTINCT category FROM cloud115_library ORDER BY category
            ''')
            
            categories = [row['category'] for row in self.local.cursor.fetchall() if row['category']]
            return categories
        except sqlite3.Error as e:
            print(f"获取115云盘文件分类错误: {e}")
            return []
            
    def update_cloud115_video_id(self, file_id, video_id, title=None):
        """更新115云盘文件的视频ID和标题
        
        Args:
            file_id: 文件ID
            video_id: 视频ID
            title: 可选的标题更新
            
        Returns:
            bool: 成功返回True，否则返回False
        """
        self.ensure_connection()
        try:
            if title is not None:
                # 更新视频ID和标题
                self.local.cursor.execute('''
                UPDATE cloud115_library SET video_id = ?, title = ? WHERE id = ?
                ''', (video_id, title, file_id))
            else:
                # 只更新视频ID
                self.local.cursor.execute('''
                UPDATE cloud115_library SET video_id = ? WHERE id = ?
                ''', (video_id, file_id))
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"更新115云盘文件视频ID错误: {e}")
            return False
            
    def update_cloud115_metadata(self, file_id, video_id=None, cover_image=None, actors=None):
        """更新115云盘文件元数据"""
        self.ensure_connection()
        try:
            # 构建更新语句和参数
            update_parts = []
            params = []
            
            if video_id is not None:
                update_parts.append("video_id = ?")
                params.append(video_id)
            
            if cover_image is not None:
                update_parts.append("cover_image = ?")
                params.append(cover_image)
            
            if actors is not None:
                update_parts.append("actors = ?")
                params.append(json.dumps(actors, ensure_ascii=False) if isinstance(actors, (list, dict)) else actors)
            
            if not update_parts:
                return False  # 没有要更新的内容
            
            # 添加文件ID
            params.append(file_id)
            
            # 执行更新
            query = f"UPDATE cloud115_library SET {', '.join(update_parts)} WHERE id = ?"
            self.local.cursor.execute(query, params)
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"更新115云盘文件元数据错误: {e}")
            return False
            
    def search_cloud115_files(self, query, category=None, limit=None, offset=0, sort_by='date_added', sort_order='desc'):
        """搜索115云盘文件"""
        self.ensure_connection()
        try:
            # 构建查询语句
            search_query = "SELECT * FROM cloud115_library WHERE title LIKE ? OR description LIKE ? OR video_id LIKE ?"
            search_params = [f"%{query}%", f"%{query}%", f"%{query}%"]
            
            # 添加分类过滤
            if category:
                search_query += " AND category = ?"
                search_params.append(category)
            
            # 添加排序
            search_query += " " + self._get_order_clause(sort_by, sort_order)
            
            # 添加分页
            if limit is not None:
                search_query += " LIMIT ? OFFSET ?"
                search_params.extend([limit, offset])
            
            # 执行查询
            self.local.cursor.execute(search_query, search_params)
            
            return [dict(row) for row in self.local.cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"搜索115云盘文件错误: {e}")
            return []
            
    def update_cloud115_movie_info(self, file_id, title=None, date=None):
        """更新115云盘文件的电影信息（从movies表同步）
        
        Args:
            file_id: 115云盘文件ID
            title: 电影标题
            date: 发布日期
            
        Returns:
            bool: 成功返回True，否则返回False
        """
        self.ensure_connection()
        try:
            # 构建更新SQL
            update_fields = []
            params = []
            
            if title is not None:
                update_fields.append("title = ?")
                params.append(title)
                
            if date is not None:
                update_fields.append("date = ?")
                params.append(date)
                
            if not update_fields:
                return True  # 没有需要更新的字段
                
            # 追加file_id参数
            params.append(file_id)
            
            # 执行更新
            self.local.cursor.execute(f'''
            UPDATE cloud115_library
            SET {", ".join(update_fields)}
            WHERE id = ?
            ''', params)
            
            self.local.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"更新115云盘电影信息错误: {e}")
            return False 