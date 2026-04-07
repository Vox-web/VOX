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

from audio_utils import resample_audio

logger = logging.getLogger("vox.transcriber")

SAMPLE_RATE = 16000


# ---------------------------------------------------------------------------
# Результат транскрипции (совместим со старым интерфейсом)
# ---------------------------------------------------------------------------
@dataclass
class TranscriptResult:
    """Результат транскрипции."""
    text: str
    is_final: bool        # True = конец фразы (включая таймер-flush)
    language: str         # "en", "de", "uk", ...
    confidence: float     # 0.0 — 1.0
    commit_final: bool = False  # True = безопасно переводить (DG speech_final / UtteranceEnd / stop-flush)
                                # False = таймер-flush (_delayed_flush / _interim_flush) — Solo переводит, Duo/Room ждут


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
        self._keepalive_task: Optional[asyncio.Task] = None
        self._language: Optional[str] = None
        self._finals_buffer: list[str] = []
        self._flush_task: Optional[asyncio.Task] = None  # таймер авто-flush
        self._last_lang: str = "unknown"
        self._last_confidence: float = 0.0

        # Клиентский flush зависших interim'ов (без Finalize — он ломает Deepgram)
        self._pending_interim_text: Optional[str] = None
        self._interim_flush_task: Optional[asyncio.Task] = None

        # Фактическая частота входного потока из браузера.
        # Deepgram получает уже приведённый к 16 кГц поток.
        self._input_sample_rate: int = SAMPLE_RATE
        self._debug_audio_chunks_seen: int = 0
        self._debug_results_seen: int = 0
        self._debug_empty_results_seen: int = 0

        # Очередь результатов — читается обработчиком в main.py
        self.results: asyncio.Queue[TranscriptResult] = asyncio.Queue()

    def set_input_sample_rate(self, sample_rate: Optional[int]):
        """Установить фактическую sample rate входного float32 потока из браузера."""
        try:
            rate = int(sample_rate) if sample_rate else SAMPLE_RATE
        except (TypeError, ValueError):
            rate = SAMPLE_RATE
        if not 8000 <= rate <= 192000:
            rate = SAMPLE_RATE
        self._input_sample_rate = rate
        logger.info(f"🎚️ Deepgram input sample rate set to {self._input_sample_rate} Hz")

    @property
    def is_active(self) -> bool:
        """Активно ли соединение с Deepgram."""
        return self._ws is not None and not self._ws.closed

    async def _delayed_flush(self, delay: float):
        """
        Таймер: якщо після is_final без speech_final пройшло `delay` секунд
        і буфер не порожній — емітуємо превью БЕЗ очищення буфера.
        Буфер навмисно не очищається: щоб при реальному speech_final
        зібрати ПОВНИЙ текст всієї репліки (Solo+Duo+Room коректно).
        commit_final=False — Solo переводить превью, Duo/Room ігнорують.
        """
        try:
            logger.info(f"🧭 [DG TRACE] delayed_flush_sleep {delay}s")
            await asyncio.sleep(delay)
            if self._finals_buffer:
                full_text = " ".join(self._finals_buffer)
                # НЕ очищаємо _finals_buffer — він потрібен для повного commit_final
                logger.info(f"📝 [{self._last_lang}] (flush timer preview) {full_text}")
                await self.results.put(TranscriptResult(
                    text=full_text,
                    is_final=True,
                    commit_final=False,
                    language=self._last_lang,
                    confidence=self._last_confidence,
                ))
        except asyncio.CancelledError:
            logger.info("🧭 [DG TRACE] delayed_flush cancelled")
            pass

    async def _interim_flush(self, delay: float = 2.0):
        """
        Клиентский flush зависших interim'ов.

        Если interim пришёл, но final не появился за `delay` секунд —
        эмитим preview БЕЗ очистки _pending_interim_text.
        Текст намеренно оставляем: stop() и UtteranceEnd подберут его корректно.
        commit_final=False — Solo переводит превью, Duo/Room игнорируют.
        БЕЗ Finalize-команды в Deepgram (GitHub #1035).
        """
        try:
            logger.info(f"🧭 [DG TRACE] interim_flush_sleep {delay}s")
            await asyncio.sleep(delay)
            if self._pending_interim_text:
                text = self._pending_interim_text
                # НЕ очищаємо — stop()/UtteranceEnd підберуть повний текст
                logger.info(f"📝 [{self._last_lang}] (interim flush preview) {text}")
                await self.results.put(TranscriptResult(
                    text=text,
                    is_final=True,
                    commit_final=False,
                    language=self._last_lang,
                    confidence=self._last_confidence,
                ))
        except asyncio.CancelledError:
            pass
        
    async def _keepalive_loop(self, interval: float = 4.0):
        """
        Периодически шлёт Deepgram KeepAlive, чтобы сессия не закрывалась во время тишины.
        Важно: отправляем JSON-строку, то есть text websocket frame.
        """
        try:
            while True:
                await asyncio.sleep(interval)
                if self.is_active:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))
                    logger.info("🧭 [DG TRACE] sent KeepAlive")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"⚠️ Deepgram KeepAlive error: {e}")    

    async def start(
        self,
        language: Optional[str] = None,
        input_sample_rate: Optional[int] = None,
        model: Optional[str] = None,
        endpointing: int = 300,
    ):
        """
        Открыть новую сессию транскрипции.

        Args:
            language:    Код языка ("en", "ru", ...) или None для автоопределения
            endpointing: Пауза (мс) для определения конца фразы.
                         Solo: 300ms (быстрый отклик).
                         Duo/Room: 700ms (не режет речь на коротких паузах).
        """
        await self.stop()

        if not self.api_key:
            logger.error("❌ Невозможно запустить Deepgram без API ключа")
            return

        # Очищаем очередь и буфер
        self._finals_buffer = []
        self._pending_interim_text = None
        if self._interim_flush_task and not self._interim_flush_task.done():
            self._interim_flush_task.cancel()
            self._interim_flush_task = None
        while not self.results.empty():
            try:
                self.results.get_nowait()
            except asyncio.QueueEmpty:
                break

        self._language = language
        if input_sample_rate is not None:
            self.set_input_sample_rate(input_sample_rate)
        self._debug_audio_chunks_seen = 0

        # Параметры Deepgram
        selected_model = model or ("nova-3" if language == "multi" else "nova-2")

        params = [
            f"model={selected_model}",
            "interim_results=true",
            "utterance_end_ms=1500",
            f"endpointing={endpointing}",
            "encoding=linear16",
            f"sample_rate={SAMPLE_RATE}",
            "channels=1",
            "punctuate=true",
        ]

        if language == "multi":
            params.append("language=multi")
        elif language:
            params.append(f"language={language}")
        else:
            # Для streaming auto mode используем multilingual режим
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
                self._keepalive_task = asyncio.create_task(self._keepalive_loop())
                logger.info(f"🎤 Deepgram сессия открыта (язык: {language or 'auto'})")
                logger.info(f"🧭 [DG TRACE] session_start lang={language or 'auto'} input_sr={self._input_sample_rate} target_sr={SAMPLE_RATE}")
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
            if not pcm_float32_bytes:
                return

            if len(pcm_float32_bytes) % 4 != 0:
                trimmed = len(pcm_float32_bytes) - (len(pcm_float32_bytes) % 4)
                if trimmed <= 0:
                    return
                logger.warning(
                    f"⚠️ Аудио чанк некратен float32 ({len(pcm_float32_bytes)} байт), "
                    f"обрезаем до {trimmed}"
                )
                pcm_float32_bytes = pcm_float32_bytes[:trimmed]

            audio = np.frombuffer(pcm_float32_bytes, dtype=np.float32)
            if audio.size == 0:
                return

            if np.any(np.isnan(audio)) or np.any(np.isinf(audio)):
                logger.warning("⚠️ Аудио чанк содержит NaN/Inf — пропускаем")
                return

            input_sr = self._input_sample_rate or SAMPLE_RATE
            raw_samples = int(audio.size)
            rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0

            if input_sr != SAMPLE_RATE:
                audio = resample_audio(audio, input_sr, SAMPLE_RATE)

            audio = np.clip(audio, -1.0, 1.0)
            int16_data = (audio * 32767.0).astype(np.int16)

            self._debug_audio_chunks_seen += 1
            if self._debug_audio_chunks_seen <= 5 or self._debug_audio_chunks_seen % 250 == 0:
                approx_ms_in = (raw_samples / input_sr) * 1000.0 if input_sr else 0.0
                approx_ms_out = (len(audio) / SAMPLE_RATE) * 1000.0 if len(audio) else 0.0
                logger.info(
                    "🎚️ Deepgram chunk #%s: in_sr=%s raw_samples=%s out_samples=%s "
                    "rms=%.5f peak=%.5f in_ms=%.1f out_ms=%.1f",
                    self._debug_audio_chunks_seen,
                    input_sr,
                    raw_samples,
                    len(audio),
                    rms,
                    peak,
                    approx_ms_in,
                    approx_ms_out,
                )

            await self._ws.send(int16_data.tobytes())
        except websockets.ConnectionClosed:
            logger.warning("⚠️ Deepgram соединение закрыто при отправке")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки аудио: {e}")

    async def stop(self):
        """Закрыть сессию Deepgram."""
        logger.info(f"🧭 [DG TRACE] stop_begin active={self.is_active} finals={len(self._finals_buffer)} pending_interim={bool(self._pending_interim_text)} input_sr={self._input_sample_rate}")

        # pending_interim_text уже содержит finals_buffer + последний interim-хвост,
        # поэтому он — наиболее полный источник. finals_buffer берём только если
        # interim не было. Объединять нельзя — будет дубль.
        if self._pending_interim_text:
            full_text = self._pending_interim_text
        elif self._finals_buffer:
            full_text = " ".join(self._finals_buffer)
        else:
            full_text = ""

        self._finals_buffer = []
        self._pending_interim_text = None

        if full_text:
            logger.info(f"📝 [{self._last_lang}] (stop flush) {full_text}")
            await self.results.put(TranscriptResult(
                text=full_text,
                is_final=True,
                commit_final=True,
                language=self._last_lang,
                confidence=self._last_confidence,
            ))

        if self._interim_flush_task and not self._interim_flush_task.done():
            self._interim_flush_task.cancel()
            self._interim_flush_task = None

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except (asyncio.CancelledError, Exception):
                pass
            self._receive_task = None

        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except (asyncio.CancelledError, Exception):
                pass
            self._keepalive_task = None    

        if self._ws and not self._ws.closed:
            try:
                logger.info("🧭 [DG TRACE] send CloseStream")
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
                logger.info("🧭 [DG TRACE] websocket closed")
            except Exception:
                pass

        self._ws = None
        self._finals_buffer = []
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            self._flush_task = None
        logger.info("🧭 [DG TRACE] stop_end")

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

                msg_type = data.get("type")
                self._debug_results_seen += 1
                if msg_type != 'Results' or self._debug_results_seen <= 10 or self._debug_results_seen % 25 == 0:
                    logger.info("🧭 [DG TRACE] recv #%s type=%s", self._debug_results_seen, msg_type)

                # UtteranceEnd: Deepgram обнаружил тишину после utterance_end_ms.
                # pending_interim_text уже содержит finals_buffer + хвост,
                # поэтому он приоритетнее. finals_buffer берём только если interim нет.
                if msg_type == "UtteranceEnd":
                    logger.info("🧭 [DG TRACE] UtteranceEnd finals=%s pending_interim=%s", len(self._finals_buffer), bool(self._pending_interim_text))
                    if self._flush_task and not self._flush_task.done():
                        logger.info("🧭 [DG TRACE] cancel delayed flush due UtteranceEnd")
                        self._flush_task.cancel()
                        self._flush_task = None
                    if self._interim_flush_task and not self._interim_flush_task.done():
                        logger.info("🧭 [DG TRACE] cancel interim flush due UtteranceEnd")
                        self._interim_flush_task.cancel()
                        self._interim_flush_task = None

                    if self._pending_interim_text:
                        full_text = self._pending_interim_text
                    elif self._finals_buffer:
                        full_text = " ".join(self._finals_buffer)
                    else:
                        full_text = ""

                    self._finals_buffer = []
                    self._pending_interim_text = None

                    if full_text:
                        logger.info(f"📝 [{self._last_lang}] (UtteranceEnd) {full_text}")
                        await self.results.put(TranscriptResult(
                            text=full_text,
                            is_final=True,
                            commit_final=True,
                            language=self._last_lang or "unknown",
                            confidence=self._last_confidence,
                        ))
                    continue

                if msg_type != "Results":
                    continue

                channel = data.get("channel", {})
                alternatives = channel.get("alternatives", [])
                if not alternatives:
                    logger.info("🧭 [DG TRACE] Results without alternatives")
                    continue

                alt = alternatives[0]
                text = alt.get("transcript", "").strip()
                confidence = alt.get("confidence", 0.0)
                is_final = data.get("is_final", False)
                speech_final = data.get("speech_final", False)

                # Определяем язык
                if self._language == "multi":
                    # Для multilingual streaming Deepgram возвращает languages[]
                    # и word-level language в alternatives[0]
                    langs = alt.get("languages") or []
                    words = alt.get("words") or []

                    if langs:
                        lang = langs[0]
                    elif words:
                        counts = {}
                        for w in words:
                            wl = w.get("language")
                            if wl:
                                counts[wl] = counts.get(wl, 0) + 1
                        lang = max(counts, key=counts.get) if counts else "unknown"
                    else:
                        lang = "unknown"
                else:
                    lang = self._language
                    if not lang:
                        detected = channel.get("detected_language")
                        if detected:
                            lang = detected

                if not text:
                    self._debug_empty_results_seen += 1
                    if self._debug_empty_results_seen <= 10 or self._debug_empty_results_seen % 25 == 0:
                        logger.info("🧭 [DG TRACE] empty transcript is_final=%s speech_final=%s lang=%s empty_count=%s", is_final, speech_final, lang or 'unknown', self._debug_empty_results_seen)
                    continue

                logger.info("🧭 [DG TRACE] Results text=%r is_final=%s speech_final=%s lang=%s conf=%.3f finals_buffer=%s", text[:120], is_final, speech_final, lang or 'unknown', confidence, len(self._finals_buffer))

                if not is_final:
                    # Interim: накопленные finals + текущий interim
                    full_text = " ".join(self._finals_buffer + [text])
                    await self.results.put(TranscriptResult(
                        text=full_text,
                        is_final=False,
                        language=lang or "unknown",
                        confidence=confidence,
                    ))
                    # Запоминаем interim и запускаем таймер клиентского flush (2с)
                    self._pending_interim_text = full_text
                    self._last_lang = lang or "unknown"
                    self._last_confidence = confidence
                    if self._interim_flush_task and not self._interim_flush_task.done():
                        logger.info("🧭 [DG TRACE] cancel previous interim flush timer")
                        self._interim_flush_task.cancel()
                    logger.info("🧭 [DG TRACE] schedule interim flush timer 2.0s text=%r", full_text[:120])
                    self._interim_flush_task = asyncio.create_task(self._interim_flush(2.0))

                elif is_final:
                    # Final пришёл — отменяем interim flush
                    self._pending_interim_text = None
                    if self._interim_flush_task and not self._interim_flush_task.done():
                        logger.info("🧭 [DG TRACE] cancel interim flush due final result")
                        self._interim_flush_task.cancel()
                        self._interim_flush_task = None

                    self._finals_buffer.append(text)
                    self._last_lang = lang or "unknown"
                    self._last_confidence = confidence

                    if speech_final:
                        # Конец фразы — скасуємо таймер і флашимо одразу
                        if self._flush_task and not self._flush_task.done():
                            logger.info("🧭 [DG TRACE] cancel delayed flush due speech_final")
                            self._flush_task.cancel()
                            self._flush_task = None

                        full_text = " ".join(self._finals_buffer)
                        self._finals_buffer = []

                        logger.info(f"📝 [{lang or '?'}] {full_text}")

                        await self.results.put(TranscriptResult(
                            text=full_text,
                            is_final=True,
                            commit_final=True,
                            language=lang or "unknown",
                            confidence=confidence,
                        ))
                    else:
                        # Частичный final — показываем как preview и запускаємо таймер
                        full_text = " ".join(self._finals_buffer)
                        await self.results.put(TranscriptResult(
                            text=full_text,
                            is_final=False,
                            language=lang or "unknown",
                            confidence=confidence,
                        ))
                        # Скинути попередній таймер і запустити новий (800ms)
                        if self._flush_task and not self._flush_task.done():
                            logger.info("🧭 [DG TRACE] cancel previous delayed flush timer")
                            self._flush_task.cancel()
                        logger.info("🧭 [DG TRACE] schedule delayed flush 0.8s buffer=%r", full_text[:120])
                        self._flush_task = asyncio.create_task(self._delayed_flush(0.8))

        except websockets.ConnectionClosed:
            logger.info("🔇 Deepgram соединение закрыто")
            logger.info("🧭 [DG TRACE] receive_loop connection closed")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Deepgram ошибка приёма: {e}", exc_info=True)