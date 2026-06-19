import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import time
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
DB_PATH = os.getenv("DB_PATH", "666_store.sqlite3")

ADMIN_IDS = [7147977803, 6635235920, 1805486928]

app = FastAPI(title="666 Store API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = Router()
dp = Dispatcher()

bot: Optional[Bot] = None
polling_task: Optional[asyncio.Task] = None


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


def init_db() -> None:
    with get_db() as db:
        db.execute("PRAGMA journal_mode=WAL")

        db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                price_uah INTEGER NOT NULL,
                price_eur INTEGER NOT NULL,
                stars INTEGER NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT DEFAULT '',
                username TEXT DEFAULT '',
                country TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_id TEXT NOT NULL,
                country TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS review_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                rating INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        products = [
            (
                "tshirt-black",
                "666 Black T-Shirt",
                "tshirt",
                "Черная oversize футболка с принтом 666.",
                999,
                24,
                120,
            ),
            (
                "tshirt-red",
                "666 Red Flame T-Shirt",
                "tshirt",
                "Футболка с огненным принтом и плотным хлопком.",
                1099,
                27,
                135,
            ),
            (
                "hoodie-black",
                "666 Dark Hoodie",
                "hoodie",
                "Теплое худи с объемным капюшоном и принтом 666.",
                2199,
                54,
                270,
            ),
            (
                "hoodie-flame",
                "666 Flame Hoodie",
                "hoodie",
                "Премиум худи с красным акцентом и streetwear-посадкой.",
                2499,
                61,
                310,
            ),
        ]

        db.executemany("""
            INSERT OR IGNORE INTO products
                (id, name, category, description, price_uah, price_eur, stars)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, products)

        db.commit()


def upsert_user(
    user_id: int,
    first_name: str = "",
    username: str = "",
    country: str = "",
) -> None:
    with get_db() as db:
        existing = db.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if existing:
            db.execute("""
                UPDATE users
                SET
                    first_name = COALESCE(NULLIF(?, ''), first_name),
                    username = COALESCE(NULLIF(?, ''), username),
                    country = COALESCE(NULLIF(?, ''), country),
                    last_seen = ?
                WHERE user_id = ?
            """, (first_name, username, country, now_iso(), user_id))
        else:
            db.execute("""
                INSERT INTO users
                    (user_id, first_name, username, country, created_at, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, first_name, username, country, now_iso(), now_iso()))

        db.commit()


def validate_telegram_init_data(init_data: str) -> Optional[dict]:
    if not BOT_TOKEN or not init_data:
        return None

    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(parsed.items())
        )

        secret_key = hmac.new(
            key=b"WebAppData",
            msg=BOT_TOKEN.encode(),
            digestmod=hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        auth_date = int(parsed.get("auth_date", "0"))

        if time.time() - auth_date > 7 * 24 * 60 * 60:
            return None

        user_raw = parsed.get("user")

        if not user_raw:
            return None

        return json.loads(user_raw)

    except Exception:
        return None


def get_current_user(x_telegram_init_data: Optional[str]) -> dict:
    user = validate_telegram_init_data(x_telegram_init_data or "")

    if not user:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    return user


def get_current_admin(x_telegram_init_data: Optional[str]) -> dict:
    user = get_current_user(x_telegram_init_data)
    user_id = int(user["id"])

    if user_id not in ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Access denied")

    return user


class SyncUserIn(BaseModel):
    country: str = Field(default="", pattern="^(UA|EU)?$")


class TrackClickIn(BaseModel):
    product_id: str
    country: str = Field(default="", pattern="^(UA|EU)?$")


class CreateReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str = Field(min_length=2, max_length=700)


class GrantReviewIn(BaseModel):
    user_id: int
    product_id: str


@app.get("/")
async def root():
    return {
        "name": "666 Store API",
        "status": "ok",
        "products": "/api/products",
        "reviews": "/api/reviews",
    }


@app.get("/api/products")
async def get_products():
    with get_db() as db:
        rows = db.execute("""
            SELECT id, name, category, description, price_uah, price_eur, stars
            FROM products
            ORDER BY category DESC, name ASC
        """).fetchall()

    return rows_to_dicts(rows)


@app.get("/api/reviews")
async def get_reviews():
    with get_db() as db:
        rows = db.execute("""
            SELECT id, user_id, product_id, product_name, rating, text, created_at
            FROM reviews
            ORDER BY id DESC
            LIMIT 100
        """).fetchall()

    return rows_to_dicts(rows)


@app.post("/api/users")
async def sync_user(
    payload: SyncUserIn,
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    user = get_current_user(x_telegram_init_data)

    upsert_user(
        user_id=int(user["id"]),
        first_name=user.get("first_name", ""),
        username=user.get("username", ""),
        country=payload.country,
    )

    return {"ok": True}


@app.post("/api/track-click")
async def track_click(
    payload: TrackClickIn,
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    user = validate_telegram_init_data(x_telegram_init_data or "")
    user_id = int(user["id"]) if user else None

    with get_db() as db:
        product = db.execute(
            "SELECT id FROM products WHERE id = ?",
            (payload.product_id,),
        ).fetchone()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        db.execute("""
            INSERT INTO clicks (user_id, product_id, country, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, payload.product_id, payload.country, now_iso()))

        db.commit()

    if user_id:
        upsert_user(
            user_id=user_id,
            first_name=user.get("first_name", ""),
            username=user.get("username", ""),
            country=payload.country,
        )

    return {"ok": True}


@app.get("/api/review-permission")
async def get_review_permission(
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    user = get_current_user(x_telegram_init_data)
    user_id = int(user["id"])

    with get_db() as db:
        permission = db.execute("""
            SELECT id, product_id, product_name
            FROM review_permissions
            WHERE user_id = ? AND used = 0
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,)).fetchone()

    if not permission:
        return {"allowed": False}

    return {
        "allowed": True,
        "permission_id": permission["id"],
        "product_id": permission["product_id"],
        "product_name": permission["product_name"],
    }


@app.post("/api/reviews")
async def create_review(
    payload: CreateReviewIn,
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    user = get_current_user(x_telegram_init_data)
    user_id = int(user["id"])

    with get_db() as db:
        permission = db.execute("""
            SELECT id, product_id, product_name
            FROM review_permissions
            WHERE user_id = ? AND used = 0
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,)).fetchone()

        if not permission:
            raise HTTPException(status_code=403, detail="No review permission")

        db.execute("""
            INSERT INTO reviews
                (user_id, product_id, product_name, rating, text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            permission["product_id"],
            permission["product_name"],
            payload.rating,
            payload.text.strip(),
            now_iso(),
        ))

        db.execute(
            "UPDATE review_permissions SET used = 1 WHERE id = ?",
            (permission["id"],),
        )

        db.commit()

    return {"ok": True}


@app.get("/api/admin/me")
async def admin_me(
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    user = get_current_admin(x_telegram_init_data)

    return {
        "is_admin": True,
        "user_id": int(user["id"]),
    }


@app.get("/api/admin/stats")
async def admin_stats(
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    get_current_admin(x_telegram_init_data)

    with get_db() as db:
        total_users = db.execute(
            "SELECT COUNT(*) AS count FROM users"
        ).fetchone()["count"]

        country_rows = db.execute("""
            SELECT country, COUNT(*) AS count
            FROM users
            GROUP BY country
        """).fetchall()

        click_rows = db.execute("""
            SELECT p.id, p.name, COUNT(c.id) AS clicks
            FROM products p
            LEFT JOIN clicks c ON c.product_id = p.id
            GROUP BY p.id, p.name
            ORDER BY clicks DESC
        """).fetchall()

        review_rows = db.execute("""
            SELECT id, user_id, product_id, product_name, rating, text, created_at
            FROM reviews
            ORDER BY id DESC
            LIMIT 200
        """).fetchall()

    country_distribution = {"UA": 0, "EU": 0}

    for row in country_rows:
        if row["country"] in country_distribution:
            country_distribution[row["country"]] = row["count"]

    return {
        "total_users": total_users,
        "country_distribution": country_distribution,
        "product_clicks": rows_to_dicts(click_rows),
        "reviews": rows_to_dicts(review_rows),
    }


@app.post("/api/admin/grant-review")
async def admin_grant_review(
    payload: GrantReviewIn,
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    get_current_admin(x_telegram_init_data)

    with get_db() as db:
        product = db.execute("""
            SELECT id, name
            FROM products
            WHERE id = ?
        """, (payload.product_id,)).fetchone()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        db.execute("""
            INSERT INTO review_permissions
                (user_id, product_id, product_name, used, created_at)
            VALUES (?, ?, ?, 0, ?)
        """, (
            payload.user_id,
            product["id"],
            product["name"],
            now_iso(),
        ))

        db.commit()

    if bot:
        try:
            await bot.send_message(
                chat_id=payload.user_id,
                text=(
                    f"⭐ Вам доступен отзыв о покупке: {product['name']}.\n\n"
                    "Откройте 666 Store и перейдите во вкладку «Отзывы»."
                ),
            )
        except Exception as error:
            print(f"Не удалось отправить уведомление пользователю {payload.user_id}: {error}")

    return {"ok": True}


@app.delete("/api/admin/reviews/{review_id}")
async def admin_delete_review(
    review_id: int,
    x_telegram_init_data: Optional[str] = Header(default=None),
):
    get_current_admin(x_telegram_init_data)

    with get_db() as db:
        db.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
        db.commit()

    return {"ok": True}


def get_last_reviews_text() -> str:
    with get_db() as db:
        rows = db.execute("""
            SELECT product_name, rating, text
            FROM reviews
            ORDER BY id DESC
            LIMIT 3
        """).fetchall()

    if not rows:
        return "Пока отзывов нет."

    lines = []

    for row in rows:
        stars = "⭐" * int(row["rating"])
        text = row["text"]

        if len(text) > 90:
            text = text[:87] + "..."

        lines.append(f"{stars} {row['product_name']}: {text}")

    return "\n".join(lines)


def store_keyboard() -> InlineKeyboardMarkup:
    url = WEBAPP_URL or "https://example.com"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Открыть магазин",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


@router.message()
async def any_message(message: types.Message):
    user = message.from_user

    if user:
        upsert_user(
            user_id=user.id,
            first_name=user.first_name or "",
            username=user.username or "",
        )

    text = (
        "🖤 Добро пожаловать в 666 Store.\n\n"
        "Футболки и худи уже ждут тебя в Mini App.\n\n"
        "Последние отзывы:\n"
        f"{get_last_reviews_text()}"
    )

    await message.answer(text, reply_markup=store_keyboard())


@app.on_event("startup")
async def on_startup():
    global bot, polling_task

    init_db()

    if not BOT_TOKEN:
        print("BOT_TOKEN is not set. Bot polling disabled.")
        return

    bot = Bot(token=BOT_TOKEN)
    dp.include_router(router)

    polling_task = asyncio.create_task(dp.start_polling(bot))
    print("Bot polling started.")


@app.on_event("shutdown")
async def on_shutdown():
    global bot, polling_task

    if polling_task:
        polling_task.cancel()

    if bot:
        await bot.session.close()
