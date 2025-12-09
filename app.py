import os
import json
import tempfile
from flask import Flask, request, jsonify, send_from_directory, abort
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# YT-DLP Options
AUDIO_OPTS = {
    'format': 'bestaudio/best',
    'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
}

VIDEO_OPTS = {
    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
    'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
    'merge_output_format': 'mp4',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
}

def clean_filename(filename):
    """Remove any unsafe characters from filename"""
    return ''.join(c for c in filename if c.isalnum() or c in '._- ')

@app.route('/download', methods=['GET'])
def download():
    """Download YouTube video/audio"""
    try:
        # Get parameters
        video_id = request.args.get('url', '').strip()
        media_type = request.args.get('type', 'audio').strip().lower()
        
        # Validate parameters
        if not video_id:
            return jsonify({'error': 'Missing video URL/ID'}), 400
        
        if media_type not in ['audio', 'video']:
            return jsonify({'error': 'Type must be audio or video'}), 400
        
        # Extract video ID from URL if full URL is provided
        if 'youtube.com' in video_id or 'youtu.be' in video_id:
            if 'v=' in video_id:
                video_id = video_id.split('v=')[-1].split('&')[0]
            elif 'youtu.be/' in video_id:
                video_id = video_id.split('youtu.be/')[-1].split('?')[0]
        
        if not video_id or len(video_id) < 3:
            return jsonify({'error': 'Invalid video ID'}), 400
        
        # Determine output filename
        if media_type == 'audio':
            output_filename = f"{video_id}.mp3"
            output_path = os.path.join(DOWNLOAD_DIR, output_filename)
            ydl_opts = AUDIO_OPTS.copy()
            ydl_opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, f'{video_id}.%(ext)s')
        else:
            output_filename = f"{video_id}.mp4"
            output_path = os.path.join(DOWNLOAD_DIR, output_filename)
            ydl_opts = VIDEO_OPTS.copy()
            ydl_opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, f'{video_id}.%(ext)s')
        
        # Check if file already exists
        if os.path.exists(output_path):
            app.logger.info(f"File already exists: {output_filename}")
            stream_url = f"{request.host_url.rstrip('/')}/stream/{output_filename}"
            return jsonify({
                'status': 'success',
                'stream_url': stream_url
            })
        
        # Download using yt-dlp
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extract video info first
                info = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=False)
                
                # Download the video/audio
                ydl.download([f'https://www.youtube.com/watch?v={video_id}'])
                
                # Check if file was created
                if not os.path.exists(output_path):
                    # Try to find the actual file created
                    for file in os.listdir(DOWNLOAD_DIR):
                        if file.startswith(video_id):
                            output_filename = file
                            output_path = os.path.join(DOWNLOAD_DIR, output_filename)
                            break
        
        except yt_dlp.utils.DownloadError as e:
            if "Private video" in str(e):
                return jsonify({'error': 'Video is private'}), 403
            elif "Video unavailable" in str(e):
                return jsonify({'error': 'Video unavailable'}), 404
            elif "Sign in to confirm" in str(e):
                return jsonify({'error': 'Age-restricted video. Cannot download.'}), 403
            else:
                app.logger.error(f"Download error: {str(e)}")
                return jsonify({'error': f'Download failed: {str(e)}'}), 500
        
        except Exception as e:
            app.logger.error(f"Unexpected error: {str(e)}")
            return jsonify({'error': f'Unexpected error: {str(e)}'}), 500
        
        # Verify file was created
        if not os.path.exists(output_path):
            return jsonify({'error': 'Download failed - file not created'}), 500
        
        # Create stream URL
        stream_url = f"{request.host_url.rstrip('/')}/stream/{output_filename}"
        
        return jsonify({
            'status': 'success',
            'stream_url': stream_url
        })
    
    except Exception as e:
        app.logger.error(f"Server error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/stream/<filename>', methods=['GET'])
def stream_file(filename):
    """Serve downloaded files"""
    try:
        # Security check
        filename = clean_filename(filename)
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        # Serve file
        return send_from_directory(
            DOWNLOAD_DIR,
            filename,
            as_attachment=False,
            mimetype='audio/mpeg' if filename.endswith('.mp3') else 'video/mp4'
        )
    
    except Exception as e:
        app.logger.error(f"Stream error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Heroku"""
    return jsonify({'status': 'ok', 'service': 'YouTube Downloader API'})

@app.route('/cleanup', methods=['POST'])
def cleanup():
    """Cleanup old files (optional endpoint for maintenance)"""
    try:
        import time
        current_time = time.time()
        deleted_files = []
        
        for filename in os.listdir(DOWNLOAD_DIR):
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            # Delete files older than 1 hour
            if os.path.isfile(filepath) and (current_time - os.path.getmtime(filepath)) > 3600:
                os.remove(filepath)
                deleted_files.append(filename)
        
        return jsonify({
            'status': 'success',
            'deleted': deleted_files,
            'count': len(deleted_files)
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
