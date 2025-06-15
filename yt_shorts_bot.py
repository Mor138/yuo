import os, json, random, sqlite3, tempfile, datetime, base64
from pathlib import Path
from typing import List, Dict
from dotenv import load_dotenv
from tqdm import tqdm
import sys
import subprocess

try:
    from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, vfx
except ModuleNotFoundError as e:
    print("[debug] failed to import moviepy:", e)
    subprocess.call([sys.executable, "-m", "pip", "list"])
    raise

# ---------- ИНИЦИАЛИЗАЦИЯ ----------
load_dotenv()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DB = Path("bot_state.sqlite")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "client_secret.json")

# ---------- СПИСОК ТЕМ ----------
TOPICS = [
    "Как заменить USB‑порт на смартфоне",
    "Диагностика короткого замыкания на плате ноутбука",
    "Спасение SSD после переполюсовки",
    "Почему вздуваются конденсаторы и как их подбирать",
    "Переточка шариков BGA дома"
]


def pick_new_topic() -> str:
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS log(topic TEXT PRIMARY KEY, dt TEXT)")
    seen = {row[0] for row in conn.execute("SELECT topic FROM log")}
    choices = [t for t in TOPICS if t not in seen]
    topic = random.choice(choices) if choices else random.choice(TOPICS)
    conn.close()
    return topic

# ---------- ЗАПРОС СЦЕНАРИЯ ЧЕРЕЗ DEEPSEEK ----------

def generate_script(topic: str) -> Dict:
    import requests
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    sys_prompt = (
        "Ты – YouTube-сценарист. Дай JSON без форматирования вида:"
        "{\"title\":..., \"voiceover\":..., \"shots\":[{\"img_prompt\":..., \"duration\": int_sec}, ...]}"
        "Говори по-русски, хронометраж <55 сек, максимум 6 сцен."
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"Тема: {topic}"},
        ]
    }
    resp = requests.post("https://api.deepseek.com/v1/chat/completions", headers=headers, json=payload)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    return json.loads(content)

# ---------- ПОДСТАВКА ОСТАЛЬНЫХ ЧАСТЕЙ ----------
# (оставить make_assets, build_video, upload_video, logging и pipeline как есть)
# Замени только openai на DeepSeek в блоке generate_script.

# Не забудь в GitHub Secrets сохранить ключ как DEEPSEEK_API_KEY.
