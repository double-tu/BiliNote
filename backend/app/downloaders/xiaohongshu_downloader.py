import json
import logging
import os
import re
import shutil
import subprocess
from typing import Dict, Optional, Tuple, Union
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from app.downloaders.base import Downloader
from app.enmus.note_enums import DownloadQuality
from app.models.audio_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult
from app.services.cookie_manager import CookieConfigManager
from app.utils.path_helper import get_data_dir


logger = logging.getLogger(__name__)

_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
_PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "User-Agent": _DESKTOP_USER_AGENT,
}
_DOWNLOAD_HEADERS = {
    "Referer": "https://www.xiaohongshu.com/",
    "User-Agent": _DESKTOP_USER_AGENT,
}
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_NOTE_ID_PATTERN = re.compile(
    r"/(?:discovery/item|explore|item)/([0-9a-fA-F]+)",
    re.IGNORECASE,
)
_SHORT_LINK_HOSTS = {"xhslink.com", "xhslink.cn"}


def extract_xiaohongshu_share_url(raw_input: str) -> Optional[str]:
    """从小红书分享文案或纯链接中提取首个 HTTP(S) URL。"""
    if not raw_input:
        return None
    match = _URL_PATTERN.search(str(raw_input))
    if not match:
        return None
    return match.group(0).rstrip("，。；;、)]}>\"'”’")


def _ffmpeg_binary() -> str:
    configured = os.getenv("FFMPEG_BIN_PATH", "").strip().strip('"')
    if configured:
        if os.path.isfile(configured):
            return configured
        if os.path.isdir(configured):
            for filename in ("ffmpeg.exe", "ffmpeg"):
                candidate = os.path.join(configured, filename)
                if os.path.isfile(candidate):
                    return candidate
    return shutil.which("ffmpeg") or "ffmpeg"


def extract_xiaohongshu_note_id(url: str) -> Tuple[Optional[str], Optional[str]]:
    """从完整小红书链接中提取笔记 ID 与访问所需的 xsec_token。"""
    parsed = urlparse(url)
    match = _NOTE_ID_PATTERN.search(parsed.path)
    note_id = match.group(1) if match else None
    xsec_token = parse_qs(parsed.query).get("xsec_token", [None])[0]
    return note_id, xsec_token


def _parse_initial_state(html: str) -> Optional[dict]:
    match = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>",
        html or "",
        re.DOTALL,
    )
    if not match:
        return None

    # 页面数据是 JavaScript 对象，其中偶尔包含 JSON 不认识的 undefined。
    raw_state = re.sub(r"\bundefined\b", "null", match.group(1))
    try:
        return json.loads(raw_state)
    except json.JSONDecodeError as exc:
        logger.warning("小红书页面状态解析失败: %s", exc)
        return None


def _pick_note(state: dict, note_id: Optional[str]) -> Optional[dict]:
    note_map = (
        (state.get("note") or {}).get("noteDetailMap")
        or state.get("noteDetailMap")
        or {}
    )
    if not isinstance(note_map, dict) or not note_map:
        return None

    entry = note_map.get(note_id) if note_id else None
    if not entry:
        entry = next(iter(note_map.values()), None)
    if not isinstance(entry, dict):
        return None
    note = entry.get("note") or entry
    return note if isinstance(note, dict) else None


def _pick_video_url(note: dict) -> Optional[str]:
    video = note.get("video") or {}
    stream = ((video.get("media") or {}).get("stream") or {})
    for codec in ("h264", "h265", "av1"):
        for candidate in stream.get(codec) or []:
            if not isinstance(candidate, dict):
                continue
            direct_url = candidate.get("masterUrl")
            if direct_url:
                return direct_url
            backup_urls = candidate.get("backupUrls") or []
            if backup_urls:
                return backup_urls[0]

    consumer = video.get("consumer") or {}
    origin_key = consumer.get("originVideoKey") or consumer.get("origin_video_key")
    if origin_key:
        return f"https://sns-video-bd.xhscdn.com/{origin_key}"
    return None


def _pick_cover_url(note: dict) -> str:
    images = note.get("imageList") or note.get("image_list") or []
    if images and isinstance(images[0], dict):
        first = images[0]
        return (
            first.get("urlDefault")
            or first.get("urlPre")
            or first.get("url")
            or ""
        )
    return ""


def _pick_duration(note: dict) -> float:
    video = note.get("video") or {}
    media = video.get("media") or {}
    candidates = [
        video.get("duration"),
        (video.get("capa") or {}).get("duration"),
        media.get("duration"),
        (media.get("video") or {}).get("duration"),
    ]
    for value in candidates:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        # 小红书页面常以毫秒返回时长；较小数值则按秒处理。
        return duration / 1000 if duration >= 1000 else duration
    return 0.0


class XiaohongshuDownloader(Downloader):
    """解析小红书视频笔记并下载视频/音频。图文笔记不在支持范围内。"""

    def __init__(self):
        super().__init__()
        self._cookie = CookieConfigManager().get("xiaohongshu")
        self._info_cache: Dict[str, dict] = {}

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(_PAGE_HEADERS)
        if self._cookie:
            session.headers["Cookie"] = self._cookie
        return session

    def _resolve_url(self, raw_input: str, session: requests.Session) -> str:
        share_url = extract_xiaohongshu_share_url(raw_input)
        if not share_url:
            raise ValueError("未在输入内容中找到有效的小红书链接")

        host = urlparse(share_url).netloc.lower().split(":", 1)[0]
        if host in _SHORT_LINK_HOSTS or any(host.endswith(f".{item}") for item in _SHORT_LINK_HOSTS):
            try:
                response = session.get(
                    share_url,
                    allow_redirects=True,
                    stream=True,
                    timeout=15,
                )
                response.raise_for_status()
                resolved_url = response.url
                response.close()
            except requests.RequestException as exc:
                raise ValueError("小红书短链接解析失败，请检查网络后重试") from exc
        else:
            resolved_url = share_url

        resolved_host = urlparse(resolved_url).netloc.lower().split(":", 1)[0]
        if resolved_host != "xiaohongshu.com" and not resolved_host.endswith(".xiaohongshu.com"):
            raise ValueError("链接未跳转到小红书页面，无法继续解析")
        return resolved_url

    @staticmethod
    def _canonical_url(resolved_url: str) -> Tuple[str, str, Optional[str]]:
        note_id, xsec_token = extract_xiaohongshu_note_id(resolved_url)
        if not note_id:
            raise ValueError("无法从小红书链接中提取笔记 ID")

        query = urlencode({"xsec_token": xsec_token}) if xsec_token else ""
        canonical_url = f"https://www.xiaohongshu.com/discovery/item/{note_id}"
        if query:
            canonical_url = f"{canonical_url}?{query}"
        return canonical_url, note_id, xsec_token

    def fetch_video_info(self, raw_input: str) -> dict:
        """解析分享文本/短链/完整链接，返回视频元数据和 CDN 直链。"""
        cache_key = str(raw_input)
        cached = self._info_cache.get(cache_key)
        if cached:
            return cached

        with self._new_session() as session:
            resolved_url = self._resolve_url(cache_key, session)
            canonical_url, note_id, _ = self._canonical_url(resolved_url)
            try:
                response = session.get(canonical_url, timeout=25)
                response.raise_for_status()
            except requests.RequestException as exc:
                raise ValueError("小红书页面请求失败，请检查网络、链接或 Cookie") from exc

        if not response.text:
            raise ValueError("小红书页面返回为空，链接可能已失效或需要登录")

        state = _parse_initial_state(response.text)
        if not state:
            raise ValueError("无法解析小红书页面数据，页面结构可能已更新或需要登录")
        note = _pick_note(state, note_id)
        if not note:
            raise ValueError("未找到小红书笔记详情，内容可能已删除或不可见")

        direct_url = _pick_video_url(note)
        if not direct_url:
            raise ValueError("该小红书笔记不是视频笔记，当前暂不支持图文笔记")

        resolved_note_id = note.get("noteId") or note.get("note_id") or note_id
        description = note.get("desc") or ""
        title = note.get("title") or description[:80] or f"小红书视频 {resolved_note_id}"
        author = (note.get("user") or {}).get("nickname") or ""
        info = {
            "note_id": str(resolved_note_id),
            "title": title,
            "author": author,
            "cover_url": _pick_cover_url(note),
            "duration": _pick_duration(note),
            "direct_url": direct_url,
            "webpage_url": resolved_url,
            "description": description,
        }
        if len(self._info_cache) >= 16:
            self._info_cache.pop(next(iter(self._info_cache)))
        self._info_cache[cache_key] = info
        logger.info("小红书视频解析成功: note_id=%s title=%s", resolved_note_id, title[:40])
        return info

    @staticmethod
    def _download_stream(direct_url: str, output_path: str) -> None:
        partial_path = f"{output_path}.part"
        try:
            with requests.get(
                direct_url,
                headers=_DOWNLOAD_HEADERS,
                stream=True,
                timeout=(15, 60),
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type:
                    raise ValueError("小红书视频直链返回了网页内容，直链可能已过期")
                with open(partial_path, "wb") as output_file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output_file.write(chunk)
            if not os.path.exists(partial_path) or os.path.getsize(partial_path) == 0:
                raise ValueError("小红书视频下载结果为空")
            os.replace(partial_path, output_path)
        except (requests.RequestException, OSError) as exc:
            raise ValueError("小红书视频下载失败，请稍后重试") from exc
        finally:
            if os.path.exists(partial_path):
                try:
                    os.remove(partial_path)
                except OSError:
                    logger.warning("清理小红书临时下载文件失败: %s", partial_path)

    def _download_video_from_info(self, info: dict, output_dir: str) -> str:
        video_path = os.path.join(output_dir, f"{info['note_id']}.mp4")
        if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
            return video_path
        self._download_stream(info["direct_url"], video_path)
        return video_path

    def download(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
        quality: DownloadQuality = "fast",
        need_video: Optional[bool] = False,
        skip_download: bool = False,
    ) -> AudioDownloadResult:
        del quality, need_video
        output_dir = output_dir or get_data_dir() or self.cache_data
        os.makedirs(output_dir, exist_ok=True)

        info = self.fetch_video_info(str(video_url))
        audio_path = os.path.join(output_dir, f"{info['note_id']}.mp3")
        video_path = os.path.join(output_dir, f"{info['note_id']}.mp4")

        if not skip_download and not (os.path.exists(audio_path) and os.path.getsize(audio_path) > 0):
            video_path = self._download_video_from_info(info, output_dir)
            try:
                subprocess.run(
                    [
                        _ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", video_path, "-vn", "-acodec", "libmp3lame", audio_path,
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                if os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except OSError:
                        logger.warning("清理不完整的小红书音频失败: %s", audio_path)
                raise ValueError("无法从小红书视频提取音频，请检查 FFmpeg 配置") from exc

        return AudioDownloadResult(
            file_path=audio_path,
            title=info["title"],
            duration=info["duration"],
            cover_url=info["cover_url"],
            platform="xiaohongshu",
            video_id=info["note_id"],
            raw_info={
                "tags": info["description"],
                "uploader": info["author"],
                "webpage_url": info["webpage_url"],
            },
            video_path=video_path if os.path.exists(video_path) else None,
        )

    def download_video(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
    ) -> str:
        output_dir = output_dir or get_data_dir() or self.cache_data
        os.makedirs(output_dir, exist_ok=True)
        info = self.fetch_video_info(str(video_url))
        return self._download_video_from_info(info, output_dir)

    def download_subtitles(
        self,
        video_url: str,
        output_dir: str = None,
        langs: list = None,
    ) -> Optional[TranscriptResult]:
        del video_url, output_dir, langs
        # 小红书页面没有稳定、公开的平台字幕接口，交由现有 ASR 流程处理。
        return None
