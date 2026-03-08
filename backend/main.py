"""
VOX — Real-Time AI Translation Platform
Главный сервер FastAPI

Этап 1.5: Solo + Web-комната (Room)
- Deepgram Nova-2 для streaming транскрипции
- WebSocket для Solo и Room режимов
- Параллельный перевод + TTS + рассылка
"""

import os
import json
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from transcriber import DeepgramTranscriber, TranscriptResult
from translator import Translator
from tts_engine import TTSEngine
from room_manager import RoomManager, ParticipantState
from vox_db import (init_db, register_user, login_user,
                    get_user_by_token, add_review, get_reviews,
                    approve_review, delete_review, get_all_users)

# ---------------------------------------------------------------------------
# Загрузка конфигурации
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("vox")

# ---------------------------------------------------------------------------
# Глобальные компоненты
# ---------------------------------------------------------------------------
translator: Translator | None = None
tts_engine: TTSEngine | None = None
room_manager: RoomManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация компонентов при старте сервера."""
    global translator, tts_engine, room_manager

    logger.info("🚀 VOX сервер запускается...")

    dg_key = os.getenv("DEEPGRAM_API_KEY")
    if not dg_key:
        logger.error("❌ DEEPGRAM_API_KEY не задан! Добавьте в .env")
    else:
        logger.info("✅ Deepgram API key найден")

    translator = Translator()
    logger.info("✅ Переводчик готов")

    tts_engine = TTSEngine()
    logger.info("✅ TTS движок готов")

    base_url = os.getenv("BASE_URL", "")
    room_manager = RoomManager(base_url=base_url)
    logger.info("✅ RoomManager готов")

    logger.info("🟢 VOX сервер готов к работе!")
    yield

    if room_manager:
        for room_id in list(room_manager.rooms.keys()):
            await room_manager.close_room(room_id)
    logger.info("🔴 VOX сервер останавливается...")


# ---------------------------------------------------------------------------
# FastAPI приложение
# ---------------------------------------------------------------------------
app = FastAPI(title="VOX", version="0.3.0", lifespan=lifespan)
init_db()

# ── Billing ──
from billing_db import migrate as billing_migrate
from billing import billing_router, send_verification_email
billing_migrate()
app.include_router(billing_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Используем .resolve() чтобы получить абсолютный путь без ошибок симлинков
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

# ---------------------------------------------------------------------------
# Конфигурация Solo-сессии
# ---------------------------------------------------------------------------
session_config = {
    "target_lang": os.getenv("DEFAULT_TARGET_LANG", "uk"),
    "source_lang": None,
    "is_listening": False,
}

# ---------------------------------------------------------------------------
# Pydantic модели
# ---------------------------------------------------------------------------
class RegisterBody(BaseModel):
    email: str
    name: str
    password: str

class LoginBody(BaseModel):
    email: str
    password: str

class ReviewBody(BaseModel):
    rating: int
    text: str
    source: str = "landing"
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None

ADMIN_LOGIN    = os.getenv("ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "kozerog")


# ===========================================================================
# Статические страницы
# ===========================================================================
@app.get("/")
async def serve_index():
    html_path = FRONTEND_DIR / "index.html"
    if not html_path.exists():
        # Выведет точный путь в логи Railway (в консоль)
        logger.error(f"❌ 404 Ошибка: Файл не найден по пути {html_path}")
        # Выведет точный путь тебе прямо на экран в браузере
        raise HTTPException(
            status_code=404, 
            detail=f"Сбой путей: файл не найден по адресу {html_path}"
        )
    return FileResponse(html_path)


@app.get("/landing")
async def serve_landing():
    html_path = FRONTEND_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(html_path)


@app.get("/host")
async def serve_host():
    html_path = FRONTEND_DIR / "host.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="host.html not found")
    return FileResponse(html_path)


@app.get("/solo")
async def serve_solo():
    html_path = FRONTEND_DIR / "solo.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="solo.html not found")
    return FileResponse(html_path)


@app.get("/admin")
async def serve_admin():
    html_path = FRONTEND_DIR / "admin.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="admin.html not found")
    return FileResponse(html_path)


@app.get("/cabinet")
async def serve_cabinet():
    html_path = FRONTEND_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(html_path)


@app.get("/review")
async def serve_review():
    html_path = FRONTEND_DIR / "review_form.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="review_form.html not found")
    return FileResponse(html_path)


# Алиас: host.html ссылается на /review_form.html?source=host — поддерживаем оба пути
@app.get("/review_form.html")
async def serve_review_form_html():
    html_path = FRONTEND_DIR / "review_form.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="review_form.html not found")
    return FileResponse(html_path)


@app.get("/room/{room_id}")
async def serve_guest(room_id: str):
    if not room_manager.room_exists(room_id):
        raise HTTPException(status_code=404, detail="Room not found")
    html_path = FRONTEND_DIR / "guest.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="guest.html not found")
    return FileResponse(html_path)


# ===========================================================================
# REST: Статус
# ===========================================================================
@app.get("/status")
async def get_status():
    return JSONResponse({
        "status": "ok",
        "transcriber": "deepgram",
        "translator_ready": translator is not None,
        "tts_ready": tts_engine is not None,
        "active_rooms": len(room_manager.rooms) if room_manager else 0,
        "config": session_config,
    })


@app.get("/api/config")
async def api_config():
    """Публичная конфигурация для клиентских страниц. BASE_URL из .env."""
    base_url = os.getenv("BASE_URL", "").rstrip("/")
    return JSONResponse({
        "landing_url": f"{base_url}/landing" if base_url else "/landing",
    })


# ===========================================================================
# REST: Solo конфигурация
# ===========================================================================
@app.post("/set-config")
async def set_config(config: dict):
    if "target_lang" in config:
        lang = config["target_lang"]
        if lang in Translator.SUPPORTED_LANGUAGES:
            session_config["target_lang"] = lang
            logger.info(f"🌐 Solo — язык перевода: {lang}")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    if "source_lang" in config:
        lang = config["source_lang"]
        if lang is None or lang in Translator.SUPPORTED_LANGUAGES:
            session_config["source_lang"] = lang
            logger.info(f"🎤 Solo — язык ввода: {lang or 'auto'}")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    return JSONResponse({"status": "ok", "config": session_config})


# ===========================================================================
# REST: Управление комнатами
# ===========================================================================
@app.post("/room/create")
async def create_room(body: dict):
    host_lang = body.get("host_language", "uk")
    max_p = body.get("max_participants", 10)
    room, qr_code = room_manager.create_room(host_lang, max_participants=max_p)
    return JSONResponse({
        "status": "ok",
        "room_id": room.room_id,
        "qr_code": qr_code,
        "room": room.to_dict(),
    })


@app.post("/room/{room_id}/join")
async def join_room(room_id: str, body: dict):
    language = body.get("language", "en")
    display_name = body.get("display_name", "")
    room = room_manager.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    participant = room_manager.join_room(room_id, language, display_name)
    if not participant:
        raise HTTPException(status_code=400, detail="Room is full")
    await room_manager.notify_host_participant_joined(room, participant)
    return JSONResponse({
        "status": "ok",
        "guest_id": participant.guest_id,
        "display_name": participant.display_name,
        "room": room.to_dict(),
    })


@app.get("/room/{room_id}/status")
async def room_status(room_id: str):
    room = room_manager.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return JSONResponse({"status": "ok", "room": room.to_dict()})


@app.delete("/room/{room_id}")
async def delete_room(room_id: str):
    if not room_manager.room_exists(room_id):
        raise HTTPException(status_code=404, detail="Room not found")
    await room_manager.close_room(room_id)
    return JSONResponse({"status": "ok"})


@app.post("/room/{room_id}/grant-speak/{guest_id}")
async def grant_speak(room_id: str, guest_id: str):
    await room_manager.grant_speak(room_id, guest_id)
    return JSONResponse({"status": "ok"})


@app.post("/room/{room_id}/revoke-speak/{guest_id}")
async def revoke_speak(room_id: str, guest_id: str):
    await room_manager.revoke_speak(room_id, guest_id)
    return JSONResponse({"status": "ok"})


@app.post("/room/{room_id}/deny-speak/{guest_id}")
async def deny_speak(room_id: str, guest_id: str):
    await room_manager.deny_speak(room_id, guest_id)
    return JSONResponse({"status": "ok"})


@app.post("/room/{room_id}/mute/{guest_id}")
async def mute_participant(room_id: str, guest_id: str):
    await room_manager.mute_participant(room_id, guest_id)
    return JSONResponse({"status": "ok"})


@app.post("/room/{room_id}/unmute/{guest_id}")
async def unmute_participant(room_id: str, guest_id: str):
    await room_manager.unmute_participant(room_id, guest_id)
    return JSONResponse({"status": "ok"})


@app.post("/room/{room_id}/kick/{guest_id}")
async def kick_participant(room_id: str, guest_id: str):
    await room_manager.kick_participant(room_id, guest_id)
    return JSONResponse({"status": "ok"})


# ===========================================================================
# WebSocket: Solo режим (Phase 3 proxy: аудіо → Railway → Deepgram)
# ===========================================================================
@app.websocket("/ws/solo")
async def websocket_solo(ws: WebSocket):
    await ws.accept()
    logger.info("🔌 WebSocket підключено (Solo)")

    from billing_db import deduct_session_cost as _deduct
    _user_id = None
    try:
        first_raw = await asyncio.wait_for(ws.receive(), timeout=5.0)
        if first_raw.get("text"):
            first = json.loads(first_raw["text"])
            if first.get("type") == "auth":
                _user = get_user_by_token(first.get("token", ""))
                _user_id = _user["id"] if _user else None
            elif first.get("type") == "config":
                if first.get("source_lang"):
                    session_config["source_lang"] = first["source_lang"]
                if first.get("target_lang"):
                    session_config["target_lang"] = first["target_lang"]
    except (asyncio.TimeoutError, Exception):
        pass

    if _user_id:
        logger.info(f"💳 Solo: user_id={_user_id} (billing active)")
    else:
        logger.info("💳 Solo: anonymous session (no billing)")

    async def billing_tick():
        if not _user_id:
            return
        while True:
            await asyncio.sleep(60)
            try:
                new_balance = _deduct(_user_id, "solo", 0)
                logger.info(f"💸 Solo billing tick: user={_user_id} balance={new_balance:.4f}")
                if new_balance <= 0:
                    await ws.send_json({"type": "session_ended", "reason": "no_balance"})
                    await ws.close()
                    return
                elif new_balance <= 0.10:
                    minutes_left = max(1, round(new_balance / 0.05))
                    await ws.send_json({"type": "balance_warning", "minutes_left": minutes_left})
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"Solo billing tick error: {e}")

    billing_task = asyncio.create_task(billing_tick())

    dg = DeepgramTranscriber()
    src_lang = session_config.get("source_lang") or "uk"
    await dg.start(language=src_lang)

    async def handle_results():
        while True:
            try:
                result = await dg.results.get()
            except asyncio.CancelledError:
                break
            try:
                await ws.send_json({
                    "type": "transcript",
                    "text": result.text,
                    "is_final": result.is_final,
                    "language": result.language,
                    "confidence": result.confidence,
                })
                if result.is_final and result.text.strip():
                    source_lang = result.language or session_config.get("source_lang") or "uk"
                    target_lang = session_config["target_lang"]
                    if source_lang != target_lang:
                        translated = await asyncio.to_thread(
                            translator.translate, result.text, source_lang, target_lang,
                        )
                        await ws.send_json({
                            "type": "translation",
                            "text": translated,
                            "lang_from": source_lang,
                            "lang_to": target_lang,
                        })
                        audio_bytes = await asyncio.to_thread(
                            tts_engine.synthesize, translated, target_lang,
                        )
                        if audio_bytes:
                            await ws.send_bytes(b"AUDIO:" + audio_bytes)
                    else:
                        await ws.send_json({
                            "type": "translation",
                            "text": result.text,
                            "lang_from": source_lang,
                            "lang_to": target_lang,
                            "note": "same_language",
                        })
                    logger.info(f"📝 [{source_lang}→{target_lang}] {result.text[:60]}")
            except Exception as e:
                if "disconnect" in str(e).lower():
                    break
                logger.error(f"Solo result error: {e}")
                break

    result_task = asyncio.create_task(handle_results())

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "bytes" in message and message["bytes"]:
                if not dg.is_active:
                    await dg.start(session_config.get("source_lang") or "uk")
                await dg.send_audio(message["bytes"])
            elif "text" in message and message["text"]:
                try:
                    msg = json.loads(message["text"])
                    msg_type = msg.get("type")
                    if msg_type == "ping":
                        pass
                    elif msg_type == "tts_done":
                        pass
                    elif msg_type == "config":
                        new_src = msg.get("source_lang")
                        new_tgt = msg.get("target_lang")
                        if new_tgt:
                            session_config["target_lang"] = new_tgt
                        if new_src and new_src != session_config.get("source_lang"):
                            session_config["source_lang"] = new_src
                            await dg.stop()
                            await dg.start(language=new_src)
                        src = session_config.get("source_lang")
                        logger.info(f"🎤 Solo config: source={src}, target={session_config['target_lang']}")
                except json.JSONDecodeError:
                    pass
    except WebSocketDisconnect:
        logger.info("🔌 WebSocket відключено (Solo)")
    except Exception as e:
        logger.error(f"❌ Помилка WebSocket Solo: {e}", exc_info=True)
    finally:
        result_task.cancel()
        billing_task.cancel()
        try:
            await result_task
        except (asyncio.CancelledError, Exception):
            pass
        await dg.stop()


# ===========================================================================
# WebSocket: Хост комнаты
# ===========================================================================
@app.websocket("/ws/room/{room_id}/host")
async def websocket_room_host(ws: WebSocket, room_id: str):
    await ws.accept()

    room = room_manager.get_room(room_id)
    if not room:
        await ws.send_json({"type": "error", "message": "Room not found"})
        await ws.close(code=4004, reason="Room not found")
        return

    # Reconnect: якщо хост повертається в межах grace period
    if room.host_disconnected_at is not None:
        room_manager.host_reconnected(room_id, ws)
        logger.info(f"🔄 Хост перепідключився до кімнати '{room_id}'")
    else:
        room.host_websocket = ws
        logger.info(f"🔌 Хост подключён к комнате '{room_id}'")

    # Billing auth — токен може прийти як перше або будь-яке text-повідомлення
    from billing_db import deduct_session_cost as _deduct
    _user_id = None

    dg = DeepgramTranscriber()
    await dg.start(language=room.host_language)

    await ws.send_json({"type": "room_state", "room": room.to_dict()})

    # Keepalive ping від сервера не потрібен — пінг іде з клієнта
    # Але треба відповідати на pong (або просто ігнорувати)

    async def billing_tick():
        nonlocal _user_id
        if not _user_id:
            return
        while True:
            await asyncio.sleep(60)
            try:
                guest_count = len(room.participants)
                new_balance = _deduct(_user_id, "room", guest_count)
                logger.info(f"💸 Room billing tick: user={_user_id} guests={guest_count} balance={new_balance:.4f}")
                if new_balance <= 0:
                    await ws.send_json({"type": "session_ended", "reason": "no_balance"})
                    await ws.close()
                    return
                elif new_balance <= 0.10:
                    minutes_left = max(1, round(new_balance / (0.05 * max(1, guest_count))))
                    await ws.send_json({"type": "balance_warning", "minutes_left": minutes_left})
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"Room billing tick error: {e}")

    billing_tasks = [asyncio.create_task(billing_tick())]

    async def handle_results():
        while True:
            try:
                result = await dg.results.get()
            except asyncio.CancelledError:
                break

            try:
                await ws.send_json({
                    "type": "transcript",
                    "text": result.text,
                    "is_final": result.is_final,
                    "language": result.language,
                    "confidence": result.confidence,
                })

                if result.is_final and result.text.strip():
                    source_lang = result.language

                    await _process_room_speech(room, result, speaker_guest_id=None)

                    target_langs = set()
                    for gid, p in room.participants.items():
                        if p.state != ParticipantState.MUTED and p.language != source_lang:
                            target_langs.add(p.language)

                    if target_langs:
                        translations = await translator.translate_parallel(
                            result.text, source_lang, list(target_langs),
                        )
                        for lang, translated in translations.items():
                            await ws.send_json({
                                "type": "translation",
                                "text": translated,
                                "lang_from": source_lang,
                                "lang_to": lang,
                            })
                    else:
                        await ws.send_json({
                            "type": "translation",
                            "text": result.text,
                            "lang_from": source_lang,
                            "lang_to": source_lang,
                            "note": "same_language",
                        })

            except Exception as e:
                if "disconnect" in str(e).lower():
                    break
                logger.error(f"Host result error: {e}")
                # Не виходимо — продовжуємо чекати наступний результат

    result_task = asyncio.create_task(handle_results())

    try:
        while True:
            message = await ws.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"]:
                if room.active_speaker is not None:
                    continue
                if not dg.is_active:
                    await dg.start(room.host_language)
                await dg.send_audio(message["bytes"])

            elif "text" in message and message["text"]:
                try:
                    msg = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")
                action = msg.get("action")
                guest_id = msg.get("guest_id")

                # Auth для білінгу (може прийти будь-коли на початку)
                if msg_type == "auth" and not _user_id:
                    _user = get_user_by_token(msg.get("token", ""))
                    _user_id = _user["id"] if _user else None
                    if _user_id:
                        logger.info(f"💳 Room: user_id={_user_id} (billing active)")
                        billing_tasks[0].cancel()
                        billing_tasks[0] = asyncio.create_task(billing_tick())
                    continue

                if msg_type == "ping":
                    continue

                if not action or not guest_id:
                    continue

                if action == "grant_speak":
                    await dg.stop()
                    translator.clear_context()
                    await room_manager.grant_speak(room_id, guest_id)
                elif action == "revoke_speak":
                    await room_manager.revoke_speak(room_id, guest_id)
                    translator.clear_context()
                    await dg.start(room.host_language)
                elif action == "deny_speak":
                    await room_manager.deny_speak(room_id, guest_id)
                elif action == "mute":
                    await room_manager.mute_participant(room_id, guest_id)
                elif action == "unmute":
                    await room_manager.unmute_participant(room_id, guest_id)
                elif action == "kick":
                    await room_manager.kick_participant(room_id, guest_id)

                await ws.send_json({"type": "room_state", "room": room.to_dict()})

    except WebSocketDisconnect:
        logger.info(f"🔌 Хост відключився від кімнати '{room_id}'")
        await room_manager.set_host_disconnected(room_id)
    except RuntimeError as e:
        if "disconnect" in str(e).lower():
            logger.info(f"🔌 Хост відключився від кімнати '{room_id}'")
            await room_manager.set_host_disconnected(room_id)
        else:
            logger.error(f"❌ Ошибка WebSocket хоста: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"❌ Ошибка WebSocket хоста: {e}", exc_info=True)
        try:
            await ws.close(code=1011, reason=str(e))
        except Exception:
            pass
    finally:
        result_task.cancel()
        billing_tasks[0].cancel()
        try:
            await result_task
        except (asyncio.CancelledError, Exception):
            pass
        await dg.stop()

# ===========================================================================
# WebSocket: Участник комнаты
# ===========================================================================
@app.websocket("/ws/room/{room_id}/guest/{guest_id}")
async def websocket_room_guest(ws: WebSocket, room_id: str, guest_id: str):
    await ws.accept()

    room = room_manager.get_room(room_id)
    if not room:
        await ws.send_json({"type": "error", "message": "Room not found"})
        await ws.close(code=4004, reason="Room not found")
        return

    participant = room.participants.get(guest_id)
    if not participant:
        await ws.send_json({"type": "error", "message": "Guest not found"})
        await ws.close(code=4004, reason="Guest not found")
        return

    participant.websocket = ws
    logger.info(f"🔌 Участник '{participant.display_name}' подключён к '{room_id}'")

    await room_manager.notify_host_participant_joined(room, participant)

    dg = DeepgramTranscriber()
    guest_speaking = False

    async def handle_results():
        while True:
            try:
                result = await dg.results.get()
            except asyncio.CancelledError:
                break

            try:
                logger.info(
                    f"📝 [GUEST DEBUG] Deepgram результат '{participant.display_name}': "
                    f"is_final={result.is_final} lang={result.language} text='{result.text[:50]}'"
                )
                await ws.send_json({
                    "type": "transcript",
                    "text": result.text,
                    "is_final": result.is_final,
                    "language": result.language,
                    "confidence": result.confidence,
                })

                if result.is_final and result.text.strip():
                    await _process_room_speech(room, result, speaker_guest_id=guest_id)

                    target_lang = room.host_language
                    if target_lang != result.language:
                        translated = await asyncio.to_thread(
                            translator.translate,
                            result.text, result.language, target_lang,
                        )
                    else:
                        translated = result.text
                    await ws.send_json({
                        "type": "translation",
                        "text": translated,
                        "lang_from": result.language,
                        "lang_to": target_lang,
                    })

            except Exception as e:
                if "disconnect" in str(e).lower():
                    break
                logger.error(f"Guest result error: {e}")
                # Не виходимо — продовжуємо чекати наступний результат

    result_task = asyncio.create_task(handle_results())

    try:
        while True:
            message = await ws.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"]:
                chunk_size = len(message["bytes"])
                if participant.state != ParticipantState.SPEAKING:
                    logger.warning(
                        f"🚫 [GUEST DEBUG] Аудио от '{participant.display_name}' проигнорировано — "
                        f"state={participant.state} (ожидается SPEAKING). "
                        f"Байт: {chunk_size}. guest_speaking={guest_speaking}"
                    )
                    if guest_speaking:
                        await dg.stop()
                        guest_speaking = False
                    continue

                if not guest_speaking or not dg.is_active:
                    logger.info(
                        f"🎤 [GUEST DEBUG] Запуск Deepgram для '{participant.display_name}' "
                        f"(lang={participant.language}, guest_speaking={guest_speaking}, dg.is_active={dg.is_active})"
                    )
                    await dg.start(participant.language)
                    guest_speaking = True

                logger.debug(
                    f"🎵 [GUEST DEBUG] Аудио → Deepgram: {chunk_size} байт, "
                    f"dg.is_active={dg.is_active}, ws.state={ws.client_state}"
                )
                await dg.send_audio(message["bytes"])

            elif "text" in message and message["text"]:
                try:
                    msg = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                action = msg.get("action")

                if action == "request_speak":
                    await room_manager.request_to_speak(room_id, guest_id)
                elif action == "cancel_request":
                    await room_manager.cancel_request(room_id, guest_id)
                elif action == "done_speaking":
                    if guest_speaking:
                        await dg.stop()
                        guest_speaking = False
                    translator.clear_context()
                    await room_manager.revoke_speak(room_id, guest_id)

    except WebSocketDisconnect:
        logger.info(f"🔌 Участник '{participant.display_name}' отключился от '{room_id}'")
        if participant.websocket is ws:
            await room_manager.leave_room(room_id, guest_id)
    except RuntimeError as e:
        if "disconnect" in str(e).lower():
            logger.info(f"🔌 Участник '{participant.display_name}' отключился от '{room_id}'")
        else:
            logger.error(f"❌ Ошибка WebSocket участника: {e}", exc_info=True)
        if participant.websocket is ws:
            await room_manager.leave_room(room_id, guest_id)
    except Exception as e:
        logger.error(f"❌ Ошибка WebSocket участника: {e}", exc_info=True)
        try:
            await ws.close(code=1011, reason=str(e))
        except Exception:
            pass
        if participant.websocket is ws:
            await room_manager.leave_room(room_id, guest_id)
    finally:
        result_task.cancel()
        try:
            await result_task
        except (asyncio.CancelledError, Exception):
            pass
        await dg.stop()


# ===========================================================================
# Общая обработка речи в комнате
# ===========================================================================
async def _process_room_speech(
    room,
    result: TranscriptResult,
    speaker_guest_id: str | None,
):
    source_lang = result.language
    target_langs = set()

    for gid, p in room.participants.items():
        if gid == speaker_guest_id:
            continue
        if p.state == ParticipantState.MUTED:
            continue
        if p.language != source_lang:
            target_langs.add(p.language)

    if speaker_guest_id and room.host_language != source_lang:
        target_langs.add(room.host_language)

    if not target_langs:
        await room_manager.broadcast_translation(
            room=room,
            transcript_text=result.text,
            source_lang=source_lang,
            translations={},
            audio_chunks={},
            speaker_guest_id=speaker_guest_id,
        )
        return

    translations = await translator.translate_parallel(
        result.text, source_lang, list(target_langs),
    )
    audio_chunks = await tts_engine.synthesize_parallel(translations)

    await room_manager.broadcast_translation(
        room=room,
        transcript_text=result.text,
        source_lang=source_lang,
        translations=translations,
        audio_chunks=audio_chunks,
        speaker_guest_id=speaker_guest_id,
    )

    logger.info(
        f"📢 Комната '{room.room_id}': "
        f"'{result.text[:40]}...' → {len(translations)} переводов"
    )


# ===========================================================================
# Auth API
# ===========================================================================
@app.post("/api/register")
async def api_register(body: RegisterBody):
    result = register_user(body.email, body.name, body.password)
    if not result["ok"]:
        errors = {
            "email_exists": "Цей email вже зареєстровано",
            "password_too_short": "Пароль занадто короткий (мін. 6 символів)",
        }
        raise HTTPException(400, errors.get(result["error"], result["error"]))
    result["user"].pop("password_hash", None)
    # Отправить верификационный email ($3 бонус)
    try:
        send_verification_email(
            result["user"]["id"],
            result["user"]["email"],
            result["user"]["name"]
        )
    except Exception as _e:
        logger.warning(f"send_verification_email failed: {_e}")
    return result


@app.post("/api/login")
async def api_login(body: LoginBody):
    result = login_user(body.email, body.password)
    if not result["ok"]:
        raise HTTPException(401, "Невірний email або пароль")
    result["user"].pop("password_hash", None)
    return result


@app.get("/api/me")
async def api_me(authorization: Optional[str] = Header(None)):
    token = (authorization or "").replace("Bearer ", "").strip()
    user = get_user_by_token(token) if token else None
    if not user:
        raise HTTPException(401, "Не авторизовано")
    return user


# ===========================================================================
# Reviews API
# ===========================================================================
@app.post("/api/reviews")
async def api_add_review(
    body: ReviewBody,
    authorization: Optional[str] = Header(None)
):
    token = (authorization or "").replace("Bearer ", "").strip()
    user = get_user_by_token(token) if token else None

    result = add_review(
        rating=body.rating,
        text=body.text,
        source=body.source,
        user_id=user["id"] if user else None,
        user_name=user["name"] if user else body.guest_name,
        user_email=user["email"] if user else body.guest_email,
    )
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    return {"ok": True, "message": "Відгук отримано, дякуємо!"}


@app.get("/api/reviews/public")
async def api_public_reviews():
    return get_reviews(approved_only=True, limit=50)


# ===========================================================================
# Admin API
# ===========================================================================
def _check_admin(authorization: Optional[str]):
    if not authorization:
        raise HTTPException(401, "Unauthorized")
    import base64
    try:
        scheme, creds = authorization.split(" ", 1)
        if scheme.lower() == "basic":
            decoded = base64.b64decode(creds).decode()
        else:
            decoded = creds
        login, pwd = decoded.split(":", 1)
        if login != ADMIN_LOGIN or pwd != ADMIN_PASSWORD:
            raise HTTPException(403, "Forbidden")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Unauthorized")


@app.get("/api/admin/reviews")
async def admin_get_reviews(authorization: Optional[str] = Header(None)):
    _check_admin(authorization)
    return get_reviews(approved_only=False, limit=500)


@app.patch("/api/admin/reviews/{review_id}/approve")
async def admin_approve_review(
    review_id: int,
    approved: bool = True,
    authorization: Optional[str] = Header(None)
):
    _check_admin(authorization)
    approve_review(review_id, approved)
    return {"ok": True}


@app.delete("/api/admin/reviews/{review_id}")
async def admin_delete_review(
    review_id: int,
    authorization: Optional[str] = Header(None)
):
    _check_admin(authorization)
    delete_review(review_id)
    return {"ok": True}


@app.get("/api/admin/users")
async def admin_get_users(authorization: Optional[str] = Header(None)):
    _check_admin(authorization)
    users = get_all_users()
    try:
        from billing_db import _conn as _billing_conn
        con = _billing_conn()
        cur = con.cursor()
        for u in users:
            cur.execute("SELECT balance, is_email_verified FROM users WHERE id=?", (u["id"],))
            row = cur.fetchone()
            if row:
                u["balance"] = float(row["balance"] or 0)
                u["is_email_verified"] = bool(row["is_email_verified"])
            else:
                u["balance"] = 0.0
                u["is_email_verified"] = False
        con.close()
    except Exception as _e:
        logger.warning(f"admin_get_users billing: {_e}")
        for u in users:
            u.setdefault("balance", 0.0)
    return users


# ===========================================================================
# PWA — manifest.json, service worker, icons
# ===========================================================================

@app.get("/manifest.json")
async def serve_manifest():
    """
    Динамический манифест PWA.
    BASE_URL берётся из .env, поэтому при смене домена ничего менять не нужно.
    """
    base_url = os.getenv("BASE_URL", "").rstrip("/")
    return JSONResponse({
        "name": "VOX — AI Translation",
        "short_name": "VOX",
        "description": "Real-time AI multilingual translation",
        "start_url": "/host",
        "display": "standalone",
        "background_color": "#09080f",
        "theme_color": "#7c6aff",
        "orientation": "portrait-primary",
        "icons": [
            {
                "src": f"{base_url}/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable"
            },
            {
                "src": f"{base_url}/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ],
        "shortcuts": [
            {
                "name": "Solo Translation",
                "short_name": "Solo",
                "url": "/host",
                "icons": [{"src": f"{base_url}/icons/icon-192.png", "sizes": "192x192"}]
            },
            {
                "name": "Create Room",
                "short_name": "Room",
                "url": "/host",
                "icons": [{"src": f"{base_url}/icons/icon-192.png", "sizes": "192x192"}]
            }
        ]
    }, headers={"Content-Type": "application/manifest+json"})


@app.get("/sw.js")
async def serve_sw():
    """Service Worker для PWA."""
    sw_path = FRONTEND_DIR / "sw.js"
    if sw_path.exists():
        return FileResponse(sw_path, media_type="application/javascript")
    # Минимальный SW если файл не найден
    sw_code = """
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => clients.claim());
self.addEventListener('fetch', e => e.respondWith(fetch(e.request)));
"""
    from fastapi.responses import Response
    return Response(content=sw_code, media_type="application/javascript")


@app.get("/icons/icon-{size}.png")
async def serve_icon(size: str):
    """
    Иконки PWA.
    Сначала ищет файл в frontend/icons/icon-{size}.png
    Если нет — генерирует дефолтную иконку VOX на лету (Pillow).
    
    Чтобы использовать свою иконку:
      Положи файл в  frontend/icons/icon-192.png  и  frontend/icons/icon-512.png
    """
    icons_dir = FRONTEND_DIR / "icons"
    icon_path = icons_dir / f"icon-{size}.png"
    if icon_path.exists():
        return FileResponse(icon_path, media_type="image/png")

    # Генерируем дефолтную иконку
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        px = int(size) if size.isdigit() else 192

        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Фон — скруглённый фиолетовый прямоугольник
        radius = px // 5
        draw.rounded_rectangle([0, 0, px, px], radius=radius,
                                fill=(124, 106, 255, 255))

        # Блик сверху-слева
        draw.ellipse([-px//3, -px//3, px//1.5, px//1.5],
                     fill=(160, 148, 255, 40))

        # Текст VOX
        font_size = px // 3
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        text = "VOX"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((px - tw) / 2, (px - th) / 2 - bbox[1] // 2),
                  text, fill=(255, 255, 255, 255), font=font)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        from fastapi.responses import Response
        return Response(content=buf.read(), media_type="image/png")

    except Exception as e:
        logger.warning(f"Icon generation failed: {e}")
        raise HTTPException(status_code=404, detail="Icon not found. Place icon-192.png and icon-512.png in frontend/icons/")


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info",
    )