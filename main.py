"""
AI-Аватар Telegram Bot — Александр Перегуда
Конвейер: тема → текст (Claude) → видео (HeyGen) → Telegram
"""

import os
import json
import time
import logging
import schedule
import requests
import anthropic
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
HEYGEN_API_KEY      = os.environ["HEYGEN_API_KEY"]
HEYGEN_AVATAR_ID    = os.environ["HEYGEN_AVATAR_ID"]
HEYGEN_VOICE_ID     = os.environ["HEYGEN_VOICE_ID"]
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
POST_TIME           = os.environ.get("POST_TIME", "10:00")

TOPICS_FILE = Path("topics.json")

def load_topics():
    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)["topics"]

def get_next_topic():
    for t in load_topics():
        if not t.get("used"):
            return t
    log.warning("Все темы использованы!")
    return None

def mark_topic_used(topic_id):
    topics = load_topics()
    for t in topics:
        if t["id"] == topic_id:
            t["used"] = True
            t["used_at"] = datetime.now().isoformat()
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump({"topics": topics}, f, ensure_ascii=False, indent=2)

SYSTEM_PROMPT = """Ты — Александр Перегуда, предприниматель из Новороссийска.
Ведёшь Telegram-канал по темам ВНЖ и GOBOX (наборы выживания).
Стиль: живой, разговорный, личный — обращаешься к читателю на "ты".
Структура: крючок → суть (3-5 абзацев) → призыв написать.
Длина: 800-1200 символов. Без хэштегов."""

def generate_post_text(topic):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    post = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Напиши пост: {topic['title']}\nКонтекст: {topic.get('context','')}"}]
    ).content[0].text

    script = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Скрипт для видео 25-35 сек (~70 слов), только речь:\n\n{post}"}]
    ).content[0].text

    log.info(f"Текст готов: {len(post)} символов")
    return post, script

def get_heygen_voice_id():
    headers = {"X-Api-Key": HEYGEN_API_KEY}
    resp = requests.get("https://api.heygen.com/v2/avatars", headers=headers, timeout=30)
    resp.raise_for_status()
    avatars = resp.json().get("data", {}).get("avatars", [])
    for a in avatars:
        if HEYGEN_AVATAR_ID in a.get("avatar_id", ""):
            voices = a.get("voice_ids", [])
            if voices:
                return voices[0]
    return HEYGEN_VOICE_ID

def create_heygen_video(script, background_url=None):
    headers = {"X-Api-Key": HEYGEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "video_inputs": [{
            "character": {"type": "avatar", "avatar_id": HEYGEN_AVATAR_ID, "avatar_style": "normal"},
            "voice": {"type": "text", "input_text": script, "voice_id": HEYGEN_VOICE_ID, "speed": 1.0}
        }],
        "dimension": {"width": 1280, "height": 720}
    }
    if background_url:
        payload["video_inputs"][0]["background"] = {"type": "image", "url": background_url}

    resp = requests.post("https://api.heygen.com/v2/video/generate", headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    video_id = resp.json()["data"]["video_id"]
    log.info(f"HeyGen video_id: {video_id}")
    return video_id

def wait_for_heygen_video(video_id):
    headers = {"X-Api-Key": HEYGEN_API_KEY}
    for i in range(40):
        time.sleep(15)
        data = requests.get(
            f"https://api.heygen.com/v1/video_status.get?video_id={video_id}",
            headers=headers, timeout=30
        ).json().get("data", {})
        status = data.get("status")
        log.info(f"Статус видео: {status} ({i+1}/40)")
        if status == "completed":
            return data["video_url"]
        if status == "failed":
            raise RuntimeError(f"HeyGen failed: {data}")
    raise TimeoutError("HeyGen timeout")

def post_to_telegram(post_text, video_bytes, topic):
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    tags = {"vnj": "#ВНЖ #эмиграция #Португалия", "gobox": "#GOBOX #выживание #снаряжение"}
    hashtags = tags.get(topic.get("category", ""), "")
    caption = f"{post_text[:900]}\n\n{hashtags}"

    requests.post(
        f"{base}/sendVideo",
        data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "supports_streaming": True},
        files={"video": ("avatar.mp4", video_bytes, "video/mp4")},
        timeout=120
    ).raise_for_status()
    log.info("Опубликовано в Telegram!")

def run_pipeline():
    log.info(f"Старт [{datetime.now().strftime('%d.%m.%Y %H:%M')}]")
    topic = get_next_topic()
    if not topic:
        return
    log.info(f"Тема: {topic['title']}")
    try:
        post_text, script = generate_post_text(topic)
        video_id = create_heygen_video(script, topic.get("background_url"))
        video_url = wait_for_heygen_video(video_id)
        video_bytes = requests.get(video_url, timeout=120).content
        post_to_telegram(post_text, video_bytes, topic)
        mark_topic_used(topic["id"])
        log.info(f"Готово: {topic['title']}")
    except Exception as e:
        log.error(f"Ошибка: {e}", exc_info=True)

if __name__ == "__main__":
    log.info(f"Запуск. Постинг в {POST_TIME}")
    schedule.every().day.at(POST_TIME).do(run_pipeline)
    # run_pipeline()  # раскомментируй для немедленного теста
    while True:
        schedule.run_pending()
        time.sleep(60)
