"""
audio_pipeline.py — handles speech-to-text (Whisper) and text-to-speech (OpenAI TTS).

Normalizes incoming audio to 16 kHz / 16-bit / mono WAV before sending to Whisper.
Returns 16 kHz / 16-bit / mono WAV from TTS for the ESP32 to play directly.
"""

import io
import os
import logging
from openai import OpenAI
from pydub import AudioSegment

log = logging.getLogger(__name__)


class AudioPipeline:
    def __init__(self):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set")
        self.client = OpenAI(api_key=api_key)
        self.stt_model = os.environ.get("STT_MODEL", "whisper-1")
        self.tts_model = os.environ.get("TTS_MODEL", "tts-1")
        self.tts_voice = os.environ.get("TTS_VOICE", "nova")

    def _normalize_to_16k_mono_wav(self, raw_bytes: bytes) -> bytes:
        """Force any incoming audio to 16 kHz, 16-bit, mono WAV."""
        seg = AudioSegment.from_file(io.BytesIO(raw_bytes))
        seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        out = io.BytesIO()
        seg.export(out, format="wav")
        return out.getvalue()

    def transcribe(self, raw_bytes: bytes) -> str:
        norm = self._normalize_to_16k_mono_wav(raw_bytes)
        buf = io.BytesIO(norm)
        buf.name = "audio.wav"
        response = self.client.audio.transcriptions.create(
            model=self.stt_model,
            file=buf,
        )
        return (response.text or "").strip()

    def synthesize(self, text: str) -> bytes:
        """Generate spoken reply audio. Returns 16 kHz mono PCM WAV."""
        # OpenAI TTS doesn't directly emit 16k mono WAV; ask for wav and downsample.
        response = self.client.audio.speech.create(
            model=self.tts_model,
            voice=self.tts_voice,
            input=text,
            response_format="wav",
        )
        raw = response.content
        return self._normalize_to_16k_mono_wav(raw)
