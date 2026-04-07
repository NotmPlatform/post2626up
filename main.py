
import html
import os
import re
from pathlib import Path
from typing import List, Tuple

import requests
from openai import OpenAI


WP_SITE = os.environ["WP_SITE"].rstrip("/")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]  # например @yourchannel
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

CHANNEL_FOOTER = os.getenv("CHANNEL_FOOTER", "Web3 Carrier | 2026UP")
BUTTON_TEXT = os.getenv("BUTTON_TEXT", "Читать полностью")
POST_MAX_CHARS = int(os.getenv("POST_MAX_CHARS", "1050"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
LAST_POST_FILE = DATA_DIR / "last_post_id.txt"

client = OpenAI(api_key=OPENAI_API_KEY)
session = requests.Session()
session.headers.update(
    {
        "User-Agent": "2026UP-Telegram-Autoposter/1.1",
        "Accept": "application/json",
    }
)


def strip_html(text: str) -> str:
    text = text or ""
    # Сначала превращаем блочные теги в пробел, затем убираем остальной HTML.
    text = re.sub(r"</?(p|div|br|li|ul|ol|h1|h2|h3|h4|h5|h6)[^>]*>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def cleanup_generated_text(text: str) -> str:
    text = text or ""
    text = text.replace("\r", "")
    text = re.sub(r"https?://\S+", "", text)  # ссылка должна быть только в кнопке
    text = re.sub(re.escape(CHANNEL_FOOTER), "", text, flags=re.I)
    text = re.sub(r"@\w+", "", text)  # подпись ставим сами, единым стилем
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def shorten_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit(" ", 1)[0].strip()
    return shortened + "…"


def get_latest_post() -> dict:
    url = f"{WP_SITE}/wp-json/wp/v2/posts"
    params = {"per_page": 1, "orderby": "date", "order": "desc"}
    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    posts = response.json()

    if not posts:
        raise RuntimeError("На сайте нет опубликованных постов.")

    return posts[0]


def read_last_post_id() -> str | None:
    if LAST_POST_FILE.exists():
        return LAST_POST_FILE.read_text(encoding="utf-8").strip()
    return None


def save_last_post_id(post_id: str) -> None:
    LAST_POST_FILE.write_text(str(post_id), encoding="utf-8")


def prepare_source(post: dict) -> Tuple[str, str, str, str]:
    title = strip_html(post.get("title", {}).get("rendered", ""))
    excerpt = strip_html(post.get("excerpt", {}).get("rendered", ""))
    content = strip_html(post.get("content", {}).get("rendered", ""))[:3500]
    link = post.get("link", "")
    return title, excerpt, content, link


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def detect_rubric(title: str, excerpt: str, content: str) -> str:
    source = f"{title} {excerpt} {content}".lower()

    if any(word in source for word in ["ии", "ai", "нейросет", "automation", "автоматизац", "agent", "агент"]):
        return "🤖 ИИ"
    if any(word in source for word in ["работ", "ваканс", "карьер", "рынок труда", "job", "hiring"]):
        return "💼 Карьера"
    if any(word in source for word in ["bitcoin", "биткоин", "ethereum", "эфир", "web3", "coinbase", "crypto", "крипт"]):
        return "🌐 Web3"
    if any(word in source for word in ["акци", "рынок", "ipo", "m&a", "сделк", "financ", "ипотек", "инвест"]):
        return "📈 Бизнес"
    return "⚡ Тренды"


def build_fallback_bullets(title: str, excerpt: str, content: str) -> List[str]:
    source = f"{title} {excerpt} {content}".lower()

    if any(word in source for word in ["ии", "ai", "нейросет", "automation", "агент"]):
        return [
            "автоматизация уже стала реальностью",
            "рынок быстрее награждает адаптивных",
            "работа с ИИ становится новой нормой",
        ]
    if any(word in source for word in ["работ", "ваканс", "карьер", "рынок труда"]):
        return [
            "структура рынка труда быстро меняется",
            "рутинные роли под самым сильным давлением",
            "ценность смещается в сторону гибкости",
        ]
    if any(word in source for word in ["bitcoin", "биткоин", "ethereum", "эфир", "web3", "coinbase", "crypto", "крипт"]):
        return [
            "крипта все глубже входит в реальную экономику",
            "крупные игроки двигают рынок быстрее",
            "порог доверия для массовой аудитории снижается",
        ]
    return [
        "рынок реагирует на сильные сигналы быстро",
        "ценность смещается к практическому применению",
        "скорость адаптации становится преимуществом",
    ]


def build_fallback_post(title: str, excerpt: str, content: str) -> str:
    rubric = detect_rubric(title, excerpt, content)
    sentences = split_sentences(excerpt or content)

    paragraph_1 = sentences[0] if sentences else title
    paragraph_2 = sentences[1] if len(sentences) > 1 else (content[:230] if content else excerpt)

    hook_title = shorten_text(title, 78)
    paragraph_1 = shorten_text(paragraph_1, 240)
    paragraph_2 = shorten_text(paragraph_2, 240)
    bullets = [shorten_text(item, 70) for item in build_fallback_bullets(title, excerpt, content)]

    text = (
        f"{rubric}\n\n"
        f"{hook_title}\n\n"
        f"{paragraph_1}\n\n"
        f"{paragraph_2}\n\n"
        f"Почему это важно:\n"
        f"— {bullets[0]}\n"
        f"— {bullets[1]}\n"
        f"— {bullets[2]}"
    )
    return cleanup_generated_text(text)


def generate_telegram_post(title: str, excerpt: str, content: str) -> str:
    instructions = f"""
Ты редактор премиального Telegram-канала о бизнесе, карьере, ИИ и Web3.

Сделай короткий, дорогой и легко читаемый Telegram-пост на русском языке.

Строгий формат ответа:
1. Первая строка: эмодзи + короткая рубрика
2. Пустая строка
3. Сильный хук-заголовок
4. Пустая строка
5. Первый короткий абзац
6. Пустая строка
7. Второй короткий абзац
8. Пустая строка
9. Блок:
Почему это важно:
— ...
— ...
— ...

Правила:
- не используй хэштеги
- не вставляй ссылку в текст
- не добавляй подпись канала
- не используй markdown, звёздочки и кавычки вокруг всего ответа
- заголовок до 75 символов
- каждый абзац: максимум 2 коротких предложения
- каждый пункт после "Почему это важно": короткий и ударный
- текст должен читаться быстро и легко в Telegram
- общий объём: до {POST_MAX_CHARS} символов
- стиль: уверенно, современно, чисто, без воды и SEO-канцелярита
"""

    input_text = f"""
Заголовок статьи: {title}

Краткое описание: {excerpt}

Текст статьи:
{content}
"""

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            reasoning={"effort": "low"},
            instructions=instructions,
            input=input_text,
        )
        generated = cleanup_generated_text(response.output_text)

        if not generated:
            raise ValueError("OpenAI вернул пустой текст.")

        return generated

    except Exception as exc:
        print(f"OpenAI generation failed, using fallback: {exc}")
        return build_fallback_post(title, excerpt, content)


def build_final_post(text: str) -> str:
    text = cleanup_generated_text(text)
    final_text = f"{text}\n\n{CHANNEL_FOOTER}"
    final_text = re.sub(r"\n{3,}", "\n\n", final_text).strip()
    return shorten_text(final_text, 3900)


def send_to_telegram(text: str, link: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": build_final_post(text),
        "link_preview_options": {"is_disabled": True},
        "reply_markup": {
            "inline_keyboard": [
                [{"text": BUTTON_TEXT, "url": link}]
            ]
        },
    }

    response = session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()


def main() -> None:
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
