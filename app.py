import os
import json
import subprocess
import shutil
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import logging

app = Flask(__name__)
CORS(app)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_video_id(url):
    """Extract video ID from URL"""
    import re
    
    if 'youtube.com/watch?v=' in url:
        return url.split('v=')[-1].split('&')[0]
    elif 'youtu.be/' in url:
        return url.split('youtu.be/')[-1].split('?')[0]
    elif 'youtube.com/shorts/' in url:
        return url.split('shorts/')[-1].split('?')[0]
    
    # If it's already a video ID
    if len(url) in [11, 12] and ' ' not in url and '/' not in url:
        return url
    
    return url

def download_with_cookies(video_id, media_type):
    """Download using yt-dlp with cookies to bypass bot protection"""
    
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    output_file = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
    output_path = os.path.join(DOWNLOAD_DIR, output_file)
    
    # Check if file already exists
    if os.path.exists(output_path):
        logger.info(f"File already exists: {output_file}")
        return True, output_path
    
    # Build yt-dlp command with anti-bot measures
    if media_type == 'audio':
        cmd = [
            'yt-dlp',
            '-x',  # Extract audio
            '--audio-format', 'mp3',
            '--audio-quality', '0',
            '--embed-thumbnail',
            '--add-metadata',
            '--cookies-from-browser', 'chrome',  # Use browser cookies
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--referer', 'https://www.youtube.com/',
            '--sleep-interval', '5',  # Add delay between requests
            '--max-sleep-interval', '10',
            '--retries', '10',  # More retries
            '--fragment-retries', '10',
            '--skip-unavailable-fragments',
            '--no-check-certificates',
            '--force-ipv4',
            '--output', output_path.replace('.mp3', '.%(ext)s'),
            '--no-warnings',
            '--quiet',
            youtube_url
        ]
    else:  # video
        cmd = [
            'yt-dlp',
            '-f', 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            '--merge-output-format', 'mp4',
            '--embed-thumbnail',
            '--add-metadata',
            '--cookies-from-browser', 'chrome',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--referer', 'https://www.youtube.com/',
            '--sleep-interval', '5',
            '--max-sleep-interval', '10',
            '--retries', '10',
            '--fragment-retries', '10',
            '--skip-unavailable-fragments',
            '--no-check-certificates',
            '--force-ipv4',
            '--output', output_path.replace('.mp4', '.%(ext)s'),
            '--no-warnings',
            '--quiet',
            youtube_url
        ]
    
    try:
        logger.info(f"Downloading {media_type} for {video_id}")
        
        # Run yt-dlp with timeout
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=300,
            env={**os.environ, 'YTDLP_NO_WARNINGS': '1'}
        )
        
        if result.returncode == 0:
            # Check if file was created
            if os.path.exists(output_path):
                logger.info(f"Download successful: {output_file}")
                return True, output_path
            else:
                # Try alternative naming
                for file in os.listdir(DOWNLOAD_DIR):
                    if file.startswith(video_id):
                        alt_path = os.path.join(DOWNLOAD_DIR, file)
                        # Rename to standard format
                        shutil.move(alt_path, output_path)
                        return True, output_path
                
                return False, "File not created after download"
        else:
            error_msg = result.stderr
            logger.error(f"Download failed: {error_msg}")
            
            # Try alternative method if cookie method fails
            return try_alternative_method(video_id, media_type, error_msg)
            
    except subprocess.TimeoutExpired:
        return False, "Download timeout (5 minutes)"
    except Exception as e:
        logger.error(f"Exception: {str(e)}")
        return False, str(e)

def try_alternative_method(video_id, media_type, original_error):
    """Try alternative download methods"""
    
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    output_file = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
    output_path = os.path.join(DOWNLOAD_DIR, output_file)
    
    # Method 2: Use yt-dlp without cookies but with different options
    logger.info("Trying alternative download method...")
    
    if media_type == 'audio':
        cmd = [
            'yt-dlp',
            '-x',
            '--audio-format', 'mp3',
            '--audio-quality', '0',
            '--throttled-rate', '100K',  # Limit rate to avoid detection
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            '--referer', 'https://www.google.com/',
            '--sleep-interval', '10',
            '--max-sleep-interval', '30',
            '--retries', '20',
            '--output', output_path.replace('.mp3', '.%(ext)s'),
            youtube_url
        ]
    else:
        cmd = [
            'yt-dlp',
            '-f', 'worst[ext=mp4]',  # Use worst quality to avoid detection
            '--throttled-rate', '100K',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            '--referer', 'https://www.google.com/',
            '--sleep-interval', '10',
            '--max-sleep-interval', '30',
            '--retries', '20',
            '--output', output_path.replace('.mp4', '.%(ext)s'),
            youtube_url
        ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            if os.path.exists(output_path):
                return True, output_path
            else:
                # Last resort: Use external service
                return use_external_service(video_id, media_type)
        else:
            return False, f"Alternative method failed: {result.stderr}"
            
    except Exception as e:
        return False, f"Alternative method exception: {str(e)}"

def use_external_service(video_id, media_type):
    """Use external services as last resort"""
    # This is a placeholder - you can integrate with other APIs
    # For now, return error
    return False, "All download methods failed. YouTube is blocking downloads for this video."

@app.route('/download', methods=['GET'])
def download():
    """Main download endpoint"""
    try:
        video_url = request.args.get('url', '').strip()
        media_type = request.args.get('type', 'audio').strip().lower()
        
        if not video_url:
            return jsonify({'error': 'Missing URL'}), 400
        
        if media_type not in ['audio', 'video']:
            return jsonify({'error': 'Type must be audio or video'}), 400
        
        # Extract video ID
        video_id = get_video_id(video_url)
        if not video_id:
            return jsonify({'error': 'Invalid YouTube URL'}), 400
        
        # Download the file
        success, result = download_with_cookies(video_id, media_type)
        
        if success:
            stream_url = f"{request.host_url.rstrip('/')}/stream/{os.path.basename(result)}"
            return jsonify({
                'status': 'success',
                'stream_url': stream_url,
                'video_id': video_id,
                'type': media_type
            })
        else:
            return jsonify({
                'error': 'Download failed',
                'details': result,
                'video_id': video_id
            }), 500
            
    except Exception as e:
        logger.error(f"Endpoint error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/stream/<filename>', methods=['GET'])
def stream_file(filename):
    """Serve downloaded files"""
    try:
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'service': 'YouTube Downloader'})

@app.route('/cleanup', methods=['POST'])
def cleanup():
    """Clean old files"""
    try:
        deleted = []
        current_time = time.time()
        
        for filename in os.listdir(DOWNLOAD_DIR):
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 3600:  # 1 hour
                    os.remove(filepath)
                    deleted.append(filename)
        
        return jsonify({
            'status': 'success',
            'deleted': deleted,
            'count': len(deleted)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting YouTube Downloader on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
