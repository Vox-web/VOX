"""
VOX — Синтез речи (Text-to-Speech)

Два движка (порядок соответствует реальному коду synthesize()):
1. edge-tts (Microsoft) — ОСНОВНОЙ: бесплатный, быстрый
2. OpenAI TTS (tts-1) — FALLBACK при ошибке/пустом ответе edge-tts
"""

import os
import io
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("vox.tts")


class TTSEngine:
    """
    TTS движок с автоматическим fallback.

    Сначала пытается edge-tts (быстрее, бесплатно), при ошибке — OpenAI TTS.
    """

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

    # Просодия по умолчанию для «живого» звучания.
    # Чуть медленнее нейтрального темпа — убирает ощущение «синхронного тараторящего
    # робота», даёт голосу дышать. Паузы/интонацию edge-tts берёт из пунктуации текста
    # (запятые, …, ?, !), поэтому важно синтезировать фразу целиком, а не по кускам.
    DEFAULT_EDGE_RATE = "-6%"
    DEFAULT_EDGE_PITCH = "+0Hz"
    DEFAULT_EDGE_VOLUME = "+0%"

    def synthesize(
        self,
        text: str,
        lang: str,
        *,
        rate: Optional[str] = None,
        pitch: Optional[str] = None,
        volume: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        Синтезировать речь.

        Args:
            text: Текст для озвучки (лучше передавать целую фразу/абзац, а не
                  отдельные предложения — так сохраняется сквозная интонация и паузы)
            lang: Код языка ("uk", "en", ...)
            rate/pitch/volume: параметры просодии edge-tts (напр. "-6%", "+0Hz", "+0%").
                  None → берутся DEFAULT_EDGE_* для естественного звучания.

        Returns:
            MP3 bytes или None при ошибке
        """
        if not text.strip():
            return None

        voices = self._get_voices(lang)

        edge_rate = rate if rate is not None else self.DEFAULT_EDGE_RATE
        edge_pitch = pitch if pitch is not None else self.DEFAULT_EDGE_PITCH
        edge_volume = volume if volume is not None else self.DEFAULT_EDGE_VOLUME

        # Попытка 1: edge-tts (быстрее и бесплатно)
        try:
            audio = self._edge_tts_sync(
                text, voices["edge"],
                rate=edge_rate, pitch=edge_pitch, volume=edge_volume,
            )
            if audio:
                logger.info(f"🔊 edge-tts [{lang}]: {len(audio)} байт")
                return audio
            else:
                logger.warning(f"⚠️ edge-tts [{lang}]: пустой ответ, переключаюсь на OpenAI TTS")
        except Exception as e:
            logger.warning(f"⚠️ edge-tts ошибка: {e}, переключаюсь на OpenAI TTS")

        # Попытка 2: OpenAI TTS (fallback, платный)
        if self.openai_client:
            try:
                audio = self._openai_tts(text, voices["openai"])
                if audio:
                    logger.info(f"🔊 OpenAI TTS [{lang}]: {len(audio)} байт")
                    return audio
                else:
                    logger.warning(f"⚠️ OpenAI TTS [{lang}]: пустой ответ")
            except Exception as e:
                logger.error(f"❌ OpenAI TTS тоже упал: {e}")

        logger.error(f"❌ TTS [{lang}]: все движки не дали результата для '{text[:30]}...'")
        return None

    def _openai_tts(self, text: str, voice: str) -> Optional[bytes]:
        """Синтез через OpenAI TTS API (fallback)."""
        response = self.openai_client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text,
            response_format="mp3",
            # Естественный темп. Раньше стояло 1.25 — голос «тараторил» и звучал
            # синхронно-машинно. 1.0 даёт живую разговорную подачу.
            speed=1.0,
        )

        # Читаем все байты
        audio_bytes = response.content
        logger.debug(f"🔊 OpenAI TTS: {len(audio_bytes)} байт")
        return audio_bytes

    def _edge_tts_sync(
        self,
        text: str,
        voice: str,
        *,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
    ) -> Optional[bytes]:
        """
        Синтез через edge-tts (Microsoft, бесплатный).
        edge-tts — async библиотека, оборачиваем в sync.

        rate/pitch/volume передаются в Communicate — так голос звучит живее
        (естественный темп с паузами), а не «синхронно-машинно».
        """
        import edge_tts

        async def _generate():
            communicate = edge_tts.Communicate(
                text, voice, rate=rate, pitch=pitch, volume=volume,
            )
            buffer = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buffer.write(chunk["data"])
            return buffer.getvalue()

        # Запускаем async код в новом event loop
        # (потому что вызывается из sync контекста через asyncio.to_thread)
        loop = asyncio.new_event_loop()
        try:
            audio_bytes = loop.run_until_complete(_generate())
            logger.debug(f"🔊 edge-tts: {len(audio_bytes)} байт")
            return audio_bytes if audio_bytes else None
        finally:
            loop.close()

    async def synthesize_parallel(
        self, translations: dict[str, str]
    ) -> dict[str, bytes]:
        """
        Параллельный синтез для нескольких языков.
        Используется в режиме Web-комнаты.

        Args:
            translations: {lang_code: translated_text}

        Returns:
            {lang_code: mp3_bytes}
        """
        loop = asyncio.get_running_loop()
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
