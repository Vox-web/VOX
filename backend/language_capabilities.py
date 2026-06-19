"""
VOX — Единый источник правды о возможностях языков.

Раньше списки языков и их ограничения дублировались в backend
(MULTI_LANGS в main.py), host.html и docs.html и расходились между собой.
Из-за этого UI Duo предлагал пары, которые backend в режиме one-device
отвергал (pair_not_supported).

Уровни возможностей для каждого языка:
  * stt              — потоковое распознавание (Deepgram nova-2, по языку)
  * translation      — перевод (GPT)
  * solo             — Solo-режим
  * duo              — Duo «дистанционно» (отдельный STT на каждую сторону)
  * duo_one_device   — Duo «на одном устройстве» (nova-3 multi автодетект)

ВАЖНО про duo_one_device: режим работает только для языков, которые
Deepgram nova-3 реально поддерживает в multi-режиме. Пересечение этого
набора с 15 языками продукта даёт 9 языков. Остальные (uk, pl, zh, ko,
ar, tr) сознательно НЕдоступны в one-device, но доступны в Solo, Room и
Duo «дистанционно». Язык nl (нидерландский) Deepgram multi поддерживает,
но он не входит в 15 языков продукта (нет голоса TTS/перевода в наборе) —
поэтому в продукте он не предлагается вообще.
"""

# Набор языков, которые Deepgram nova-3 поддерживает в multi (one-device).
# Источник ограничения — провайдер STT.
_NOVA3_MULTI = {"en", "es", "fr", "de", "hi", "ru", "pt", "ja", "it", "nl"}

# 15 языков продукта (совпадает с Translator.SUPPORTED_LANGUAGES).
_PRODUCT_LANGUAGES = [
    ("uk", "Українська", "🇺🇦"),
    ("ru", "Русский", "🇷🇺"),
    ("en", "English", "🇬🇧"),
    ("de", "Deutsch", "🇩🇪"),
    ("pl", "Polski", "🇵🇱"),
    ("fr", "Français", "🇫🇷"),
    ("zh", "中文", "🇨🇳"),
    ("es", "Español", "🇪🇸"),
    ("it", "Italiano", "🇮🇹"),
    ("pt", "Português", "🇧🇷"),
    ("ja", "日本語", "🇯🇵"),
    ("ko", "한국어", "🇰🇷"),
    ("ar", "العربية", "🇸🇦"),
    ("tr", "Türkçe", "🇹🇷"),
    ("hi", "हिन्दी", "🇮🇳"),
]


def _build_capabilities() -> dict[str, dict]:
    caps: dict[str, dict] = {}
    for code, name, flag in _PRODUCT_LANGUAGES:
        caps[code] = {
            "code": code,
            "name": name,
            "flag": flag,
            "stt": True,
            "translation": True,
            "solo": True,
            "duo": True,  # «дистанционно» — отдельный STT на язык, работает для всех
            "duo_one_device": code in _NOVA3_MULTI,
        }
    return caps


LANGUAGE_CAPABILITIES: dict[str, dict] = _build_capabilities()

# Языки, доступные в Duo one-device (для быстрых проверок).
DUO_ONE_DEVICE_LANGUAGES = frozenset(
    code for code, c in LANGUAGE_CAPABILITIES.items() if c["duo_one_device"]
)


def supports_duo_one_device(lang_a: str, lang_b: str) -> bool:
    """True, если оба языка пары поддерживаются в Duo one-device."""
    a = (lang_a or "").split("-")[0].lower()
    b = (lang_b or "").split("-")[0].lower()
    return a in DUO_ONE_DEVICE_LANGUAGES and b in DUO_ONE_DEVICE_LANGUAGES


def default_duo_one_device_pair() -> tuple[str, str]:
    """Гарантированно поддерживаемая дефолтная пара для one-device."""
    return ("en", "ru")


def as_list() -> list[dict]:
    """Список capabilities в порядке продукта (для API/фронтенда)."""
    order = [code for code, _, _ in _PRODUCT_LANGUAGES]
    return [LANGUAGE_CAPABILITIES[code] for code in order]
