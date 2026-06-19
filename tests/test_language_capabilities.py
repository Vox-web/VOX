"""
Block C.1 — единый источник возможностей языков.
"""

import language_capabilities as lc


EXCLUDED_ONE_DEVICE = {"uk", "pl", "zh", "ko", "ar", "tr"}
INCLUDED_ONE_DEVICE = {"en", "es", "fr", "de", "hi", "ru", "pt", "ja", "it"}


def test_all_15_product_languages_present():
    codes = {c["code"] for c in lc.as_list()}
    assert len(codes) == 15
    assert EXCLUDED_ONE_DEVICE | INCLUDED_ONE_DEVICE | {"uk"} <= codes


def test_one_device_support_matches_spec():
    for code in INCLUDED_ONE_DEVICE:
        assert lc.LANGUAGE_CAPABILITIES[code]["duo_one_device"] is True, code
    for code in EXCLUDED_ONE_DEVICE:
        assert lc.LANGUAGE_CAPABILITIES[code]["duo_one_device"] is False, code


def test_nl_not_offered_in_product():
    # nl поддерживается провайдером, но не входит в 15 языков продукта → скрыт
    assert "nl" not in lc.LANGUAGE_CAPABILITIES
    assert lc.supports_duo_one_device("nl", "en") is False


def test_default_pair_is_supported():
    a, b = lc.default_duo_one_device_pair()
    assert lc.supports_duo_one_device(a, b) is True


def test_pair_validation():
    assert lc.supports_duo_one_device("en", "ru") is True
    assert lc.supports_duo_one_device("uk", "en") is False
    assert lc.supports_duo_one_device("pl", "de") is False
    # нормализация регистра/региона
    assert lc.supports_duo_one_device("EN-US", "DE") is True


def test_all_levels_consistent_for_15():
    # все 15 поддерживают stt/translation/solo/duo (remote)
    for c in lc.as_list():
        assert c["stt"] and c["translation"] and c["solo"] and c["duo"]
