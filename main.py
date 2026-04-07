import os
import re
import html
import json
import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from openai import OpenAI


# =========================
# ENVIRONMENT VARIABLES
# =========================
# Required:
#   WP_SITE=https://2026up.ru
#   OPENAI_API_KEY=...
#   TELEGRAM_BOT_TOKEN=...
#   TELEGRAM_CHANNEL_ID=@your_channel
#
# Recommended:
#   OPENAI_MODEL=gpt-5.4-mini
#   CHANNEL_FOOTER=@Web2026UP | https://2026up.ru
#   BUTTON_TEXT=Читать полностью
#   DATA_DIR=/app/data
#
# Optional image settings:
#   IMAGE_ENABLED=true
#   IMAGE_MODEL=gpt-image-1-mini
#   IMAGE_SIZE=1536x1024
#   IMAGE_QUALITY=medium
#   IMAGE_VISUAL_SCORE_THRESHOLD=8
#   IMAGE_WINDOW_SIZE=10
#   IMAGE_MAX_IN_WINDOW=2
#   POST_MAX_CHARS=1050
#   PHOTO_CAPTION_MAX_CHARS=980
# =========================

WP_SITE = os.environ["WP_SITE"].rstrip("/")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
CHANNEL_FOOTER = os.getenv("CHANNEL_FOOTER", "@Web2026UP | https://2026up.ru")
BUTTON_TEXT = os.getenv("BUTTON_TEXT", "Читать полностью")
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
POST_MAX_CHARS = int(os.getenv("POST_MAX_CHARS", "1050"))
PHOTO_CAPTION_MAX_CHARS = int(os.getenv("PHOTO_CAPTION_MAX_CHARS", "980"))

IMAGE_ENABLED = os.getenv("IMAGE_ENABLED", "true").lower() == "true"
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1-mini")
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1536x1024")
IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "medium")
IMAGE_VISUAL_SCORE_THRESHOLD = int(os.getenv("IMAGE_VISUAL_SCORE_THRESHOLD", "8"))
IMAGE_WINDOW_SIZE = int(os.getenv("IMAGE_WINDOW_SIZE", "10"))
IMAGE_MAX_IN_WINDOW = int(os.getenv("IMAGE_MAX_IN_WINDOW", "2"))

client = OpenAI(api_key=OPENAI_API_KEY)

DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
LEGACY_LAST_POST_FILE = DATA_DIR / "last_post_id.txt"


def strip_html(text: str) -> str:
    text = text or ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_state() -> Dict[str, Any]:
    default_state = {
        "last_post_id": None,
        "recent_publications": [],  # list[{'post_id': str, 'used_image': bool}]
    }

    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if "recent_publications" not in state:
                state["recent_publications"] = []
            return state
        except Exception:
            pass

    if LEGACY_LAST_POST_FILE.exists():
        default_state["last_post_id"] = LEGACY_LAST_POST_FILE.read_text(encoding="utf-8").strip()

    return default_state


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_latest_post() -> Dict[str, Any]:
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
        raise RuntimeError("На сайте нет опубликованных постов.")

    return posts[0]


def prepare_source(post: Dict[str, Any]) -> Dict[str, str]:
    title = strip_html(post.get("title", {}).get("rendered", ""))
    excerpt = strip_html(post.get("excerpt", {}).get("rendered", ""))
    content = strip_html(post.get("content", {}).get("rendered", ""))
    content = content[:3500]
    link = post.get("link", "")

    return {
        "title": title,
        "excerpt": excerpt,
        "content": content,
        "link": link,
    }


def extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("Не удалось извлечь JSON из ответа модели.")

    return json.loads(match.group(0))


def normalize_post_payload(payload: Dict[str, Any], source: Dict[str, str]) -> Dict[str, Any]:
    rubric = str(payload.get("rubric", "📌 Новость")).strip()
    hook_title = str(payload.get("hook_title", source["title"])).strip()
    lead = str(payload.get("lead", source["excerpt"] or source["title"])).strip()
    details = str(payload.get("details", "")).strip()

    why_important = payload.get("why_important", [])
    if not isinstance(why_important, list):
        why_important = []
    why_important = [str(x).strip() for x in why_important if str(x).strip()][:3]
    while len(why_important) < 3:
        why_important.append("тема влияет на рынок шире, чем кажется на первый взгляд")

    try:
        visual_score = int(payload.get("visual_score", 1))
    except Exception:
        visual_score = 1
    visual_score = max(1, min(10, visual_score))

    image_prompt = str(payload.get("image_prompt", "")).strip()

    return {
        "rubric": rubric,
        "hook_title": hook_title,
        "lead": lead,
        "details": details,
        "why_important": why_important,
        "visual_score": visual_score,
        "image_prompt": image_prompt,
    }


def generate_post_package(source: Dict[str, str]) -> Dict[str, Any]:
    instructions = f"""
Ты редактор премиального Telegram-канала о бизнесе, карьере, технологиях, AI и Web3.

Сделай короткий, умный и дорогой по тону Telegram-пост по новости.

Верни только чистый JSON без пояснений и без markdown.

Формат ответа:
{{
  "rubric": "эмодзи + 1-2 слова",
  "hook_title": "короткий сильный заголовок",
  "lead": "1 короткий абзац",
  "details": "1 короткий абзац",
  "why_important": [
    "короткий пункт 1",
    "короткий пункт 2",
    "короткий пункт 3"
  ],
  "visual_score": 1,
  "image_prompt": "короткий английский prompt для editorial image или пустая строка"
}}

Правила для текста:
- язык: русский
- стиль: современно, уверенно, без воды, без канцелярита
- не как SEO-статья
- не вставляй URL в текст
- не используй хэштеги
- заголовок должен быть цепляющим, но не кликбейтным
- рубрика короткая и единообразная по стилю
- 2 абзаца максимум
- блок why_important должен быть резким и коротким
- итоговый пост должен укладываться примерно в {POST_MAX_CHARS} символов вместе с футером

Правила для image_prompt:
- только на английском
- нужен только если тема реально выиграет от визуала
- только editorial / business style
- no text, no watermark, no logo, no collage, no meme
- landscape composition
- если тема слабая для визуала, верни пустую строку

Как ставить visual_score:
- 1-4: обычная текстовая тема
- 5-7: можно без картинки
- 8-10: сильная визуальная тема, стоит делать картинку

Высокий visual_score давай только темам вроде:
- AI и рынок труда
- крупные сделки
- заметные рыночные движения
- регуляторные изменения
- сильные бизнес-тренды
- темы, где есть яркий визуальный образ или конфликт
""".strip()

    input_text = f"""
Заголовок статьи: {source['title']}

Краткое описание: {source['excerpt']}

Текст статьи:
{source['content']}
""".strip()

    response = client.responses.create(
        model=OPENAI_MODEL,
        reasoning={"effort": "low"},
        text={"verbosity": "low"},
        instructions=instructions,
        input=input_text,
    )

    payload = extract_json(response.output_text)
    return normalize_post_payload(payload, source)


def compose_telegram_text(post_package: Dict[str, Any], footer: str, max_chars: int) -> str:
    parts: List[str] = [post_package["rubric"], post_package["hook_title"], post_package["lead"]]

    if post_package["details"]:
        parts.append(post_package["details"])

    why_block = "Почему это важно:\n" + "\n".join(f"— {item}" for item in post_package["why_important"])
    parts.append(why_block)
    parts.append(footer)

    text = "\n\n".join(part.strip() for part in parts if part.strip())

    if len(text) <= max_chars:
        return text

    # 1) Try dropping details first.
    if post_package["details"]:
        reduced = dict(post_package)
        reduced["details"] = ""
        text = compose_telegram_text(reduced, footer, max_chars)
        if len(text) <= max_chars:
            return text

    # 2) Shorten lead and bullets.
    lead = post_package["lead"]
    if len(lead) > 220:
        lead = lead[:217].rstrip(" .,;:-") + "…"

    bullets = [item[:95].rstrip(" .,;:-") + ("…" if len(item) > 95 else "") for item in post_package["why_important"]]

    trimmed = dict(post_package)
    trimmed["lead"] = lead
    trimmed["details"] = ""
    trimmed["why_important"] = bullets

    text = "\n\n".join([
        trimmed["rubric"],
        trimmed["hook_title"],
        trimmed["lead"],
        "Почему это важно:\n" + "\n".join(f"— {item}" for item in trimmed["why_important"]),
        footer,
    ])

    if len(text) <= max_chars:
        return text

    # 3) Hard trim as a last resort.
    reserve = len(footer) + 40
    hard_limit = max(200, max_chars - reserve)
    body = "\n\n".join([
        trimmed["rubric"],
        trimmed["hook_title"],
        trimmed["lead"],
        "Почему это важно:\n" + "\n".join(f"— {item}" for item in trimmed["why_important"]),
    ])
    body = body[:hard_limit].rstrip(" .,;:-\n") + "…"
    return f"{body}\n\n{footer}"


def image_slots_available(state: Dict[str, Any]) -> bool:
    recent = state.get("recent_publications", [])[-IMAGE_WINDOW_SIZE:]
    used_images = sum(1 for item in recent if item.get("used_image"))
    return used_images < IMAGE_MAX_IN_WINDOW


def should_generate_image(post_package: Dict[str, Any], state: Dict[str, Any]) -> bool:
    if not IMAGE_ENABLED:
        return False
    if post_package.get("visual_score", 1) < IMAGE_VISUAL_SCORE_THRESHOLD:
        return False
    if not post_package.get("image_prompt"):
        return False
    if not image_slots_available(state):
        return False
    return True


def build_image_prompt(post_package: Dict[str, Any]) -> str:
    base_prompt = post_package.get("image_prompt", "").strip()
    style_suffix = (
        " Premium editorial business illustration, modern and minimal, strong composition, "
        "clean visual hierarchy, no text, no watermark, no logo, no collage, no UI screenshot, "
        "not cartoonish, suitable for a Telegram business news post, landscape format."
    )
    if base_prompt:
        return base_prompt + style_suffix

    return (
        f"Editorial illustration for a business news post about: {post_package['hook_title']}."
        + style_suffix
    )


def generate_image_bytes(prompt: str) -> bytes:
    result = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size=IMAGE_SIZE,
        quality=IMAGE_QUALITY,
    )

    b64_json = result.data[0].b64_json
    return base64.b64decode(b64_json)


def send_text_post(text: str, link: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "link_preview_options": {"is_disabled": True},
        "reply_markup": {
            "inline_keyboard": [
                [{"text": BUTTON_TEXT, "url": link}]
            ]
        },
    }
    response = requests.post(url, json=payload, timeout=60)
    response.raise_for_status()


def send_photo_post(image_bytes: bytes, caption: str, link: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"

    data = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "caption": caption,
        "reply_markup": json.dumps(
            {"inline_keyboard": [[{"text": BUTTON_TEXT, "url": link}]]},
            ensure_ascii=False,
        ),
    }
    files = {
        "photo": ("news_image.png", image_bytes, "image/png")
    }

    response = requests.post(url, data=data, files=files, timeout=120)
    response.raise_for_status()


def publish_post(source: Dict[str, str], post_package: Dict[str, Any], state: Dict[str, Any]) -> bool:
    full_text = compose_telegram_text(post_package, CHANNEL_FOOTER, POST_MAX_CHARS)

    if should_generate_image(post_package, state):
        try:
            image_prompt = build_image_prompt(post_package)
            image_bytes = generate_image_bytes(image_prompt)
            caption = compose_telegram_text(post_package, CHANNEL_FOOTER, PHOTO_CAPTION_MAX_CHARS)
            send_photo_post(image_bytes, caption, source["link"])
            return True
        except Exception as image_error:
            print(f"Image generation/send failed, fallback to text post: {image_error}")

    send_text_post(full_text, source["link"])
    return False


def main() -> None:
    state = load_state()
    latest_post = get_latest_post()
    post_id = str(latest_post["id"])

    if state.get("last_post_id") == post_id:
        print("Новых постов нет.")
        return

    source = prepare_source(latest_post)
    post_package = generate_post_package(source)

    used_image = publish_post(source, post_package, state)

    state["last_post_id"] = post_id
    state.setdefault("recent_publications", []).append({
        "post_id": post_id,
        "used_image": used_image,
    })
    state["recent_publications"] = state["recent_publications"][-IMAGE_WINDOW_SIZE:]
    save_state(state)

    print(f"Опубликован пост {post_id}. Картинка: {'да' if used_image else 'нет'}")


if __name__ == "__main__":
    main()
