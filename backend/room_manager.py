"""
VOX — Управление комнатами и участниками

Модуль отвечает за:
1. Создание и закрытие комнат
2. Управление участниками (подключение, отключение, состояния)
3. Протокол "поднятой руки"
4. Рассылку переводов каждому участнику на его языке
5. Генерацию QR-кода для подключения
"""

import asyncio
import io
import base64
import logging
import string
import random
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import os

from fastapi import WebSocket

logger = logging.getLogger("vox.room")

GRACE_PERIOD_SECONDS = 30  # час очікування перед закриттям кімнати після дропу хоста


# ---------------------------------------------------------------------------
# Модели данных
# ---------------------------------------------------------------------------
class ParticipantState(str, Enum):
    """Состояния участника в комнате."""
    LISTENING = "listening"       # слушает перевод, микрофон выкл
    REQUESTING = "requesting"    # нажал "Хочу сказать", ждёт разрешения
    SPEAKING = "speaking"        # получил разрешение, микрофон активен
    MUTED = "muted"              # заглушен хостом


@dataclass
class Participant:
    """Участник комнаты."""
    guest_id: str
    display_name: str
    language: str                           # "en", "de", "pl", ...
    state: ParticipantState = ParticipantState.LISTENING
    websocket: Optional[WebSocket] = None
    joined_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "guest_id": self.guest_id,
            "display_name": self.display_name,
            "language": self.language,
            "state": self.state.value,
            "joined_at": self.joined_at.isoformat(),
        }


@dataclass
class Room:
    """Комната для мультиязычного перевода."""
    room_id: str
    host_language: str
    host_websocket: Optional[WebSocket] = None
    participants: dict[str, Participant] = field(default_factory=dict)
    active_speaker: Optional[str] = None   # guest_id або None (= хост / ніхто)
    created_at: datetime = field(default_factory=datetime.utcnow)
    max_participants: int = 10
    host_disconnected_at: Optional[datetime] = None   # час дропу хоста (grace period)
    _close_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "room_id": self.room_id,
            "host_language": self.host_language,
            "participant_count": len(self.participants),
            "max_participants": self.max_participants,
            "active_speaker": self.active_speaker,
            "participants": {
                gid: p.to_dict() for gid, p in self.participants.items()
            },
            "created_at": self.created_at.isoformat(),
        }

    def get_unique_languages(self) -> set[str]:
        """Все уникальные языки участников (без хоста)."""
        return {p.language for p in self.participants.values()
                if p.state != ParticipantState.MUTED}


# ---------------------------------------------------------------------------
# RoomManager
# ---------------------------------------------------------------------------
class RoomManager:
    """
    Управление всеми комнатами.
    """

    def __init__(self, base_url: str = ""):
        self.rooms: dict[str, Room] = {}
        self.base_url = base_url  # e.g. "https://vox.railway.app"
        self._counter = 0  # для генерации display_name

    # --- Генерация ID ---
    @staticmethod
    def _generate_room_id(length: int = 6) -> str:
        chars = string.ascii_lowercase + string.digits
        return "".join(random.choices(chars, k=length))

    @staticmethod
    def _generate_guest_id() -> str:
        import uuid
        return uuid.uuid4().hex[:12]

    # --- QR-код ---
    def _generate_qr_code(self, room_id: str) -> str:
        """
        Генерирует QR-код как base64 PNG.
        """
        try:
            import qrcode
            from qrcode.image.pil import PilImage

            base_url = (self.base_url or os.getenv("BASE_URL", "http://localhost:8080")).rstrip("/")
            url = f"{base_url}/room/{room_id}"
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)

            img = qr.make_image(fill_color="#2d2640", back_color="#ffffff")
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            return f"data:image/png;base64,{b64}"
        except ImportError:
            logger.warning("qrcode не установлен, QR-код недоступен")
            return ""

    # =======================================================================
    # Создание / закрытие комнат
    # =======================================================================
    def create_room(self, host_language: str, host_ws: Optional[WebSocket] = None,
                    max_participants: int = 10) -> tuple[Room, str]:
        """
        Создать комнату.

        Returns:
            (Room, qr_code_base64)
        """
        room_id = self._generate_room_id()
        while room_id in self.rooms:
            room_id = self._generate_room_id()

        room = Room(
            room_id=room_id,
            host_language=host_language,
            host_websocket=host_ws,
            max_participants=max_participants,
        )
        self.rooms[room_id] = room

        qr = self._generate_qr_code(room_id)
        logger.info(f"🏠 Комната '{room_id}' создана (хост: {host_language})")
        return room, qr

    # =======================================================================
    # Grace period — хост тимчасово відключився
    # =======================================================================

    async def set_host_disconnected(self, room_id: str):
        """
        Хост дропнув з'єднання. Запускає grace period замість миттєвого закриття.
        Учасники отримують повідомлення що хост тимчасово відключився.
        """
        room = self.rooms.get(room_id)
        if not room:
            return
        room.host_disconnected_at = datetime.utcnow()
        room.host_websocket = None

        for p in room.participants.values():
            if p.websocket and self._is_ws_open(p.websocket):
                try:
                    await p.websocket.send_json({"type": "host_reconnecting"})
                except Exception:
                    pass

        if room._close_task and not room._close_task.done():
            room._close_task.cancel()
        room._close_task = asyncio.create_task(self._delayed_close(room_id))
        logger.info(f"⏳ Хост дропнув '{room_id}', grace period {GRACE_PERIOD_SECONDS}s")

    async def _delayed_close(self, room_id: str):
        """Закрити кімнату після grace period якщо хост не повернувся."""
        try:
            await asyncio.sleep(GRACE_PERIOD_SECONDS)
            room = self.rooms.get(room_id)
            if room and room.host_disconnected_at is not None:
                logger.info(f"⏰ Grace period закінчився, закриваємо '{room_id}'")
                await self.close_room(room_id)
        except asyncio.CancelledError:
            pass

    def host_reconnected(self, room_id: str, new_ws) -> bool:
        """
        Хост перепідключився в межах grace period.
        Скасовує таймер, відновлює кімнату.
        """
        room = self.rooms.get(room_id)
        if not room:
            return False
        if room._close_task and not room._close_task.done():
            room._close_task.cancel()
            room._close_task = None
        room.host_websocket = new_ws
        room.host_disconnected_at = None
        asyncio.create_task(self._notify_all_participants(room, {"type": "host_reconnected"}))
        logger.info(f"✅ Хост повернувся до '{room_id}'")
        return True

    async def _notify_all_participants(self, room: Room, message: dict):
        for p in room.participants.values():
            if p.websocket and self._is_ws_open(p.websocket):
                try:
                    await p.websocket.send_json(message)
                except Exception:
                    pass

    async def close_room(self, room_id: str):
        """Закрыть комнату, уведомить всех, закрыть WebSocket'ы."""
        room = self.rooms.get(room_id)
        if not room:
            return

        # Уведомляем всех участников
        for p in list(room.participants.values()):
            try:
                if p.websocket:
                    await p.websocket.send_json({"type": "room_closed"})
                    await p.websocket.close(code=1000, reason="Room closed")
            except Exception:
                pass

        # Закрываем WebSocket хоста
        try:
            if room.host_websocket:
                await room.host_websocket.close(code=1000, reason="Room closed")
        except Exception:
            pass

        del self.rooms[room_id]
        logger.info(f"🏠 Комната '{room_id}' закрыта")

    # =======================================================================
    # Управление участниками
    # =======================================================================
    def join_room(self, room_id: str, language: str,
                  display_name: str = "") -> Optional[Participant]:
        """
        Участник присоединяется к комнате.

        Returns:
            Participant или None если комната не найдена / переполнена
        """
        room = self.rooms.get(room_id)
        if not room:
            return None

        if len(room.participants) >= room.max_participants:
            return None

        guest_id = self._generate_guest_id()
        self._counter += 1
        name = display_name or f"Guest {self._counter}"

        participant = Participant(
            guest_id=guest_id,
            display_name=name,
            language=language,
        )
        room.participants[guest_id] = participant

        logger.info(
            f"👤 '{name}' ({language}) → комната '{room_id}' "
            f"[{len(room.participants)}/{room.max_participants}]"
        )
        return participant

    async def leave_room(self, room_id: str, guest_id: str):
        """Участник покидает комнату."""
        room = self.rooms.get(room_id)
        if not room or guest_id not in room.participants:
            return

        participant = room.participants[guest_id]

        # Если он был спикером — сбрасываем и уведомляем всех
        if room.active_speaker == guest_id:
            room.active_speaker = None
            await self._notify_all_participants(room, {
                "type": "speaker_changed",
                "guest_id": None,
                "display_name": None,
                "language": None,
            })
            await self._notify_host(room, {
                "type": "speaker_changed",
                "guest_id": None,
                "display_name": None,
                "language": None,
            })

        del room.participants[guest_id]

        # Уведомляем хоста
        await self._notify_host(room, {
            "type": "participant_left",
            "guest_id": guest_id,
            "display_name": participant.display_name,
        })

        logger.info(f"👤 '{participant.display_name}' покинул комнату '{room_id}'")

    # =======================================================================
    # Протокол "поднятой руки"
    # =======================================================================
    async def request_to_speak(self, room_id: str, guest_id: str):
        """Участник хочет сказать (поднимает руку)."""
        room = self.rooms.get(room_id)
        if not room or guest_id not in room.participants:
            return

        p = room.participants[guest_id]
        if p.state != ParticipantState.LISTENING:
            return  # можно просить только из LISTENING

        p.state = ParticipantState.REQUESTING

        await self._notify_host(room, {
            "type": "speak_request",
            "guest_id": guest_id,
            "display_name": p.display_name,
            "language": p.language,
        })

        logger.info(f"✋ '{p.display_name}' просит слово в '{room_id}'")

    async def grant_speak(self, room_id: str, guest_id: str):
        """Хост даёт слово участнику."""
        room = self.rooms.get(room_id)
        if not room or guest_id not in room.participants:
            return False

        p = room.participants[guest_id]

        # Если кто-то уже говорит — сначала забираем слово
        if room.active_speaker and room.active_speaker != guest_id:
            await self.revoke_speak(room_id, room.active_speaker)

        p.state = ParticipantState.SPEAKING
        room.active_speaker = guest_id

        # Уведомляем участника
        if p.websocket:
            try:
                await p.websocket.send_json({
                    "type": "speak_granted",
                })
            except Exception:
                pass

        # Уведомляем хоста
        await self._notify_host(room, {
            "type": "speaker_changed",
            "guest_id": guest_id,
            "display_name": p.display_name,
            "language": p.language,
        })

        # Уведомляем всех участников кто сейчас говорит
        for gid, participant in room.participants.items():
            if gid == guest_id:
                continue  # спикеру уже отправили speak_granted
            if participant.websocket and self._is_ws_open(participant.websocket):
                try:
                    await participant.websocket.send_json({
                        "type": "speaker_changed",
                        "guest_id": guest_id,
                        "display_name": p.display_name,
                        "language": p.language,
                    })
                except Exception:
                    pass

        logger.info(f"🎤 '{p.display_name}' получил слово в '{room_id}'")
        return True

    async def revoke_speak(self, room_id: str, guest_id: str):
        """Забрать слово у участника."""
        room = self.rooms.get(room_id)
        if not room or guest_id not in room.participants:
            return

        p = room.participants[guest_id]
        p.state = ParticipantState.LISTENING
        if room.active_speaker == guest_id:
            room.active_speaker = None

        if p.websocket:
            try:
                await p.websocket.send_json({"type": "speak_revoked"})
            except Exception:
                pass

        await self._notify_host(room, {
            "type": "speaker_changed",
            "guest_id": None,
            "display_name": None,
            "language": None,
        })

        # Уведомляем всех участников что спикер сменился
        for gid, participant in room.participants.items():
            if participant.websocket and self._is_ws_open(participant.websocket):
                try:
                    await participant.websocket.send_json({
                        "type": "speaker_changed",
                        "guest_id": None,
                        "display_name": None,
                        "language": None,
                    })
                except Exception:
                    pass

        logger.info(f"🔇 '{p.display_name}' — слово забрано в '{room_id}'")

    async def cancel_request(self, room_id: str, guest_id: str):
        """Участник отменяет запрос на речь."""
        room = self.rooms.get(room_id)
        if not room or guest_id not in room.participants:
            return

        p = room.participants[guest_id]
        if p.state == ParticipantState.REQUESTING:
            p.state = ParticipantState.LISTENING
            await self._notify_host(room, {
                "type": "request_cancelled",
                "guest_id": guest_id,
            })

    async def deny_speak(self, room_id: str, guest_id: str):
        """Хост отклоняет запрос на речь."""
        room = self.rooms.get(room_id)
        if not room or guest_id not in room.participants:
            return

        p = room.participants[guest_id]
        p.state = ParticipantState.LISTENING

        if p.websocket:
            try:
                await p.websocket.send_json({"type": "speak_denied"})
            except Exception:
                pass

    # =======================================================================
    # Мьют / Кик
    # =======================================================================
    async def mute_participant(self, room_id: str, guest_id: str):
        room = self.rooms.get(room_id)
        if not room or guest_id not in room.participants:
            return

        p = room.participants[guest_id]
        if room.active_speaker == guest_id:
            room.active_speaker = None
        p.state = ParticipantState.MUTED

        if p.websocket:
            try:
                await p.websocket.send_json({"type": "muted"})
            except Exception:
                pass

        logger.info(f"🔇 '{p.display_name}' заглушен в '{room_id}'")

    async def unmute_participant(self, room_id: str, guest_id: str):
        room = self.rooms.get(room_id)
        if not room or guest_id not in room.participants:
            return

        p = room.participants[guest_id]
        p.state = ParticipantState.LISTENING

        if p.websocket:
            try:
                await p.websocket.send_json({"type": "unmuted"})
            except Exception:
                pass

        logger.info(f"🔊 '{p.display_name}' включён в '{room_id}'")

    async def kick_participant(self, room_id: str, guest_id: str):
        room = self.rooms.get(room_id)
        if not room or guest_id not in room.participants:
            return

        p = room.participants[guest_id]
        if room.active_speaker == guest_id:
            room.active_speaker = None
            await self._notify_all_participants(room, {
                "type": "speaker_changed",
                "guest_id": None,
                "display_name": None,
                "language": None,
            })
            await self._notify_host(room, {
                "type": "speaker_changed",
                "guest_id": None,
                "display_name": None,
                "language": None,
            })

        if p.websocket:
            try:
                await p.websocket.send_json({"type": "kicked"})
                await p.websocket.close(code=1000, reason="Kicked by host")
            except Exception:
                pass

        del room.participants[guest_id]

        await self._notify_host(room, {
            "type": "participant_left",
            "guest_id": guest_id,
            "display_name": p.display_name,
        })

        logger.info(f"❌ '{p.display_name}' кикнут из '{room_id}'")

    # =======================================================================
    # Рассылка переводов
    # =======================================================================
    @staticmethod
    def _is_ws_open(ws: Optional[WebSocket]) -> bool:
        """Проверить, открыт ли WebSocket."""
        if ws is None:
            return False
        try:
            return (
                ws.client_state.name == "CONNECTED"
                and ws.application_state.name == "CONNECTED"
            )
        except Exception:
            return False

    async def broadcast_translation(
        self,
        room: Room,
        transcript_text: str,
        source_lang: str,
        translations: dict[str, str],
        audio_chunks: dict[str, bytes],
        speaker_guest_id: Optional[str] = None,
    ):
        """
        Разослать перевод каждому участнику на его языке.

        Args:
            room: Комната
            transcript_text: Оригинальный текст
            source_lang: Язык спикера
            translations: {lang_code: translated_text}
            audio_chunks: {lang_code: mp3_bytes}
            speaker_guest_id: ID спикера (None = хост)
        """
        if speaker_guest_id and speaker_guest_id in room.participants:
            speaker_name = room.participants[speaker_guest_id].display_name
        else:
            speaker_name = "Host"
        tasks = []

        for guest_id, participant in room.participants.items():
            # Не отправляем спикеру его же перевод
            if guest_id == speaker_guest_id:
                continue
            # Не отправляем MUTED участникам
            if participant.state == ParticipantState.MUTED:
                continue
            if not participant.websocket:
                continue
            if not self._is_ws_open(participant.websocket):
                continue

            lang = (participant.language or "").split("-")[0].lower()
            translated_text = translations.get(lang)
            # Если перевода нет (участник говорит на том же языке) — только transcript, без translation/audio
            if translated_text is None:
                tasks.append(
                    self._send_transcript_only(
                        participant, transcript_text, source_lang, speaker_name
                    )
                )
                continue

            tasks.append(
                self._send_to_participant(
                    participant, transcript_text, translated_text,
                    source_lang, lang, audio_chunks.get(lang),
                    speaker_name=speaker_name,
                )
            )

        # Отправляем хосту перевод на его язык (если говорит участник)
        if speaker_guest_id and self._is_ws_open(room.host_websocket):
            host_lang = (room.host_language or "").split("-")[0].lower()
            host_translated = translations.get(host_lang)
            # Если перевода нет — только transcript, без translation/audio
            if host_translated is None:
                tasks.append(
                    self._send_transcript_only_host(room, transcript_text, source_lang, speaker_name)
                )
            else:
                tasks.append(
                    self._send_to_host(
                        room, transcript_text, host_translated,
                        source_lang, host_lang, audio_chunks.get(host_lang),
                        speaker_name=speaker_name,
                    )
                )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_to_participant(
        self, participant: Participant,
        original: str, translated: str,
        lang_from: str, lang_to: str,
        audio: Optional[bytes],
        speaker_name: str = "Host",
    ):
        """Отправить перевод одному участнику."""
        if not self._is_ws_open(participant.websocket):
            return
        try:
            await participant.websocket.send_json({
                "type": "transcript",
                "text": original,
                "is_final": True,
                "language": lang_from,
                "speaker": speaker_name,
            })

            await participant.websocket.send_json({
                "type": "translation",
                "text": translated,
                "lang_from": lang_from,
                "lang_to": lang_to,
                "speaker": speaker_name,
            })

            if audio:
                await participant.websocket.send_bytes(b"AUDIO:" + audio)

        except Exception as e:
            logger.error(f"Ошибка отправки участнику {participant.guest_id}: {e}")

    async def _send_to_host(
        self, room: Room,
        original: str, translated: str,
        lang_from: str, lang_to: str,
        audio: Optional[bytes],
        speaker_name: str = "Guest",
    ):
        """Отправить перевод хосту."""
        if not self._is_ws_open(room.host_websocket):
            return
        try:
            await room.host_websocket.send_json({
                "type": "transcript",
                "text": original,
                "is_final": True,
                "language": lang_from,
                "speaker": speaker_name,
                
            })

            await room.host_websocket.send_json({
                "type": "translation",
                "text": translated,
                "lang_from": lang_from,
                "lang_to": lang_to,
                "speaker": speaker_name,
            })

            # Аудио хосту — только когда говорит гость (один язык, нет смешения)
            if audio:
                await room.host_websocket.send_bytes(b"AUDIO:" + audio)

        except Exception as e:
            logger.error(f"Ошибка отправки хосту: {e}")

    async def _send_transcript_only(
        self, participant: Participant,
        original: str, lang_from: str,
        speaker_name: str = "Host",
    ):
        """Отправить только транскрипт участнику (без перевода) — языки совпали."""
        if not self._is_ws_open(participant.websocket):
            return
        try:
            await participant.websocket.send_json({
                "type": "transcript",
                "text": original,
                "is_final": True,
                "language": lang_from,
                "speaker": speaker_name,
            })
            # Сигнал фронту: перевод не нужен, убрать ⏳
            await participant.websocket.send_json({"type": "no_translation"})
        except Exception as e:
            logger.error(f"Ошибка отправки transcript участнику {participant.guest_id}: {e}")

    async def _send_transcript_only_host(
        self, room: Room,
        original: str, lang_from: str,
        speaker_name: str = "Guest",
    ):
        """Отправить только транскрипт хосту (без перевода) — языки совпали."""
        if not self._is_ws_open(room.host_websocket):
            return
        try:
            await room.host_websocket.send_json({
                "type": "transcript",
                "text": original,
                "is_final": True,
                "language": lang_from,
                "speaker": speaker_name,
            })
            # Сигнал фронту: перевод не нужен, убрать ⏳
            await room.host_websocket.send_json({"type": "no_translation"})
        except Exception as e:
            logger.error(f"Ошибка отправки transcript хосту: {e}")

    # =======================================================================
    # Уведомление хоста
    # =======================================================================
    async def _notify_host(self, room: Room, message: dict):
        """Отправить JSON-сообщение хосту."""
        if not self._is_ws_open(room.host_websocket):
            return
        try:
            await room.host_websocket.send_json(message)
        except Exception as e:
            logger.error(f"Ошибка уведомления хоста: {e}")

    async def notify_host_participant_joined(self, room: Room, participant: Participant):
        """Уведомить хоста о подключении участника."""
        await self._notify_host(room, {
            "type": "participant_joined",
            **participant.to_dict(),
        })

    # =======================================================================
    # Утилиты
    # =======================================================================
    def get_room(self, room_id: str) -> Optional[Room]:
        return self.rooms.get(room_id)

    def room_exists(self, room_id: str) -> bool:
        return room_id in self.rooms