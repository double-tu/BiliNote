import os

from app.transcriber.openai_compatible import OpenAICompatibleTranscriber


class GroqTranscriber(OpenAICompatibleTranscriber):
    """Groq 的 Whisper 文件接口，复用 OpenAI 兼容协议和本地分片逻辑。"""

    def __init__(self):
        super().__init__(
            provider_id="groq",
            model=os.getenv("GROQ_TRANSCRIBER_MODEL", "whisper-large-v3-turbo"),
            max_upload_mb=18,
        )
