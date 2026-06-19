"""
VOX — Единый источник пути к базе данных.

ПРОБЛЕМА (аудит): раньше `vox_db.py` использовал относительный путь
"vox.db", а `billing_db.py` — абсолютный "/data/vox.db". Если переменная
окружения VOX_DB_PATH не задана, auth/users/sessions и billing/balance
работали с РАЗНЫМИ файлами БД. Этот модуль устраняет рассинхрон: оба
модуля импортируют один и тот же DB_PATH отсюда.

Правила выбора пути (одинаковые для всех модулей):
  1. Если задан VOX_DB_PATH — используется он (абсолютизируется).
  2. Иначе, если существует записываемый каталог /data (типичный
     persistent volume на Railway) — используется /data/vox.db. Это
     сохраняет текущее поведение прод-окружения.
  3. Иначе — <backend_dir>/vox.db. Путь детерминирован относительно
     расположения файла, а не текущего рабочего каталога, поэтому
     одинаково работает при запуске из корня репозитория и из backend/.

Рекомендация для продакшна: задавайте VOX_DB_PATH явно
(например VOX_DB_PATH=/data/vox.db), чтобы путь не зависел от эвристик.
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger("vox.db_config")

# Каталог, в котором лежит backend-код. Не зависит от cwd.
BACKEND_DIR = Path(__file__).resolve().parent


def _candidate_data_dir_usable() -> bool:
    """True, если каталог /data существует и доступен для записи."""
    data_dir = Path("/data")
    try:
        return data_dir.is_dir() and os.access(data_dir, os.W_OK)
    except OSError:
        return False


def resolve_db_path() -> Path:
    """Вернуть единый абсолютный путь к файлу БД по правилам модуля."""
    env_path = os.environ.get("VOX_DB_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()

    if _candidate_data_dir_usable():
        return Path("/data/vox.db")

    return (BACKEND_DIR / "vox.db").resolve()


# Единый путь к БД для всего приложения.
DB_PATH = resolve_db_path()


def ensure_db_ready(db_path: Path | None = None) -> Path:
    """
    Startup-проверка: каталог БД существует и доступен для записи.

    Создаёт родительский каталог при необходимости. Бросает понятную
    ошибку, если записать невозможно — чтобы проблема всплыла на старте,
    а не в момент первого запроса.
    """
    path = Path(db_path) if db_path is not None else DB_PATH
    parent = path.parent

    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"VOX DB: невозможно создать каталог {parent!s} для БД ({e}). "
            f"Проверьте VOX_DB_PATH и права доступа."
        ) from e

    if not os.access(parent, os.W_OK):
        raise RuntimeError(
            f"VOX DB: каталог {parent!s} недоступен для записи. "
            f"Проверьте VOX_DB_PATH и права доступа."
        )

    logger.info(
        "🗄️ VOX DB path: %s (exists=%s, dir_writable=%s)",
        path, path.exists(), os.access(parent, os.W_OK),
    )
    return path
