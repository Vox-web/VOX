"""
VOX integration example:
1) move FastAPI Swagger away from /docs
2) serve custom docs and privacy HTML
3) accept the contact form and email it to avotiyaaa@gmail.com

Environment variables:
- CONTACT_TO_EMAIL=avotiyaaa@gmail.com
- GMAIL_USER=your_gmail@gmail.com
- GMAIL_APP_PASSWORD=your_16_char_app_password
- DOCS_DIR=docs

Important:
- Gmail SMTP with app passwords requires Google 2-Step Verification and an App Password.
- For production, add rate limiting / CAPTCHA / abuse protection.
"""

from pathlib import Path
import os
import smtplib
from email.message import EmailMessage

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field


BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = Path(os.getenv("DOCS_DIR", BASE_DIR / "docs"))

app = FastAPI(
    title="VOX API",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/openapi.json",
)


class ContactPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    subject: str = Field(min_length=1, max_length=180)
    message: str = Field(min_length=1, max_length=5000)
    lang: str = Field(default="uk", pattern="^(uk|en|de)$")
    source_page: str = Field(default="/docs", max_length=200)


@app.get("/docs", include_in_schema=False)
async def serve_docs() -> FileResponse:
    return FileResponse(DOCS_DIR / "docs.html")


@app.get("/privacy", include_in_schema=False)
async def serve_privacy() -> FileResponse:
    return FileResponse(DOCS_DIR / "privacy.html")


def send_contact_email(payload: ContactPayload) -> None:
    gmail_user = os.getenv("GMAIL_USER")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD")
    contact_to = os.getenv("CONTACT_TO_EMAIL", "avotiyaaa@gmail.com")

    if not gmail_user or not gmail_app_password:
        raise RuntimeError("GMAIL_USER and GMAIL_APP_PASSWORD must be set")

    msg = EmailMessage()
    msg["Subject"] = f"[VOX Contact] {payload.subject}"
    msg["From"] = gmail_user
    msg["To"] = contact_to
    msg["Reply-To"] = payload.email
    msg.set_content(
        f"""New contact form submission from VOX

Name: {payload.name}
Email: {payload.email}
Language: {payload.lang}
Source page: {payload.source_page}

Subject:
{payload.subject}

Message:
{payload.message}
"""
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, gmail_app_password)
        smtp.send_message(msg)


@app.post("/api/contact", include_in_schema=False)
async def contact(payload: ContactPayload) -> dict:
    try:
        send_contact_email(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not send message") from exc

    return {"ok": True, "message": "Message sent"}
