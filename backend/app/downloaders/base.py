import enum

from abc import ABC, abstractmethod
from typing import Optional, Union

from app.enmus.note_enums import DownloadQuality
from app.models.notes_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult
from os import getenv
QUALITY_MAP = {
    "fast": "32",
    "medium": "64",
    "slow": "128"
}

# yt-dlp 的 `retries` 默认值(10)是命令行参数解析器给的，Python API 不套用它：
# 不显式设置时 HttpFD 拿到的是 `self.params.get('retries')` == None，而
# `RetryManager.__init__` 做的是 `self.retries = _retries or 0`——也就是
# 一次都不重试。任何一次网络抖动（例如 B 站 CDN
# upos-sz-mirror*.bilivideo.com 读超时）都会让整个笔记任务直接失败。
#
# 这里的值偏保守：笔记任务是用户在前台等的，重试太多不如早点失败让用户重来。
YDL_RETRY_OPTS = {
    "retries": 3,
    "fragment_retries": 3,
    "socket_timeout": 30,
}


class Downloader(ABC):
    def __init__(self):
        #TODO 需要修改为可配置
        self.quality = QUALITY_MAP.get('fast')
        self.cache_data=getenv('DATA_DIR')

    @abstractmethod
    def download(self, video_url: str, output_dir: str = None,
                 quality: DownloadQuality = "fast", need_video: Optional[bool] = False,
                 skip_download: bool = False) -> AudioDownloadResult:
        '''

        :param need_video:
        :param video_url: 资源链接
        :param output_dir: 输出路径 默认根目录data
        :param quality: 音频质量 fast | medium | slow
        :return:返回一个 AudioDownloadResult 类
        '''
        pass

    @staticmethod
    def download_video(self, video_url: str,
                       output_dir: Union[str, None] = None) -> str:
        pass

    def download_subtitles(self, video_url: str, output_dir: str = None,
                           langs: list = None) -> Optional[TranscriptResult]:
        '''
        尝试获取平台字幕（人工字幕或自动生成字幕）

        :param video_url: 视频链接
        :param output_dir: 输出路径
        :param langs: 优先语言列表，如 ['zh-Hans', 'zh', 'en']
        :return: TranscriptResult 或 None（无字幕时）
        '''
        return None
