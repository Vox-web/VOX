"""
VOX — Синтез речи (Text-to-Speech)

Трёхступенчатый fallback:
1. OpenAI gpt-4o-mini-tts — основной, более живой голос
2. OpenAI tts-1 — резервный OpenAI TTS
3. edge-tts (Microsoft) — последний бесплатный fallback
"""

import os
import io
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("vox.tts")


class TTSEngine:
    """
    TTS движок с трёхступенчатым fallback.

    Порядок попыток:
    1) gpt-4o-mini-tts
    2) tts-1
    3) edge-tts
    """

    OPENAI_LIVE_INSTRUCTIONS = (
        "Speak naturally, warmly, and conversationally. "
        "Use smooth pacing, subtle expressiveness, and human-like intonation. "
        "Do not sound robotic, overexcited, or theatrical."
    )

    # Карта голосов: язык → {openai_voice, edge_voice}
    VOICE_MAP = {
        "uk": {"openai": "nova",  "edge": "uk-UA-PolinaNeural"},
        "ru": {"openai": "nova",  "edge": "ru-RU-SvetlanaNeural"},
        "en": {"openai": "alloy", "edge": "en-US-AriaNeural"},
        "de": {"openai": "echo",  "edge": "de-DE-KatjaNeural"},
        "pl": {"openai": "nova",  "edge": "pl-PL-ZofiaNeural"},
        "fr": {"openai": "nova",  "edge": "fr-FR-DeniseNeural"},
        "zh": {"openai": "nova",  "edge": "zh-CN-XiaoxiaoNeural"},
        "es": {"openai": "nova",  "edge": "es-ES-ElviraNeural"},
        "it": {"openai": "nova",  "edge": "it-IT-ElsaNeural"},
        "pt": {"openai": "nova",  "edge": "pt-BR-FranciscaNeural"},
        "ja": {"openai": "nova",  "edge": "ja-JP-NanamiNeural"},
        "ko": {"openai": "nova",  "edge": "ko-KR-SunHiNeural"},
        "ar": {"openai": "nova",  "edge": "ar-SA-ZariyahNeural"},
        "tr": {"openai": "nova",  "edge": "tr-TR-EmelNeural"},
        "hi": {"openai": "nova",  "edge": "hi-IN-SwaraNeural"},
    }

    def __init__(self):
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            self.openai_client = OpenAI(api_key=api_key)
            logger.info("✅ OpenAI TTS готов")
        else:
            self.openai_client = None
            logger.warning("⚠️ OPENAI_API_KEY не задан, используется только edge-tts")

    def _get_voices(self, lang: str) -> dict:
        """Получить голоса для языка."""
        return self.VOICE_MAP.get(lang, self.VOICE_MAP["en"])

    def synthesize(self, text: str, lang: str) -> Optional[bytes]:
        """
        Синтезировать речь.

        Args:
            text: Текст для озвучки
            lang: Код языка ("uk", "en", ...)

        Returns:
            MP3 bytes или None при ошибке
        """
        text = text.strip()
        if not text:
            return None

        voices = self._get_voices(lang)

        # Попытка 1: OpenAI gpt-4o-mini-tts
        if self.openai_client:
            try:
                audio = self._openai_tts_gpt4o_mini(text, voices["openai"])
                if audio:
                    logger.info(f"🔊 OpenAI gpt-4o-mini-tts [{lang}]: {len(audio)} байт")
                    return audio
                logger.warning(
                    f"⚠️ OpenAI gpt-4o-mini-tts [{lang}]: пустой ответ, переключаюсь на tts-1"
                )
            except Exception as e:
                logger.warning(f"⚠️ OpenAI gpt-4o-mini-tts ошибка: {e}, переключаюсь на tts-1")

            # Попытка 2: OpenAI tts-1
            try:
                audio = self._openai_tts_tts1(text, voices["openai"])
                if audio:
                    logger.info(f"🔊 OpenAI tts-1 [{lang}]: {len(audio)} байт")
                    return audio
                logger.warning(
                    f"⚠️ OpenAI tts-1 [{lang}]: пустой ответ, переключаюсь на edge-tts"
                )
            except Exception as e:
                logger.warning(f"⚠️ OpenAI tts-1 ошибка: {e}, переключаюсь на edge-tts")

        # Попытка 3: edge-tts
        try:
            audio = self._edge_tts_sync(text, voices["edge"])
            if audio:
                logger.info(f"🔊 edge-tts [{lang}]: {len(audio)} байт")
                return audio
            logger.warning(f"⚠️ edge-tts [{lang}]: пустой ответ")
        except Exception as e:
            logger.error(f"❌ edge-tts тоже упал: {e}")

        logger.error(f"❌ TTS [{lang}]: все движки не дали результата для '{text[:30]}...'")
        return None

    def _openai_tts_gpt4o_mini(self, text: str, voice: str) -> Optional[bytes]:
        """Синтез через OpenAI gpt-4o-mini-tts."""
        response = self.openai_client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice=voice,
            input=text,
            instructions=self.OPENAI_LIVE_INSTRUCTIONS,
            response_format="mp3",
        )
        audio_bytes = response.content
        logger.debug(f"🔊 OpenAI gpt-4o-mini-tts: {len(audio_bytes)} байт")
        return audio_bytes if audio_bytes else None

    def _openai_tts_tts1(self, text: str, voice: str) -> Optional[bytes]:
        """Синтез через OpenAI tts-1."""
        response = self.openai_client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text,
            response_format="mp3",
            speed=1.0,
        )
        audio_bytes = response.content
        logger.debug(f"🔊 OpenAI tts-1: {len(audio_bytes)} байт")
        return audio_bytes if audio_bytes else None

    def _edge_tts_sync(self, text: str, voice: str) -> Optional[bytes]:
        """
        Синтез через edge-tts (Microsoft, бесплатный).
        edge-tts — async библиотека, оборачиваем в sync.
        """
        import edge_tts

        async def _generate():
            communicate = edge_tts.Communicate(text, voice)
            buffer = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buffer.write(chunk["data"])
            return buffer.getvalue()

        loop = asyncio.new_event_loop()
        try:
            audio_bytes = loop.run_until_complete(_generate())
            logger.debug(f"🔊 edge-tts: {len(audio_bytes)} байт")
            return audio_bytes if audio_bytes else None
        finally:
            loop.close()

    async def synthesize_parallel(self, translations: dict[str, str]) -> dict[str, bytes]:
        """
        Параллельный синтез для нескольких языков.
        Используется в режиме Web-комнаты.

        Args:
            translations: {lang_code: translated_text}

        Returns:
            {lang_code: mp3_bytes}
        """
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, self.synthesize, text, lang)
            for lang, text in translations.items()
        ]
        results = await asyncio.gather(*tasks)

        audio_map = {}
        for lang, audio in zip(translations.keys(), results):
            if audio:
                audio_map[lang] = audio

        return audio_map
