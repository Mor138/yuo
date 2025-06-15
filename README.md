# YouTube Shorts Bot

This repository contains `yt_shorts_bot.py`, a script that generates and uploads
YouTube Shorts. A GitHub Actions workflow runs the bot daily at 12:00 UTC.

## Environment variables

The bot requires the following secrets:

- `OPENAI_API_KEY` – OpenAI API key
- `DEEPSEEK_API_KEY` – DeepSeek API key
- `GOOGLE_CLIENT_SECRET_JSON` – contents of `client_secret.json` (JSON or base64)

## History

Each run writes a JSON file to `history/` with the upload information.

## Installing dependencies

Before running the bot locally you need Python 3.10+ and the packages listed in
`requirements.txt`. Install them with:

```bash
pip install -r requirements.txt
```
