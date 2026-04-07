import logging
import re
import time
from typing import Any

logger = logging.getLogger("vox.solo_semantic")


class SoloSemanticBuffer:
    """
    Solo-режим: копит финальные куски ASR, удерживает обрезанный хвост,
    и отдаёт в GPT/TTS только более связные, завершённые части.
    """

    _CARRY_WORDS = {
        # DE
        "und", "oder", "aber", "denn", "weil", "dass", "ob", "wenn",
        "mit", "zu", "von", "im", "am", "an", "auf", "für", "nach",
        # RU
        "и", "или", "но", "а", "что", "чтобы", "если", "когда",
        "в", "во", "на", "к", "ко", "с", "со", "у", "по", "из", "за", "для",
        # UK
        "і", "або", "але", "а", "що", "щоб", "якщо", "коли",
        "в", "у", "на", "до", "з", "зі", "по", "із", "для",
        # EN
        "and", "or", "but", "because", "that", "if", "when",
        "to", "of", "for", "with", "in", "on", "at", "from",
    }

    _TRAILING_PUNCT = ".,!?:;…)]}\"'»"

    def __init__(
        self,
        translator: Any,
        flush_after_sec: float = 5.0,
        hard_flush_sec: float = 8.0,
        idle_reset_sec: float = 30.0,
        min_ready_words: int = 4,
        keep_tail_words: int = 6,
    ):
        self.translator = translator
        self.flush_after_sec = flush_after_sec
        self.hard_flush_sec = hard_flush_sec
        self.idle_reset_sec = idle_reset_sec
        self.min_ready_words = min_ready_words
        self.keep_tail_words = keep_tail_words

        self._parts: list[str] = []
        self._carry: str = ""
        self._opened_at: float | None = None
        self._last_seen_at: float | None = None
        self._lang_pair: tuple[str, str] | None = None

    def reset(self):
        self._parts = []
        self._carry = ""
        self._opened_at = None
        self._last_seen_at = None
        self._lang_pair = None

    def push_and_maybe_flush(self, text: str, source_lang: str, target_lang: str) -> list[dict[str, str]]:
        text = self._normalize(text)
        if not text:
            return []

        now = time.monotonic()

        if self._lang_pair and self._lang_pair != (source_lang, target_lang):
            logger.info(
                "🧹 SoloSemanticBuffer: language pair changed %s -> %s, reset",
                self._lang_pair,
                (source_lang, target_lang),
            )
            self.translator.clear_context()
            self.reset()

        if self._last_seen_at and (now - self._last_seen_at) >= self.idle_reset_sec:
            logger.info("🧹 SoloSemanticBuffer: idle reset after %.1fs", now - self._last_seen_at)
            self.translator.clear_context()
            self.reset()

        self._lang_pair = (source_lang, target_lang)
        self._last_seen_at = now

        if self._opened_at is None:
            self._opened_at = now

        self._parts.append(text)
        elapsed = now - self._opened_at

        if elapsed < self.flush_after_sec:
            return []

        force = elapsed >= self.hard_flush_sec
        return self._flush(source_lang, target_lang, force=force)

    def flush_all(self, source_lang: str, target_lang: str) -> list[dict[str, str]]:
        return self._flush(source_lang, target_lang, force=True)

    def _flush(self, source_lang: str, target_lang: str, force: bool) -> list[dict[str, str]]:
        full = self._normalize(" ".join(part for part in [self._carry, *self._parts] if part))
        self._parts = []
        self._opened_at = None

        if not full:
            self._carry = ""
            return []

        out: list[dict[str, str]] = []

        while full:
            ready, tail = self._split_ready_and_tail(full, force=force)

            if not ready:
                self._carry = full
                break

            translated = self._translate_ready(ready, source_lang, target_lang)
            out.append(
                {
                    "source": ready,
                    "translated": translated,
                    "lang_from": source_lang,
                    "lang_to": target_lang,
                }
            )

            full = self._normalize(tail)

            if not force:
                self._carry = full
                break

        if not full:
            self._carry = ""

        return out

    def _translate_ready(self, text: str, source_lang: str, target_lang: str) -> str:
        if source_lang == target_lang:
            return self.translator.correct_asr(text, source_lang)
        return self.translator.translate(text, source_lang, target_lang)

    def _split_ready_and_tail(self, text: str, force: bool = False) -> tuple[str, str]:
        text = self._normalize(text)
        if not text:
            return "", ""

        # 1) Сильная граница: конец предложения.
        strong_matches = list(re.finditer(r'[.!?…]["»)\]]*(?:\s+|$)', text))
        if strong_matches:
            cut = strong_matches[-1].end()
            ready = text[:cut].strip()
            tail = text[cut:].strip()
            ready, tail = self._rebalance_dangling_tail(ready, tail)
            if ready:
                return ready, tail

        # 2) Более мягкая граница: запятая / двоеточие / точка с запятой.
        soft_matches = list(re.finditer(r'[,;:]\s+', text))
        if soft_matches and (force or len(text.split()) >= self.min_ready_words + 4):
            cut = soft_matches[-1].end()
            ready = text[:cut].rstrip(" ,;:")
            tail = text[cut:].strip()
            ready, tail = self._rebalance_dangling_tail(ready, tail)
            if len(ready.split()) >= self.min_ready_words:
                return ready, tail

        words = text.split()

        if force:
            return text, ""

        if len(words) < self.min_ready_words + 3:
            return "", text

        keep = min(self.keep_tail_words, max(3, len(words) // 5))
        ready_words = words[:-keep]
        tail_words = words[-keep:]

        while ready_words and self._is_carry_word(ready_words[-1]):
            tail_words.insert(0, ready_words.pop())

        ready = " ".join(ready_words).strip()
        tail = " ".join(tail_words).strip()

        if len(ready_words) < self.min_ready_words:
            return "", text

        return ready, tail

    def _rebalance_dangling_tail(self, ready: str, tail: str) -> tuple[str, str]:
        ready_words = ready.split()
        tail_words = tail.split() if tail else []

        while ready_words and self._is_carry_word(ready_words[-1]):
            tail_words.insert(0, ready_words.pop())

        return " ".join(ready_words).strip(), " ".join(tail_words).strip()

    @classmethod
    def _is_carry_word(cls, token: str) -> bool:
        token = token.strip(cls._TRAILING_PUNCT).lower()
        return token in cls._CARRY_WORDS

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()