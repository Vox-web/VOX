"""
Block C.2 — Translator потокобезопасен при параллельных переводах.

Раньше один экземпляр Translator мутировал общий _context (list) и _cache
(OrderedDict) из нескольких потоков (translate_parallel) без блокировки —
потенциальная порча данных. Тест нагружает один экземпляр конкурентными
переводами и проверяет отсутствие исключений и целостность структур.
"""

import asyncio
import threading
import time

import translator as translator_mod


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    def create(self, **kwargs):
        # имитируем сетевую задержку, чтобы потоки реально пересекались
        time.sleep(0.0005)
        user = [m for m in kwargs["messages"] if m["role"] == "user"][-1]["content"]
        return _Resp(f"T::{user}")


class _Chat:
    completions = _Completions()


class _FakeClient:
    chat = _Chat()


def _make_translator():
    t = translator_mod.Translator(cache_size=32, context_size=5)
    t.client = _FakeClient()  # обходим отсутствие OPENAI_API_KEY
    return t


def test_parallel_translate_no_corruption():
    t = _make_translator()
    errors = []

    def worker(n):
        try:
            for i in range(150):
                out = t.translate(f"hello {n}-{i}", "en", "de")
                assert out.startswith("T::")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors, errors
    # Кеш не превысил лимит и не повреждён
    assert len(t._cache) <= t._cache_size
    # Контекст ограничен и состоит из корректных записей
    assert len(t._context) <= t._context_size * 2
    for item in t._context:
        assert "source" in item and "translation" in item


def test_translate_parallel_returns_all_langs():
    t = _make_translator()
    langs = ["de", "fr", "es", "it", "pt", "ja", "ru"]
    result = asyncio.run(t.translate_parallel("good morning", "en", langs))
    assert set(result.keys()) == set(langs)
    assert all(v.startswith("T::") for v in result.values())


def test_clear_context_threadsafe():
    t = _make_translator()
    stop = threading.Event()

    def translator_worker():
        i = 0
        while not stop.is_set():
            t.translate(f"x{i}", "en", "de")
            i += 1

    def clearer():
        for _ in range(200):
            t.clear_context()
            time.sleep(0.0003)

    th = threading.Thread(target=translator_worker)
    th.start()
    clearer()
    stop.set()
    th.join()
    assert len(t._context) <= t._context_size * 2
