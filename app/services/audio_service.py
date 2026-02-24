from pathlib import Path
from pydub import AudioSegment

from app.storage import audio_dir, music_dir, exports_dir


def _find_music_file(music_id: str) -> Path | None:
    mdir = music_dir()
    for f in mdir.iterdir():
        if f.stem == music_id and f.suffix != ".json":
            return f
    return None


def concat_speech_chunks(paper_id: str) -> AudioSegment:
    """Concatenate all speech chunks for a paper into one AudioSegment."""
    chunk_dir = audio_dir() / paper_id
    files = sorted(chunk_dir.glob("chunk_*.wav"))
    if not files:
        raise FileNotFoundError(f"No audio chunks found for paper {paper_id}")

    combined = AudioSegment.empty()
    for f in files:
        combined += AudioSegment.from_wav(str(f))
    return combined


def mix_audio(
    paper_id: str,
    music_id: str | None = None,
    speech_volume: float = 1.0,
    music_volume: float = 0.3,
) -> Path:
    """Mix speech with optional background music and export as MP3."""
    speech = concat_speech_chunks(paper_id)

    # Apply speech volume (convert linear 0-1 to dB)
    if speech_volume != 1.0:
        db_change = 20 * __import__("math").log10(max(speech_volume, 0.01))
        speech = speech + db_change

    if music_id:
        music_path = _find_music_file(music_id)
        if music_path:
            music = AudioSegment.from_file(str(music_path))

            # Loop music to match speech length
            if len(music) < len(speech):
                repeats = (len(speech) // len(music)) + 1
                music = music * repeats
            music = music[: len(speech)]

            # Apply music volume
            if music_volume != 1.0:
                db_change = 20 * __import__("math").log10(max(music_volume, 0.01))
                music = music + db_change

            # Overlay
            speech = speech.overlay(music)

    out_dir = exports_dir()
    output_path = out_dir / f"{paper_id}_mixed.mp3"
    speech.export(str(output_path), format="mp3")
    return output_path
