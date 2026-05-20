"""
video/tts_engine.py
Text-to-Speech: converts narration script → MP3 audio file.
Supports gTTS (Google) and pyttsx3 (offline).
"""

import os
import tempfile
import time
from pathlib import Path
from loguru import logger


class TTSEngine:
    def __init__(self, engine: str = "kokoro", language: str = "en", output_dir: str = "./output"):
        self.engine_name = engine
        self.language = language
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def synthesize(self, text: str, filename: str) -> str:
        """Convert text to speech. Returns path to audio file."""
        out_path = str(self.output_dir / filename)

        if self.engine_name == "gtts":
            return self._gtts(text, out_path)
        elif self.engine_name == "kokoro":
            return self._kokoro(text, out_path)
        elif self.engine_name == "pyttsx3":
            return self._pyttsx3(text, out_path)
        else:
            raise ValueError(f"Unknown TTS engine: {self.engine_name}")

    def synthesize_segments(self, segments: list[dict], base_name: str) -> list[dict]:
        """Synthesize each script segment separately, return list with audio paths."""
        result = []
        for i, seg in enumerate(segments):
            text = seg.get("text", "")
            if not text.strip():
                continue
            fname = f"{base_name}_seg_{i:03d}.mp3"
            audio_path = self.synthesize(text, fname)
            result.append({**seg, "audio_path": audio_path})
            time.sleep(0.3)  # avoid rate limits on gTTS
        return result

    def _gtts(self, text: str, out_path: str) -> str:
        from gtts import gTTS
        tts = gTTS(text=text, lang=self.language, slow=False)
        tts.save(out_path)
        logger.debug(f"gTTS saved: {out_path}")
        return out_path

    def _pyttsx3(self, text: str, out_path: str) -> str:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 165)   # WPM
        engine.setProperty("volume", 1.0)
        # Try to pick a good voice
        voices = engine.getProperty("voices")
        for v in voices:
            if "english" in v.name.lower() or "en_us" in v.id.lower():
                engine.setProperty("voice", v.id)
                break
        engine.save_to_file(text, out_path)
        engine.runAndWait()
        logger.debug(f"pyttsx3 saved: {out_path}")
        return out_path

    def _kokoro(self, text: str, out_path: str) -> str:
        import soundfile as sf
        from kokoro_onnx import Kokoro
        
        # Look for models/kokoro-v0_19.onnx
        model_path = os.path.join("models", "kokoro-v0_19.onnx")
        voices_path = os.path.join("models", "voices.json")
        
        if not os.path.exists(model_path):
            logger.warning("Kokoro ONNX model not found. Run setup_models.py first. Falling back to gTTS.")
            return self._gtts(text, out_path)
            
        kokoro = Kokoro(model_path, voices_path)
        # Choose a reliable, highly-rated English voice
        voice_id = "af_sarah" if self.language == "en" else "af_sarah"
        
        samples, sample_rate = kokoro.create(text, voice=voice_id, speed=1.0, lang="en-us")
        sf.write(out_path, samples, sample_rate)
        
        logger.debug(f"Kokoro saved: {out_path}")
        return out_path
