# VOX — Real-Time AI Translation Platform
## Полная техническая спецификация (v3.1)

---

## ЦЕЛЬ ПРОЕКТА

Мультиязычная платформа **VOX** для перевода речи в режиме реального времени с задержкой ≤1.5 секунд. Два режима: персональный (Solo) и Web-комната (Room) с множеством участников.

**Философия:** Хост управляет всем через смартфон. Участникам нужен только браузер — никаких приложений, регистраций, установок.

**Сценарии использования:**
- Деловые переговоры с иностранными партнёрами
- Международные вебинары и онлайн-встречи
- Экскурсии для мультиязычных групп
- Медицинские консультации с иностранными пациентами
- Конференции — синхронный перевод в карманном формате

**Деплой:** GitHub → Railway.app

**Продакшн:** https://web-production-bd9a.up.railway.app

---

## РЕЖИМЫ РАБОТЫ

### Solo — персональный перевод
Один слушатель, один спикер. Пользователь надевает наушники, нажимает «Слушать», слышит перевод речи на своём языке. Серверный антиэхо мьютит микрофон во время воспроизведения TTS.

### Room — мультиязычная Web-комната
Хост создаёт комнату → QR-код → участники сканируют → выбирают язык → слышат перевод. Хост — дирижёр: даёт слово, заглушает, отключает.

**Подрежимы Room:**
- **Офлайн** — участники физически рядом, наушники обязательны
- **Онлайн без видео** — голосовая комната с переводом
- **Онлайн с видео (Фаза 2)** — WebRTC видеоконференция с AI-переводом

---

## АРХИТЕКТУРА

### Solo pipeline

```
[Микрофон] → PCM float32 → WebSocket
    → [FastAPI Server]
    → [Deepgram Nova-2 Streaming] — транскрипция (~300мс)
        ├── interim results → preview в UI (мгновенно)
        └── speech_final → финальный текст
    → [GPT-4o-mini] — контекстный перевод (~700мс)
    → [edge-tts / OpenAI TTS fallback] — синтез речи (~300мс)
    → MP3 → WebSocket → браузер → наушники
    → [Антиэхо: сервер мьютит вход, клиент шлёт tts_done]
```

### Room pipeline

```
[Активный спикер] → PCM → WebSocket
    → [FastAPI + RoomManager] — per-speaker Deepgram instance
    → [Deepgram Nova-2 Streaming] — транскрипция
    → [asyncio.gather] — ПАРАЛЛЕЛЬНЫЙ перевод на N языков
        ├── GPT-4o-mini → язык 1
        ├── GPT-4o-mini → язык 2
        └── GPT-4o-mini → язык N
    → [asyncio.gather] — ПАРАЛЛЕЛЬНЫЙ TTS для N языков
        ├── edge-tts → аудио 1
        ├── edge-tts → аудио 2
        └── edge-tts → аудио N
    → WebSocket → каждому участнику ЕГО аудио + speaker_name
    → WebSocket → хосту: ВСЕ переводы текстом + аудио только от гостей
```

**Задержка:** ~0.8–1.5с (Deepgram ~300мс + GPT ~700мс + edge-tts ~300мс). Параллельная обработка — одинаково для любого количества языков.

---

## УПРАВЛЕНИЕ УЧАСТНИКАМИ (ROOM)

### State Machine

```
LISTENING   — слушает перевод, микрофон выключен (по умолчанию)
REQUESTING  — нажал «Хочу сказать», ждёт разрешения хоста
SPEAKING    — получил разрешение, микрофон активен, голос переводится всем
MUTED       — заглушен хостом
```

### Переходы

```
LISTENING ──[участник: «Хочу сказать»]──→ REQUESTING
REQUESTING ──[хост: «Разрешить»]──→ SPEAKING
SPEAKING ──[участник: «Готово» / хост: «Забрать слово»]──→ LISTENING
LISTENING ←──[хост: «Включить»]── MUTED
LISTENING ──[хост: «Заглушить»]──→ MUTED
```

**Правило:** В любой момент говорит только ОДИН человек.

### Протокол «Поднятой руки»

1. Участник видит кнопку на своём языке: "I want to speak" / "Хочу сказати" / "Ich möchte sprechen"
2. Нажимает → запрос на сервер → хост видит уведомление
3. Хост: «Разрешить» или «Отклонить»
4. При разрешении: индикатор "SPEAK NOW" + активация микрофона
5. Участник говорит → перевод всем (включая хосту) на их язык
6. Все видят: "🎤 Obi is speaking..."
7. В истории — имя спикера рядом со временем

---

## СТЕК ТЕХНОЛОГИЙ

### Backend (Python 3.11+)

| Компонент | Библиотека | Назначение |
|-----------|-----------|------------|
| Web-сервер | FastAPI + Uvicorn | HTTP + WebSocket |
| STT | Deepgram Nova-2 Streaming API | Стриминг, ~300мс, 0 CPU |
| Перевод | OpenAI GPT-4o-mini | Контекстный, rolling history |
| TTS (primary) | edge-tts (Microsoft) | Бесплатно, ~300мс |
| TTS (fallback) | OpenAI TTS tts-1 | Платный, автоматический fallback |
| Аудио | numpy | float32 → int16, ресэмплинг |
| Биллинг | Stripe + stripe-python | Checkout Sessions, Webhooks |
| Email | Resend API | Верификация, бонусы |
| БД | SQLite (WAL mode) | Users, sessions, reviews, payments |
| QR-код | qrcode + Pillow | Генерация QR для комнат |

### Frontend (чистый HTML/JS, без фреймворков)

| Компонент | API | Назначение |
|-----------|-----|------------|
| Захват аудио | AudioWorklet API | Низкоуровневый захват, минимальная задержка |
| Передача | WebSocket API | Постоянное соединение с сервером |
| Воспроизведение | Web Audio API | TTS в наушниках/динамиках |
| Микрофон | getUserMedia API | Доступ к микрофону |
| i18n | Встроенный GUEST_I18N | 15 языков в guest.html |
| PWA | Service Worker | Network-first, офлайн-кеш статики |

### Инфраструктура

| Сервис | Назначение |
|--------|------------|
| GitHub | Хранение кода |
| Railway.app | Деплой, HTTPS, env variables |
| Deepgram | STT API |
| OpenAI | Translation + TTS API |
| Stripe | Платежи |
| Resend | Transactional email |

---

## СТРУКТУРА ПРОЕКТА

```
VOX/
├── backend/
│   ├── main.py              # FastAPI: WebSocket (Solo, Room), REST, раздача HTML
│   │                        # ~1320 строк, auth, billing integration
│   ├── room_manager.py      # RoomManager: комнаты, участники, broadcast
│   │                        # ~700 строк, state machine, QR, speaker tracking
│   ├── transcriber.py       # DeepgramTranscriber: Nova-2 Streaming WebSocket
│   │                        # ~512 строк, interim/final buffering, ресэмплинг
│   ├── translator.py        # Translator: GPT-4o-mini, LRU кеш, rolling context
│   │                        # ~200 строк, 15 языков, parallel translate
│   ├── tts_engine.py        # TTSEngine: edge-tts primary, OpenAI fallback
│   │                        # ~180 строк, 15 языков, parallel synth
│   ├── audio_utils.py       # PCM конвертация, валидация, ресэмплинг
│   │                        # ~100 строк, используется transcriber.py
│   ├── billing.py           # FastAPI Router: Stripe, email, баланс
│   │                        # ~366 строк, checkout, webhook, billing_tick
│   ├── billing_db.py        # SQLite: миграции биллинга, CRUD баланса/платежей
│   │                        # ~220 строк, ALTER TABLE, verify email
│   ├── vox_db.py            # SQLite: users, sessions, reviews
│   │                        # ~180 строк, auth, CRUD
│   ├── main_patch.py        # Инструкция интеграции auth/admin маршрутов
│   └── requirements.txt     # Python зависимости (~50MB)
│
├── frontend/
│   ├── index.html           # Landing + кабинет: auth, баланс, Stripe, отзывы
│   │                        # ~1427 строк
│   ├── host.html            # Host UI: Solo + Room, панель управления
│   ├── guest.html           # Guest UI: 15 языков i18n, hand-raise, audio
│   ├── solo.html            # Отдельная Solo-страница
│   ├── admin.html           # Админка: пользователи, отзывы, платежи, баланс
│   ├── review_form.html     # Форма отзыва: UK/EN/DE, auth-aware
│   ├── docs.html            # Документация / О продукте: UK/EN/DE
│   └── sw.js                # Service Worker: Network-first, precache
│
├── .env.example
├── .gitignore
├── Procfile
├── railway.toml
├── README.md
└── VOX_PROJECT_SPEC.md
```

---

## ДЕТАЛЬНОЕ ОПИСАНИЕ МОДУЛЕЙ

### 1. main.py — Главный сервер (~1320 строк)

**Инициализация (lifespan):**
- Загрузка env, создание Translator, TTSEngine, RoomManager
- init_db() из vox_db + billing_migrate() из billing_db
- Подключение billing_router

**Статические страницы:**
```
GET /           → index.html (landing)
GET /landing    → index.html
GET /host       → host.html
GET /solo       → solo.html
GET /admin      → admin.html
GET /cabinet    → index.html
GET /review     → review_form.html
GET /docs       → docs.html
GET /room/{id}  → guest.html (если комната существует)
```

**REST API — Комнаты:**
```
POST   /room/create                    → room_id + QR base64
POST   /room/{id}/join                 → guest_id + room state
GET    /room/{id}/status               → room state
DELETE /room/{id}                      → закрыть комнату
POST   /room/{id}/grant-speak/{gid}
POST   /room/{id}/revoke-speak/{gid}
POST   /room/{id}/deny-speak/{gid}
POST   /room/{id}/mute/{gid}
POST   /room/{id}/unmute/{gid}
POST   /room/{id}/kick/{gid}
```

**REST API — Auth / Users:**
```
POST   /api/register        → регистрация + auto-login + verification email
POST   /api/login           → логин + session token
GET    /api/me              → текущий пользователь (Bearer token)
POST   /api/reviews         → добавить отзыв (auth или анонимно)
GET    /api/reviews/public  → одобренные отзывы
```

**REST API — Admin (Basic Auth):**
```
GET    /api/admin/users
PATCH  /api/admin/users/{id}    → имя, email, статус, пароль, баланс
DELETE /api/admin/users/{id}    → удалить пользователя + данные
GET    /api/admin/reviews
PATCH  /api/admin/reviews/{id}/approve
DELETE /api/admin/reviews/{id}
GET    /api/admin/payments
POST   /api/admin/adjust-balance
```

**REST API — Billing:**
```
GET    /api/balance              → баланс + est_minutes
POST   /api/create-checkout      → Stripe Checkout Session URL
POST   /api/webhook             → Stripe Webhook (confirm_stripe_payment)
POST   /api/send-verification   → повторное письмо верификации
GET    /api/verify-email?token= → подтверждение + $3 бонус
```

**WebSocket — Solo (/ws/solo):**
1. Принимает `{type: "auth", token}` — привязка к пользователю для биллинга
2. Принимает `{type: "config", source_lang, target_lang}` — lazy start Deepgram
3. Принимает бинарные аудио чанки (float32 PCM) → Deepgram
4. Принимает `{type: "tts_done"}` → снятие серверного мьюта
5. Отправляет: transcript (interim/final), translation, audio (MP3)
6. Billing tick каждые 60 секунд: списание $0.05/мин
7. При balance ≤ 0 → session_ended, при balance ≤ $0.10 → balance_warning

**WebSocket — Room Host (/ws/room/{id}/host):**
1. Бинарные аудио → host_transcriber (Deepgram)
2. speech_final → translate_parallel на все языки участников
3. TTS parallel → broadcast каждому на его языке
4. Хост получает ВСЕ переводы текстом + аудио только от гостей
5. Guest management: grant/revoke/mute/kick через JSON-сообщения

**WebSocket — Room Guest (/ws/room/{id}/guest/{gid}):**
1. Бинарные аудио только в состоянии SPEAKING
2. Pre-roll буфер: аудио копится пока guest в REQUESTING, флашится при grant
3. Действия: request_speak, cancel_request, done_speaking
4. Получает: transcript, translation, audio (на своём языке), speaker_changed

### 2. room_manager.py — Управление комнатами (~700 строк)

**Модели данных:**
- `ParticipantState` (Enum): LISTENING, REQUESTING, SPEAKING, MUTED
- `Participant` (dataclass): guest_id, display_name, language, state, websocket
- `Room` (dataclass): room_id, host_language, host_websocket, participants, active_speaker

**RoomManager:**
- `create_room()` → Room + QR-код (qrcode + Pillow)
- `join_room()` → Participant + уведомление хосту
- `leave_room()` → удаление + speaker_changed если спикер ушёл
- `request_to_speak()` → REQUESTING + уведомление хосту
- `grant_speak()` → SPEAKING + speaker_changed всем
- `revoke_speak()` → LISTENING + speaker_changed всем
- `deny_speak()` → LISTENING
- `mute_participant()` / `unmute_participant()`
- `kick_participant()` → WebSocket close
- `close_room()` → закрытие всех соединений
- `broadcast_translation()` → рассылка с speaker_name на языке каждого
- Grace period (30 сек) при отключении хоста перед закрытием комнаты

### 3. transcriber.py — Deepgram Nova-2 (~512 строк)

**DeepgramTranscriber:**
- WebSocket к `wss://api.deepgram.com/v1/listen`
- Параметры: model=nova-2, interim_results=true, utterance_end_ms=1000, endpointing=300, encoding=linear16, sample_rate=16000, smart_format=true
- `start(language)` → открывает WS, запускает _receive_loop
- `send_audio(pcm_float32_bytes)` → конвертация float32→int16 через numpy, ресэмплинг через audio_utils.resample_audio
- `stop()` → CloseStream, закрытие WS
- **Стратегия буферизации:**
  - interim (is_final=false) → preview, объединяя накопленные finals
  - is_final без speech_final → накапливает в _finals_buffer
  - speech_final → собирает буфер, отправляет финал
- `TranscriptResult`: text, is_final, language, confidence

### 4. translator.py — Контекстный перевод (~200 строк)

**Translator:**
- 15 языков: uk, ru, en, de, pl, fr, zh, es, it, pt, ja, ko, ar, tr, hi
- `translate(text, source_lang, target_lang)` → перевод через GPT-4o-mini
- LRU кеш (50 записей)
- Rolling context: последние 5 пар source→translation в system prompt
- ASR-aware промпт: учитывает ошибки распознавания, реконструирует смысл
- `translate_parallel(text, source_lang, target_langs)` → asyncio.gather + run_in_executor
- `clear_context()` → при смене спикера

### 5. tts_engine.py — Синтез речи (~180 строк)

**TTSEngine:**
- 15 языков × 2 голоса (edge + openai) = VOICE_MAP
- `synthesize(text, lang)` → MP3 bytes
  - Попытка 1: edge-tts (бесплатно, ~300мс)
  - Попытка 2: OpenAI TTS tts-1 (fallback, платный)
- `synthesize_parallel(translations)` → asyncio.gather для N языков
- edge-tts: async библиотека, обёрнута в sync через new_event_loop

### 6. billing.py — Биллинг (~366 строк)

**billing_router (prefix=/api):**
- Stripe Checkout Sessions (суммы: $5, $10, $20, $50)
- Stripe Webhook: checkout.session.completed → confirm payment + update balance
- Email верификация через Resend: HTML-письмо с кнопкой → $3 бонус при первой верификации
- `billing_tick()` — вызывается каждые 60 сек активной сессии:
  - Списание $0.05 × max(1, guests) за минуту
  - balance_warning при ≤ 2 минутах
  - session_ended при balance ≤ 0
- MIN_BALANCE_TO_START = $0.25

### 7. billing_db.py — БД биллинга (~220 строк)

- `migrate()` — ALTER TABLE users ADD COLUMN (balance, is_email_verified, bonus_given, email_verify_token) + CREATE TABLE payments
- `get_user_balance()`, `update_balance()`, `deduct_session_cost()`
- `generate_verify_token()`, `verify_email_token()` → $3 бонус
- `create_payment_record()`, `confirm_stripe_payment()`
- `admin_adjust_balance()`, `get_user_by_id()`, `get_all_payments()`

### 8. vox_db.py — Основная БД (~180 строк)

- SQLite с WAL mode + foreign keys
- Таблицы: users, sessions, reviews
- Auth: `register_user()`, `login_user()`, `get_user_by_token()` (salted SHA-256)
- Reviews: `add_review()`, `get_reviews()`, `approve_review()`, `delete_review()`
- `get_all_users()` для админки
- Session tokens: 30-дневный TTL

### 9. audio_utils.py — Аудио утилиты (~100 строк)

- `pcm_bytes_to_numpy()` — бинарные PCM → numpy float32
- `validate_audio_chunk()` — проверка NaN/Inf, диапазон [-1.0, 1.0]
- `normalize_audio()` — нормализация в [-1.0, 1.0]
- `resample_audio()` — ресэмплинг через numpy интерполяцию
- Используется в transcriber.py для ресэмплинга перед отправкой в Deepgram

---

## БИЛЛИНГ-МОДЕЛЬ

| Параметр | Значение |
|----------|----------|
| Solo | $0.05/мин |
| Room | $0.05 × кол-во гостей/мин |
| Бонус за email | $3.00 (однократно) |
| Минимум для старта | $0.25 |
| Пополнение | $5 / $10 / $20 / $50 через Stripe |
| Предупреждение | За 2 минуты до исчерпания |
| Auto-stop | При balance ≤ 0 |

---

## ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ

```env
# Обязательные
DEEPGRAM_API_KEY=dg_...
OPENAI_API_KEY=sk-...
PORT=8080

# Биллинг
STRIPE_SECRET_KEY=sk_...
STRIPE_WEBHOOK_SECRET=whsec_...
RESEND_API_KEY=re_...
BASE_URL=https://your-domain.com

# Админка
ADMIN_LOGIN=admin
ADMIN_PASSWORD=...

# Настройки
DEFAULT_TARGET_LANG=uk
MAX_ROOM_PARTICIPANTS=10
ROOM_TIMEOUT_MINUTES=120
```

---

## КЛЮЧЕВЫЕ АРХИТЕКТУРНЫЕ РЕШЕНИЯ

### 1. Deepgram вместо Whisper + Silero VAD
- **Было:** faster-whisper + Silero VAD → 3-5с задержка, ~800МБ RAM, torch
- **Стало:** Deepgram Nova-2 Streaming → ~300мс, 0 CPU/RAM локально
- **Бонус:** interim results, встроенный VAD, punctuation, smart_format

### 2. edge-tts как основной TTS
- **Было:** OpenAI TTS primary → edge-tts fallback
- **Стало:** edge-tts primary (бесплатно, ~300мс) → OpenAI TTS fallback
- **Экономия:** ~$0.015 за фразу × тысячи фраз

### 3. Серверный антиэхо (Solo)
- TTS из динамика → микрофон → бесконечный цикл
- Решение: флаг `tts_playing`, клиент шлёт `tts_done`

### 4. Lazy start Deepgram
- Клиент шлёт `{type: "config", source_lang, target_lang}` при коннекте
- Deepgram стартует только после получения конфига с правильным языком

### 5. Per-speaker Deepgram instances
- Отдельный DeepgramTranscriber для хоста и каждого спикера-гостя
- Только один активен в любой момент — thread-safety

### 6. Контекстный перевод (Rolling History)
- Последние 5 пар source/translation в system prompt
- clear_context() при смене спикера — без кросс-контаминации

### 7. Speaker tracking в Room
- speaker_changed broadcast всем
- speaker_name в каждом transcript/translation
- Постоянный индикатор "🎤 Name is speaking..."

### 8. Guest pre-roll буфер
- Аудио копится пока гость в REQUESTING
- При grant_speak → flush pre-roll в WebSocket
- Начало фразы не теряется

### 9. Billing tick
- asyncio.create_task для фонового списания каждые 60 сек
- Независим от аудио-потока
- Graceful session termination с предупреждением

---

## FRONTEND-СТРАНИЦЫ

### index.html — Landing + Кабинет (~1427 строк)
- Тёмная тема: фиолетовый акцент (#7c6aff), шрифты Syne + DM Sans
- Hero-секция: анимированный заголовок, CTA "Start Translating"
- Features: 6 карточек с иконками
- Supported languages: 15 флагов
- Auth модалка: регистрация / логин
- Кабинет: баланс, email верификация, Stripe пополнение, выход
- Публичные отзывы: карусель
- PWA: Service Worker регистрация

### host.html — Интерфейс хоста
- Выбор режима: Solo / Room
- Solo: языки, кнопка записи, транскрипт, перевод, история
- Room: создание комнаты, QR-код, список участников, управление
- Room panel: grant/revoke/mute/kick для каждого участника
- Speaker indicator, multi-language translation display
- Billing integration: auth token → WebSocket

### guest.html — Интерфейс участника
- Мультиязычный UI (15 языков, i18n объект)
- i18n переключается автоматически при выборе языка
- Hand-raise кнопка (локализованная)
- Speaker indicator
- Conversation history с именами спикеров
- Auto-reconnect (каждые 3 сек, кроме кода 4004)

### admin.html — Админ-панель
- Basic Auth
- Вкладки: Users, Reviews, Payments
- Users: CRUD, баланс, активация/деактивация
- Reviews: approve/delete
- Payments: лог всех транзакций

### review_form.html — Форма отзыва
- 3 языка (UK/EN/DE)
- Star rating (1-5)
- Auth-aware: показывает имя если залогинен
- Source tracking: landing / host

### docs.html — Документация / О продукте
- 3 языка (UK/EN/DE)
- Обзор продукта, как работает, API, тарификация
- Matching design system (тёмная тема, фиолетовый акцент)

### sw.js — Service Worker
- Network-first с fallback на кеш
- Precache: /host, /manifest.json, icons
- Исключения: WebSocket, API, /room/ — всегда в сеть

---

## ЗАВИСИМОСТИ (requirements.txt)

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
websockets==12.0
python-multipart==0.0.9
python-dotenv==1.0.0
numpy==1.26.4
soundfile==0.12.1
edge-tts>=7.0.0
openai>=1.60.0
setuptools>=69.0.0,<78
qrcode[pil]==7.4.2
Pillow==10.4.0
requests
httpx>=0.27.0
```

**Биллинг-зависимости (в requirements.txt или отдельно):**
```
stripe
resend
```

**Размер:** ~50MB (вместо ~800MB с torch/whisper в v2.0)

---

## ТЕСТИРОВАНИЕ

### Solo
1. WebSocket config → Deepgram start с правильным языком
2. PCM float32 → Deepgram → TranscriptResult
3. Контекстный перевод: "I do" в контексте свадьбы → "Согласен"
4. Антиэхо: tts_playing мьютит вход, tts_done снимает
5. edge-tts fallback → OpenAI TTS при 403
6. Billing tick: списание, предупреждение, auto-stop

### Room
7. Создание комнаты → room_id + QR
8. Подключение → participant + уведомление хосту
9. Hand-raise → request → grant → speak → revoke
10. speaker_changed → все участники видят имя
11. Pre-roll: аудио из REQUESTING флашится при grant
12. Мьют → участник не получает аудио
13. Кик → WebSocket закрыт
14. Параллельный перевод → N языков за < 2 сек
15. Хост видит [EN] + [DE] + [PL] одновременно
16. Хост слышит аудио только от гостей
17. История с speaker_name
18. Reconnect: код 4004 → не переподключается к мёртвой комнате
19. Grace period: 30 сек при дропе хоста

### Auth / Billing
20. Регистрация → token + verification email
21. Email verification → $3 бонус (однократно)
22. Stripe Checkout → webhook → balance update
23. Admin: CRUD users, reviews, payments

---

## ТЕХНИЧЕСКИЕ ЗАМЕЧАНИЯ

1. **Deepgram:** Бесплатный tier — 200 мин/мес. Продакшн: ~$0.0043/мин Nova-2.
2. **edge-tts:** Microsoft периодически обновляет TrustedClientToken. При 403 → `pip install edge-tts --upgrade`. OpenAI TTS fallback автоматический.
3. **HTTPS:** AudioWorklet + getUserMedia требуют HTTPS. Localhost работает без. Railway — auto HTTPS.
4. **Railway:** ~100MB RAM. Бесплатного тира достаточно для тестирования.
5. **asyncio.gather():** Параллельный перевод + TTS. Задержка = одного запроса, не N.
6. **Per-speaker Deepgram:** Только один активен. grant_speak → хост стоп, гость старт.
7. **WebSocket reconnect (guest):** Каждые 3 сек, кроме 4004 (комната не найдена).
8. **SQLite WAL:** Позволяет concurrent reads. Для масштабирования → PostgreSQL.
9. **Stripe test mode:** Тестовые ключи sk_test_... для разработки.

---

## СРАВНЕНИЕ С КОНКУРЕНТАМИ

| Функция | VOX | Google Translate | Zoom Translation | iTranslate |
|---------|-----|-----------------|-----------------|------------|
| Приложение для участников | Не нужно | Нужно | Нужно | Нужно |
| Мультиязычная комната | ✅ | ❌ | ✅ (живые переводчики) | ❌ |
| Хост контролирует всё | ✅ | ❌ | Частично | ❌ |
| AI перевод в реальном времени | ✅ | Частично | ❌ (люди) | Частично |
| Работает в браузере | ✅ | ❌ | ✅ | ❌ |
| Бесплатный вход для участников | ✅ | ✅ | ❌ | ❌ |

---

## ROADMAP

### Фаза 1: Ядро ✅
Solo + Room, Deepgram STT, GPT перевод, edge-tts, hand-raise, speaker tracking

### Фаза 1.5: Монетизация ✅
Auth, billing (Stripe), email verification, admin panel, reviews

### Фаза 2: Рост (в работе)
- Product Hunt launch
- Crowdfunding campaign (Kickstarter/Indiegogo)
- Документация и marketing pages
- Performance optimization

### Фаза 3: Масштабирование (планы)
- WebRTC видеоконференция
- Streaming TTS (chunked) для меньшей задержки
- PostgreSQL вместо SQLite
- Suб-комнаты (breakout rooms)
- Запись встречи (аудио + стенограмма)
- Автоопределение языка
- Роли: несколько со-хостов
- Telegram/Slack интеграция

---

*VOX — часть экосистемы AI-продуктов.*
*AURA — healthcare voice assistant | ATHENA — smart companion | VOX — real-time translation*

*Спецификация v3.1 — Март 2026*
