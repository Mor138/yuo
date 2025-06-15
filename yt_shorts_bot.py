#!/usr/bin/env python3
"""
yt_shorts_bot.py – автоматический генератор и загрузчик YouTube‑Shorts
----------------------------------------------------------------------

Pipeline:
1. Берёт тему из таблицы/списка (pick_topic)
2. Генерирует сценарий ChatGPT (generate_script)
3. Делает ассеты (картинки DALL·E, звук TTS) – make_assets
4. Склеивает вертикальное видео MoviePy – build_video
5. Загружает в канал через YouTube Data API – upload_video
6. Логирует публикацию в SQLite – mark_done

⚙️  Требования
```
pip install --upgrade google-api-python-client google-auth google-auth-oauthlib \
            openai moviepy pillow python-dotenv tqdm
```

🔐  Переменные окружения (или .env):
```text
OPENAI_API_KEY=sk-...
GOOGLE_CLIENT_SECRET=file://client_secret.json   # или base64://...
CHANNEL_UPLOAD_PLAYLIST="UUxxxxxxxxxxxxxxxx"  # опционально
ELEVENLABS_API_KEY=...                          # для TTS (или другой движок)
```

📼  Ограничения и квоты
* Загрузка `videos.insert` расходует 1600 ед. квоты (10 000 бесплатно/сутки).  ([developers.google.com](https://developers.google.com/youtube/v3/docs/videos/insert?utm_source=chatgpt.com))
* Для раскрытия синтетики выставляется `status.containsSyntheticMedia = true` (API rev 2024‑10‑30).  ([developers.google.com](https://developers.google.com/youtube/v3/revision_history))

Шаблон создан для Python 3.10+; запускается раз в час кроном или GitHub Actions.
"""

from __future__ import annotations
import os, json, random, sqlite3, tempfile, datetime, time, base64
from pathlib import Path
from typing import List, Dict

import openai
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import sys
import subprocess

try:
    from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, vfx
except ModuleNotFoundError as e:
    print("[debug] failed to import moviepy:", e)
    subprocess.call([sys.executable, "-m", "pip", "list"])
    raise
from dotenv import load_dotenv
from tqdm import tqdm

# -----------------------------------------------------------
# 0. INIT & CONFIG
# -----------------------------------------------------------
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
DB = Path("bot_state.sqlite")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "client_secret.json")
print("[debug] Python", sys.version)
subprocess.call([sys.executable, "-m", "pip", "--version"])

# -----------------------------------------------------------
# 1. TOPIC SOURCE
# -----------------------------------------------------------
TOPICS = [
    "Как заменить USB‑порт на смартфоне",
    "Диагностика короткого замыкания на плате ноутбука",
    "Спасение SSD после переполюсовки",
    "Почему вздуваются конденсаторы и как их подбирать" ,
    "Переточка шариков BGA дома"
]


def pick_new_topic() -> str:
    """Возвращает тему, которой ещё не было."""
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS log(topic TEXT PRIMARY KEY, dt TEXT)")
    seen = {row[0] for row in conn.execute("SELECT topic FROM log")}
    choices = [t for t in TOPICS if t not in seen]
    topic = random.choice(choices) if choices else random.choice(TOPICS)
    conn.close()
    return topic

# -----------------------------------------------------------
# 2. GPT SCRIPT
# -----------------------------------------------------------

def generate_script(topic: str) -> Dict:
    """Запрашивает у GPT JSON‑сценарий: title, voiceover, 4‑6 сцен."""
    sys_prompt = (
        "Ты – YouTube‑сценарист. Дай JSON без форматирования вида:\n"
        "{\"title\":..., \"voiceover\":..., \"shots\":[{\"img_prompt\":..., \"duration\": int_sec}, ...]}\n"
        "Говори по‑русски, хронометраж <55 сек, максимум 6 сцен."
    )
    resp = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"Тема: {topic}"},
        ],
        temperature=0.8,
    )
    content = resp.choices[0].message.content.strip()
    return json.loads(content)

# -----------------------------------------------------------
# 3. ASSET GENERATION (DALL·E + TTS)
# -----------------------------------------------------------

def make_assets(script: Dict) -> (List[Path], Path):
    images = []
    for i, shot in enumerate(tqdm(script["shots"], desc="Images")):
        prompt = shot["img_prompt"] + ", cinematic, 8k, vertical"
        igen = openai.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1080x1920",
        )
        url = igen.data[0].url
        img_path = Path(tempfile.mktemp(suffix=f"_{i}.png"))
        # Загрузка файла
        from urllib.request import urlretrieve
        urlretrieve(url, img_path)
        images.append(img_path)

    # ---- TTS (пример с OpenAI "tts-1") ----
    audio_resp = openai.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=script["voiceover"]
    )
    voice_path = Path(tempfile.mktemp(suffix=".mp3"))
    voice_path.write_bytes(audio_resp.audio.data)
    return images, voice_path

# -----------------------------------------------------------
# 4. BUILD VIDEO
# -----------------------------------------------------------

def build_video(images: List[Path], voice: Path, out: Path, script: Dict):
    audio_clip = AudioFileClip(str(voice))
    img_clips = []
    total_duration = 0.0
    for img, shot in zip(images, script["shots"]):
        dur = shot["duration"]
        clip = (
            ImageClip(str(img))
            .set_duration(dur)
            .resize(height=1920)
            .set_position("center")
        )
        # лёгкий Ken‑Burns
        clip = clip.fx(vfx.zoom_in, 1.05)
        img_clips.append(clip)
        total_duration += dur

    video = concatenate_videoclips(img_clips, method="compose").set_audio(audio_clip)
    # Укорачиваем/растягиваем аудио до длины видео
    audio_clip = audio_clip.set_duration(total_duration)
    video = video.set_audio(audio_clip)

    video.write_videofile(
        str(out), fps=30, codec="libx264", audio_codec="aac", bitrate="3M"
    )

# -----------------------------------------------------------
# 5. YOUTUBE AUTH & UPLOAD
# -----------------------------------------------------------

def yt_service():
    if CLIENT_SECRET.startswith("file://"):
        path = CLIENT_SECRET[7:]
    elif CLIENT_SECRET.startswith("base64://"):
        raw = CLIENT_SECRET[9:]
        path = tempfile.mktemp()
        Path(path).write_bytes(base64.b64decode(raw))
    else:
        path = CLIENT_SECRET
    flow = InstalledAppFlow.from_client_secrets_file(path, SCOPES)
    creds = flow.run_local_server(port=0)
    return build("youtube", "v3", credentials=creds)


def upload_video(path: Path, meta: Dict, yt):
    body = {
        "snippet": {
            "title": meta["title"] + " #shorts",
            "description": "AI‑generated electronics repair tip\n#shorts",
            "tags": ["electronics", "repair", "AI", "shorts"],
            "categoryId": "28"  # Tech
        },
        "status": {
            "privacyStatus": "public",
            "containsSyntheticMedia": True  # раскрываем синтетику
        }
    }
    media = MediaFileUpload(str(path), chunksize=-1, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    with tqdm(desc="Upload", total=100) as bar:
        while response is None:
            status, response = request.next_chunk()
            if status:
                bar.update(int(status.progress() * 100) - bar.n)
    return response["id"]

# -----------------------------------------------------------
# 6. LOGGING
# -----------------------------------------------------------

def mark_done(topic: str, video_id: str):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT OR IGNORE INTO log VALUES (?,?)", (topic, video_id))
    conn.commit()
    conn.close()

def save_history(topic: str, video_id: str, dt: datetime.datetime) -> None:
    """Writes a JSON log with info about the run."""
    hist = Path("history")
    hist.mkdir(exist_ok=True)
    info = {
        "time": dt.isoformat(),
        "topic": topic,
        "video_id": video_id,
    }
    (hist / f"{dt.date()}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

# -----------------------------------------------------------
# 7. MAIN PIPELINE
# -----------------------------------------------------------

def pipeline():
    now = datetime.datetime.utcnow()
    topic = pick_new_topic()
    print("Topic:", topic)

    script = generate_script(topic)
    images, voice = make_assets(script)

    out = Path(tempfile.mktemp(suffix=".mp4"))
    print("Building video →", out)
    build_video(images, voice, out, script)

    yt = yt_service()
    vid = upload_video(out, script, yt)
    print("🎉 Uploaded http://youtube.com/watch?v=" + vid)

    mark_done(topic, vid)
    save_history(topic, vid, now)

# -----------------------------------------------------------

if __name__ == "__main__":
    try:
        pipeline()
    except Exception as e:
        print("ERROR:", e)
        raise
