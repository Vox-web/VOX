"""
VOX — Streaming транскрипция через Deepgram Nova-2

Заменяет Whisper + Silero VAD.
Преимущества:
  - Реальный стриминг (текст появляется по мере речи)
  - Задержка ~300мс вместо 3-5с
  - Ноль нагрузки на CPU (всё на стороне Deepgram)
  - Нет torch/whisper зависимостей (−500MB)

Pipeline:
  Аудио чанк (float32) → int16 → Deepgram WebSocket → interim/final results
"""

import os
import json
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import websockets

logger = logging.getLogger("vox.transcriber")

SAMPLE_RATE = 16000


# ---------------------------------------------------------------------------
# Результат транскрипции (совместим со старым интерфейсом)
# ---------------------------------------------------------------------------
@dataclass
class TranscriptResult:
    """Результат транскрипции."""
    text: str
    is_final: bool        # True = конец фразы, можно переводить
    language: str         # "en", "de", "uk", ...
    confidence: float     # 0.0 — 1.0


# ---------------------------------------------------------------------------
# Deepgram Streaming Transcriber
# ---------------------------------------------------------------------------
class DeepgramTranscriber:
    """
    Потоковый транскрайбер через Deepgram Nova-2.

    Управляет WebSocket-соединением с Deepgram API.
    Принимает PCM float32 аудио, конвертирует в int16,
    отправляет в Deepgram, получает результаты в asyncio.Queue.

    Использование:
        dg = DeepgramTranscriber()
        await dg.start(language="ru")
        await dg.send_audio(pcm_float32_bytes)
        result = await dg.results.get()  # TranscriptResult
        await dg.stop()
    """

    def __init__(self):
        self.api_key = os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            logger.warning("⚠️ DEEPGRAM_API_KEY не задан! Транскрипция недоступна.")

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._language: Optional[str] = None
        self._finals_buffer: list[str] = []

        # Очередь результатов — читается обработчиком в main.py
        self.results: asyncio.Queue[TranscriptResult] = asyncio.Queue()

    @property
    def is_active(self) -> bool:
        """Активно ли соединение с Deepgram."""
        return self._ws is not None and not self._ws.closed

    async def start(self, language: Optional[str] = None):
        """
        Открыть новую сессию транскрипции.

        Args:
            language: Код языка ("en", "ru", ...) или None для автоопределения
        """
        await self.stop()

        if not self.api_key:
            logger.error("❌ Невозможно запустить Deepgram без API ключа")
            return

        # Очищаем очередь и буфер
        self._finals_buffer = []
        while not self.results.empty():
            try:
                self.results.get_nowait()
            except asyncio.QueueEmpty:
                break

        self._language = language

        # Параметры Deepgram
        params = [
            "model=nova-2",
            "interim_results=true",
            "utterance_end_ms=1000",
            "endpointing=150",
            "encoding=linear16",
            f"sample_rate={SAMPLE_RATE}",
            "channels=1",
            "punctuate=true",
        ]

        if language:
            params.append(f"language={language}")
        else:
            # Streaming не поддерживает detect_language=true
            # Nova-2 поддерживает language=multi для авто-определения
            params.append("language=multi")

        url = f"wss://api.deepgram.com/v1/listen?{'&'.join(params)}"

        max_retries = 5
        for attempt in range(max_retries):
            try:
                logger.info(f"🔗 Deepgram URL: ...?{'&'.join(params)}" +
                            (f" (попытка {attempt+1}/{max_retries})" if attempt > 0 else ""))
                self._ws = await websockets.connect(
                    url,
                    extra_headers={"Authorization": f"Token {self.api_key}"},
                    ping_interval=20,
                    close_timeout=5,
                )
                self._receive_task = asyncio.create_task(self._receive_loop())
                logger.info(f"🎤 Deepgram сессия открыта (язык: {language or 'auto'})")
                return  # Успех — выходим
            except Exception as e:
                self._ws = None
                if attempt < max_retries - 1:
                    delay = min(0.5 * (2 ** attempt), 5.0)  # 0.5, 1, 2, 4, 5 сек
                    logger.warning(
                        f"⚠️ Deepgram подключение не удалось (попытка {attempt+1}): {e}. "
                        f"Повтор через {delay:.1f}с..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"❌ Не удалось подключиться к Deepgram после {max_retries} попыток: {e}")
                    logger.error(f"   Проверь DEEPGRAM_API_KEY в .env (начинается с 'dg_...' или длинный hex)")

    async def send_audio(self, pcm_float32_bytes: bytes):
        """
        Отправить аудио чанк в Deepgram.

        Args:
            pcm_float32_bytes: PCM float32 байты из браузера
        """
        if not self.is_active:
            return

        try:
            audio = np.frombuffer(pcm_float32_bytes, dtype=np.float32)
            int16_data = (audio * 32767).clip(-32768, 32767).astype(np.int16)
            await self._ws.send(int16_data.tobytes())
        except websockets.ConnectionClosed:
            logger.warning("⚠️ Deepgram соединение закрыто при отправке")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки аудио: {e}")

    async def stop(self):
        """Закрыть сессию Deepgram."""
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except (asyncio.CancelledError, Exception):
                pass
            self._receive_task = None

        if self._ws and not self._ws.closed:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass

        self._ws = None
        self._finals_buffer = []

    async def _receive_loop(self):
        """
        Фоновая задача: читает результаты из Deepgram WebSocket.

        Deepgram присылает:
        - interim (is_final=false): предварительный текст
        - final (is_final=true): подтверждённый фрагмент
        - speech_final=true: конец фразы (пауза), можно переводить

        Стратегия:
        - interim → отправляем preview (is_final=False)
        - is_final без speech_final → накапливаем, отправляем preview
        - speech_final → собираем буфер, отправляем финал
        """
        try:
            async for raw_msg in self._ws:
                data = json.loads(raw_msg)

                if data.get("type") != "Results":
                    continue

                channel = data.get("channel", {})
                alternatives = channel.get("alternatives", [])
                if not alternatives:
                    continue

                alt = alternatives[0]
                text = alt.get("transcript", "").strip()
                confidence = alt.get("confidence", 0.0)
                is_final = data.get("is_final", False)
                speech_final = data.get("speech_final", False)

                # Определяем язык (для auto-detect)
                lang = self._language
                if not lang:
                    detected = channel.get("detected_language")
                    if detected:
                        lang = detected

                if not text:
                    continue

                if not is_final:
                    # Interim: накопленные finals + текущий interim
                    full_text = " ".join(self._finals_buffer + [text])
                    await self.results.put(TranscriptResult(
                        text=full_text,
                        is_final=False,
                        language=lang or "unknown",
                        confidence=confidence,
                    ))

                elif is_final:
                    self._finals_buffer.append(text)

                    if speech_final:
                        # Конец фразы
                        full_text = " ".join(self._finals_buffer)
                        self._finals_buffer = []

                        logger.info(f"📝 [{lang or '?'}] {full_text}")

                        await self.results.put(TranscriptResult(
                            text=full_text,
                            is_final=True,
                            language=lang or "unknown",
                            confidence=confidence,
                        ))
                    else:
                        # Частичный final — показываем как preview
                        full_text = " ".join(self._finals_buffer)
                        await self.results.put(TranscriptResult(
                            text=full_text,
                            is_final=False,
                            language=lang or "unknown",
                            confidence=confidence,
                        ))

        except websockets.ConnectionClosed:
            logger.info("🔇 Deepgram соединение закрыто")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Deepgram ошибка приёма: {e}", exc_info=True)