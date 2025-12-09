from flask import Flask, request, jsonify, send_file
import yt_dlp
import os

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def download_media(video_id, type_):
    file_ext = "mp3" if type_ == "audio" else "mp4"
    file_path = f"{DOWNLOAD_FOLDER}/{video_id}.{file_ext}"

    if os.path.exists(file_path):  # cached file
        return file_path

    ydl_opts = {
        "format": "bestaudio/best" if type_ == "audio" else "best",
        "outtmpl": file_path,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }] if type_ == "audio" else []
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

    return file_path

@app.route("/")
def home():
    return jsonify({"status": "Your API is running!"})

@app.route("/download")
def download():
    video_id = request.args.get("url")
    type_ = request.args.get("type")

    if not video_id or not type_:
        return jsonify({"error": "Missing url or type"}), 400

    file_path = download_media(video_id, type_)

    return jsonify({
        "status": "success",
        "video_id": video_id,
        "type": type_,
        "downloaded": True,
        "stream_url": f"{request.host_url}stream/{video_id}.{ 'mp3' if type_=='audio' else 'mp4'}"
    })

@app.route("/stream/<path:filename>")
def stream(filename):
    file_path = f"{DOWNLOAD_FOLDER}/{filename}"
    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404
    return send_file(file_path, as_attachment=False)

if __name__ == "__main__":
    app.run(debug=True)
