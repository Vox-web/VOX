"""
VOX — Аудио утилиты

Функции для конвертации и валидации аудио данных.
"""

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger("vox.audio")

SAMPLE_RATE = 16000      # Whisper ожидает 16кГц
CHUNK_SAMPLES = 512      # 32мс при 16кГц
BYTES_PER_SAMPLE = 4     # float32 = 4 байта


def pcm_bytes_to_numpy(data: bytes) -> Optional[np.ndarray]:
    """
    Конвертировать бинарные PCM данные в numpy массив.

    Args:
        data: PCM float32 байты (little-endian)

    Returns:
        numpy float32 массив или None при ошибке
    """
    try:
        if len(data) == 0:
            return None

        # Проверяем что длина кратна размеру float32
        if len(data) % BYTES_PER_SAMPLE != 0:
            logger.warning(
                f"Длина данных ({len(data)}) не кратна {BYTES_PER_SAMPLE}"
            )
            # Обрезаем до кратной длины
            data = data[: len(data) - (len(data) % BYTES_PER_SAMPLE)]

        audio = np.frombuffer(data, dtype=np.float32)
        return audio

    except Exception as e:
        logger.error(f"Ошибка конвертации PCM: {e}")
        return None


def validate_audio_chunk(chunk: np.ndarray) -> bool:
    """
    Проверить аудио чанк на валидность.

    Args:
        chunk: numpy массив

    Returns:
        True если чанк валиден
    """
    if chunk is None or len(chunk) == 0:
        return False

    # Проверяем на NaN и Inf
    if np.any(np.isnan(chunk)) or np.any(np.isinf(chunk)):
        logger.warning("Аудио содержит NaN/Inf значения")
        return False

    # Проверяем диапазон (PCM float32 должен быть в [-1.0, 1.0])
    max_val = np.max(np.abs(chunk))
    if max_val > 10.0:  # Некоторый запас сверх нормы
        logger.warning(f"Аудио значения вне диапазона: max={max_val:.2f}")
        return False

    return True


def normalize_audio(audio: np.ndarray) -> np.ndarray:
    """
    Нормализовать аудио в диапазон [-1.0, 1.0].

    Args:
        audio: numpy float32 массив

    Returns:
        Нормализованный массив
    """
    max_val = np.max(np.abs(audio))
    if max_val > 0:
        return audio / max_val
    return audio


def resample_audio(
    audio: np.ndarray, orig_sr: int, target_sr: int = SAMPLE_RATE
) -> np.ndarray:
    """
    Ресэмплировать аудио к целевой частоте.

    Args:
        audio: Исходный аудио массив
        orig_sr: Исходная частота дискретизации
        target_sr: Целевая частота (по умолчанию 16кГц)

    Returns:
        Ресэмплированный массив
    """
    if orig_sr == target_sr:
        return audio

    # Простой ресэмплинг через интерполяцию
    duration = len(audio) / orig_sr
    target_len = int(duration * target_sr)
    indices = np.linspace(0, len(audio) - 1, target_len)
    resampled = np.interp(indices, np.arange(len(audio)), audio)
    return resampled.astype(np.float32)
