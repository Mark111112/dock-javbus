#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import argparse
import requests
from tqdm import tqdm
from javbus_db import JavbusDatabase

class JavbusDataGenerator:
    """JavBus数据生成器，用于从API获取数据并存储到数据库"""
    
    def __init__(self, api_base_url, db_path="javbus_data.db"):
        """初始化数据生成器"""
        self.api_base_url = api_base_url
        self.db = JavbusDatabase(db_path)
        
        # 设置请求头，模拟浏览器行为
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://www.javbus.com/",
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }
    
    def close(self):
        """关闭数据库连接"""
        self.db.close()
    
    def fetch_star(self, star_id):
        """获取演员信息并保存到数据库"""
        try:
            # 检查数据库中是否已有最新数据
            cached_star = self.db.get_star(star_id)
            if cached_star:
                print(f"使用缓存的演员数据: {star_id}")
                return cached_star
            
            # 从API获取演员信息
            response = requests.get(f"{self.api_base_url}/stars/{star_id}", headers=self.headers)
            
            if response.status_code != 200:
                print(f"获取演员信息失败: {response.status_code}")
                return None
            
            star_data = response.json()
            
            # 保存到数据库
            if self.db.save_star(star_data):
                print(f"保存演员信息成功: {star_data.get('name', '')} ({star_id})")
                return star_data
            else:
                print(f"保存演员信息失败: {star_id}")
                return None
        except Exception as e:
            print(f"获取演员信息异常: {str(e)}")
            return None
    
    def fetch_movie(self, movie_id):
        """获取影片信息并保存到数据库"""
        try:
            # 检查数据库中是否已有最新数据
            cached_movie = self.db.get_movie(movie_id)
            if cached_movie:
                print(f"使用缓存的影片数据: {movie_id}")
                return cached_movie
            
            # 从API获取影片信息
            response = requests.get(f"{self.api_base_url}/movies/{movie_id}", headers=self.headers)
            
            if response.status_code != 200:
                print(f"获取影片信息失败: {response.status_code}")
                return None
            
            movie_data = response.json()
            
            # 保存到数据库
            if self.db.save_movie(movie_data):
                print(f"保存影片信息成功: {movie_data.get('title', '')} ({movie_id})")
                
                # 同时保存演员信息
                stars = movie_data.get('stars', [])
                for star in stars:
                    star_id = star.get('id')
                    if star_id:
                        self.fetch_star(star_id)
                
                return movie_data
            else:
                print(f"保存影片信息失败: {movie_id}")
                return None
        except Exception as e:
            print(f"获取影片信息异常: {str(e)}")
            return None
    
    def search_and_save_stars(self, keyword, max_pages=1):
        """搜索演员并保存到数据库"""
        try:
            # 先检查数据库中是否有匹配的演员
            cached_stars = self.db.search_stars(keyword)
            if cached_stars:
                print(f"从数据库中找到 {len(cached_stars)} 个匹配的演员")
                return cached_stars
            
            # 从API搜索演员
            all_stars = []
            
            for page in range(1, max_pages + 1):
                # 搜索影片
                response = requests.get(f"{self.api_base_url}/movies/search", params={
                    "keyword": keyword,
                    "page": str(page),
                    "magnet": "all"
                }, headers=self.headers)
                
                if response.status_code != 200:
                    print(f"搜索影片失败: {response.status_code}")
                    continue
                
                data = response.json()
                movies = data.get("movies", [])
                
                if not movies:
                    break
                
                # 从影片中提取演员
                for movie in tqdm(movies, desc=f"处理第{page}页影片"):
                    movie_id = movie.get("id")
                    if movie_id:
                        movie_data = self.fetch_movie(movie_id)
                        if movie_data:
                            stars = movie_data.get("stars", [])
                            for star in stars:
                                star_id = star.get("id")
                                star_name = star.get("name", "")
                                if star_id and keyword.lower() in star_name.lower():
                                    # 获取完整的演员信息
                                    star_data = self.fetch_star(star_id)
                                    if star_data and star_data not in all_stars:
                                        all_stars.append(star_data)
            
            print(f"共找到 {len(all_stars)} 个匹配的演员")
            return all_stars
        except Exception as e:
            print(f"搜索演员异常: {str(e)}")
            return []
    
    def fetch_star_movies(self, star_id, max_pages=5):
        """获取演员的所有影片并保存到数据库"""
        try:
            # 先检查数据库中是否有该演员的影片
            cached_movies = self.db.get_star_movies(star_id)
            if cached_movies:
                print(f"从数据库中找到 {len(cached_movies)} 部演员影片")
                return cached_movies
            
            # 从API获取演员影片
            all_movies = []
            
            for page in range(1, max_pages + 1):
                # 搜索演员参演的影片
                response = requests.get(f"{self.api_base_url}/movies", params={
                    "filterType": "star",
                    "filterValue": star_id,
                    "page": str(page),
                    "magnet": "all"
                }, headers=self.headers)
                
                if response.status_code != 200:
                    print(f"获取演员影片失败: {response.status_code}")
                    continue
                
                data = response.json()
                movies = data.get("movies", [])
                pagination = data.get("pagination", {})
                
                if not movies:
                    break
                
                # 获取每部影片的详细信息
                for movie in tqdm(movies, desc=f"处理第{page}页影片"):
                    movie_id = movie.get("id")
                    if movie_id:
                        movie_data = self.fetch_movie(movie_id)
                        if movie_data:
                            all_movies.append(movie_data)
                
                # 检查是否有下一页
                has_next_page = pagination.get("hasNextPage", False)
                if not has_next_page:
                    break
            
            print(f"共找到 {len(all_movies)} 部演员影片")
            return all_movies
        except Exception as e:
            print(f"获取演员影片异常: {str(e)}")
            return []
    
    def clean_database(self):
        """清理过期数据"""
        print("开始清理过期数据...")
        if self.db.clear_expired_data():
            print("清理过期数据完成")
        else:
            print("清理过期数据失败")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="JavBus数据生成器")
    parser.add_argument("--api", type=str, default="http://192.168.1.246:8922/api", help="JavBus API地址")
    parser.add_argument("--db", type=str, default="javbus_data.db", help="数据库文件路径")
    parser.add_argument("--clean", action="store_true", help="清理过期数据")
    parser.add_argument("--star", type=str, help="获取指定演员信息")
    parser.add_argument("--movie", type=str, help="获取指定影片信息")
    parser.add_argument("--search", type=str, help="搜索演员")
    parser.add_argument("--star-movies", type=str, help="获取演员的所有影片")
    parser.add_argument("--max-pages", type=int, default=5, help="最大页数")
    
    args = parser.parse_args()
    
    generator = JavbusDataGenerator(args.api, args.db)
    
    try:
        if args.clean:
            generator.clean_database()
        
        if args.star:
            generator.fetch_star(args.star)
        
        if args.movie:
            generator.fetch_movie(args.movie)
        
        if args.search:
            generator.search_and_save_stars(args.search, args.max_pages)
        
        if args.star_movies:
            generator.fetch_star_movies(args.star_movies, args.max_pages)
        
        # 如果没有指定任何操作，显示帮助信息
        if not (args.clean or args.star or args.movie or args.search or args.star_movies):
            parser.print_help()
    finally:
        generator.close()


if __name__ == "__main__":
    main() 