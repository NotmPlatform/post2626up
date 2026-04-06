import os
import re
import html
from pathlib import Path

import requests
from openai import OpenAI


WP_SITE = os.environ["WP_SITE"].rstrip("/")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]   # например @yourchannel
CHANNEL_HANDLE = os.getenv("CHANNEL_HANDLE", "@yourchannel")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
LAST_POST_FILE = DATA_DIR / "last_post_id.txt"

client = OpenAI(api_key=OPENAI_API_KEY)


def strip_html(text: str) -> str:
    text = text or ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_latest_post() -> dict:
    url = f"{WP_SITE}/wp-json/wp/v2/posts"
    params = {
        "per_page": 1,
        "orderby": "date",
        "order": "desc",
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    posts = response.json()

    if not posts:
        raise RuntimeError("На сайте нет постов.")

    return posts[0]


def read_last_post_id():
    if LAST_POST_FILE.exists():
        return LAST_POST_FILE.read_text(encoding="utf-8").strip()
    return None


def save_last_post_id(post_id: str):
    LAST_POST_FILE.write_text(str(post_id), encoding="utf-8")


def prepare_source(post: dict):
    title = strip_html(post.get("title", {}).get("rendered", ""))
    excerpt = strip_html(post.get("excerpt", {}).get("rendered", ""))
    content = strip_html(post.get("content", {}).get("rendered", ""))[:3000]
    link = post.get("link", "")
    return title, excerpt, content, link


def generate_telegram_post(title: str, excerpt: str, content: str) -> str:
    instructions = f"""
Ты редактор премиального Telegram-канала о бизнесе, финансах и Web3.

Сделай короткий Telegram-пост на русском языке.

Структура:
1. Первая строка: эмодзи + короткая рубрика
2. Вторая строка: сильный хук-заголовок
3. Затем 2 коротких абзаца по сути
4. Затем блок:
Почему это важно:
— ...
— ...
— ...
5. Последняя строка: {CHANNEL_HANDLE}

Правила:
- дорого, уверенно, современно
- без воды
- без канцелярита
- без хэштегов
- не как SEO-статья
- не вставляй ссылку в текст
- максимум ~1200 символов
"""

    input_text = f"""
Заголовок статьи: {title}

Краткое описание: {excerpt}

Текст статьи:
{content}
"""

    response = client.responses.create(
        model=OPENAI_MODEL,
        reasoning={"effort": "low"},
        instructions=instructions,
        input=input_text,
    )

    return response.output_text.strip()


def send_to_telegram(text: str, link: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "link_preview_options": {
            "is_disabled": True
        },
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": "Читать новость",
                        "url": link
                    }
                ]
            ]
        }
    }

    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()


def main():
    post = get_latest_post()
    post_id = str(post["id"])

    if read_last_post_id() == post_id:
        print("Новых постов нет.")
        return

    title, excerpt, content, link = prepare_source(post)
    tg_text = generate_telegram_post(title, excerpt, content)

    send_to_telegram(tg_text, link)
    save_last_post_id(post_id)

    print(f"Опубликован пост {post_id}")


if __name__ == "__main__":
    main()
