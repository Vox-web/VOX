"""
VOX — единый источник версии приложения.

Один source of truth для версии: FastAPI(version=...), /api/build-info и
README ссылаются сюда. Держите в согласии с frontend (VOX_FRONTEND_VERSION
в pwa-install.js) и SW_VERSION в sw.js.
"""

__version__ = "3.2.1"
