from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    kokoro_model_path: str = "data/models/kokoro-v1.0.onnx"
    kokoro_voices_path: str = "data/models/voices-v1.0.bin"
    tts_workers: int = 1
    musicgen_model: str = "facebook/musicgen-small"
    host: str = "0.0.0.0"
    port: int = 8000

    base_dir: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = base_dir / "data"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
