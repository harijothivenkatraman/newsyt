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
        elif self.engine_name == "piper":
            return self._piper(text, out_path)
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

        cache_dir = os.getenv("ML_CACHE_DIR", "./models")

        # Primary location (flat layout)
        model_path = os.path.join(cache_dir, "kokoro-v0_19.onnx")
        voices_path = os.path.join(cache_dir, "voices.json")

        # Fallback: HuggingFace snapshot cache layout (hexgrad/Kokoro-82M)
        if not os.path.exists(model_path):
            hf_model_dir = os.path.join(cache_dir, "models--hexgrad--Kokoro-82M")
            snapshots_dir = os.path.join(hf_model_dir, "snapshots")
            if os.path.isdir(snapshots_dir):
                for snap in os.listdir(snapshots_dir):
                    snap_path = os.path.join(snapshots_dir, snap)
                    candidate = os.path.join(snap_path, "kokoro-v0_19.onnx")
                    if os.path.exists(candidate):
                        model_path = candidate
                        voices_candidate = os.path.join(snap_path, "voices.json")
                        if os.path.exists(voices_candidate):
                            voices_path = voices_candidate
                        break

        if not os.path.exists(model_path):
            logger.warning("Kokoro ONNX model not found. Run setup_models.py first. Falling back to gTTS.")
            return self._gtts(text, out_path)

        if not os.path.exists(voices_path):
            logger.warning("Kokoro voices.json not found. Falling back to gTTS.")
            return self._gtts(text, out_path)

        kokoro = Kokoro(model_path, voices_path)
        # Choose a reliable, highly-rated English voice
        voice_id = "af_sarah" if self.language == "en" else "af_sarah"

        samples, sample_rate = kokoro.create(text, voice=voice_id, speed=1.0, lang="en-us")
        sf.write(out_path, samples, sample_rate)

        logger.debug(f"Kokoro saved: {out_path}")
        return out_path


    def _piper(self, text: str, out_path: str) -> str:
        import subprocess
        
        # Paths
        piper_exe = os.path.join("bin", "piper", "piper.exe")
        model_path = os.path.join("models", "en_US-lessac-medium.onnx")
        
        if not os.path.exists(piper_exe):
            logger.warning("piper.exe not found in bin/piper/. Run setup_models.py. Falling back to gTTS.")
            return self._gtts(text, out_path)
            
        if not os.path.exists(model_path):
            logger.warning("Piper model not found. Falling back to gTTS.")
            return self._gtts(text, out_path)
        
        wav_path = out_path.replace(".mp3", ".wav")
        
        # Run piper.exe
        # Example: echo "Hello world" | piper.exe --model en_US-lessac-medium.onnx --output_file output.wav
        try:
            # We use text as input via stdin
            process = subprocess.Popen(
                [piper_exe, "--model", model_path, "--output_file", wav_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8"
            )
            stdout, stderr = process.communicate(input=text)
            
            if process.returncode != 0:
                logger.error(f"Piper binary failed: {stderr}")
                return self._gtts(text, out_path)
                
            # Convert WAV to MP3 using ffmpeg so other components expecting MP3 don't break
            subprocess.run(["ffmpeg", "-y", "-i", wav_path, out_path], capture_output=True, check=True)
            os.remove(wav_path)
            logger.debug(f"Piper saved: {out_path}")
            return out_path
            
        except Exception as e:
            logger.warning(f"Piper processing failed: {e}. Falling back to gTTS.")
            if os.path.exists(wav_path):
                os.remove(wav_path)
            return self._gtts(text, out_path)
