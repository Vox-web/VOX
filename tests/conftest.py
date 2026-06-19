"""
Общая настройка тестов VOX.

ВАЖНО: VOX_DB_PATH выставляется в изолированный временный файл ДО любого
импорта backend-модулей, потому что db_config.DB_PATH вычисляется один раз
при импорте. Так тесты никогда не трогают реальную БД.
"""

import os
import sys
import tempfile
from pathlib import Path

# 1) Изолированная тестовая БД — до импортов backend.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="vox_test_db_")
os.environ.setdefault("VOX_DB_PATH", str(Path(_TEST_DB_DIR) / "vox_test.db"))

# 2) Делаем backend импортируемым как пакетные модули верхнего уровня
#    (так же, как при запуске `cd backend && uvicorn main:app`).
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
