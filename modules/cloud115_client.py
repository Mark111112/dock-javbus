"""115 云盘客户端模块 - 集成 OpenAPI 和 driver 两种认证方式。

该模块提供统一的 115 云盘访问接口，支持：
1. 官方 OpenAPI（基于 OAuth Token）
2. 115driver 内部 API（基于 Cookie）

使用方式会根据配置自动选择或回退。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from requests.cookies import create_cookie

from modules import m115_crypto


__all__ = [
    "Cloud115Client",
    "Cloud115AuthError",
    "Cloud115RateLimitError",
]


# ============================================================================
# 异常定义
# ============================================================================


class Cloud115AuthError(RuntimeError):
    """115 认证失败（Token 或 Cookie 失效）。"""


class Cloud115RateLimitError(RuntimeError):
    """115 API 触发限流。"""


class Cloud115ConfigError(RuntimeError):
    """115 配置错误。"""


# ============================================================================
# 常量
# ============================================================================

DEFAULT_USER_AGENT = "Mozilla/5.0 115Browser/27.0.5.7"
DEFAULT_LOGIN_CHECK_INTERVAL = 300
MAX_LIST_LIMIT = 1150

VIDEO_EXTENSIONS = {
    "mp4", "mkv", "avi", "wmv", "mov", "flv", "m4v", "rmvb", "rm", "ts", "webm",
}


# ============================================================================
# OpenAPI 客户端
# ============================================================================


class OpenAPIClient:
    """115 官方 OpenAPI 客户端（基于 OAuth Token）。"""

    BASE_URL = "https://proapi.115.com/open"

    def __init__(self, token_file: str, timeout: int = 15):
        self.token_file = token_file
        self.timeout = timeout
        self._lock = threading.Lock()

    def _load_token(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.token_file):
            return None
        try:
            with open(self.token_file, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _save_token(self, token_data: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        with open(self.token_file, "w", encoding="utf-8") as fh:
            json.dump(token_data, fh, ensure_ascii=False, indent=2)

    def _ensure_token(self) -> str:
        token = self._load_token()
        if not token or "access_token" not in token:
            raise Cloud115AuthError("115 OpenAPI 未登录，请先获取 Token")

        expires_at = token.get("expires_at")
        if expires_at and expires_at < time.time():
            if not self._refresh_token():
                raise Cloud115AuthError("115 OpenAPI Token 已过期且刷新失败")
            token = self._load_token()

        return token.get("access_token", "")

    def _refresh_token(self) -> bool:
        token = self._load_token()
        if not token or "refresh_token" not in token:
            return False

        try:
            response = requests.post(
                "https://passportapi.115.com/open/refreshToken",
                data={"refresh_token": token["refresh_token"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return False

        if payload.get("state") != 1 or "data" not in payload:
            return False

        new_token = payload["data"]
        if "expires_in" in new_token:
            new_token["expires_at"] = time.time() + new_token["expires_in"]

        self._save_token(new_token)
        return True

    def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        access_token = self._ensure_token()
        url = f"{self.BASE_URL}/{endpoint}"
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            if response.status_code in (401, 403):
                if self._refresh_token():
                    access_token = self._ensure_token()
                    headers = {"Authorization": f"Bearer {access_token}"}
                    response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
                    response.raise_for_status()
                    return response.json()
            raise

    def list_files(self, cid: str = "0", limit: int = 1150, offset: int = 0, **kwargs) -> Dict[str, Any]:
        params = {
            "cid": cid,
            "limit": limit,
            "offset": offset,
            "show_dir": kwargs.get("show_dir", 1),
            "aid": kwargs.get("aid", 1),
            "o": kwargs.get("order", "user_utime"),
            "asc": kwargs.get("asc", 0),
        }
        return self._request("ufile/files", params)

    def get_folder_info(self, file_id: str) -> Dict[str, Any]:
        return self._request("folder/get_info", {"file_id": file_id})

    def get_file_info(self, file_id: str) -> Dict[str, Any]:
        return self._request("folder/get_info", {"file_id": file_id})

    def get_file_info_by_pickcode(self, pickcode: str) -> Dict[str, Any]:
        return self._request("file/info", {"pick_code": pickcode})

    def get_video_play(self, pickcode: str) -> Dict[str, Any]:
        return self._request("video/play", {"pick_code": pickcode})


# ============================================================================
# Driver 客户端（基于 Cookie）
# ============================================================================


@dataclass
class DriverCredential:
    """115driver Cookie 凭据。"""

    uid: str
    cid: str
    seid: str
    kid: str = ""

    KEYS: Sequence[str] = ("UID", "CID", "SEID", "KID")

    @classmethod
    def from_cookie(cls, cookie: str) -> "DriverCredential":
        pairs = {}
        for item in cookie.split(";"):
            if not item.strip() or "=" not in item:
                continue
            key, value = item.split("=", 1)
            key = key.strip().upper()
            value = value.strip()
            if key in cls.KEYS and value:
                pairs[key] = value

        missing = [key for key in cls.KEYS[:3] if not pairs.get(key)]
        if missing:
            raise Cloud115ConfigError(f"115driver Cookie 缺少字段: {', '.join(missing)}")

        return cls(
            uid=pairs.get("UID", ""),
            cid=pairs.get("CID", ""),
            seid=pairs.get("SEID", ""),
            kid=pairs.get("KID", ""),
        )

    def as_dict(self) -> Dict[str, str]:
        data = {"UID": self.uid, "CID": self.cid, "SEID": self.seid}
        if self.kid:
            data["KID"] = self.kid
        return data


class DriverClient:
    """115driver 内部 API 客户端（基于 Cookie）。"""

    STATUS_CHECK_URL = "https://my.115.com/?ct=guide&ac=status"
    FILE_LIST_URLS = (
        "https://webapi.115.com/files",
        "http://web.api.115.com/files",
    )
    FILE_INFO_URL = "https://webapi.115.com/files/get_info"
    FOLDER_INFO_URL = "https://webapi.115.com/category/get"
    DOWNLOAD_API_URL = "https://proapi.115.com/app/chrome/downurl"
    DOWNLOAD_ANDROID_API_URL = "https://proapi.115.com/android/2.0/ufile/download"

    def __init__(
        self,
        credential: DriverCredential,
        timeout: int = 15,
        user_agent: str = DEFAULT_USER_AGENT,
        api_urls: Optional[Sequence[str]] = None,
        login_check_interval: int = DEFAULT_LOGIN_CHECK_INTERVAL,
    ):
        self.credential = credential
        self.timeout = timeout
        self.login_check_interval = max(30, login_check_interval)
        self.file_api_urls = list(api_urls or self.FILE_LIST_URLS)

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Referer": "https://115.com/",
            "Accept": "application/json, text/plain, */*",
        })

        for domain in (".115.com", "115.com"):
            for name, value in credential.as_dict().items():
                cookie = create_cookie(name=name, value=value, domain=domain, path="/")
                self.session.cookies.set_cookie(cookie)

        self._lock = threading.Lock()
        self._last_login_check = 0.0

    def ensure_login(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_login_check) < self.login_check_interval:
            return

        params = {"_": str(int(now * 1000))}
        with self._lock:
            response = self.session.get(self.STATUS_CHECK_URL, params=params, timeout=self.timeout)

        if response.status_code in (401, 511):
            raise Cloud115AuthError("115driver Cookie 已失效")

        try:
            payload = response.json()
            if payload.get("state") in (False, 0):
                raise Cloud115AuthError("115driver Cookie 已失效")
        except Exception:
            pass

        self._last_login_check = now

    def list_files(self, cid: str = "0", limit: int = MAX_LIST_LIMIT, offset: int = 0, **kwargs) -> Dict[str, Any]:
        self.ensure_login()
        params = {
            "aid": "1",
            "cid": str(cid or "0"),
            "o": kwargs.get("order", "user_utime"),
            "asc": str(int(kwargs.get("asc", 0))),
            "offset": str(max(0, offset)),
            "show_dir": str(int(kwargs.get("show_dir", 1))),
            "limit": str(min(max(1, limit), MAX_LIST_LIMIT)),
            "fc_mix": "0",
            "format": "json",
            "record_open_time": "1",
            "snap": "0",
        }

        last_error = None
        for api_url in self.file_api_urls:
            try:
                with self._lock:
                    response = self.session.get(api_url, params=params, timeout=self.timeout)
                if response.status_code == 403:
                    last_error = Cloud115RateLimitError("115driver 限流 (HTTP 403)")
                    continue
                response.raise_for_status()
                payload = response.json()
                return self._normalize_list_response(payload)
            except Cloud115RateLimitError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                continue

        if last_error:
            raise last_error
        raise RuntimeError("115driver 所有端点均失败")

    def get_folder_info(self, file_id: str) -> Dict[str, Any]:
        self.ensure_login()
        params = {"cid": str(file_id)}
        with self._lock:
            response = self.session.get(self.FOLDER_INFO_URL, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        return self._normalize_folder_info(payload, file_id)

    def get_file_info(self, file_id: str) -> Dict[str, Any]:
        self.ensure_login()
        params = {"file_id": str(file_id)}
        with self._lock:
            response = self.session.get(self.FILE_INFO_URL, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        return self._normalize_file_info(payload)

    def get_download_info(
        self,
        pickcode: str,
        *,
        user_agent: Optional[str] = None,
        use_android_api: bool = False,
    ) -> Dict[str, Any]:
        """通过 m115 加密协议获取文件下载信息。"""

        if not pickcode:
            raise ValueError("pickcode 不能为空")

        self.ensure_login()

        request_payload = {"pickcode": pickcode}
        endpoint = self.DOWNLOAD_API_URL
        if use_android_api:
            request_payload = {"pick_code": pickcode}
            endpoint = self.DOWNLOAD_ANDROID_API_URL

        key = m115_crypto.generate_key()
        encoded_data = m115_crypto.encode(json.dumps(request_payload, ensure_ascii=False).encode("utf-8"), key)

        params = {"t": str(int(time.time()))}
        form_data = {"data": encoded_data}

        headers = dict(self.session.headers)
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        if user_agent:
            headers["User-Agent"] = user_agent

        logging.getLogger("Cloud115Client").debug(
            "115 download request: endpoint=%s params=%s data_length=%s",
            endpoint,
            params,
            len(encoded_data),
        )

        with self._lock:
            response = self.session.post(
                endpoint,
                params=params,
                data=form_data,
                headers=headers,
                timeout=self.timeout,
            )

        response.raise_for_status()
        payload = response.json()

        if not payload.get("state"):
            message = (
                payload.get("msg")
                or payload.get("message")
                or payload.get("error")
                or payload.get("errtype")
                or "115 driver 获取下载信息失败"
            )
            code = payload.get("msg_code") or payload.get("errNo") or payload.get("errno") or ""
            raise RuntimeError(f"115 driver 获取下载信息失败: {message} (code={code})")

        encoded_response = payload.get("data")
        if not encoded_response:
            raise RuntimeError("115 driver 返回的数据为空，无法解析下载信息")

        try:
            decrypted = m115_crypto.decode(str(encoded_response), key)
        except Exception as exc:  # pragma: no cover - defensive
            raise RuntimeError(f"115 driver 下载信息解密失败: {exc}") from exc

        try:
            download_data = json.loads(decrypted.decode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            raise RuntimeError(f"115 driver 下载信息解析JSON失败: {exc}") from exc

        if not isinstance(download_data, dict):
            raise RuntimeError("115 driver 下载信息格式异常")

        for info in download_data.values():
            if not isinstance(info, dict):
                continue
            file_size = info.get("file_size")
            try:
                file_size_int = int(file_size)
            except (TypeError, ValueError):
                file_size_int = -1

            if file_size_int < 0:
                raise RuntimeError("115 driver 返回的文件大小无效，可能下载任务失败")

            result = {
                "file_name": info.get("file_name"),
                "file_size": file_size_int,
                "pick_code": info.get("pick_code"),
                "url": info.get("url", {}).get("url") if isinstance(info.get("url"), dict) else info.get("url"),
                "client": info.get("url", {}).get("client") if isinstance(info.get("url"), dict) else None,
                "oss_id": info.get("url", {}).get("oss_id") if isinstance(info.get("url"), dict) else None,
                "raw": info,
                "decoded_data": download_data,
                "encoded_data": encoded_response,
                "response_payload": payload,
                "request_headers": dict(response.request.headers),
            }

            if not result["url"]:
                raise RuntimeError("115 driver 返回的数据中没有下载链接")

            return result

        raise RuntimeError("115 driver 下载信息中未找到文件记录")

    def _normalize_list_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        files = payload.get("data") or []
        normalized = []
        if isinstance(files, Iterable):
            for item in files:
                if isinstance(item, dict):
                    normalized.append(self._normalize_file_entry(item))

        return {
            "state": 1,
            "code": 0,
            "message": "",
            "data": normalized,
            "count": payload.get("count", len(normalized)),
            "offset": payload.get("offset", 0),
        }

    def _normalize_file_entry(self, item: Dict[str, Any]) -> Dict[str, Any]:
        cid = str(item.get("cid") or "").strip()
        fid = str(item.get("fid") or "").strip()
        is_file = bool(fid)
        if not fid:
            fid = cid

        name = str(item.get("n") or item.get("name") or "").strip()
        ico = str(item.get("ico") or "").strip()
        size_raw = item.get("s") or item.get("size") or 0
        try:
            size_int = int(size_raw)
        except (TypeError, ValueError):
            size_int = 0

        # 图片URL字段处理：
        # u = 缩略图URL (小图，_100)
        # uo = 原图URL (大图，_0)
        # 注意：Driver API 返回的 uo 可能是错误的（也是 _100），需要修正
        image_url_original = str(item.get("uo") or "").strip()
        image_url_thumb = str(item.get("u") or "").strip()
        
        # 修正图片URL：将 _100 替换为 _0 以获取原图
        # 115的图片URL格式：http://thumb.115.com/thumb/.../xxx_100?... (缩略图)
        #                   http://thumb.115.com/thumb/.../xxx_0?...   (原图)
        if image_url_original and '_100?' in image_url_original:
            # 将 _100? 替换为 _0? 以获取原图
            image_url_original = image_url_original.replace('_100?', '_0?')
        elif not image_url_original and image_url_thumb:
            # 如果 uo 为空，从 u 转换
            if '_100?' in image_url_thumb:
                image_url_original = image_url_thumb.replace('_100?', '_0?')
            else:
                image_url_original = image_url_thumb
        
        return {
            "aid": str(item.get("aid") or "").strip(),
            "cid": cid,
            "fid": fid,
            "pid": str(item.get("pid") or "").strip(),
            "n": name,
            "fn": name,
            "ico": ico,
            "pc": str(item.get("pc") or item.get("pick_code") or "").strip(),
            "pick_code": str(item.get("pc") or item.get("pick_code") or "").strip(),
            "sha": str(item.get("sha") or "").strip(),
            "tp": item.get("tp") or item.get("create_time"),
            "t": item.get("t") or item.get("update_time"),
            "ut": item.get("t") or item.get("update_time"),
            "upt": item.get("t") or item.get("update_time"),
            "te": item.get("te") or item.get("t") or item.get("update_time"),
            "fs": size_int,
            "s": size_int,
            "size": size_int,
            "m": item.get("m"),
            "fl": item.get("fl") or item.get("labels") or [],
            "thumb": item.get("thumb") or "",
            "uo": image_url_original,  # 原图URL (大图) - 优先使用
            "u": image_url_thumb,      # 缩略图URL (小图)
            "fc": "1" if is_file else "0",
            "isv": 1 if ico.lower() in VIDEO_EXTENSIONS else 0,
        }

    def _normalize_folder_info(self, payload: Dict[str, Any], file_id: str) -> Dict[str, Any]:
        data = payload or {}
        normalized = {
            "file_id": str(file_id),
            "file_name": data.get("file_name") or data.get("name") or "",
            "pick_code": data.get("pick_code") or data.get("pickcode") or "",
            "size": data.get("size") or data.get("file_size") or "",
        }

        paths = []
        raw_paths = data.get("paths") or data.get("path") or []
        if isinstance(raw_paths, dict):
            raw_paths = list(raw_paths.values())
        if isinstance(raw_paths, list):
            for entry in raw_paths:
                if isinstance(entry, dict):
                    paths.append({
                        "file_id": entry.get("file_id") or entry.get("id"),
                        "file_name": entry.get("file_name") or entry.get("name"),
                    })
        normalized["paths"] = paths
        return {"state": 1, "code": 0, "message": "", "data": normalized}

    def _normalize_file_info(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        files = payload.get("data") or []
        if isinstance(files, list) and files:
            entry = files[0]
        elif isinstance(files, dict):
            entry = next(iter(files.values())) if files else {}
        else:
            entry = {}

        normalized_entry = self._normalize_file_entry(entry) if isinstance(entry, dict) else {}
        file_size = normalized_entry.get("s") or normalized_entry.get("size") or 0
        try:
            file_size = int(file_size)
        except (TypeError, ValueError):
            file_size = 0

        data = {
            "file_id": normalized_entry.get("fid"),
            "file_name": normalized_entry.get("fn"),
            "pick_code": normalized_entry.get("pick_code"),
            "file_size": file_size,
            "cid": normalized_entry.get("cid"),
        }
        return {"state": 1, "code": 0, "message": "", "data": data}

    def get_video_play(self, pickcode: str) -> Dict[str, Any]:
        """通过 driver 获取视频下载地址（使用内部 API）。
        
        注意：driver 无法获取多清晰度，只能获取原始文件的下载地址。
        
        Args:
            pickcode: 文件的 pickcode
            
        Returns:
            包含视频播放信息的字典，格式与 OpenAPI 兼容
        """
        self.ensure_login()
        
        # 115 内部 API 的下载地址获取端点
        url = "https://webapi.115.com/files/download"
        params = {"pickcode": pickcode}
        
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"115 driver 获取下载地址失败: {exc}")
        except ValueError as exc:
            raise RuntimeError(f"115 driver 解析响应失败: {exc}")
        
        # 检查返回状态
        if not payload.get("state"):
            error_msg = payload.get("message") or payload.get("error") or payload.get("errNo") or "获取下载地址失败"
            raise RuntimeError(f"115 driver 获取下载地址失败: {error_msg}")
        
        # 提取下载URL - 115的下载API返回格式通常是 data.url 或 data[pickcode].url
        data = payload.get("data") or {}
        download_url = None
        
        # 尝试多种可能的返回格式
        if isinstance(data, dict):
            # 格式1: data.url.url
            if "url" in data and isinstance(data["url"], dict):
                download_url = data["url"].get("url")
            # 格式2: data.url (直接是字符串)
            elif "url" in data and isinstance(data["url"], str):
                download_url = data["url"]
            # 格式3: data[pickcode].url
            elif pickcode in data and isinstance(data[pickcode], dict):
                url_data = data[pickcode].get("url", {})
                if isinstance(url_data, dict):
                    download_url = url_data.get("url")
                elif isinstance(url_data, str):
                    download_url = url_data
            # 格式4: data.download_url
            else:
                download_url = data.get("download_url") or data.get("file_url")
        elif isinstance(data, str):
            download_url = data
        
        if not download_url:
            raise RuntimeError(f"115 driver 返回的数据中未找到下载地址，返回数据: {payload}")
        
        # 规范化为与 OpenAPI 兼容的格式（只有一个"原画"清晰度）
        return {
            "state": 1,
            "data": {
                "video_url": [
                    {
                        "definition": 100,
                        "title": "原画",
                        "url": download_url
                    }
                ]
            }
        }


# ============================================================================
# 统一客户端（组合 OpenAPI 和 Driver）
# ============================================================================


class Cloud115Client:
    """115 云盘统一客户端，支持 OpenAPI 和 driver 两种模式。

    mode 取值:
    - `openapi`: 仅使用官方 OpenAPI
    - `driver`: 优先使用 driver（失败回退 OpenAPI）
    - `auto`: 优先 driver，失败自动回退
    """

    def __init__(
        self,
        *,
        token_file: str,
        driver_cookie: Optional[str] = None,
        driver_cookie_file: Optional[str] = None,
        mode: str = "openapi",
        timeout: int = 15,
        driver_user_agent: str = DEFAULT_USER_AGENT,
        driver_api_urls: Optional[Sequence[str]] = None,
        driver_login_check_interval: int = DEFAULT_LOGIN_CHECK_INTERVAL,
        logger: Optional[logging.Logger] = None,
    ):
        self.mode = (mode or "openapi").lower()
        self.logger = logger or logging.getLogger(__name__)

        # 初始化 OpenAPI 客户端
        self.openapi = OpenAPIClient(token_file, timeout)

        # 初始化 Driver 客户端
        self.driver: Optional[DriverClient] = None
        if driver_cookie or driver_cookie_file:
            try:
                cookie_text = driver_cookie
                if not cookie_text and driver_cookie_file and os.path.exists(driver_cookie_file):
                    with open(driver_cookie_file, "r", encoding="utf-8") as fh:
                        cookie_text = fh.read().strip()

                if cookie_text:
                    credential = DriverCredential.from_cookie(cookie_text)
                    self.driver = DriverClient(
                        credential,
                        timeout=timeout,
                        user_agent=driver_user_agent,
                        api_urls=driver_api_urls,
                        login_check_interval=driver_login_check_interval,
                    )
                    self.logger.info("115driver 客户端初始化成功")
            except Exception as exc:
                self.logger.warning("115driver 初始化失败: %s", exc)
                self.driver = None

        if self.mode in ("driver", "auto") and self.driver is None:
            self.logger.warning("mode=%s 但 driver 未初始化，将使用 OpenAPI", self.mode)

    def _iter_backends(self, method: str) -> Iterable[Tuple[str, Callable[..., Any]]]:
        driver_ok = self.driver is not None and hasattr(self.driver, method)
        openapi_ok = hasattr(self.openapi, method)

        if self.mode == "openapi" or not driver_ok:
            if openapi_ok:
                yield "openapi", getattr(self.openapi, method)
            if driver_ok:
                yield "driver", getattr(self.driver, method)
            return

        if driver_ok:
            yield "driver", getattr(self.driver, method)
        if openapi_ok:
            yield "openapi", getattr(self.openapi, method)

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        last_error = None
        for backend_name, func in self._iter_backends(method):
            try:
                return func(*args, **kwargs)
            except Cloud115AuthError as exc:
                if backend_name == "driver":
                    last_error = exc
                    self.logger.debug("115driver 鉴权失败: %s", exc)
                    continue
                raise
            except Cloud115RateLimitError as exc:
                if backend_name == "driver":
                    last_error = exc
                    self.logger.debug("115driver 限流: %s", exc)
                    continue
                raise
            except Exception as exc:
                if backend_name == "driver":
                    last_error = exc
                    self.logger.warning("115driver 异常 (%s): %s", method, exc)
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError(f"所有 115 客户端均失败: {method}")

    def list_files(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._call("list_files", *args, **kwargs)

    def get_folder_info(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._call("get_folder_info", *args, **kwargs)

    def get_file_info(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._call("get_file_info", *args, **kwargs)

    def get_file_info_by_pickcode(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        # driver 暂不支持
        return self.openapi.get_file_info_by_pickcode(*args, **kwargs)

    def get_video_play(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        # driver 现在支持视频播放地址获取
        return self._call("get_video_play", *args, **kwargs)

    def get_download_info(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if not self.driver:
            raise Cloud115ConfigError("115driver 未配置，无法获取下载信息")
        return self.driver.get_download_info(*args, **kwargs)


