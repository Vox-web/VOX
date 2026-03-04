# VOX — Real-Time AI Translation Platform
## Полная техническая спецификация проекта (v3.0)

---

## 🎯 ЦЕЛЬ ПРОЕКТА

Создать мультиязычную платформу **VOX** для перевода речи в режиме реального времени с задержкой не более 1.5 секунд. Платформа работает в двух режимах: персональный (один слушатель — один спикер) и Web-комната (множество участников, каждый слышит перевод на своём языке).

**Ключевая философия:** Один человек (хост) управляет всем через свой смартфон. Участникам не нужно скачивать приложение — только отсканировать QR-код и открыть веб-страницу в браузере.

**Сценарии использования:**
- Деловые переговоры с иностранными партнёрами (офлайн, все в одной комнате)
- Международные вебинары и онлайн-встречи (дистанционно, с видео)
- Экскурсии для мультиязычных групп
- Медицинские консультации с иностранными пациентами
- Конференции и ассамблеи — синхронный перевод в карманном формате

**Разработчик:** Python-разработчик с опытом работы с FastAPI, базовыми знаниями фронтенда.

**Деплой:** GitHub → Railway.app (облако).

---

## 🔀 РЕЖИМЫ РАБОТЫ

### Режим 1: Персональный (Solo)
Один слушатель, один спикер. Пользователь надевает наушники, нажимает кнопку «Слушать» и слышит перевод речи спикера на своём языке. Серверный антиэхо-механизм мьютит микрофон на время воспроизведения TTS.

### Режим 2: Web-комната (Room)
Хост создаёт комнату → на экране появляется QR-код → участники сканируют и попадают на веб-страницу → выбирают свой язык → слушают перевод. Хост управляет всем: даёт слово, заглушает, отключает.

**Подрежимы Web-комнаты:**

- **Офлайн (все рядом):** Участники физически в одной комнате. Наушники обязательны. Только аудиоперевод.
- **Онлайн без видео:** Голосовая комната с переводом. Участники на дистанции.
- **Онлайн с видео (Фаза 2):** Полноценная видеоконференция с AI-переводом через WebRTC.

**Оба режима** доступны из единого интерфейса `host.html` — хост выбирает Solo или Room на стартовом экране.

---

## 🏗️ АРХИТЕКТУРА СИСТЕМЫ

### Персональный режим (Solo)

```
[Микрофон телефона/ноутбука]
        ↓ (WebSocket, непрерывный аудиопоток 16кГц PCM float32→int16)
[FastAPI WebSocket Server]
        ↓ (WebSocket config: source_lang, target_lang)
[Deepgram Nova-2 Streaming API — транскрипция в реальном времени]
   ├── interim results → preview в UI (мгновенно)
   └── speech_final → финальный текст для перевода
        ↓ (текст на языке оригинала)
[GPT-4o-mini — контекстный перевод (с rolling history)]
        ↓ (текст на целевом языке)
[edge-tts (primary) / OpenAI TTS (fallback) — синтез речи]
        ↓ (аудио MP3)
[WebSocket → браузер → наушники пользователя]
        ↓
[Антиэхо: сервер мьютит вход пока TTS играет, клиент шлёт tts_done]
```

### Режим Web-комнаты (Room)

```
[Активный спикер говорит в микрофон]
        ↓ (WebSocket, PCM float32)
[FastAPI Server — RoomManager]
        ↓ (per-speaker Deepgram instance)
[Deepgram Nova-2 Streaming — транскрипция]
        ↓ (текст на языке спикера + speaker_name)
[asyncio.gather — ПАРАЛЛЕЛЬНЫЙ перевод на N языков]
   ├── GPT-4o-mini → польский
   ├── GPT-4o-mini → английский
   └── GPT-4o-mini → немецкий  (и т.д.)
        ↓
[asyncio.gather — ПАРАЛЛЕЛЬНЫЙ TTS для N языков]
   ├── edge-tts → аудио на польском
   ├── edge-tts → аудио на английском
   └── edge-tts → аудио на немецком  (и т.д.)
        ↓
[WebSocket → каждому участнику ЕГО аудиопоток + speaker_name]
[WebSocket → хосту: текст перевода на все языки + аудио только от гостей]
```

**Критически важно:** Все запросы перевода и TTS летят параллельно через `asyncio.gather()`. Никакой последовательной обработки.

**Итоговая задержка:** ~0.8 — 1.5 секунды (Deepgram ~300мс + GPT ~700мс + edge-tts ~300мс). При параллельной обработке — одинаково для любого количества языков.

---

## 👥 УПРАВЛЕНИЕ УЧАСТНИКАМИ (WEB-КОМНАТА)

### Состояния участника (State Machine)

```
LISTENING         — слушает перевод, микрофон выключен (по умолчанию)
REQUESTING        — нажал "Хочу сказать", ждёт разрешения хоста
SPEAKING          — получил разрешение, микрофон активен, его голос переводится всем
MUTED             — заглушен хостом, не слышит перевод и не может говорить
```

### Переходы между состояниями

```
                    Хост нажимает "Заглушить"
LISTENING ──────────────────────────────────────→ MUTED
    ↑                                                ↓
    │  Хост нажимает "Включить"                      │
    ←────────────────────────────────────────────────←
    │
    ↓  Участник нажимает "Хочу сказать"
REQUESTING
    ↓  Хост нажимает "Разрешить"
SPEAKING
    ↓  Участник нажимает "Готово" / Хост нажимает "Забрать слово"
LISTENING
```

### Протокол "Поднятой руки"

1. Участник видит кнопку на своём языке: "I want to speak" / "Ich möchte sprechen" / "Хочу сказать" / "我想发言"
2. Нажимает → запрос летит на сервер → хост видит уведомление
3. Хост нажимает «Разрешить» или «Отклонить»
4. При разрешении: надпись "SPEAK NOW" / "ГОВОРИТЕ" / "SPRECHEN SIE" + активация микрофона
5. Участник говорит → перевод всем остальным (включая хосту) на их язык
6. Все участники видят постоянный индикатор "🎤 Obi is speaking..." / "🎤 Host is speaking..."
7. В истории переводов отображается имя спикера рядом со временем

**Правило:** В любой момент времени говорит ТОЛЬКО ОДИН человек. Хост — дирижёр.

---

## 🎛️ ИНТЕРФЕЙС ХОСТА (host.html)

Единый интерфейс для обоих режимов. Стартовый экран — выбор Solo или Room.

### Стартовый экран
```
┌──────────────────────────────────────────────┐
│              V O X                            │
│  [🎧 Solo — Персональный режим]              │
│  [🌐 Room — Создать Web-комнату]             │
└──────────────────────────────────────────────┘
```

### Solo режим
```
┌──────────────────────────────────────────────┐
│  🟢 Connected                                │
│  Исходный: [Русский ▼]  Перевод: [English ▼] │
│  [🎤 СЛУШАТЬ]  [⏹ СТОП]                     │
│  Оригинал: "Привет, как дела?"               │
│  Перевод: "Hi, how are you?"                 │
│  История...                                  │
└──────────────────────────────────────────────┘
```

### Room режим — Панель управления
```
┌──────────────────────────────────────────────┐
│  VOX ROOM: ABC123           [QR-код]         │
│  Участников: 3                               │
├──────────────────────────────────────────────┤
│  🟢 Obi — English          [🔇] [❌]        │
│  🟡 Hans — Deutsch          [✋ Просит]       │
│       [✅ Разрешить] [❌ Отклонить]          │
│  🟢 Ewa — Polski            [🔇] [❌]       │
├──────────────────────────────────────────────┤
│  Активный спикер: Ты (Хост)                  │
│  [🎤 ГОВОРИТЬ]  [⏹ СТОП]                    │
├──────────────────────────────────────────────┤
│  Оригинал: "Давайте обсудим условия..."      │
│  [EN] Let's discuss the terms...             │
│  [DE] Lassen Sie uns die Bedingungen...      │
│  [PL] Omówmy warunki...                      │
│  История (с именами спикеров)...             │
└──────────────────────────────────────────────┘
```

**Хост видит ВСЕ переводы** в формате `[EN] text\n[DE] text\n[PL] text`.
**Хост слышит аудио** только когда говорит гость (один язык — нет смешения).

---

## 📱 ИНТЕРФЕЙС УЧАСТНИКА (guest.html)

Минимальный, мультиязычный (15 языков). i18n переключается при выборе языка.

```
┌──────────────────────────────────────────────┐
│  VOX · Room: ABC123                          │
│  🟢 Verbunden                                │
│                                              │
│  [🖐 Ich möchte sprechen]                    │
│                                              │
│  🎤 Host is speaking...     ← индикатор     │
│                                              │
│  ORIGINAL: "Привет, как дела?"               │
│  ÜBERSETZUNG: "Hallo, wie geht's?"          │
│                                              │
│  VERLAUF:                                    │
│  Host · 20:58                                │
│    Привет, как дела?                         │
│    Hallo, wie geht's?                        │
│  Obi · 20:57                                 │
│    Hello. It's okay.                         │
│    Hallo. Es ist in Ordnung.                 │
└──────────────────────────────────────────────┘
```

---

## 📦 СТЕК ТЕХНОЛОГИЙ

### Бэкенд (Python 3.11+)
| Компонент | Библиотека | Назначение |
|-----------|-----------|------------|
| Web-сервер | `fastapi` + `uvicorn` | HTTP + WebSocket |
| Транскрипция | **Deepgram Nova-2 Streaming API** | Реальный стриминг, ~300мс задержка, zero CPU |
| Перевод | OpenAI API `gpt-4o-mini` | Контекстный перевод с rolling history |
| TTS (основной) | **`edge-tts` (Microsoft)** | Бесплатно, быстро (~300мс) |
| TTS (fallback) | OpenAI API `tts-1` | Платный fallback при ошибке edge-tts |
| Аудио конвертация | `numpy` | float32 → int16 для Deepgram |
| QR-код | `qrcode` + `Pillow` | Генерация QR-кода комнаты |
| Окружение | `python-dotenv` | Переменные окружения |

### Убрано из v2.0 (больше не используется)
| Убрано | Причина |
|--------|---------|
| `faster-whisper` + `ctranslate2` | Заменён на Deepgram Nova-2 |
| `silero-vad` + `torch` + `torchaudio` | VAD встроен в Deepgram |
| `pyannote.audio` | Speaker Profile не реализован в v3 |
| `soundfile`, `librosa` | Не нужны — конвертация через numpy |
| `audio_utils.py` | Мёртвый код от Whisper, удалён |
| `speaker_profile.py` | Не реализован |
| `solo.html` | Solo режим встроен в host.html |

### Фронтенд (чистый HTML/JS, без фреймворков)
| Компонент | Технология | Назначение |
|-----------|-----------|------------|
| Захват аудио | `AudioWorklet API` | Низкоуровневый захват с минимальной задержкой |
| Передача | `WebSocket API` | Постоянное соединение с сервером |
| Воспроизведение | `Web Audio API` | Воспроизведение TTS в наушниках/динамиках |
| Микрофон участника | `getUserMedia API` | Доступ к микрофону через браузер |
| i18n | Встроенный объект `GUEST_I18N` | 15 языков в guest.html |
| Видео (Фаза 2) | `WebRTC API` | Peer-to-peer видеосвязь |
| Стили | Чистый CSS | Светлая тема, адаптивный |

### Инфраструктура
| Компонент | Сервис |
|-----------|--------|
| Хранение кода | GitHub |
| Деплой | Railway.app |
| API ключи | Railway environment variables |
| STT API | Deepgram (deepgram.com) |
| Translation API | OpenAI (platform.openai.com) |

---

## 📁 СТРУКТУРА ПРОЕКТА

```
vox/
├── backend/
│   ├── main.py                 # FastAPI: WebSocket (Solo, Room Host, Room Guest), REST, раздача HTML
│   ├── room_manager.py         # Управление комнатами, участниками, broadcast с speaker_name
│   ├── transcriber.py          # Deepgram Nova-2 Streaming (WebSocket → results queue)
│   ├── translator.py           # GPT-4o-mini перевод (LRU кеш + rolling context)
│   ├── tts_engine.py           # edge-tts (primary) + OpenAI TTS (fallback)
│   ├── host.html               # Интерфейс хоста (Solo + Room) — раздаётся FastAPI
│   ├── guest.html              # Интерфейс участника (15 языков i18n)
│   └── requirements.txt        # Python зависимости
├── Procfile
├── railway.toml
├── .env.example
├── .gitignore
└── README.md
```

---

## 🔧 ДЕТАЛЬНОЕ ОПИСАНИЕ МОДУЛЕЙ

### 1. `backend/main.py` — Главный сервер

```python
# Реализовано:

# 1. FastAPI app с CORS
# 2. Раздача фронтенд-файлов:
#    GET "/" → host.html (Solo + Room)
#    GET "/room/{room_id}" → guest.html
# 3. WebSocket эндпоинты:
#    /ws/solo — персональный режим
#      - Принимает {type: "config", source_lang, target_lang} → lazy start Deepgram
#      - Принимает бинарные аудио чанки (float32 PCM)
#      - Принимает {type: "tts_done"} → снимает серверный мьют
#      - Антиэхо: tts_playing флаг мьютит вход пока TTS играет
#    /ws/room/{room_id}/host — хост комнаты
#      - Бинарные аудио → Deepgram → перевод → broadcast
#      - translate_parallel на все языки участников
#      - Хост получает ВСЕ переводы текстом, аудио только от гостей
#    /ws/room/{room_id}/guest/{guest_id} — участник
#      - Бинарные аудио (только в состоянии SPEAKING)
#      - Действия: request_speak, cancel_request, done_speaking
# 4. REST эндпоинты:
#    POST /room/create — создать комнату (room_id + QR base64)
#    POST /room/{room_id}/join — присоединиться (guest_id)
#    POST /room/{room_id}/kick/{guest_id}
#    POST /room/{room_id}/mute/{guest_id}
#    POST /room/{room_id}/unmute/{guest_id}
#    POST /room/{room_id}/grant-speak/{guest_id}
#    POST /room/{room_id}/revoke-speak/{guest_id}
#    DELETE /room/{room_id}
#    POST /set-config — смена языков Solo
```

### 2. `backend/room_manager.py` — Управление комнатами

```python
# Реализовано:

# === МОДЕЛИ ДАННЫХ ===
# Enum ParticipantState: LISTENING, REQUESTING, SPEAKING, MUTED
# Dataclass Participant: guest_id, display_name, language, state, websocket, joined_at
# Dataclass Room: room_id, host_language, host_websocket, participants, active_speaker, ...

# === КЛАСС RoomManager ===
# create_room() → Room + QR-код
# join_room() → Participant + уведомление хосту
# leave_room() → удаление + уведомление
# request_to_speak() → REQUESTING + уведомление хосту
# grant_speak() → SPEAKING + speaker_changed → ВСЕМ участникам
# revoke_speak() → LISTENING + speaker_changed → ВСЕМ
# mute_participant() / unmute_participant()
# kick_participant() / close_room()
#
# broadcast_translation():
#   - Определяет speaker_name (display_name гостя или "Host")
#   - Отправляет каждому участнику: transcript + translation + audio (на его языке)
#   - Отправляет хосту: transcript + translation + audio (только от гостей)
#   - Все сообщения содержат поле "speaker" для отображения в истории
#
# speaker_changed уведомление:
#   - При grant_speak → всем участникам (кроме спикера) + хосту
#   - При revoke_speak → всем участникам + хосту
#   - Содержит: guest_id, display_name, language
```

### 3. `backend/transcriber.py` — Deepgram Nova-2 Streaming

```python
# Класс DeepgramTranscriber:
#
# __init__():
#   - DEEPGRAM_API_KEY из env
#   - results: asyncio.Queue[TranscriptResult]
#
# start(language: Optional[str]):
#   - Открывает WebSocket к wss://api.deepgram.com/v1/listen
#   - Параметры: model=nova-2, interim_results=true, utterance_end_ms=1000,
#     endpointing=300, encoding=linear16, sample_rate=16000, smart_format=true
#   - language=xx или language=multi (авто)
#   - Запускает _receive_loop как asyncio.Task
#
# send_audio(pcm_float32_bytes):
#   - np.frombuffer → float32 → int16 → ws.send()
#
# stop():
#   - Отправляет CloseStream, закрывает WS
#
# _receive_loop():
#   - Стратегия буферизации:
#     interim (is_final=false) → preview, объединяя накопленные finals
#     is_final без speech_final → накапливает в _finals_buffer
#     speech_final → собирает буфер, отправляет финал для перевода
#
# Dataclass TranscriptResult:
#   text: str, is_final: bool, language: str, confidence: float
```

### 4. `backend/translator.py` — Контекстный перевод

```python
# Класс Translator:
#
# 15 языков: uk, ru, en, de, pl, fr, zh, es, it, pt, ja, ko, ar, tr, hi
#
# translate(text, source_lang, target_lang) -> str:
#   - LRU кеш (50 записей)
#   - Rolling context: последние 5 пар source→translation
#   - Системный промпт: профессиональный синхронный переводчик
#   - ASR-aware: промпт учитывает ошибки распознавания речи
#   - model=gpt-4o-mini, temperature=0.2, max_tokens=300
#
# translate_parallel(text, source_lang, target_langs) -> dict:
#   - asyncio.gather + run_in_executor
#   - Все языки переводятся параллельно
#
# clear_context():
#   - Вызывается при смене спикера в main.py
```

### 5. `backend/tts_engine.py` — Синтез речи

```python
# Класс TTSEngine:
#
# VOICE_MAP: 15 языков с edge и openai голосами
#
# synthesize(text, lang) -> bytes:
#   1. edge-tts (основной, бесплатный, ~300мс)
#   2. OpenAI TTS tts-1 (fallback, платный, ~1-3с)
#   - Автоматический fallback при ошибке edge-tts
#
# synthesize_parallel(translations: dict) -> dict:
#   - asyncio.gather для параллельного синтеза на N языков
```

---

## ⚙️ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (.env)

```env
# Deepgram (транскрипция)
DEEPGRAM_API_KEY=dg_...

# OpenAI (перевод + TTS fallback)
OPENAI_API_KEY=sk-...

# Настройки
DEFAULT_TARGET_LANG=uk
MAX_ROOM_PARTICIPANTS=10
ROOM_TIMEOUT_MINUTES=120

PORT=8080
```

---

## 📋 ЗАВИСИМОСТИ (requirements.txt)

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
websockets>=12.0
python-multipart==0.0.9
python-dotenv==1.0.0

# Аудио конвертация
numpy>=1.26.4

# TTS
edge-tts>=6.1.10

# OpenAI (перевод + TTS fallback)
openai>=1.60.0

# QR-код
qrcode[pil]==7.4.2
Pillow>=10.4.0
```

**Размер зависимостей:** ~50МБ (вместо ~800МБ с torch/whisper в v2.0).

---

## 🔑 КЛЮЧЕВЫЕ АРХИТЕКТУРНЫЕ РЕШЕНИЯ (v3.0)

### 1. Deepgram вместо Whisper + Silero VAD
- **Было:** faster-whisper локально + Silero VAD → 3-5с задержка, ~800МБ RAM, torch зависимость
- **Стало:** Deepgram Nova-2 Streaming API → ~300мс задержка, 0 CPU, 0 RAM
- **Бонус:** interim results (текст появляется по мере речи), встроенный VAD, punctuation

### 2. edge-tts как основной TTS
- **Было:** OpenAI TTS (платный) → edge-tts (fallback)
- **Стало:** edge-tts (бесплатный, ~300мс) → OpenAI TTS (fallback)
- **Экономия:** ~$0.015 за каждую фразу × тысячи фраз

### 3. Серверный антиэхо (Solo)
- **Проблема:** TTS из динамика → микрофон → повторная транскрипция → бесконечный цикл
- **Решение:** Флаг `tts_playing` на сервере, клиент шлёт `{type: "tts_done"}` по окончании
- Браузерное echo cancellation ненадёжно при громкости

### 4. Lazy start Deepgram через WebSocket config
- **Проблема:** Deepgram стартовал с language=multi вместо language=ru
- **Решение:** Клиент шлёт `{type: "config", source_lang, target_lang}` сразу при коннекте
- Deepgram инициализируется только после получения конфига

### 5. Per-speaker Deepgram instances
- **Проблема:** Один инстанс Deepgram для хоста и гостя → thread-safety crash
- **Решение:** Отдельный DeepgramTranscriber для каждого спикера (host_transcriber + guest_transcriber)
- Только один активен в любой момент (хост ИЛИ гость)

### 6. Контекстный перевод (Rolling History)
- **Проблема:** "I do" → "Я делаю" вместо контекстного значения
- **Решение:** Последние 5 пар source/translation передаются в GPT как контекст
- `clear_context()` при смене спикера — предотвращает кросс-контаминацию

### 7. Speaker tracking в Room
- **Проблема:** Участники не видят кто говорит, история без имён
- **Решение:** `speaker_changed` broadcast всем + `speaker_name` в каждом transcript/translation
- Постоянный индикатор "🎤 Obi is speaking..." (отдельный div, не затирается транскриптом)

---

## 🚀 ДЕПЛОЙ НА RAILWAY

### Procfile
```
web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

### railway.toml
```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "uvicorn backend.main:app --host 0.0.0.0 --port $PORT"
restartPolicyType = "on_failure"
```

### Переменные окружения в Railway
```
DEEPGRAM_API_KEY=dg_...
OPENAI_API_KEY=sk-...
PORT=8080
```

---

## 🔄 СТАТУС РАЗРАБОТКИ

### ✅ ФАЗА 1: ЯДРО — ЗАВЕРШЕНА

- ✅ FastAPI сервер с WebSocket (Solo + Room)
- ✅ Deepgram Nova-2 Streaming транскрипция
- ✅ GPT-4o-mini контекстный перевод с параллельными запросами
- ✅ edge-tts + OpenAI TTS fallback с параллельным синтезом
- ✅ RoomManager: создание, подключение, hand-raise, mute, kick
- ✅ host.html: Solo + Room в едином интерфейсе
- ✅ guest.html: мультиязычный UI (15 языков)
- ✅ Серверный антиэхо (Solo)
- ✅ Speaker tracking: индикатор + имена в истории
- ✅ Перевод на все языки хосту одновременно
- ✅ QR-код для Room

### 🔜 ФАЗА 2: ВИДЕОСВЯЗЬ (WebRTC) — НА БУДУЩЕЕ

- Peer-to-peer видео через WebRTC
- STUN/TURN серверы
- Сетка видео в UI
- Подсветка активного спикера

---

## 🧪 ТЕСТИРОВАНИЕ

```python
# === Тесты Solo ===
# Тест 1: WebSocket config → Deepgram start с правильным языком
# Тест 2: PCM float32 → Deepgram → TranscriptResult
# Тест 3: Перевод с контекстом — "I do" в контексте свадьбы → "Согласен"
# Тест 4: Антиэхо — tts_playing мьютит вход, tts_done снимает
# Тест 5: edge-tts fallback → OpenAI TTS при 403

# === Тесты Web-комнаты ===
# Тест 6: Создание комнаты → room_id + QR
# Тест 7: Подключение → participant + уведомление хосту
# Тест 8: Поднятая рука → request → grant → speak → revoke
# Тест 9: speaker_changed → все участники видят имя спикера
# Тест 10: Мьют → участник не получает аудио
# Тест 11: Кик → WebSocket закрыт
# Тест 12: Параллельный перевод → N языков за < 2 сек
# Тест 13: Хост видит [EN] + [DE] + [PL] одновременно
# Тест 14: Хост слышит аудио только от гостей
# Тест 15: История содержит speaker_name
# Тест 16: Reconnect — код 4004 → не переподключается к мёртвой комнате
```

---

## ⚠️ ВАЖНЫЕ ТЕХНИЧЕСКИЕ ЗАМЕЧАНИЯ

1. **Deepgram API ключ:** Бесплатный tier — 200 минут/месяц. Для продакшна нужен платный план (~$0.0043/мин Nova-2).

2. **edge-tts стабильность:** Microsoft периодически обновляет TrustedClientToken. При 403 — `pip install edge-tts --upgrade`. Fallback на OpenAI TTS работает автоматически.

3. **AudioWorklet / getUserMedia:** Требует HTTPS. Localhost работает без HTTPS. Railway — автоматически HTTPS.

4. **Railway ресурсы:** ~100МБ RAM (вместо ~800МБ с torch/whisper). Бесплатного тира Railway достаточно для тестирования.

5. **asyncio.gather() — краеугольный камень:** Параллельная обработка перевода и TTS. Задержка = время одного запроса, не N.

6. **Per-speaker Deepgram:** Только один Deepgram instance активен. При grant_speak хост останавливается, гость стартует. При revoke — наоборот.

7. **WebSocket reconnect:** Гость автоматически переподключается каждые 3 сек, КРОМЕ кода 4004 (комната не найдена).

---

## 💡 ВОЗМОЖНЫЕ УЛУЧШЕНИЯ

- Сохранение стенограммы встречи в PDF (мультиязычной)
- Telegram/Slack интеграция
- Streaming TTS (chunked) для ещё меньшей задержки
- Суб-комнаты (breakout rooms)
- Запись встречи (аудио + стенограмма)
- Роли: несколько со-хостов
- Автоопределение языка (Deepgram detect_language)
- Интеграция с календарями
- Бизнес-версия: брендирование, аналитика, админ-панель
- Speaker Profile (изоляция голоса в шумной среде)

---

## 📊 СРАВНЕНИЕ С КОНКУРЕНТАМИ

| Функция | VOX | Google Translate | Zoom Translation | iTranslate |
|---------|-----|-----------------|-----------------|------------|
| Участникам нужно приложение | ❌ Нет | ✅ Да | ✅ Да | ✅ Да |
| Мультиязычная комната | ✅ | ❌ | ✅ (платно, живые переводчики) | ❌ |
| Хост контролирует всё | ✅ | ❌ | Частично | ❌ |
| AI перевод в реальном времени | ✅ | Частично | ❌ (люди) | Частично |
| Работает в браузере | ✅ | ❌ | ✅ | ❌ |
| Бесплатный вход для участников | ✅ | ✅ | ❌ | ❌ |

**Ключевое преимущество VOX:** Единственное решение, где хост полностью управляет переводом через свой смартфон, а участникам не нужно ничего кроме браузера и наушников.

---

*Проект VOX — часть экосистемы AI-ассистентов разработчика.*
*AURA — healthcare голосовой ассистент*
*ATHENA — smart companion для пожилых людей*
*VOX — реал-тайм мультиязычная переводческая платформа*

*Спецификация v3.0 — 1 марта 2026*
