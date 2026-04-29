import logging
import re
import time
from typing import Any

logger = logging.getLogger("vox.solo_semantic")


class SoloSemanticBuffer:
    """
    Solo-режим: копит финальные куски ASR, удерживает обрезанный хвост,
    и отдаёт в GPT/TTS только более связные, завершённые части.

    Главный механизм качества — semantic gate в Translator. Этот буфер
    его НЕ обходит в нормальной работе. Force-bypass срабатывает только в
    двух аварийных случаях:
      1) `_looks_complete_now()` уже видит явно завершённую мысль
         (точка/!/? или 2+ предложения) — gate тут уже ничего не улучшит;
      2) carry физически переполнился (см. MAX_CARRY_WORDS / MAX_CARRY_AGE_SEC)
         — это страховка от зависания gate, а не компромисс по качеству.
    """

    # Аварийные пороги. В нормальной работе НЕ должны срабатывать —
    # окно flush_after_sec=5 вычистит carry задолго до этих лимитов.
    MAX_CARRY_WORDS = 70       # ~30-40 секунд речи
    MAX_CARRY_AGE_SEC = 20.0   # 4 полных окна по 5 сек

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
        # Возраст самого старого непереведённого куска. НЕ сбрасывается при hold —
        # благодаря этому hard_flush_sec реально считается от появления текста,
        # а не от последней попытки flush.
        self._opened_at: float | None = None
        # Когда carry стал непустым в результате hold'а — для MAX_CARRY_AGE_SEC.
        self._carry_started_at: float | None = None
        self._last_seen_at: float | None = None
        self._lang_pair: tuple[str, str] | None = None

    def reset(self):
        self._parts = []
        self._carry = ""
        self._opened_at = None
        self._carry_started_at = None
        self._last_seen_at = None
        self._lang_pair = None

    def _clear_translator_context(self):
        if hasattr(self.translator, "clear_context"):
            try:
                self.translator.clear_context()
            except Exception as e:
                logger.warning("⚠️ SoloSemanticBuffer: clear_context failed: %r", e)

    @classmethod
    def _looks_complete_now(cls, text: str) -> bool:
        text = cls._normalize(text)
        if not text:
            return False

        words = text.split()
        # Повышен порог: короткая фраза с точкой не должна обходить gate
        if len(words) < 8:
            return False

        has_terminal = bool(re.search(r'[.!?…]["»)\]]*\s*$', text))
        strong_count = len(re.findall(r'[.!?…]["»)\]]*(?:\s+|$)', text))

        # 3+ завершённых предложения — однозначно дозрело, независимо от прочего
        if strong_count >= 3:
            return True

        # Конечный знак + 2+ предложений + достаточная длина
        if has_terminal and strong_count >= 2 and len(words) >= 8:
            return True

        return False

    def push_and_maybe_flush(self, text: str, source_lang: str, target_lang: str) -> list[dict[str, str]]:
        text = self._normalize(text)
        if not text:
            return []

        now = time.monotonic()
        out: list[dict[str, str]] = []

        # Смена языковой пары — жёсткий reset
        if self._lang_pair and self._lang_pair != (source_lang, target_lang):
            logger.info(
                "🧹 SoloSemanticBuffer: language pair changed %s -> %s, reset",
                self._lang_pair,
                (source_lang, target_lang),
            )
            self._clear_translator_context()
            self.reset()

        # Длинная пауза: сначала пытаемся дожать pending chunk.
        elif self._last_seen_at and (now - self._last_seen_at) >= self.idle_reset_sec:
            logger.info("🧹 SoloSemanticBuffer: idle gap %.1fs", now - self._last_seen_at)

            had_pending = bool(self._carry or self._parts)
            if had_pending:
                logger.info("🧠 SoloSemanticBuffer: force flush stale pending chunk before reset")
                flushed = self._flush(source_lang, target_lang, force=True)
                out.extend(flushed)

            # Если после force flush carry всё ещё остался — сохраняем его:
            # пользователю важно качество, обрывки мысли не дропаем,
            # ждём продолжения от следующего push.
            if self._carry:
                logger.info("🧠 SoloSemanticBuffer: keep pending carry after idle gap")
            else:
                self._clear_translator_context()
                self.reset()

        self._lang_pair = (source_lang, target_lang)
        self._last_seen_at = now

        if self._opened_at is None:
            self._opened_at = now

        self._parts.append(text)

        full_now = self._normalize(" ".join(part for part in [self._carry, *self._parts] if part))
        elapsed = now - self._opened_at

        # Аварийный клапан №1: carry физически переполнен.
        # В норме сюда не попадаем — это защита от зависшего gate.
        carry_words = len(self._carry.split()) if self._carry else 0
        parts_words = sum(len(p.split()) for p in self._parts)
        total_pending = carry_words + parts_words
        carry_age = (now - self._carry_started_at) if self._carry_started_at else 0.0

        if total_pending >= self.MAX_CARRY_WORDS or carry_age >= self.MAX_CARRY_AGE_SEC:
            logger.warning(
                "🚨 SoloSemanticBuffer: carry overflow words=%d age=%.1fs → force flush",
                total_pending, carry_age,
            )
            out.extend(self._flush(source_lang, target_lang, force=True))
            return out

        # Если уже сейчас видим законченную мысль — не ждём ещё один push.
        # На force gate тоже обходим: мысль объективно дозрела.
        if self._looks_complete_now(full_now):
            logger.info("🧠 SoloSemanticBuffer: immediate flush complete chunk")
            out.extend(self._flush(source_lang, target_lang, force=True))
            return out

        # Классическое окно 5 сек
        if elapsed < self.flush_after_sec:
            return out

        force = elapsed >= self.hard_flush_sec
        out.extend(self._flush(source_lang, target_lang, force=force))
        return out

    def flush_all(self, source_lang: str, target_lang: str) -> list[dict[str, str]]:
        return self._flush(source_lang, target_lang, force=True)

    def _flush(self, source_lang: str, target_lang: str, force: bool) -> list[dict[str, str]]:
        full = self._normalize(" ".join(part for part in [self._carry, *self._parts] if part))
        logger.info(
            "🧠 SoloSemanticBuffer._flush: force=%s full_words=%d",
            force, len(full.split()) if full else 0,
        )
        self._parts = []
        # ВАЖНО: _opened_at НЕ сбрасываем безусловно. Если flush ничего не выпустил
        # (carry остался), таймер должен продолжать тикать от исходного момента,
        # чтобы hard_flush_sec работал по своему прямому назначению.

        if not full:
            self._carry = ""
            self._opened_at = None
            self._carry_started_at = None
            return []

        out: list[dict[str, str]] = []

        while full:
            ready, tail = self._split_ready_and_tail(full, force=force)

            if not ready:
                self._carry = full
                # carry остался — таймеры продолжают тикать
                if self._carry_started_at is None:
                    self._carry_started_at = time.monotonic()
                break

            # При force всё равно прогоняем через gate — он может отполировать фразу.
            # Но игнорируем emit_now=False: у нас таймаут или аварийный клапан,
            # держать дольше нельзя. Ориентировочная стоимость: ~+150-300 токенов/force-flush.
            if force:
                try:
                    gate = self._translate_ready(ready, source_lang, target_lang)
                    if not gate.get("emit_now", False):
                        # Gate хочет подождать, но мы не можем — берём его spoken_text
                        # или падаем обратно на прямой перевод.
                        polished = (gate.get("spoken_text") or "").strip()
                        if not polished:
                            if source_lang == target_lang:
                                polished = self.translator.correct_asr(ready, source_lang)
                            else:
                                polished = self.translator.translate(ready, source_lang, target_lang)
                        gate = {
                            "emit_now": True,
                            "spoken_text": polished,
                            "reason": f"force_override:{gate.get('reason', 'hold')}",
                        }
                except Exception as e:
                    logger.warning("⚠️ force translate failed: %r", e)
                    try:
                        if source_lang == target_lang:
                            spoken = self.translator.correct_asr(ready, source_lang)
                        else:
                            spoken = self.translator.translate(ready, source_lang, target_lang)
                    except Exception:
                        spoken = ready
                    gate = {
                        "emit_now": True,
                        "spoken_text": spoken,
                        "reason": "force_fallback_after_gate_error",
                    }
            else:
                gate = self._translate_ready(ready, source_lang, target_lang)

            if not gate.get("emit_now", False):
                logger.info(
                    "🧠 SoloSemanticBuffer: hold for next chunk reason=%r src=%r",
                    gate.get("reason", ""),
                    ready[:160],
                )
                self._carry = self._normalize(" ".join(part for part in [ready, tail] if part))
                if self._carry_started_at is None:
                    self._carry_started_at = time.monotonic()
                break

            translated = (gate.get("spoken_text") or "").strip()
            if not translated:
                self._carry = self._normalize(" ".join(part for part in [ready, tail] if part))
                if self._carry_started_at is None:
                    self._carry_started_at = time.monotonic()
                break

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
                # Если что-то осталось в carry — продолжаем считать его возраст,
                # если carry опустел — сбрасываем оба таймера.
                if self._carry:
                    if self._carry_started_at is None:
                        self._carry_started_at = time.monotonic()
                else:
                    self._carry_started_at = None
                    self._opened_at = None
                break

        if not full:
            self._carry = ""
            self._carry_started_at = None
            self._opened_at = None

        return out

    def _translate_ready(self, text: str, source_lang: str, target_lang: str) -> dict:
        if hasattr(self.translator, "translate_with_semantic_gate"):
            return self.translator.translate_with_semantic_gate(text, source_lang, target_lang)

        # Fallback для совместимости со старым translator.py
        if source_lang == target_lang:
            spoken = self.translator.correct_asr(text, source_lang)
        else:
            spoken = self.translator.translate(text, source_lang, target_lang)

        return {
            "emit_now": True,
            "spoken_text": spoken,
            "reason": "legacy_fallback",
        }

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