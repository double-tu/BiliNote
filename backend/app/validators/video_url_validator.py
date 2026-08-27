from pydantic import AnyUrl, validator, BaseModel, field_validator, model_validator
import re
from urllib.parse import urlparse

from app.utils.url_parser import extract_first_http_url, normalize_video_url

SUPPORTED_PLATFORMS = {
    "bilibili": r"(https?://)?(www\.)?bilibili\.com/video/[a-zA-Z0-9]+",
    "youtube": r"(https?://)?(www\.)?(youtube\.com/(watch\?v=|shorts/)|youtu\.be/)[\w\-]+",
    "douyin": "douyin",
    "kuaishou": "kuaishou",
}


def is_supported_video_url(url: str) -> bool:
    parsed = urlparse(url)

    # 检查是否为Bilibili的短链接
    if parsed.netloc == "b23.tv":
        return True

    host = parsed.netloc.lower().split(":", 1)[0]
    if host in {"xhslink.com", "xhslink.cn"} or host.endswith((".xhslink.com", ".xhslink.cn")):
        return bool(parsed.path.strip("/"))
    if host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com"):
        return bool(re.search(r"/(?:discovery/item|explore|item)/[0-9a-fA-F]+", parsed.path))

    for name, pattern in SUPPORTED_PLATFORMS.items():
        if pattern in ["douyin", "kuaishou"]:
            if pattern in url:
                return True
        else:
            if re.match(pattern, url):
                return True
    return False


class VideoRequest(BaseModel):
    url: AnyUrl
    platform: str

    @model_validator(mode="before")
    @classmethod
    def normalize_url(cls, data):
        if isinstance(data, dict) and data.get("platform") == "bilibili" and data.get("url"):
            data["url"] = normalize_video_url(str(data["url"]))
        elif isinstance(data, dict) and data.get("platform") == "xiaohongshu" and data.get("url"):
            data["url"] = extract_first_http_url(str(data["url"])) or data["url"]
        return data

    @field_validator("url")
    def validate_video_url(cls, v):
        if not is_supported_video_url(str(v)):
            raise ValueError("暂不支持该视频平台或链接格式无效")
        return v
