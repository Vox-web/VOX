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
        "Translate as faithfully as possible to the speaker's intended meaning. "
        "Correct only obvious ASR mistakes when the intended meaning is highly clear from context. "
        "If the source is unclear, fragmented, noisy, or ambiguous, stay close to the original rather than guessing. "
        "If a phrase is incomplete, keep it incomplete in translation instead of inventing a polished meaning. "
        "Preserve the speaker's tone, intent, emotional color, and sentence type. "
        "Do not make the translation more literary, formal, or verbose than the original. "
        "Prefer short, natural, speakable phrasing that sounds good in TTS. "
        "Use punctuation that improves speech rhythm and intonation. "
        "Keep names, places, brands, numbers, and technical terms accurate. "
        "Remove only meaningless noise that harms clarity. "
        "Do not summarize, explain, answer, censor, or rephrase beyond what is needed for accurate translation. "
        "Output ONLY the final translation. "
        "No explanations. "
        "No quotes. "
        "No alternatives. "
        "If the input is a single word, translate only that word."
    )

    def __init__(self, cache_size: int = 50, context_size: int = 5):
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
        # Один и тот же язык — возвращаем как есть
        if source_lang == target_lang:
            return text

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