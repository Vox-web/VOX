"""
Block — фронтенд онбординга активации аккаунта (статические проверки файлов).

Покрывает (по ТЗ):
  16 при email_verification_required фронтенд НЕ создаёт WebSocket;
  17 не показывает generic «Помилка з'єднання» при email_verification_required;
  18 activation modal доступна в Solo + обоих Duo-host + Room host;
  19 после refresh статус/баланс обновляются без logout/login.
"""

from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def _read(name):
    return (FRONTEND / name).read_text(encoding="utf-8")


def test_activation_runtime_is_loaded_on_host_and_landing():
    for page in ("host.html", "index.html"):
        assert '<script src="/vox-activation.js"></script>' in _read(page), page


def test_activation_module_never_opens_websocket():
    # Гейт работает на HTTP-статусе; модуль активации не должен трогать WS.
    js = _read("vox-activation.js")
    assert "new WebSocket" not in js
    assert "VoxSocket" not in js
    assert "/api/account/status" in js
    assert "/api/auth/resend-verification" in js


def test_all_four_host_modes_call_account_gate():
    host = _read("host.html")
    # Solo, Duo one-device, Duo remote host, Room host.
    assert host.count("await VoxActivation.ensureReady()") == 4


def test_host_routes_verification_terminal_to_modal_not_generic_error():
    host = _read("host.html")
    assert "VoxActivation.handleTerminal('email_verification_required'" in host
    # Активация показывается отдельной модалкой, а не общим connection error.


def test_activation_modal_has_required_ukrainian_copy():
    js = _read("vox-activation.js")
    assert "Підтвердіть email, щоб активувати акаунт" in js
    assert "після підтвердження ви отримаєте $3" in js
    assert "Перевірте папку «Спам»" in js
    assert "Надіслати лист повторно" in js
    assert "Я підтвердив email — перевірити статус" in js


def test_status_refresh_updates_balance_without_relogin():
    js = _read("vox-activation.js")
    host = _read("host.html")
    # Модуль шлёт событие обновления аккаунта…
    assert "vox:account-updated" in js
    # …а host слушает его и перезагружает баланс (без logout/login).
    assert "vox:account-updated" in host
    # Статус перепроверяется на возврате в приложение.
    for evt in ("visibilitychange", "pageshow", "focus"):
        assert evt in js, evt


def test_register_button_shows_animated_spinner_not_static_dots():
    html = _read("index.html")
    # Кнопки регистрации/логина используют анимированный спиннер, а не «...».
    assert ".btn-loading" in html and "@keyframes btnspin" in html
    assert html.count("classList.add('btn-loading')") >= 2  # register + login
    # Старые статичные точки на submit-кнопках убраны.
    assert "btn.textContent='...'" not in html


def test_register_activation_modal_disabled_in_test_mode():
    html = _read("index.html")
    # ТЕСТОВЫЙ РЕЖИМ: после регистрации модалка активации не показывается
    # (вызов закомментирован), бонус $3 начисляется сразу.
    assert "// if(window.VoxActivation) VoxActivation.showActivation" in html
    assert "$3 added for testing" in html or "Нараховано $3" in html


def test_activation_modal_has_honest_failed_delivery_copy():
    js = _read("vox-activation.js")
    assert (
        "Не вдалося надіслати лист підтвердження. Натисніть «Надіслати лист повторно»."
        in js
    )
    # Текст выбирается по фактическому состоянию доставки.
    assert "email_delivery_state" in js


def test_register_response_carries_delivery_state():
    # Бэкенд честно отдаёт состояние доставки во фронтенд.
    main_src = (
        Path(__file__).resolve().parents[1] / "backend" / "main.py"
    ).read_text(encoding="utf-8")
    assert 'result["email_delivery_state"] = "sent" if sent else "failed"' in main_src
    # И не остаётся «слепого» фонового потока отправки в регистрации.
    assert "threading.Thread(\n            target=send_verification_email" not in main_src
