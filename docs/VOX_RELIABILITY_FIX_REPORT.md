# VOX — Reliability Fix Report

Ветка: `fix/vox-pwa-reliability` · Версия после правок: **3.2.0**
Дата работы: 2026-06-20 · Ничего не запушено и не задеплоено.

---

## 1. Подтверждённые пункты аудита

| # | Пункт аудита | Подтверждён | Доказательство |
|---|--------------|-------------|----------------|
| A | Разные пути БД у auth и billing | ✅ ДА | `backend/vox.db` имеет users/sessions/reviews, но **нет колонки `balance` и таблицы `payments`** — billing мигрировал в другой файл (`/data/vox.db`). `VOX_DB_PATH` в `.env` не задан. Проверено `scripts/audit_split_databases.py`. |
| B1 | Бонус $3 выдавался при регистрации, письмо обещало после verify | ✅ ДА | `api_register` начислял бонус сразу (`main.py`), `verify_email_token` — повторно нет. Обещание письма было ложным. |
| B2 | `check_balance_for_start` нигде не вызывался; первая минута бесплатна | ✅ ДА | WS-хендлеры имели свои inline billing-loop'ы; `billing.py:billing_tick`/`check_balance_for_start` — мёртвый код. |
| B3 | Room списывал за 0 гостей | ✅ ДА | `deduct_session_cost` использует `max(1,guests)` → 1 списание при 0 гостях. |
| C1 | UI Duo предлагал больше языков, чем backend поддерживает в one-device | ✅ ДА | Селекты — 15 языков; `MULTI_LANGS` (10) хардкодом; дефолт `/duo/create` `lang_a="uk"` **не в** MULTI_LANGS; `nl` в MULTI_LANGS, но не в продуктовых 15. |
| C2 | Race в `Translator` (`_context`/`_cache`) | ✅ ДА (потенциальный) | `translate_parallel` запускает `self.translate` в нескольких потоках на одном экземпляре, мутируя общий list/OrderedDict без блокировки. |
| D | Solo не слал/не валидировал `audio_meta`, нет ресэмплинга | ✅ ДА | `/ws/solo` обрабатывал только ping/tts_done/config; полагался на `AudioContext({sampleRate:16000})`. |
| E | Нестабильность PWA на Android | ✅ ЧАСТИЧНО (см. §2) | Наиболее вероятная причина — «полуоткрытый» WebSocket + отсутствие liveness-watchdog. |
| F1 | README/.env упоминают Resend, код использует Gmail SMTP | ✅ ДА | `billing.py`/`main.py` шлют через `smtplib.SMTP_SSL`. `RESEND_API_KEY` — мёртвая переменная. |
| F2 | Расхождение версий (FastAPI 0.3.0 vs README 3.1) и Procfile≠README | ✅ ДА | Было `version="0.3.0"`, README «3.1», README-Procfile отличался от фактического. |
| — | Закоммичена БД с персональными данными; `.gitignore` сломан кавычками | ✅ ДА | `backend/vox.db` был в индексе; паттерны `"*.db"` в кавычках не работали. |

Не подтверждено / переклассифицировано:
- «Stale JS из service worker» как первопричина Android-симптома — **маловероятно**: HTML не кешируется, инлайновый JS грузится свежим при reload. Кешировались только иконки/manifest/`pwa-install.js`. Реальный вклад: установленная PWA может долго жить без reload → видит старый код, пока не очистят кэш. Это устранено update-flow (§2).

---

## 2. Причины нестабильности PWA и что сделано

**Главная гипотеза (высокая уверенность): «полуоткрытый» WebSocket.**
На Android при сворачивании/смене сети сокет часто остаётся `readyState=OPEN`, но фактически мёртв. Старый код `wsClosedOrBroken()` проверял только `CLOSING/CLOSED`, поэтому такое состояние не детектировалось: аудио уходило «в никуда» → «перестало слышать диктовку». А `logout+login` помогали, потому что это **полная перезагрузка страницы** (свежий HTML + сброс всего JS-состояния и сокета), а не просто reconnect.

Что сделано:
1. **Liveness/stale-watchdog** (`host.html`): любое входящее сообщение/`pong` обновляет метку активности; если в активной сессии нет сообщений > 30с — авто-`repair`. Клиент шлёт ping каждые 15с, сервер отвечает `pong` → при живом сокете метка свежая, при мёртвом — устаревает и срабатывает reconnect. Watchdog работает и на переднем плане (периодический `setInterval`), а не только по lifecycle-событиям.
2. **Переиспользуемая state machine** (`frontend/vox-connection.js`, 10 node-тестов): состояния `connecting/connected/reconnecting/offline/auth_required/insufficient_balance/microphone_error/closed`; backoff+jitter; лимит попыток; single-connection guard; heartbeat; stale-watchdog; повторная отправка `config`/`audio_meta`; отмена таймеров при stop. (Готовая инфраструктура; найден и исправлен латентный баг `_lastActivity==0`.)
3. **Mic lifecycle** (`host.html`): подписка на `track 'ended'/'mute'/'unmute'`; при `ended` в активной сессии — `repair`; состояние в `window._voxMicState`.
4. **SW update flow** (`sw.js`+`pwa-install.js`): версионированный кеш, убран авто-`skipWaiting`, баннер «Доступна новая версия» + контролируемый reload с guard от reload-loop (`hadController`), периодическая `reg.update()`. Устраняет «застревание» установленной PWA на старом коде.
5. **Диагностика** (`vox-diagnostics.js`): скрываемая панель (`?diag=1` или 5 тапов в угол) — версии, состояние WS, last close, reconnect attempts, sample rate, mic, online. Помогает быстро понять: сеть / авторизация / баланс / WS / SW / микрофон.

Вторичные факторы, устранённые попутно: рассинхрон БД (мог вызывать «помогает только relogin», т.к. сессия/баланс читались из разных файлов); отсутствие повторной отправки `audio_meta` после reconnect.

---

## 3. Изменённые/новые файлы

**Backend:** `db_config.py`(new), `version.py`(new), `language_capabilities.py`(new), `vox_db.py`, `billing_db.py`, `billing.py`, `main.py`, `translator.py`, `audio_utils.py`, `tts_engine.py`, `solo_semantic.py`. Убран из индекса `backend/vox.db` (файл на диске сохранён).

**Frontend:** `sw.js`, `pwa-install.js`, `host.html`, `docs.html`, `vox-connection.js`(new), `vox-diagnostics.js`(new).

**Scripts:** `scripts/audit_split_databases.py`(new), `scripts/migrate_split_databases.py`(new).

**Config/docs:** `.gitignore`, `.env.example`(new), `README.md`, `docs/VOX_RELIABILITY_FIX_REPORT.md`(new).

**Tests:** `tests/conftest.py`, `tests/test_db_path_unified.py`, `tests/test_split_db_scripts.py`, `tests/test_billing_bonus_balance.py`, `tests/test_language_capabilities.py`, `tests/test_translator_threadsafe.py`, `tests/test_audio_resample.py`, `tests/test_email_mock.py`, `tests/test_version_consistency.py`, `tests/test_api_smoke.py`, `tests/js/test_vox_connection.js`.

---

## 4. Коммиты (атомарные, по блокам)

```
327c4a3 fix(db): unify DB path for auth and billing (single source of truth)
b4bbc72 fix(billing): single bonus on email verify, start-balance gate, room 0-guest
767b53c fix(lang+translator): single language-capabilities source, thread-safe Translator
952d4d0 fix(audio): Solo sends/validates audio_meta + shared resampling path
1c57daa fix(pwa): SW update flow, WS reconnect state machine, stale watchdog, mic lifecycle, diagnostics
3820446 fix(config): single version source, Gmail email, scoped defaults, docs/.env
5bdf769 test(api): in-process TestClient smoke for status/languages/static/register
```

---

## 5. Тесты и результаты

Запуск Python: `python -m pytest tests/ -q` → **50 passed**.
Запуск JS: `node tests/js/test_vox_connection.js` → **10 passed**.

| Область | Файл | Что проверяет |
|---|---|---|
| Единый путь БД | `test_db_path_unified.py` | auth и billing используют один файл; VOX_DB_PATH override; общий users |
| Анализ/миграция БД | `test_split_db_scripts.py` | dry-run не пишет; идемпотентность; нет дублей платежей; стоп при конфликте |
| Бонус/баланс/room | `test_billing_bonus_balance.py` | нет бонуса при регистрации; один бонус при verify; повтор не даёт второй; **8 параллельных потоков → ровно 1 бонус**; гейт баланса; room×guests; room_billable |
| Языки | `test_language_capabilities.py` | 15 языков; one-device = 9; nl скрыт; дефолтная пара валидна; нормализация |
| Translator concurrency | `test_translator_threadsafe.py` | 8 потоков × 150 переводов без порчи cache/context; clear_context под нагрузкой |
| Аудио | `test_audio_resample.py` | ресэмплинг 8/16/44.1/48k→16k; выбор и валидация sample rate |
| Email (mock) | `test_email_mock.py` | SMTP замокан; токен; flow verify→бонус; нет креденшелов→False |
| Версии | `test_version_consistency.py` | backend == SW == frontend (3.2.0) |
| API smoke | `test_api_smoke.py` | /status, /api/languages, статика JS, validate-only /set-config, регистрация без бонуса |
| WS state machine | `js/test_vox_connection.js` | connect/resend, single-socket, heartbeat, stale→reconnect, backoff+лимит, stop, terminal, offline |

Также выполнен **локальный запуск** через `TestClient` (in-process): `/status`=200, `/api/languages`=15 языков (default en/ru), новые JS отдаются, `/set-config` валидирует и не мутирует, регистрация даёт balance=0.0.

---

## 6. Что НЕ удалось проверить локально

- **Реальный браузер/Android PWA**: установка, фон→resume, Wi-Fi→mobile, обновление SW-баннером, восстановление микрофона — требуют устройства/эмулятора. Логика state machine покрыта unit-тестами, но фактическое поведение на железе не проверено.
- **Service Worker в браузере**: тестируется только статикой/синтаксисом; реальная регистрация/`controllerchange`/кеш — не запускались (нет headless-браузера/Playwright в окружении).
- **Stripe Checkout/Webhook**: не дергались (нужны ключи Stripe; пакет `stripe` в окружении тестов отсутствует — сделан опциональным).
- **Полный прогон приложения под `uvicorn` с реальными Deepgram/OpenAI**: не выполнялся (внешние API-ключи, расход средств). Проверен импорт и in-process эндпоинты.
- **edge-tts/реальный TTS, реальная транскрипция Deepgram** — не вызывались.
- Окружение тестов: системный Python 3.12 с `websockets 16`, `numpy 2.x` (новее запиннованных). Юнит-тесты сетевые пути не трогают, но прод использует запиннованные версии — поведение `websockets 12` отдельно не верифицировалось здесь.

---

## 7. Шаги деплоя

### Локально
```bash
git checkout fix/vox-pwa-reliability
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt
pip install pytest                                    # для тестов
cp .env.example .env                                  # заполнить ключи; задать VOX_DB_PATH
cd backend && uvicorn main:app --host 0.0.0.0 --port 8080
# тесты из корня:  python -m pytest tests/ -q  &&  node tests/js/test_vox_connection.js
```

### Production (Railway)
1. **До деплоя** задать переменные окружения: `VOX_DB_PATH=/data/vox.db` (persistent volume), `DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ADMIN_PASSWORD` (сменить дефолт!), при необходимости Stripe.
2. **Проверить раскол БД** на сервере (READ-ONLY):
   ```bash
   python scripts/audit_split_databases.py --db-a /data/vox.db --db-b backend/vox.db
   ```
3. Если данные разошлись — dry-run, затем (по отдельной команде) применить:
   ```bash
   python scripts/migrate_split_databases.py --target /data/vox.db --source <другой.db>           # dry-run
   python scripts/migrate_split_databases.py --target /data/vox.db --source <другой.db> --apply    # с backup
   ```
4. Запушить ветку, смержить, дать Railway пересобрать (`cd backend && uvicorn main:app`).
5. После деплоя инкрементировать версию в `backend/version.py`, `frontend/pwa-install.js` (`VOX_FRONTEND_VERSION`) и `frontend/sw.js` (`SW_VERSION`) при будущих изменениях — баннер обновления покажется автоматически.

> Пуш/деплой не выполнены — ждут отдельной команды.

---

## 8. Android test checklist (10–15 минут)

1. Открыть сайт в Chrome (Android), «Добавить на главный экран», запустить как PWA.
2. **Solo**: выбрать пару (напр. uk→en), «Слушать», говорить — проверить транскрипт/перевод/звук.
3. Свернуть приложение на ~1 минуту, вернуться — диктовка должна возобновиться без relogin (watchdog/repair).
4. Переключить Wi-Fi → мобильный интернет во время сессии — соединение должно восстановиться.
5. Включить «В самолёте» на 20–30с, выключить (`offline → online`) — авто-reconnect.
6. **Duo «на одному пристрої»**: выбрать `uk`/`pl` (с «*») → кнопка должна показать сообщение «не поддерживается, используйте Remote». Выбрать `en`/`ru` → работает.
7. **Duo «Дистанційно (QR)»**: открыть QR на втором телефоне, проверить двусторонний перевод.
8. **Room**: создать комнату, зайти гостем со второго устройства, проверить перевод; убедиться, что без гостей баланс хоста не убывает.
9. Подождать молчание > 30с в активной Solo — НЕ должно ложно реконнектить (pong держит соединение живым).
10. Открыть `?diag=1` — панель «Диагностика соединения» показывает версии и состояние WS.
11. Изменить `SW_VERSION` (тест) и перезайти — должен появиться баннер «Доступна новая версия» → «Обновить» → один reload, без петли.
12. Проверить микрофон: отозвать разрешение в настройках — должна показаться понятная ошибка микрофона, а не «зависание».
13. **Баланс**: пользователь с балансом < $0.25 — старт сессии должен сразу вернуть `insufficient_balance`, без бесплатной минуты.

---

## 9. Действия пользователя вручную после деплоя

1. **Сменить секреты** (история git всё ещё содержит `backend/vox.db` и `.env`-подсказки): ротировать пароли пользователей не нужно (хэши), но рекомендовано сменить `ADMIN_PASSWORD`, Deepgram/OpenAI/Stripe/Gmail-ключи, т.к. ранее в репозитории были утечки. Для полной очистки истории — `git filter-repo` (по отдельному решению).
2. Задать `VOX_DB_PATH=/data/vox.db` в Railway (иначе путь выбирается эвристикой).
3. Прогнать `scripts/audit_split_databases.py` на проде и, при расколе, миграцию (§7).
4. Удалить из Railway-переменных мёртвые `RESEND_API_KEY`, `WHISPER_MODEL`, `VAD_THRESHOLD`.
5. Убедиться, что `GMAIL_USER`/`GMAIL_APP_PASSWORD` заданы (иначе письма верификации не уходят и бонус $3 нельзя получить — по новой политике бонус только после verify).
6. Прогнать Android-чеклист (§8).

---

## Приложение. Конкретные изменения логики (не «молча»)

- **Бонус $3**: теперь начисляется **только после подтверждения email** (раньше — сразу при регистрации). Атомарно/идемпотентно. Старые пользователи с `bonus_given=1` повторно не получат.
- **Гейт баланса**: сессия (Solo/Duo/Room) не стартует при балансе < $0.25; биллинг переведён на **prepay** (списание в начале минуты) — нет бесплатной первой минуты.
- **Room при 0 гостях**: списаний нет.
- **/set-config**: больше не меняет глобальные дефолты (был кросс-юзер баг) — только валидирует.
- **Duo one-device**: языки `uk, pl, zh, ko, ar, tr` недоступны в этом режиме осознанно (ограничение nova-3 multi); доступны в Solo/Room/Duo-Remote. `nl` скрыт (нет в 15 языках продукта).
- **SW**: больше не делает авто-`skipWaiting` — обновление применяется по кнопке баннера.
