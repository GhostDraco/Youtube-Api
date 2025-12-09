from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import uuid
import os

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def download_audio(url):
    file_id = str(uuid.uuid4())
    output_path = f"{DOWNLOAD_FOLDER}/{file_id}.mp3"
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_path
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return file_id

def download_video(url):
    file_id = str(uuid.uuid4())
    output_path = f"{DOWNLOAD_FOLDER}/{file_id}.mp4"
    ydl_opts = {
        "format": "best",
        "outtmpl": output_path
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return file_id

@app.route("/")
def home():
    return "Your YouTube Downloader API is Running 🔥"

@app.route("/audio")
def audio_api():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400
    file_id = download_audio(url)
    return jsonify({"link": f"https://{HEROKU_APP_NAME}.herokuapp.com/stream/{file_id}.mp3"})

@app.route("/video")
def video_api():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400
    file_id = download_video(url)
    return jsonify({"link": f"https://{HEROKU_APP_NAME}.herokuapp.com/stream/{file_id}.mp4"})

@app.route("/stream/<path:filename>")
def stream_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=False)

# Required for Heroku to detect server
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
