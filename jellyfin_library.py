import os
import time
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional
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
        self.user_id = None

        # 设置日志
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)
        
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

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS jelibrary_sync (
                library_id TEXT PRIMARY KEY,
                last_sync_date_created TEXT,
                last_sync_date_last_saved TEXT,
                last_sync_ts INTEGER
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
            self.client.config.data["auth.ssl"] = str(server_url).startswith('https')

            # 使用API密钥认证
            if api_key:
                self.client.config.data["app.name"] = 'BusPre'
                self.client.config.data["app.version"] = '1.0.0'
                self.client.authenticate({
                    "Servers": [{
                        "AccessToken": api_key,
                        "address": (server_url or "").rstrip("/")
                    }]
                }, discover=False)
                self.logger.info(f"使用API密钥连接到服务器 {server_url} 成功")
                try:
                    user_info = self.client.jellyfin.get_user()
                    self.user_id = user_info.get("Id") if isinstance(user_info, dict) else None
                except Exception:
                    self.user_id = None
                return True

            # 使用用户名和密码认证
            elif username and password:
                self.client.auth.connect_to_address((server_url or "").rstrip("/"))
                result = self.client.auth.login((server_url or "").rstrip("/"), username, password)
                if result:
                    self.logger.info(f"使用用户名/密码连接到服务器 {server_url} 成功")
                    try:
                        user_info = self.client.jellyfin.get_user()
                        self.user_id = user_info.get("Id") if isinstance(user_info, dict) else None
                    except Exception:
                        self.user_id = None
                return bool(result)

            return False
        except Exception as e:
            self.logger.error(f"连接Jellyfin服务器错误: {e}")
            return False

    @staticmethod
    def _parse_iso8601(value: str) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        try:
            # Jellyfin 常见格式：2023-04-12T12:52:30.0000000Z（7位小数 + Z）
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"

            tz_suffix = ""
            if re.search(r"[+-]\d\d:\d\d$", text):
                tz_suffix = text[-6:]
                base = text[:-6]
            else:
                base = text

            if "." in base:
                prefix, frac = base.split(".", 1)
                frac_digits = "".join(ch for ch in frac if ch.isdigit())
                frac_digits = (frac_digits + "000000")[:6]
                base = f"{prefix}.{frac_digits}"

            parsed = datetime.fromisoformat(base + tz_suffix)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    def _probe_library_latest_date_created(self, library_id: str) -> Optional[str]:
        try:
            result = self.get_library_items(
                library_id,
                start_index=0,
                limit=1,
                sort_by="DateCreated",
                sort_order="Descending",
            )
            items = result.get("items", []) or []
            if items:
                return items[0].get("DateCreated")
        except Exception as e:
            self.logger.warning(f"探测DateCreated失败 library_id={library_id}: {e}")
        return None

    def _probe_library_latest_date_last_saved(self, library_id: str) -> Optional[str]:
        try:
            result = self.get_library_items(
                library_id,
                start_index=0,
                limit=1,
                sort_by="DateLastSaved",
                sort_order="Descending",
            )
            items = result.get("items", []) or []
            if items:
                return items[0].get("DateLastSaved")
        except Exception as e:
            self.logger.warning(f"探测DateLastSaved失败 library_id={library_id}: {e}")
        return None

    @classmethod
    def _max_iso8601(cls, a: Optional[str], b: Optional[str]) -> Optional[str]:
        if not a:
            return b
        if not b:
            return a
        da = cls._parse_iso8601(a)
        db = cls._parse_iso8601(b)
        if not da and not db:
            return a
        if not da:
            return b
        if not db:
            return a
        return a if da >= db else b

    def get_library_sync_state(self, library_id: str) -> dict:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT library_id, last_sync_date_created, last_sync_date_last_saved, last_sync_ts FROM jelibrary_sync WHERE library_id = ?",
                (library_id,),
            )
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else {}
        except Exception as e:
            if "no such table" in str(e).lower():
                try:
                    self.ensure_database()
                except Exception:
                    pass
            self.logger.warning(f"读取jelibrary_sync失败 library_id={library_id}: {e}")
            return {}

    def upsert_library_sync_state(
        self,
        library_id: str,
        last_sync_date_created: Optional[str] = None,
        last_sync_date_last_saved: Optional[str] = None,
    ) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            now = int(time.time())
            cursor.execute(
                '''
                INSERT INTO jelibrary_sync (library_id, last_sync_date_created, last_sync_date_last_saved, last_sync_ts)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(library_id) DO UPDATE SET
                    last_sync_date_created = COALESCE(excluded.last_sync_date_created, jelibrary_sync.last_sync_date_created),
                    last_sync_date_last_saved = COALESCE(excluded.last_sync_date_last_saved, jelibrary_sync.last_sync_date_last_saved),
                    last_sync_ts = excluded.last_sync_ts
                ''',
                (library_id, last_sync_date_created, last_sync_date_last_saved, now),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            if "no such table" in str(e).lower():
                try:
                    self.ensure_database()
                    return self.upsert_library_sync_state(
                        library_id=library_id,
                        last_sync_date_created=last_sync_date_created,
                        last_sync_date_last_saved=last_sync_date_last_saved,
                    )
                except Exception:
                    pass
            self.logger.warning(f"写入jelibrary_sync失败 library_id={library_id}: {e}")

    @staticmethod
    def _format_duration(seconds: int) -> str:
        try:
            seconds = int(seconds)
        except Exception:
            return ""
        if seconds <= 0:
            return ""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    @staticmethod
    def _format_size(num_bytes: int) -> str:
        try:
            size = float(num_bytes)
        except Exception:
            return ""
        if size <= 0:
            return ""
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        unit_index = 0
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1
        if unit_index == 0:
            return f"{int(size)} {units[unit_index]}"
        return f"{size:.2f} {units[unit_index]}"

    def get_item_metadata(self, item_id: str) -> dict:
        """从 Jellyfin 读取单个项目的元数据（类别、演员、日期、简介、时长、分辨率、文件大小等）"""
        if not self.client or not item_id:
            return {}

        try:
            params = {
                "Ids": item_id,
                "Fields": "Overview,Genres,People,PremiereDate,RunTimeTicks,MediaSources,MediaStreams,Path",
            }
            raw = self.client.jellyfin.items(handler="", action="GET", params=params)
            if isinstance(raw, dict) and raw.get("Items"):
                item = raw.get("Items", [])[0] or {}
            elif isinstance(raw, list) and raw:
                item = raw[0] or {}
            else:
                item = raw or {}
            if not isinstance(item, dict):
                return {}

            premiere_date = item.get("PremiereDate") or ""
            if isinstance(premiere_date, str) and "T" in premiere_date:
                premiere_date = premiere_date.split("T")[0]

            runtime_ticks = item.get("RunTimeTicks") or 0
            runtime_seconds = 0
            try:
                runtime_seconds = int(int(runtime_ticks) / 10_000_000)
            except Exception:
                runtime_seconds = 0

            media_sources = item.get("MediaSources") or []
            primary_source = media_sources[0] if media_sources else {}
            file_size_bytes = primary_source.get("Size") or 0

            media_streams = primary_source.get("MediaStreams") or item.get("MediaStreams") or []
            video_stream = None
            for stream in media_streams:
                if isinstance(stream, dict) and stream.get("Type") == "Video":
                    video_stream = stream
                    break

            width = (video_stream or {}).get("Width") if isinstance(video_stream, dict) else None
            height = (video_stream or {}).get("Height") if isinstance(video_stream, dict) else None

            actors = []
            for person in item.get("People") or []:
                if isinstance(person, dict) and person.get("Type") == "Actor" and person.get("Name"):
                    actors.append(person["Name"])

            genres = [g for g in (item.get("Genres") or []) if isinstance(g, str) and g.strip()]

            resolution_text = ""
            if width and height:
                resolution_text = f"{width} x {height}"

            return {
                "genres": genres,
                "actors": actors,
                "premiere_date": premiere_date,
                "overview": item.get("Overview") or "",
                "runtime_seconds": runtime_seconds,
                "runtime_text": self._format_duration(runtime_seconds),
                "width": width,
                "height": height,
                "resolution_text": resolution_text,
                "file_size_bytes": file_size_bytes,
                "file_size_text": self._format_size(file_size_bytes),
            }
        except Exception as e:
            self.logger.warning(f"读取Jellyfin项目元数据失败 item_id={item_id}: {e}")
            return {}
    
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
    
    def get_library_items(
        self,
        library_id,
        start_index=0,
        limit=100,
        sort_by=None,
        sort_order=None,
        min_date_created=None,
        min_date_last_saved=None,
    ):
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
                "Fields": "Path,Overview,PremiereDate,MediaSources,ProviderIds,MediaStreams,ImageTags,BackdropImageTags,DateCreated,DateLastSaved",
                "IncludeItemTypes": "Movie,Episode,Video",  # 明确指定需要的项目类型
            }
            if self.user_id:
                params["UserId"] = self.user_id
            if sort_by:
                params["SortBy"] = sort_by
            if sort_order:
                params["SortOrder"] = sort_order
            if min_date_created:
                params["MinDateCreated"] = min_date_created
            if min_date_last_saved:
                params["MinDateLastSaved"] = min_date_last_saved

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

    def _save_jellyfin_item(self, cursor, item, library_id: str, library_name: str) -> dict:
        item_id = item.get("Id")
        title = item.get("Name", "")

        if item.get("IsFolder", False):
            return {"ok": False, "skipped": True, "item_id": item_id, "title": title}

        # 从标题中提取video_id
        video_id = self.extract_video_id(title)

        # 如果从标题中提取失败，尝试从提供者ID中提取
        if not video_id and item.get("ProviderIds"):
            provider_ids = item.get("ProviderIds", {})
            if provider_ids.get("Tmdb"):
                video_id = f"TMDB-{provider_ids['Tmdb']}"
            elif provider_ids.get("Imdb"):
                video_id = f"IMDB-{provider_ids['Imdb']}"

        # 获取播放URL
        play_url = self.get_play_url(item_id)

        # 获取路径
        path = ""
        if item.get("MediaSources") and len(item["MediaSources"]) > 0:
            path = item["MediaSources"][0].get("Path", "")

        # 获取封面图：优先 Backdrop，其次 Primary
        cover_image = ""
        if item_id:
            server_url = (self.client.config.data.get('auth.server') or "").rstrip("/")
            if item.get("BackdropImageTags"):
                cover_image = f"{server_url}/Items/{item_id}/Images/Backdrop/0"
            else:
                cover_image = f"{server_url}/Items/{item_id}/Images/Primary"

        # 获取演员
        actors = []
        if item.get("People"):
            actors = [person.get("Name") for person in item["People"] if person.get("Type") == "Actor"]

        # 获取日期
        date = item.get("PremiereDate", "")
        if date:
            date = date.split("T")[0]

        now = int(time.time())

        cursor.execute(
            '''
            INSERT OR REPLACE INTO jelmovie
            (title, jellyfin_id, item_id, video_id, library_name, library_id,
            play_url, path, cover_image, actors, date, date_added)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                title,
                self.client.config.data.get('auth.server'),
                item_id,
                video_id,
                library_name,
                library_id,
                play_url,
                path,
                cover_image,
                json.dumps(actors) if actors else "",
                date,
                now,
            ),
        )

        return {"ok": True, "item_id": item_id, "title": title, "video_id": video_id}
    
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
            max_date_created = None
            max_date_last_saved = None

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
                    try:
                        saved = self._save_jellyfin_item(cursor, item, library_id=library_id, library_name=library_name)
                        if saved.get("skipped"):
                            continue

                        if saved.get("ok"):
                            imported_count += 1
                            item_id = saved.get("item_id")
                            success_dict[item_id] = {"title": saved.get("title"), "video_id": saved.get("video_id")}

                            max_date_created = self._max_iso8601(max_date_created, item.get("DateCreated"))
                            max_date_last_saved = self._max_iso8601(max_date_last_saved, item.get("DateLastSaved"))
                        else:
                            failed_count += 1
                            failed_list.append(f"{saved.get('title')} (ID: {saved.get('item_id')})")

                    except Exception as e:
                        title = item.get("Name", "")
                        item_id = item.get("Id")
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

            # Ensure we always set a sync point after full import (required for incremental sync).
            # Prefer server-side probe (stable even if item list order / parsing differs).
            probed_created = self._probe_library_latest_date_created(library_id)
            probed_saved = self._probe_library_latest_date_last_saved(library_id)
            if probed_created:
                max_date_created = probed_created
            if probed_saved:
                max_date_last_saved = probed_saved
            if not max_date_created:
                max_date_created = datetime.now(timezone.utc).isoformat()

            self.upsert_library_sync_state(
                library_id=library_id,
                last_sync_date_created=max_date_created,
                last_sync_date_last_saved=max_date_last_saved,
            )
            self.logger.info(
                f"库同步点已更新 library_id={library_id}, last_sync_date_created={max_date_created}, last_sync_date_last_saved={max_date_last_saved or ''}"
            )

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

    def sync_library_incremental_by_date_created(self, library_id: str, library_name: str) -> dict:
        """按 DateCreated 增量导入 Jellyfin 库（要求已做过至少一次全量导入以建立同步点）。"""
        if not self.client:
            self.logger.error("未连接到Jellyfin服务器")
            return {"imported": 0, "failed": 0, "needs_full_import": True, "message": "未连接到Jellyfin服务器"}

        sync_state = self.get_library_sync_state(library_id) or {}
        last_created = (sync_state.get("last_sync_date_created") or "").strip()
        if not last_created:
            return {
                "imported": 0,
                "failed": 0,
                "needs_full_import": True,
                "message": "未找到该库的同步点，请先对该库执行一次“导入此库”（全量）以初始化增量同步。",
            }

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            imported_count = 0
            failed_count = 0
            success_dict = {}
            failed_list = []
            max_date_created = last_created
            max_date_last_saved = sync_state.get("last_sync_date_last_saved")

            start_index = 0
            limit = 200

            self.logger.info(f"开始增量导入Jellyfin库(按DateCreated) {library_name} (ID: {library_id}), since={last_created}")

            while True:
                result = self.get_library_items(
                    library_id,
                    start_index=start_index,
                    limit=limit,
                    sort_by="DateCreated",
                    sort_order="Ascending",
                    min_date_created=last_created,
                )
                items = result.get("items", [])
                total_count = result.get("total_count", 0)

                if not items:
                    break

                self.logger.info(
                    f"增量批次: {start_index}-{start_index+len(items)}/{total_count}, 本批次项目数: {len(items)}"
                )

                for item in items:
                    try:
                        saved = self._save_jellyfin_item(cursor, item, library_id=library_id, library_name=library_name)
                        if saved.get("skipped"):
                            continue
                        if saved.get("ok"):
                            imported_count += 1
                            item_id = saved.get("item_id")
                            success_dict[item_id] = {"title": saved.get("title"), "video_id": saved.get("video_id")}
                            max_date_created = self._max_iso8601(max_date_created, item.get("DateCreated"))
                            max_date_last_saved = self._max_iso8601(max_date_last_saved, item.get("DateLastSaved"))
                        else:
                            failed_count += 1
                            failed_list.append(f"{saved.get('title')} (ID: {saved.get('item_id')})")
                    except Exception as e:
                        title = item.get("Name", "")
                        item_id = item.get("Id")
                        self.logger.error(f"增量导入项目错误: {e}, 项目: {title}")
                        failed_count += 1
                        failed_list.append(f"{title} (ID: {item_id})")

                start_index += len(items)
                conn.commit()

                if start_index >= total_count:
                    break

            conn.commit()
            conn.close()

            # Always refresh sync point via probe (handles empty incremental batches and avoids timestamp parsing issues).
            probed_created = self._probe_library_latest_date_created(library_id) or max_date_created
            probed_saved = self._probe_library_latest_date_last_saved(library_id) or max_date_last_saved
            if not probed_created:
                probed_created = max_date_created or datetime.now(timezone.utc).isoformat()

            self.upsert_library_sync_state(
                library_id=library_id,
                last_sync_date_created=probed_created,
                last_sync_date_last_saved=probed_saved,
            )

            return {
                "imported": imported_count,
                "failed": failed_count,
                "details": {"success": success_dict, "failed": failed_list},
                "since_date_created": last_created,
                "new_last_sync_date_created": probed_created,
            }
        except Exception as e:
            self.logger.error(f"增量导入Jellyfin库错误: {e}")
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
