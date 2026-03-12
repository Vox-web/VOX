Для отправки изменений из локального репозитория на GitHub обычно используется стандартная последовательность из четырех команд:

1. **Проверка состояния**
Перед началом работы стоит увидеть, какие файлы были изменены:
`git status`
2. **Добавление файлов в индекс (Staging)**
Чтобы подготовить файлы к сохранению, их нужно добавить.
* Добавить конкретный файл: `git add <имя_файла>`
* Добавить все измененные файлы сразу: `git add .`


3. **Создание коммита (Фиксация изменений)**
Нужно сохранить добавленные изменения с описанием того, что было сделано:
`git commit -m "Ваше сообщение о внесенных изменениях"`
4. **Отправка на GitHub (Push)**
Теперь изменения отправляются в удаленный репозиторий:
`git push origin <название_ветки>`
*(Чаще всего основной веткой является `main` или `master`)*

git add .
git commit -m "Ваше сообщение о внесенных изменениях"
git push origin main

Полезный лайфхак
Если вы не хотите каждый раз вводить origin main, при первой отправке используйте флаг -u:
git push -u origin main

После этого Git «запомнит» связь, и в будущем вам достаточно будет писать просто:
git push


---

### Дополнительные полезные команды:

* **git pull** — перед отправкой своих данных полезно скачать актуальную версию проекта из облака, чтобы избежать конфликтов.
* **git remote -v** — позволяет проверить, к какому именно репозиторию на GitHub привязан ваш локальный проект.
* **git log** — просмотр истории ваших предыдущих коммитов.





# VOX — Real-Time AI Translation Platform

Мультиязычная платформа для перевода речи в реальном времени.
15 языков, задержка ~1 сек, участникам нужен только браузер.

🌐 **Live:** [web-production-bd9a.up.railway.app](https://web-production-bd9a.up.railway.app)

---

## Что это

VOX — карманный синхронный переводчик. Один человек (хост) управляет всем через смартфон. Участникам не нужно скачивать приложение — достаточно отсканировать QR-код и открыть страницу в браузере.

**Сценарии:**
- Деловые переговоры с иностранными партнёрами
- Международные вебинары и онлайн-встречи
- Экскурсии для мультиязычных групп
- Медицинские консультации с иностранными пациентами
- Конференции — синхронный перевод в карманном формате

## Режимы работы

### Solo — персональный перевод
Один микрофон, один слушатель. Выбираешь язык источника и перевода, нажимаешь «Слушать» — и слышишь перевод в наушниках в реальном времени.

### Room — мультиязычная комната
Хост создаёт комнату → на экране QR-код → участники сканируют → каждый выбирает свой язык → слышит перевод. Хост управляет: даёт слово, заглушает, отключает.

## Быстрый старт

### 1. Клонировать
```bash
git clone https://github.com/Vox-web/VOX.git
cd VOX
```

### 2. Виртуальное окружение
```bash
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows
```

### 3. Установить зависимости
```bash
pip install -r backend/requirements.txt
```

### 4. Настроить переменные окружения
```bash
cp .env.example .env
```

Минимальные переменные:
```env
DEEPGRAM_API_KEY=dg_...       # deepgram.com — бесплатно 200 мин/мес
OPENAI_API_KEY=sk-...         # platform.openai.com
PORT=8080
```

### 5. Запустить
```bash
cd backend
python main.py
```

### 6. Открыть
```
http://localhost:8080
```

## Структура проекта

```
VOX/
├── backend/
│   ├── main.py              # FastAPI сервер — WebSocket + REST + раздача HTML
│   ├── room_manager.py      # Управление комнатами, участниками, broadcast
│   ├── transcriber.py       # Deepgram Nova-2 Streaming транскрипция
│   ├── translator.py        # GPT-4o-mini контекстный перевод
│   ├── tts_engine.py        # edge-tts (primary) + OpenAI TTS (fallback)
│   ├── audio_utils.py       # PCM конвертация, ресэмплинг, валидация
│   ├── billing.py           # Stripe Checkout, webhook, баланс, email верификация
│   ├── billing_db.py        # Миграции и SQL для биллинга
│   ├── vox_db.py            # SQLite: users, sessions, reviews
│   └── requirements.txt
│
├── frontend/
│   ├── index.html           # Landing page + кабинет пользователя
│   ├── host.html            # Интерфейс хоста (Solo + Room)
│   ├── guest.html           # Интерфейс участника (15 языков, i18n)
│   ├── solo.html            # Отдельная Solo-страница
│   ├── admin.html           # Админ-панель (пользователи, отзывы, платежи)
│   ├── review_form.html     # Форма отзыва (UK/EN/DE)
│   ├── docs.html            # Документация / О продукте (UK/EN/DE)
│   └── sw.js                # Service Worker (PWA, Network-first)
│
├── .env.example
├── .gitignore
├── Procfile
├── railway.toml
├── README.md
└── VOX_PROJECT_SPEC.md
```

## Технологии

| Слой | Технология | Назначение |
|------|-----------|------------|
| Web-сервер | FastAPI + Uvicorn | HTTP, WebSocket, REST API |
| STT | Deepgram Nova-2 Streaming | Транскрипция ~300мс, встроенный VAD |
| Перевод | GPT-4o-mini | Контекстный перевод с rolling history |
| TTS (основной) | edge-tts (Microsoft) | Бесплатный, ~300мс |
| TTS (fallback) | OpenAI TTS tts-1 | Платный, автоматический fallback |
| Биллинг | Stripe Checkout + Webhooks | Пополнение баланса, поминутная тарификация |
| Email | Resend API | Верификация email, $3 бонус |
| БД | SQLite (WAL mode) | Users, sessions, reviews, payments |
| Frontend | Vanilla HTML/JS | AudioWorklet, Web Audio API, WebSocket |

## Поддерживаемые языки

🇺🇦 Українська · 🇷🇺 Русский · 🇬🇧 English · 🇩🇪 Deutsch · 🇵🇱 Polski · 🇫🇷 Français · 🇨🇳 中文 · 🇪🇸 Español · 🇮🇹 Italiano · 🇧🇷 Português · 🇯🇵 日本語 · 🇰🇷 한국어 · 🇸🇦 العربية · 🇹🇷 Türkçe · 🇮🇳 हिन्दी

## Переменные окружения

```env
# Обязательные
DEEPGRAM_API_KEY=dg_...          # STT
OPENAI_API_KEY=sk-...            # Перевод + TTS fallback
PORT=8080

# Биллинг (опционально)
STRIPE_SECRET_KEY=sk_...         # Stripe
STRIPE_WEBHOOK_SECRET=whsec_...  # Stripe Webhooks
RESEND_API_KEY=re_...            # Email верификация
BASE_URL=https://your-domain.com # Для email-ссылок и Stripe redirect

# Админка
ADMIN_LOGIN=admin
ADMIN_PASSWORD=your_secure_password

# Настройки
DEFAULT_TARGET_LANG=uk
MAX_ROOM_PARTICIPANTS=10
ROOM_TIMEOUT_MINUTES=120
```

## Деплой на Railway

1. Создать репозиторий на GitHub и запушить код
2. Подключить репозиторий к [Railway](https://railway.com/)
3. Добавить переменные окружения
4. Deploy

```
# Procfile
web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

## API эндпоинты

### Страницы
| Маршрут | Описание |
|---------|----------|
| `GET /` | Landing page |
| `GET /host` | Интерфейс хоста |
| `GET /solo` | Solo-страница |
| `GET /room/{id}` | Интерфейс гостя |
| `GET /admin` | Админ-панель |
| `GET /review` | Форма отзыва |
| `GET /docs` | Документация |

### WebSocket
| Эндпоинт | Описание |
|----------|----------|
| `WS /ws/solo` | Solo-режим (аудио → перевод → TTS) |
| `WS /ws/room/{id}/host` | Хост комнаты |
| `WS /ws/room/{id}/guest/{gid}` | Участник комнаты |

### REST API
| Метод | Путь | Описание |
|-------|------|----------|
| `POST` | `/api/register` | Регистрация |
| `POST` | `/api/login` | Вход |
| `GET` | `/api/me` | Текущий пользователь |
| `GET` | `/api/balance` | Баланс |
| `POST` | `/api/create-checkout` | Stripe Checkout |
| `POST` | `/api/webhook` | Stripe Webhook |
| `POST` | `/api/reviews` | Оставить отзыв |
| `GET` | `/api/reviews/public` | Публичные отзывы |
| `POST` | `/room/create` | Создать комнату |
| `POST` | `/room/{id}/join` | Присоединиться |
| `POST` | `/room/{id}/grant-speak/{gid}` | Дать слово |
| `POST` | `/room/{id}/revoke-speak/{gid}` | Забрать слово |

## Стоимость

| Режим | Стоимость | Примечание |
|-------|-----------|------------|
| Solo | $0.05/мин | 1 спикер |
| Room | $0.05 × гостей/мин | Параллельный перевод |
| Бонус | $3 при верификации email | ~60 минут Solo |

---

**VOX** — часть экосистемы AI-продуктов: AURA (healthcare voice assistant), ATHENA (smart companion), VOX (real-time translation).

*Версия 3.1 — Март 2026*


Карта: 4242 4242 4242 4242
MM/ГГ: любая будущая дата, например 12/26
CVV: любые 3 цифры, например 123
Имя: любое
Адрес: любой