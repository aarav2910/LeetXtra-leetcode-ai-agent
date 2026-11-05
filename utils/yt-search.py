
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

API_KEY = os.getenv("YOUTUBE_API_KEY")

def search_youtube(problem_name: str, language: str):
    if not API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is missing from the project-root .env file.")
    url = "https://www.googleapis.com/youtube/v3/search"

    params = {
        "part": "snippet",
        "q": f"{problem_name} {language} LeetCode solution",
        "type": "video",
        "order": "relevance",
        "maxResults": 4,
        "key": API_KEY
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()

    videos = []

    for item in data.get("items", []):
        video_id = item["id"]["videoId"]
        snippet = item["snippet"]

        videos.append({
            "title": snippet["title"],
            "channel": snippet["channelTitle"],
            "thumbnail": snippet["thumbnails"]["medium"]["url"],
            "video_url": f"https://www.youtube.com/watch?v={video_id}"
        })

    return videos
