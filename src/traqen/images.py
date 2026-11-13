"""Images in traqs: added in the editor, stored in the database (so a backup
is still one file), and shown only to their owner."""

import secrets
import sqlite3
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response

from . import db as dbm
from .auth import CSRF, current_user, get_db

router = APIRouter()

MAX_BYTES = 5 * 1024 * 1024
# Unused images are kept this long, so one added in an editor that hasn't
# been saved yet isn't dropped.
KEEP_UNUSED = timedelta(days=1)


def image_type(data: bytes) -> str | None:
    """What the image is, from its first bytes (not what the browser said)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@router.post("/traqs/{traq_id}/images", dependencies=CSRF)
async def add_image(
    traq_id: int,
    image: UploadFile,
    user=Depends(current_user),
    db=Depends(get_db),
):
    owned = db.execute(
        "SELECT 1 FROM traqs WHERE id = ? AND user_id = ?", (traq_id, user["id"])
    ).fetchone()
    if owned is None:
        raise HTTPException(404, "Traq not found")
    data = await image.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Images can be up to 5 MB")
    content_type = image_type(data)
    if content_type is None:
        raise HTTPException(415, "Only PNG, JPEG, GIF, and WebP images can be added")
    image_id = secrets.token_urlsafe(12)
    db.execute(
        "INSERT INTO images (id, user_id, content_type, data, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (image_id, user["id"], content_type, data, dbm.stamp()),
    )
    return {"url": f"/images/{image_id}"}


@router.get("/images/{image_id}")
def show_image(image_id: str, user=Depends(current_user), db=Depends(get_db)):
    image = db.execute(
        "SELECT content_type, data FROM images WHERE id = ? AND user_id = ?",
        (image_id, user["id"]),
    ).fetchone()
    if image is None:
        raise HTTPException(404, "Image not found")
    return Response(
        image["data"],
        media_type=image["content_type"],
        headers={
            # An image never changes: its id is new for every one added.
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


def drop_unused(db: sqlite3.Connection, user_id: int) -> None:
    """Drop the user's images that none of their traqs show any more."""
    db.execute(
        """
        DELETE FROM images WHERE user_id = ? AND created_at <= ? AND NOT EXISTS (
            SELECT 1 FROM traqs WHERE traqs.user_id = images.user_id
            AND instr(traqs.text, '/images/' || images.id) > 0
        )
        """,
        (user_id, dbm.ago(KEEP_UNUSED)),
    )
