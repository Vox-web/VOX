"""
VOX — Перевод текста через GPT-4o-mini

Модуль отвечает за:
1. Перевод текста с одного языка на другой
2. Кеширование последних переводов
3. Параллельный перевод на несколько языков (для Web-комнаты)
"""

import os
import logging
import asyncio
from collections import OrderedDict

from openai import OpenAI

logger = logging.getLogger("vox.translator")


class Translator:
    """
    Переводчик на базе GPT-4o-mini.

    Работает как синхронный переводчик-профессионал:
    сохраняет стиль речи, не добавляет пояснений.
    """

    SUPPORTED_LANGUAGES = {
        "uk": "Ukrainian",
        "ru": "Russian",
        "en": "English",
        "de": "German",
        "pl": "Polish",
        "fr": "French",
        "zh": "Chinese",
        "es": "Spanish",
        "it": "Italian",
        "pt": "Portuguese",
        "ja": "Japanese",
        "ko": "Korean",
        "ar": "Arabic",
        "tr": "Turkish",
        "hi": "Hindi",
    }

    SYSTEM_PROMPT = (
        "You are a real-time spoken-language interpreter for live speech translation. "
        "Translate from {source} to {target} for immediate TTS playback. "
        "The input comes from automatic speech recognition (ASR) and may contain errors, "
        "mishearings, broken phrases, filler words, or incomplete sentences. "
        "IMPORTANT: Use the recent conversation context aggressively to reconstruct the speaker's "
        "true intent when the ASR output is garbled, phonetically corrupted, or partially missing. "
        "If a phrase is phonetically close to a real word/sentence given the context, correct it confidently. "
        "Preserve the speaker's tone, intent, emotional color, and sentence type. "
        "Do not make the translation more literary, formal, or verbose than the original. "
        "Prefer short, natural, speakable phrasing that sounds good in TTS. "
        "Use punctuation only when it was clearly present in the original speech. "
        "Do NOT add periods, commas or other punctuation that was not in the original. "
        "Preserve the natural spoken rhythm without over-punctuating. "
        "Keep names, places, brands, numbers, and technical terms accurate. "
        "Remove only meaningless noise that harms clarity. "
        "Do not summarize, explain, answer, censor, or rephrase beyond what is needed for accurate translation. "
        "Output ONLY the final translation. "
        "No explanations. "
        "No quotes. "
        "No alternatives. "
        "If the input is a single word, translate only that word."
    )

    # Промпт для коррекции ASR без перевода (source == target)
    ASR_CORRECTION_PROMPT = (
        "You are an ASR (automatic speech recognition) post-processor for live speech. "
        "Your task: reconstruct the speaker's actual words from potentially garbled ASR output. "
        "Language: {lang}. "
        "The input may have: missing letters/syllables, wrong word boundaries, phonetic substitutions, "
        "mixed-up prepositions, or partial words — typical ASR recognition errors at speed. "
        "Use the recent conversation context to understand the topic and confidently restore the intended phrase. "
        "Rules: "
        "- If ASR output makes no sense but is phonetically close to a real phrase, output the real phrase. "
        "- If the meaning is already clear despite minor errors, fix the errors and output clean text. "
        "- Preserve the speaker's natural register, tone, and sentence structure. "
        "- Do NOT invent information not hinted at by the ASR output or context. "
        "- Do NOT add explanations, commentary, or alternatives. "
        "- Output ONLY the corrected phrase, nothing else."
    )

    def __init__(self, cache_size: int = 50, context_size: int = 10):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("⚠️ OPENAI_API_KEY не задан! Перевод будет недоступен.")
            self.client = None
        else:
            self.client = OpenAI(api_key=api_key)
            logger.info("✅ OpenAI клиент создан")

        # LRU-кеш переводов
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_size = cache_size

        # Контекст последних фраз для качественного перевода
        self._context: list[dict] = []  # [{"source": "...", "translation": "..."}]
        self._context_size = context_size

    def clear_context(self):
        """Сброс контекста разговора (при смене спикера/сессии)."""
        self._context = []
        logger.debug("🧹 Контекст переводчика сброшен")

    def _cache_key(self, text: str, source: str, target: str) -> str:
        return f"{source}:{target}:{text.lower().strip()}"

    def correct_asr(self, text: str, lang: str) -> str:
        """
        Корректировать ASR-ошибки без перевода (source == target).

        Использует контекст разговора чтобы восстановить искажённые фразы.
        Например: "Ка сегодня сходили у врачу?" → "Как сегодня сходили к врачу?"

        Args:
            text: Сырой ASR текст
            lang: Код языка ("ru", "uk", "en", ...)

        Returns:
            Исправленный текст. При ошибке — оригинальный текст.
        """
        if self.client is None:
            return text

        # Если текст выглядит нормально (длиннее 3 слов) — всё равно
        # прогоняем через коррекцию с контекстом
        lang_name = self.SUPPORTED_LANGUAGES.get(lang, lang)

        try:
            messages = [
                {
                    "role": "system",
                    "content": self.ASR_CORRECTION_PROMPT.format(lang=lang_name),
                }
            ]

            # Контекст последних фраз — ключевой сигнал для восстановления
            if self._context:
                context_lines = [
                    item["source"]
                    for item in self._context[-self._context_size:]
                ]
                messages.append({
                    "role": "system",
                    "content": (
                        "Recent conversation (same speaker, same topic):\n"
                        + "\n".join(f"- {line}" for line in context_lines)
                    ),
                })

            messages.append({"role": "user", "content": text})

            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.1,   # Низкая температура — нужна точность, не креатив
                max_tokens=200,
            )

            corrected = response.choices[0].message.content.strip()

            # Сохраняем оригинальный ASR в контекст (не исправленный),
            # чтобы модель видела паттерн ошибок
            self._context.append({"source": text, "translation": corrected})
            if len(self._context) > self._context_size * 2:
                self._context = self._context[-self._context_size:]

            if corrected != text:
                logger.info(f"🔧 ASR fix [{lang}]: «{text}» → «{corrected}»")
            return corrected

        except Exception as e:
            logger.error(f"❌ ASR correction error: {e}")
            return text

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Перевести текст.

        Args:
            text: Текст для перевода
            source_lang: Код языка источника ("en", "de", ...)
            target_lang: Код целевого языка ("uk", "ru", ...)

        Returns:
            Переведённый текст. При ошибке — оригинальный текст.
        """
        # Один и тот же язык — корректируем ASR вместо перевода
        if source_lang == target_lang:
            return self.correct_asr(text, source_lang)

        # Проверяем кеш
        key = self._cache_key(text, source_lang, target_lang)
        if key in self._cache:
            self._cache.move_to_end(key)
            logger.debug(f"📋 Кеш: {text[:30]}...")
            return self._cache[key]

        # Нет API клиента
        if self.client is None:
            logger.warning("⚠️ OpenAI клиент не инициализирован, возвращаю оригинал")
            return text

        try:
            source_name = self.SUPPORTED_LANGUAGES.get(source_lang, source_lang)
            target_name = self.SUPPORTED_LANGUAGES.get(target_lang, target_lang)

            messages = [
                {
                    "role": "system",
                    "content": self.SYSTEM_PROMPT.format(
                        source=source_name, target=target_name
                    ),
                },
            ]

            # Добавляем контекст последних фраз
            if self._context:
                context_lines = []
                for item in self._context[-self._context_size:]:
                    context_lines.append(
                        f'[{item["source"]}] → [{item["translation"]}]'
                    )
                messages.append({
                    "role": "system",
                    "content": (
                        "Recent conversation context (for reference only, "
                        "do NOT repeat these translations):\n"
                        + "\n".join(context_lines)
                    ),
                })

            messages.append({"role": "user", "content": text})

            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.2,
                max_tokens=300,
            )

            translated = response.choices[0].message.content.strip()

            # Сохраняем в контекст
            self._context.append({"source": text, "translation": translated})
            if len(self._context) > self._context_size * 2:
                self._context = self._context[-self._context_size:]

            # Сохраняем в кеш
            self._cache[key] = translated
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

            logger.info(f"🌐 [{source_lang}→{target_lang}] {text[:30]}... → {translated[:30]}...")
            return translated

        except Exception as e:
            logger.error(f"❌ Ошибка перевода: {e}")
            return text  # Возвращаем оригинал при ошибке

    async def translate_parallel(
        self, text: str, source_lang: str, target_langs: list[str]
    ) -> dict[str, str]:
        """
        Параллельный перевод на несколько языков.
        Используется в режиме Web-комнаты.

        Args:
            text: Текст для перевода
            source_lang: Код языка источника
            target_langs: Список целевых языков

        Returns:
            dict: {lang_code: translated_text}
        """
        # Убираем дубликаты и исходный язык
        langs = [l for l in set(target_langs) if l != source_lang]

        if not langs:
            return {}

        # Запускаем переводы параллельно
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, self.translate, text, source_lang, lang)
            for lang in langs
        ]
        results = await asyncio.gather(*tasks)

        return dict(zip(langs, results))