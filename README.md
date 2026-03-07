# VOX — Real-Time AI Translation Platform

Мультиязычная платформа для перевода речи в режиме реального времени.
15 языков, задержка ~1 сек, участникам нужен только браузер.

## Быстрый старт (локально)

### 1. Клонировать и перейти в папку
```bash
cd VOX
```

### 2. Создать виртуальное окружение
```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac
```

### 3. Установить зависимости
```bash
pip install -r backend/requirements.txt
```

### 4. Создать .env файл
```bash
copy .env.example .env
```
Заполнить:
```env
DEEPGRAM_API_KEY=dg_...       # deepgram.com → бесплатно 200 мин/мес
OPENAI_API_KEY=sk-...         # platform.openai.com
PORT=8080
```

### 5. Запустить сервер
```bash
cd backend
python main.py
```

### 6. Открыть в браузере
```
http://localhost:8080
```

## Режимы работы

**Solo** — персональный перевод. Один микрофон, один слушатель. Выбираешь язык источника и перевода, нажимаешь «Слушать».

**Room** — мультиязычная комната. Хост создаёт комнату → участники сканируют QR-код → каждый слышит перевод на своём языке. Хост управляет: даёт слово, заглушает, отключает.

## Деплой на Railway

1. Создать GitHub репозиторий и запушить код
2. Подключить репозиторий к Railway
3. Добавить переменные окружения (`DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, `PORT=8080`)
4. Deploy!

## Структура

```
VOX/
├── backend/
│   ├── main.py              # FastAPI сервер (WebSocket + REST)
│   ├── room_manager.py      # Управление комнатами и участниками
│   ├── transcriber.py       # Deepgram Nova-2 Streaming
│   ├── translator.py        # GPT-4o-mini контекстный перевод
│   ├── tts_engine.py        # edge-tts + OpenAI TTS fallback
│   ├── host.html            # Интерфейс хоста (Solo + Room)
│   ├── guest.html           # Интерфейс участника (15 языков)
│   └── requirements.txt     # Зависимости
├── .env.example
├── .gitignore
├── Procfile
├── railway.toml
└── README.md
```

## Технологии

- **Backend:** FastAPI, WebSocket, Python 3.11+
- **STT:** Deepgram Nova-2 Streaming API (~300мс)
- **Translation:** GPT-4o-mini с контекстом разговора
- **TTS:** edge-tts (primary, бесплатный) + OpenAI TTS (fallback)
- **Frontend:** Vanilla HTML/JS, AudioWorklet API, Web Audio API

## Поддерживаемые языки

🇺🇦 Українська · 🇷🇺 Русский · 🇬🇧 English · 🇩🇪 Deutsch · 🇵🇱 Polski · 🇫🇷 Français · 🇨🇳 中文 · 🇪🇸 Español · 🇮🇹 Italiano · 🇧🇷 Português · 🇯🇵 日本語 · 🇰🇷 한국어 · 🇸🇦 العربية · 🇹🇷 Türkçe · 🇮🇳 हिन्दी



https://deepgram.com/
Аккаунт привязан к obivan.ua@gmaul.com


cd /путь/к/проекту

git init
git add .
git commit -m "initial commit"

# Вставь свою ссылку из GitHub:
git remote add origin https://github.com/Vox-web/VOX.git   
git branch -M main
git push -u origin main

https://railway.com/
https://web-production-bd9a.up.railway.app     -   адрес сайта!

https://www.producthunt.com/    -  опубликовать для продвижения и поиска инвестора уже после биллинга!


Моя тесовая карта
Номер:  4242 4242 4242 4242
Дата:   будь-яка майбутня (напр. 12/27)
CVV:    будь-які 3 цифри (напр. 123)
Адреса: будь-яка