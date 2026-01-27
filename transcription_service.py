import json
import threading
from typing import Optional, List, Dict, Any, Callable

from modules.speaches_stt_client import SpeachesSTTClient, configure_speaches_from_dict
from modules.fwhisper_client import FasterWhisperClient, configure_fwhisper_from_dict


_CLIENT_FACTORY: Callable[[], object] = lambda: SpeachesSTTClient()
_PROVIDER: str = "speaches"
_DEFAULT_LANGUAGE: str = "ja-JP"


def configure_transcription_from_dict(config_section: Optional[Dict[str, Any]]) -> None:
    """
    Update transcription provider based on config.json transcription section.
    provider: speaches | fwhisper
    """
    global _CLIENT_FACTORY, _PROVIDER, _DEFAULT_LANGUAGE

    provider = ""
    if isinstance(config_section, dict):
        provider = str(config_section.get("provider") or "").strip().lower()
        lang = str(config_section.get("language") or "").strip()
        if lang:
            _DEFAULT_LANGUAGE = lang

    if provider == "fwhisper":
        configure_fwhisper_from_dict(config_section)
        _CLIENT_FACTORY = lambda: FasterWhisperClient()
        _PROVIDER = "fwhisper"
    else:
        configure_speaches_from_dict(config_section)
        _CLIENT_FACTORY = lambda: SpeachesSTTClient()
        _PROVIDER = "speaches"


class TranscriptionEngine:
    """
    语音识别引擎包装类。

    当前实现：使用 Speaches 提供的 OpenAI 兼容 STT 接口。
    """

    def __init__(self, source_lang: str, target_lang: Optional[str], sample_rate: int) -> None:
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.sample_rate = sample_rate

        self._client = _CLIENT_FACTORY()
        self._buffer = bytearray()

        self._min_seconds = 2.0
        self._min_bytes = int(self.sample_rate * 2 * self._min_seconds)

    def process_chunk(self, pcm_chunk: bytes) -> List[Dict[str, Any]]:
        """
        处理一段 16-bit PCM 音频，返回零个或多个识别结果片段。

        目前采用简单分段策略：累计约几秒的音频后打包调用一次 STT，
        得到整句文本后作为 is_final=True 片段返回。
        """
        if not pcm_chunk:
            return []

        self._buffer.extend(pcm_chunk)

        if len(self._buffer) < self._min_bytes:
            return []

        pcm_data = bytes(self._buffer)
        self._buffer.clear()

        text = self._client.transcribe_pcm(
            pcm_data,
            sample_rate=self.sample_rate,
            language=self.source_lang,
        )

        if not text:
            return []

        return [
            {
                "text": text,
                "is_final": True,
            }
        ]


class TranscriptionSession:
    """一次视频播放对应一个转写会话。"""

    def __init__(self, movie_id: str, source_lang: str, target_lang: Optional[str], sample_rate: int) -> None:
        self.movie_id = movie_id
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.sample_rate = sample_rate
        self.engine = TranscriptionEngine(source_lang, target_lang, sample_rate)
        self._lock = threading.Lock()
        self._closed = False

    def add_audio(self, pcm_chunk: bytes) -> List[Dict[str, Any]]:
        """接收一段音频数据并返回识别结果列表。"""
        if self._closed:
            return []

        with self._lock:
            results = self.engine.process_chunk(pcm_chunk)

        return results or []

    def close(self) -> None:
        """结束本次会话，释放底层资源。"""
        self._closed = True


def handle_transcription_ws(ws) -> None:
    """
    WebSocket 处理函数，由 webserver.py 中的 @sock.route('/ws/transcription') 调用。

    协议约定：
    - 文本消息（JSON）：
        { "type": "start", "movieId": "...", "sourceLang": "ja-JP", "targetLang": "zh-CN", "sampleRate": 16000 }
        { "type": "stop" }
    - 二进制消息：
        16-bit PCM 小端音频数据块，采样率由 sampleRate 指定。
    - 返回消息（JSON）：
        { "type": "started" }
        { "type": "transcript", "text": "...", "is_final": false }
        { "type": "error", "message": "..." }
    """

    session: Optional[TranscriptionSession] = None

    try:
        while True:
            message = ws.receive()
            if message is None:
                break

            if isinstance(message, str):
                try:
                    data = json.loads(message)
                except Exception:
                    continue

                msg_type = data.get("type")

                if msg_type == "start":
                    movie_id = str(data.get("movieId") or "").strip()
                    source_lang = str(data.get("sourceLang") or _DEFAULT_LANGUAGE or "ja-JP")
                    target_lang_raw = data.get("targetLang")
                    target_lang = str(target_lang_raw) if target_lang_raw else None
                    sample_rate = int(data.get("sampleRate") or 16000)

                    if session:
                        session.close()

                    session = TranscriptionSession(
                        movie_id=movie_id,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        sample_rate=sample_rate,
                    )

                    ws.send(json.dumps({"type": "started"}))

                elif msg_type == "stop":
                    if session:
                        session.close()
                    break

                continue

            if isinstance(message, (bytes, bytearray)):
                if not session:
                    continue

                try:
                    results = session.add_audio(message)
                except Exception as e:
                    ws.send(
                        json.dumps(
                            {
                                "type": "error",
                                "message": f"STT error: {str(e)}",
                            }
                        )
                    )
                    continue

                for r in results:
                    ws.send(
                        json.dumps(
                            {
                                "type": "transcript",
                                "text": r.get("text", ""),
                                "is_final": bool(r.get("is_final", False)),
                            }
                        )
                    )

    finally:
        if session:
            session.close()
