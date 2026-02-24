from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    tts_model: str = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
    tts_workers: int = 1
    musicgen_model: str = "facebook/musicgen-small"
    host: str = "0.0.0.0"
    port: int = 8000

    base_dir: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = base_dir / "data"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
