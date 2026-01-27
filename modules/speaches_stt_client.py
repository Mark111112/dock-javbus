from __future__ import annotations

import io
import os
import wave
from dataclasses import dataclass
from typing import Optional, Dict, Any

import logging
import requests


@dataclass
class SpeachesConfig:
    """
    Speaches STT 配置（基于 OpenAI 兼容接口）

    优先从环境变量读取，便于在 Docker / 本地灵活配置：
    - SPEACHES_BASE_URL，例如: http://localhost:8000/v1
    - SPEACHES_API_KEY，Speaches 要求非空字符串即可
    - SPEACHES_STT_MODEL，默认: Systran/faster-whisper-small
    - SPEACHES_TIMEOUT，HTTP 超时时间（秒）
    """

    base_url: str = os.getenv("SPEACHES_BASE_URL", "http://localhost:8000/v1")
    api_key: str = os.getenv("SPEACHES_API_KEY", "cant-be-empty")
    model: str = os.getenv("SPEACHES_STT_MODEL", "Systran/faster-whisper-small")
    timeout: float = float(os.getenv("SPEACHES_TIMEOUT", "15"))


# 模块级默认配置与开关，由 config.json 驱动
DEFAULT_SPEACHES_CONFIG = SpeachesConfig()
_SPEACHES_ENABLED: bool = True


def configure_speaches_from_dict(config_section: Optional[Dict[str, Any]]) -> None:
    """
    根据 config.json 中的 transcription 段落更新 Speaches 默认配置。

    期望结构示例（config.json 顶层）:
    {
      "transcription": {
        "enabled": true,
        "provider": "speaches",
        "api_base_url": "http://host.docker.internal:8000/v1",
        "api_key": "cant-be-empty",
        "model": "Systran/faster-whisper-small",
        "timeout": 15
      }
    }
    """
    global DEFAULT_SPEACHES_CONFIG, _SPEACHES_ENABLED

    base = SpeachesConfig()

    if not isinstance(config_section, dict):
        DEFAULT_SPEACHES_CONFIG = base
        _SPEACHES_ENABLED = True
        return

    enabled = config_section.get("enabled")
    if isinstance(enabled, bool):
        _SPEACHES_ENABLED = enabled
    else:
        _SPEACHES_ENABLED = True

    provider = str(config_section.get("provider") or "").strip().lower()
    if provider and provider not in ("speaches", "speaches_ai"):
        # 当前仅支持 Speaches，其它 provider 视为禁用
        _SPEACHES_ENABLED = False

    api_base_url = (
        str(config_section.get("api_base_url") or config_section.get("base_url") or "").strip()
    )
    if api_base_url:
        base.base_url = api_base_url

    api_key = str(config_section.get("api_key") or config_section.get("api_token") or "").strip()
    if api_key:
        base.api_key = api_key

    model = str(config_section.get("model") or "").strip()
    if model:
        base.model = model

    timeout_value = config_section.get("timeout")
    if timeout_value is not None:
        try:
            base.timeout = float(timeout_value)
        except Exception:
            pass

    DEFAULT_SPEACHES_CONFIG = base


class SpeachesSTTClient:
    """Speaches 语音转写客户端，基于 OpenAI 兼容 HTTP 接口。"""

    def __init__(self, config: Optional[SpeachesConfig] = None) -> None:
        self.config = config or DEFAULT_SPEACHES_CONFIG

    def transcribe_pcm(
        self,
        pcm_data: bytes,
        sample_rate: int,
        language: Optional[str] = None,
    ) -> str:
        """
        接收 16-bit PCM 小端音频，封装为 WAV 后调用 Speaches STT。
        返回识别出的完整文本字符串，如失败则返回空字符串。
        """
        if not pcm_data or not _SPEACHES_ENABLED:
            return ""

        wav_bytes = self._pcm_to_wav(pcm_data, sample_rate)
        return self.transcribe_wav(wav_bytes, language=language)

    def transcribe_wav(
        self,
        wav_bytes: bytes,
        language: Optional[str] = None,
    ) -> str:
        """直接使用 WAV 字节数据调用 Speaches STT 接口。"""
        if not wav_bytes or not _SPEACHES_ENABLED:
            return ""

        url = self._build_transcriptions_url()
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
        }

        files = {
            "file": ("audio.wav", wav_bytes, "audio/wav"),
        }

        data = {
            "model": self.config.model,
            "response_format": "json",
        }

        lang_code = self._normalize_language(language)
        if lang_code:
            data["language"] = lang_code

        logger = logging.getLogger("Transcription.Speaches")

        try:
            resp = requests.post(
                url,
                headers=headers,
                data=data,
                files=files,
                timeout=self.config.timeout,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Speaches STT HTTP error: %s", exc, exc_info=True)
            return ""

        try:
            payload = resp.json()
        except Exception as exc:
            logger.error("Speaches STT parse error: %s", exc, exc_info=True)
            return ""

        if isinstance(payload, dict):
            text = payload.get("text")
            if isinstance(text, str):
                return text

        logger.warning("Speaches STT response missing 'text' field: %r", payload)
        return ""

    def _build_transcriptions_url(self) -> str:
        base = (self.config.base_url or "").rstrip("/")
        return f"{base}/audio/transcriptions"

    @staticmethod
    def _pcm_to_wav(pcm_data: bytes, sample_rate: int) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)
        return buffer.getvalue()

    @staticmethod
    def _normalize_language(language: Optional[str]) -> Optional[str]:
        if not language:
            return None
        lang = str(language).strip()
        if not lang:
            return None
        parts = lang.split("-")
        return parts[0].lower()

