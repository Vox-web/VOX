# VOX Billing — Чеклист налаштування

## 1. STRIPE DASHBOARD (stripe.com)

### A. Отримати ключі
1. Зайти в https://dashboard.stripe.com/apikeys
2. Скопіювати **Secret key** (sk_live_... або sk_test_... для тестів)
3. Додати в Railway env: `STRIPE_SECRET_KEY=sk_live_...`

### B. Налаштувати Webhook
1. Dashboard → **Developers → Webhooks → Add endpoint**
2. URL: `https://your-app.railway.app/api/webhook`
3. Events to listen: `checkout.session.completed`
4. Після створення — скопіювати **Signing secret** (whsec_...)
5. Додати в Railway env: `STRIPE_WEBHOOK_SECRET=whsec_...`

### C. Тестування (рекомендовано перед продакшн)
- Використовуй тестові ключі (sk_test_...)
- Тестова картка: `4242 4242 4242 4242`, будь-який CVV, будь-яка майбутня дата
- Stripe CLI для локального тестування вебхуків:
  ```bash
  stripe listen --forward-to localhost:8080/api/webhook
  ```

---

## 2. EMAIL — GMAIL (від акаунта GMAIL_USER)

Листи йдуть ВІД `GMAIL_USER`. На Railway вихідний SMTP заблокований (логи: 465 →
[Errno 101], 587 → timeout), тому в PROD використовується **Gmail API по HTTPS**.
SMTP лишається лише як локальний fallback. Жодного стороннього сервісу/домену.

> ⚠️ App Password (`GMAIL_APP_PASSWORD`) працює ТІЛЬКИ для SMTP. Для Gmail API
> потрібні окремі OAuth2-креди (нижче).

### A. Отримати OAuth2-креди для Gmail API
1. Google Cloud Console → New Project (напр. «VOX Mail»).
2. APIs & Services → Library → увімкнути **Gmail API**.
3. APIs & Services → OAuth consent screen → External → додати свій Gmail у Test users.
4. Credentials → Create Credentials → **OAuth client ID** → type **Web application**.
   - Authorized redirect URI: `https://developers.google.com/oauthplayground`
   - Зберегти **Client ID** і **Client Secret**.
5. https://developers.google.com/oauthplayground → ⚙ (Settings) → «Use your own
   OAuth credentials» → вставити Client ID/Secret.
6. Зліва ввести scope: `https://www.googleapis.com/auth/gmail.send` →
   Authorize APIs → увійти своїм Gmail → Exchange authorization code for tokens.
7. Скопіювати **Refresh token**.

### B. Додати в Railway env
```
GMAIL_USER=youraddress@gmail.com
GMAIL_CLIENT_ID=xxxxx.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=GOCSPX-xxxxxxxx
GMAIL_REFRESH_TOKEN=1//xxxxxxxx
```
(App Password лишай лише для локальної розробки.)

---

## 3. RAILWAY ENV VARIABLES

Додати в Settings → Variables:

```
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
GMAIL_USER=youraddress@gmail.com
GMAIL_CLIENT_ID=xxxxx.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=GOCSPX-xxxxxxxx
GMAIL_REFRESH_TOKEN=1//xxxxxxxx
BASE_URL=https://your-app.railway.app
```

---

## 4. REQUIREMENTS.TXT — додати пакети

```
stripe>=7.0.0
```

---

## 5. MAIN.PY — мінімальні правки

### Місце 1: блок імпортів (після `from vox_db import ...`)
```python
from billing import billing_router, send_verification_email
from billing_db import migrate as billing_migrate
```

### Місце 2: після `init_db()` (рядок ~89)
```python
billing_migrate()
app.include_router(billing_router)
```

### Місце 3: в endpoint `/api/register`, після `result["user"].pop("password_hash", None)`
```python
    if result.get("ok") and result.get("user"):
        try:
            send_verification_email(
                result["user"]["id"],
                result["user"]["email"],
                result["user"]["name"]
            )
        except Exception as _e:
            logger.warning(f"send_verification_email failed: {_e}")
```

---

## 6. ДЕПЛОЙ

```bash
# Railway автоматично перезапускає при пуші
git add billing.py billing_db.py host.html admin.html index.html
git commit -m "feat: add billing system (Stripe + Resend)"
git push
```

---

## 7. ПЕРЕВІРКА ПІСЛЯ ДЕПЛОЮ

- [ ] `GET /api/balance` → повертає `{"ok":true,"balance":0.0}`
- [ ] `POST /api/create-checkout` з body `{"amount":5}` → повертає Stripe URL
- [ ] `GET /api/verify-email?token=test` → редирект на `/host?verified=0`
- [ ] `GET /api/admin/payments` з Basic auth → повертає `[]`
- [ ] В host.html кнопка 💰 з'являється після авторизації
- [ ] В admin.html є таб "Платежі"
- [ ] На index.html видно кнопку "Спробувати безкоштовно — $3 бонус"
