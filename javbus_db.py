import os
import json
import sqlite3
import time
import threading
from datetime import datetime, timedelta

class JavbusDatabase:
    """JavBus数据库类，用于存储和检索演员和影片信息"""
    
    def __init__(self, db_file="javbus_data.db"):
        """初始化数据库连接"""
        self.db_path = db_file
        self.local = threading.local()  # 使用线程本地存储
        self.connect()
        self.create_tables()
    
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
            # 创建演员表
            self.local.cursor.execute('''
            CREATE TABLE IF NOT EXISTS stars (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                avatar TEXT,
                birthday TEXT,
                age TEXT,
                height TEXT,
                bust TEXT,
                waistline TEXT,
                hipline TEXT,
                birthplace TEXT,
                hobby TEXT,
                last_updated INTEGER,
                data JSON
            )
            ''')
            
            # 创建影片表
            self.local.cursor.execute('''
            CREATE TABLE IF NOT EXISTS movies (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                cover TEXT,
                date TEXT,
                publisher TEXT,
                last_updated INTEGER,
                data JSON
            )
            ''')
            
            # 创建演员-影片关联表
            self.local.cursor.execute('''
            CREATE TABLE IF NOT EXISTS star_movie (
                star_id TEXT,
                movie_id TEXT,
                PRIMARY KEY (star_id, movie_id),
                FOREIGN KEY (star_id) REFERENCES stars (id),
                FOREIGN KEY (movie_id) REFERENCES movies (id)
            )
            ''')
            
            # 创建搜索历史表
            self.local.cursor.execute('''
            CREATE TABLE IF NOT EXISTS search_history (
                keyword TEXT,
                search_time INTEGER,
                PRIMARY KEY (keyword)
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