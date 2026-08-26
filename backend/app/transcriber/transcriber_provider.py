import os
import platform
import threading
from enum import Enum

from app.transcriber.groq import GroqTranscriber
from app.transcriber.whisper import WhisperTranscriber
from app.transcriber.bcut import BcutTranscriber
from app.transcriber.kuaishou import KuaishouTranscriber
from app.utils.logger import get_logger

logger = get_logger(__name__)

class TranscriberType(str, Enum):
    FAST_WHISPER = "fast-whisper"
    MLX_WHISPER = "mlx-whisper"
    BCUT = "bcut"
    KUAISHOU = "kuaishou"
    GROQ = "groq"
    OPENAI_COMPATIBLE = "openai-compatible"
    QWEN = "qwen"
    DOUBAO = "doubao"

# 在 Apple 平台尝试导入 MLX Whisper（不再依赖环境变量，支持前端动态切换）
MLX_WHISPER_AVAILABLE = False
if platform.system() == "Darwin":
    try:
        from app.transcriber.mlx_whisper_transcriber import MLXWhisperTranscriber
        MLX_WHISPER_AVAILABLE = True
        logger.info("MLX Whisper 可用，已导入")
    except ImportError:
        logger.warning("MLX Whisper 导入失败，可能未安装 mlx_whisper")

logger.info('初始化转录服务提供器')

# 转录器单例缓存
_transcribers = {
    TranscriberType.FAST_WHISPER: None,
    TranscriberType.MLX_WHISPER: None,
    TranscriberType.BCUT: None,
    TranscriberType.KUAISHOU: None,
    TranscriberType.GROQ: None,
    TranscriberType.OPENAI_COMPATIBLE: None,
    TranscriberType.QWEN: None,
    TranscriberType.DOUBAO: None,
}

# Cache instances together with their constructor configuration. The
# transcriber choice and Whisper model size can be changed from the frontend,
# so caching by transcriber type alone would keep using the first loaded model.
_transcriber_configs = {key: None for key in _transcribers}
_transcriber_init_lock = threading.Lock()

# 公共实例初始化函数
def _init_transcriber(key: TranscriberType, cls, *args, **kwargs):
    init_config = (args, tuple(sorted(kwargs.items())))
    with _transcriber_init_lock:
        instance = _transcribers[key]
        if instance is None or _transcriber_configs[key] != init_config:
            action = "创建" if instance is None else "按新配置重新创建"
            logger.info(f'{action} {cls.__name__} 实例: {key}')
            try:
                new_instance = cls(*args, **kwargs)
            except Exception as e:
                logger.error(f"{cls.__name__} 创建失败: {e}")
                raise
            _transcribers[key] = new_instance
            _transcriber_configs[key] = init_config
            logger.info(f'{cls.__name__} 创建成功')
        return _transcribers[key]

# 各类型获取方法
def get_groq_transcriber():
    return _init_transcriber(TranscriberType.GROQ, GroqTranscriber)

def get_whisper_transcriber(model_size="base", device="cuda"):
    return _init_transcriber(TranscriberType.FAST_WHISPER, WhisperTranscriber, model_size=model_size, device=device)

def get_bcut_transcriber():
    return _init_transcriber(TranscriberType.BCUT, BcutTranscriber)

def get_kuaishou_transcriber():
    return _init_transcriber(TranscriberType.KUAISHOU, KuaishouTranscriber)

def get_openai_compatible_transcriber(provider_id="openai", model="whisper-1"):
    from app.transcriber.openai_compatible import OpenAICompatibleTranscriber

    return _init_transcriber(
        TranscriberType.OPENAI_COMPATIBLE,
        OpenAICompatibleTranscriber,
        provider_id=provider_id,
        model=model,
    )

def get_qwen_transcriber(provider_id="qwen", model="qwen3-asr-flash-realtime"):
    from app.transcriber.native_asr import QwenFileTranscriber, QwenRealtimeTranscriber
    # realtime 模型必须走 WebSocket；qwen-audio/qwen3-asr 非 realtime 模型
    # 走百炼 HTTP 文件接口，避免把整段文件突发写入 realtime 会话。
    transcriber_cls = QwenRealtimeTranscriber if "realtime" in (model or "").lower() else QwenFileTranscriber
    return _init_transcriber(TranscriberType.QWEN, transcriber_cls, provider_id=provider_id, model=model)

def get_doubao_transcriber(provider_id="volcengine", model="bigmodel"):
    from app.transcriber.native_asr import DoubaoTranscriber
    return _init_transcriber(TranscriberType.DOUBAO, DoubaoTranscriber, provider_id=provider_id, model=model)

def get_mlx_whisper_transcriber(model_size="base"):
    if not MLX_WHISPER_AVAILABLE:
        logger.warning("MLX Whisper 不可用，请确保在 Apple 平台且已安装 mlx_whisper")
        raise ImportError("MLX Whisper 不可用")
    return _init_transcriber(TranscriberType.MLX_WHISPER, MLXWhisperTranscriber, model_size=model_size)

# 通用入口
def get_transcriber(
    transcriber_type="fast-whisper",
    model_size=None,
    device="cuda",
    provider_id=None,
    model=None,
):
    """
    获取指定类型的转录器实例

    参数:
        transcriber_type: 支持 "fast-whisper", "mlx-whisper", "bcut", "kuaishou", "groq", "openai-compatible"
        model_size: 模型大小，适用于 whisper 类；未提供时才读取环境变量默认值
        device: 设备类型（如 cuda / cpu），仅 whisper 使用

    返回:
        对应类型的转录器实例
    """
    logger.info(f'请求转录器类型: {transcriber_type}')

    try:
        transcriber_enum = TranscriberType(transcriber_type)
    except ValueError:
        logger.warning(f'未知转录器类型 "{transcriber_type}"，默认使用 fast-whisper')
        transcriber_enum = TranscriberType.FAST_WHISPER

    # The explicit value normally comes from the persisted frontend setting and
    # must take precedence over Docker's startup default.
    whisper_model_size = model_size or os.environ.get("WHISPER_MODEL_SIZE", "base")

    if transcriber_enum == TranscriberType.FAST_WHISPER:
        return get_whisper_transcriber(whisper_model_size, device=device)

    elif transcriber_enum == TranscriberType.MLX_WHISPER:
        if not MLX_WHISPER_AVAILABLE:
            raise RuntimeError(
                "MLX Whisper 不可用：需要 macOS 平台并安装 mlx_whisper 包 (pip install mlx_whisper)。"
                "请在「音频转写配置」页面切换到其他转写引擎。"
            )
        return get_mlx_whisper_transcriber(whisper_model_size)

    elif transcriber_enum == TranscriberType.BCUT:
        return get_bcut_transcriber()

    elif transcriber_enum == TranscriberType.KUAISHOU:
        return get_kuaishou_transcriber()

    elif transcriber_enum == TranscriberType.GROQ:
        return get_groq_transcriber()

    elif transcriber_enum == TranscriberType.OPENAI_COMPATIBLE:
        return get_openai_compatible_transcriber(
            provider_id=provider_id or os.environ.get("TRANSCRIBER_PROVIDER_ID", "openai"),
            model=model or os.environ.get("TRANSCRIBER_MODEL", "whisper-1"),
        )

    elif transcriber_enum == TranscriberType.QWEN:
        return get_qwen_transcriber(
            provider_id=provider_id or os.environ.get("TRANSCRIBER_PROVIDER_ID", "qwen"),
            model=model or os.environ.get("TRANSCRIBER_MODEL", "qwen3-asr-flash-realtime"),
        )

    elif transcriber_enum == TranscriberType.DOUBAO:
        return get_doubao_transcriber(
            provider_id=provider_id or os.environ.get("TRANSCRIBER_PROVIDER_ID", "volcengine"),
            model=model or os.environ.get("TRANSCRIBER_MODEL", "bigmodel"),
        )

    # fallback
    logger.warning(f'未识别转录器类型 "{transcriber_type}"，使用 fast-whisper 作为默认')
    return get_whisper_transcriber(whisper_model_size, device=device)
