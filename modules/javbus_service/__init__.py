"""JavBus 服务模块入口。

提供统一的工厂方法，根据配置选择外部或内部实现。
"""

from __future__ import annotations

import logging
from typing import Optional

from javbus_db import JavbusDatabase

from .base import JavbusClientProtocol
from .external_client import ExternalJavbusClient
from .internal_client import InternalJavbusClient


DEFAULT_SECTION = "javbus"


def get_javbus_client(
    config: dict,
    *,
    logger: Optional[logging.Logger] = None,
    db: Optional[JavbusDatabase] = None,
) -> JavbusClientProtocol:
    """根据配置返回合适的 JavBus 客户端实现。"""

    logger = logger or logging.getLogger("JavBusService")

    section = config.get(DEFAULT_SECTION, {}) if isinstance(config, dict) else {}
    mode = (section.get("mode") or "external").lower()
    timeout = int(section.get("timeout", 10)) if isinstance(section, dict) else 10
    external_url = _resolve_external_api_url(config, section)

    def build_external_client() -> Optional[JavbusClientProtocol]:
        if not external_url:
            return None
        return ExternalJavbusClient(
            external_url,
            timeout=timeout,
            logger=logger.getChild("External"),
        )

    if mode == "internal":
        internal_section = section if isinstance(section, dict) else {}
        internal_config = internal_section.get("internal", {}) or {}
        allow_fallback = internal_section.get("allow_external_fallback", True)
        fallback_client = build_external_client() if allow_fallback else None
        
        # 获取 base_url 用于爬虫
        base_url = config.get("base_url", "https://www.javbus.com")

        logger.info("使用内部 JavBus 模式 (base_url=%s, fallback=%s)", base_url, bool(fallback_client))
        try:
            return InternalJavbusClient(
                internal_section,
                db=db,
                fallback_client=fallback_client,
                logger=logger.getChild("Internal"),
                base_url=base_url,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("初始化内部 JavBus 模块失败: %s", exc)
            if fallback_client:
                logger.warning("回退至外部 JavBus API")
                return fallback_client
            raise

    # 默认使用外部 API
    external_client = build_external_client()
    if not external_client:
        raise ValueError("未配置可用的 JavBus 外部 API 地址")
    logger.info("使用外部 JavBus API: %s", external_url)
    return external_client


def _resolve_external_api_url(config: dict, section: dict) -> str:
    """兼容旧的配置字段，解析外部 API 地址。"""

    # 优先使用环境变量在 webserver 中覆写过的值
    if isinstance(section, dict):
        api_url = section.get("external_api_url")
        if api_url:
            return api_url.rstrip("/")

    legacy_url = None
    if isinstance(config, dict):
        legacy_url = config.get("api_url")

    if legacy_url:
        return str(legacy_url).rstrip("/")

    # 回退到默认官方地址
    return "https://www.javbus.com/api".rstrip("/")


__all__ = ["get_javbus_client", "JavbusClientProtocol"]


