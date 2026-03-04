# 📌 Куди вставити кнопку відгуку в host.html

## 1. CSS — додай перед закриваючим `</style>`:

```css
/* ── Review Button ── */
.review-fab {
    position: fixed;
    bottom: 24px;
    left: 24px;
    z-index: 50;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text2);
    font-size: 13px;
    font-weight: 500;
    padding: 9px 16px;
    border-radius: 99px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 7px;
    box-shadow: var(--shadow);
    transition: all 0.25s;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    text-decoration: none;
}
.review-fab:hover {
    border-color: var(--accent);
    color: var(--accent);
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(124,106,255,0.2);
}
```

---

## 2. HTML — вставь **перед закриваючим `</body>`**:

```html
<!-- ── Review FAB Button ── -->
<a class="review-fab" href="/review_form.html?source=host" target="_blank">
    <span>⭐</span>
    <span>Відгук</span>
</a>
```

---

## 3. Зареєструй нові маршрути в main.py

### 3a. Підключи новий файл `vox_db.py` (скопіюй у папку проєкту).

### 3b. Підключи файл `main_patch.py` (скопіюй у папку проєкту).

### 3c. В `main.py` в розділ імпортів додай:

```python
from vox_db import (
    init_db, register_user, login_user,
    get_user_by_token, add_review, get_reviews,
    approve_review, delete_review, get_all_users
)
from fastapi.middleware.cors import CORSMiddleware
```

### 3d. Після `app = FastAPI(...)` додай CORS і init:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    init_db()
    # ... інший startup код
```

### 3e. Вставь всі маршрути з `main_patch.py` в `main.py`.

---

## 4. Додай статичні файли в main.py

```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

@app.get("/")
async def landing():
    return FileResponse("landing.html")

@app.get("/admin")
async def admin_panel():
    return FileResponse("admin.html")

@app.get("/review_form.html")
async def review_form():
    return FileResponse("review_form.html")
```

---

## 5. Скопіюй файли в папку проєкту:
- `landing.html` → корінь проєкту
- `admin.html`   → корінь проєкту
- `review_form.html` → корінь проєкту
- `vox_db.py`    → корінь проєкту
- `main_patch.py` → тільки для перегляду (код вставити в main.py)

---

## ✅ Готово!

| URL | Сторінка |
|-----|----------|
| `/` | Landing Page |
| `/host` | Host App (existing) |
| `/admin` | Admin Panel (логін: admin / пароль: kozerog) |
| `/review_form.html?source=host` | Форма відгуку |
| `POST /api/register` | Реєстрація |
| `POST /api/login` | Вхід |
| `GET /api/me` | Поточний юзер |
| `POST /api/reviews` | Надіслати відгук |
| `GET /api/admin/reviews` | Адмін: всі відгуки |
| `PATCH /api/admin/reviews/{id}/approve` | Адмін: опублікувати |
| `DELETE /api/admin/reviews/{id}` | Адмін: видалити |
| `GET /api/admin/users` | Адмін: всі юзери |
