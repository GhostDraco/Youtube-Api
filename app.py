import os
import json
import subprocess
import shutil
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
COOKIES_PATH = os.path.join(BASE_DIR, 'cookies.txt')

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_video_id(url):
    """Extract video ID from URL"""
    import re
    
    url = url.strip()
    
    # Direct video ID (most common)
    if len(url) == 11 and ' ' not in url and '/' not in url and '=' not in url:
        return url
    
    # Extract from various YouTube URL formats
    patterns = [
        r'youtube\.com/watch\?v=([^&]+)',
        r'youtu\.be/([^?]+)',
        r'youtube\.com/embed/([^?]+)',
        r'youtube\.com/shorts/([^?]+)',
        r'youtube\.com/v/([^?]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    # If URL contains v= parameter
    if 'v=' in url:
        return url.split('v=')[-1].split('&')[0]
    
    return url

def check_cookies():
    """Check if cookies.txt exists and is valid"""
    if not os.path.exists(COOKIES_PATH):
        logger.error("❌ cookies.txt NOT FOUND in app directory!")
        logger.info("Please add cookies.txt file in the same directory as app.py")
        return False
    
    file_size = os.path.getsize(COOKIES_PATH)
    
    # Check file content
    with open(COOKIES_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read(500)  # Read first 500 chars
    
    if file_size < 100:
        logger.warning(f"⚠️  cookies.txt is too small: {file_size} bytes")
        return False
    
    # Check if it looks like a Netscape cookies file
    if 'youtube.com' in content.lower() or 'netscape' in content.lower():
        logger.info(f"✅ Valid cookies.txt found: {file_size} bytes")
        return True
    else:
        logger.warning("⚠️  cookies.txt doesn't look like a proper cookies file")
        return True  # Still try to use it

def download_with_cookies(video_id, media_type):
    """Download using yt-dlp with cookies.txt"""
    
    output_file = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
    output_path = os.path.join(DOWNLOAD_DIR, output_file)
    
    # Check if file already exists and is valid
    if os.path.exists(output_path):
        file_size = os.path.getsize(output_path)
        if file_size > 50000:  # At least 50KB for audio, more for video
            logger.info(f"✅ File already exists: {output_file} ({file_size/1024:.1f} KB)")
            return True, output_path
        else:
            # Remove corrupted file
            os.remove(output_path)
            logger.warning(f"⚠️  Removed corrupted file: {output_file}")
    
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Build yt-dlp command with cookies
    cmd = [
        'yt-dlp',
        '--cookies', COOKIES_PATH,
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        '--referer', 'https://www.youtube.com/',
        '--retries', '20',
        '--fragment-retries', '20',
        '--skip-unavailable-fragments',
        '--no-check-certificates',
        '--force-ipv4',
        '--geo-bypass',
        '--socket-timeout', '30',
        '--source-address', '0.0.0.0',
        '--no-warnings',
        '--quiet',
    ]
    
    # Add media type specific options
    if media_type == 'audio':
        cmd.extend([
            '-x',  # Extract audio
            '--audio-format', 'mp3',
            '--audio-quality', '0',  # Best quality
            '--embed-thumbnail',
            '--add-metadata',
            '--postprocessor-args', '-metadata comment=""',
            '--output', os.path.join(DOWNLOAD_DIR, f'{video_id}.%(ext)s'),
        ])
    else:  # video
        cmd.extend([
            '-f', 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            '--merge-output-format', 'mp4',
            '--embed-thumbnail',
            '--add-metadata',
            '--output', os.path.join(DOWNLOAD_DIR, f'{video_id}.%(ext)s'),
        ])
    
    cmd.append(youtube_url)
    
    try:
        logger.info(f"📥 Starting download: {video_id} ({media_type})")
        
        # Set environment variables
        env = os.environ.copy()
        env['YTDLP_NO_WARNINGS'] = '1'
        
        # Run download with timeout
        start_time = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)  # 10 minutes timeout
        
        download_time = time.time() - start_time
        
        if result.returncode == 0:
            # Check if file was created
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                logger.info(f"✅ Download successful: {output_file} ({file_size/1024:.1f} KB, {download_time:.1f}s)")
                return True, output_path
            else:
                # Try to find any file with this video ID
                for filename in os.listdir(DOWNLOAD_DIR):
                    if filename.startswith(video_id):
                        found_path = os.path.join(DOWNLOAD_DIR, filename)
                        # Rename to correct extension
                        if filename != output_file:
                            shutil.move(found_path, output_path)
                            logger.info(f"📝 Renamed {filename} to {output_file}")
                        return True, output_path
        
        # If download failed, show error
        error_msg = result.stderr[:500] if result.stderr else "Unknown error"
        logger.error(f"❌ Download failed: {error_msg}")
        
        # Try without cookies as fallback
        return try_without_cookies(video_id, media_type, error_msg)
        
    except subprocess.TimeoutExpired:
        logger.error(f"⏰ Download timeout for {video_id}")
        return False, "Download timeout (10 minutes)"
    except Exception as e:
        logger.error(f"💥 Download exception: {str(e)}")
        return False, str(e)

def try_without_cookies(video_id, media_type, previous_error):
    """Try download without cookies as fallback"""
    logger.info("🔄 Trying download without cookies...")
    
    output_file = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
    output_path = os.path.join(DOWNLOAD_DIR, output_file)
    
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Simple command without cookies
    cmd = [
        'yt-dlp',
        '-x' if media_type == 'audio' else '-f best[ext=mp4]',
        '--audio-format', 'mp3' if media_type == 'audio' else '',
        '--audio-quality', '0' if media_type == 'audio' else '',
        '--output', output_path.replace('.mp3', '.%(ext)s').replace('.mp4', '.%(ext)s'),
        '--quiet',
        youtube_url
    ]
    
    # Remove empty strings
    cmd = [c for c in cmd if c != '']
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0 and os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            if file_size > 10000:
                logger.info(f"✅ Fallback download successful: {file_size/1024:.1f} KB")
                return True, output_path
        
        return False, f"Both methods failed. Original error: {previous_error}"
        
    except Exception as e:
        return False, f"Fallback also failed: {str(e)}"

@app.route('/download', methods=['GET'])
def download():
    """Main download endpoint"""
    try:
        video_url = request.args.get('url', '').strip()
        media_type = request.args.get('type', 'audio').strip().lower()
        
        # Validate input
        if not video_url:
            return jsonify({'error': 'Missing YouTube URL or Video ID'}), 400
        
        if media_type not in ['audio', 'video']:
            return jsonify({'error': 'Type must be "audio" or "video"'}), 400
        
        # Extract video ID
        video_id = get_video_id(video_url)
        if not video_id or len(video_id) != 11:
            return jsonify({'error': 'Invalid YouTube URL or Video ID'}), 400
        
        logger.info(f"📨 Download request: {video_id} ({media_type})")
        
        # Check cookies
        if not check_cookies():
            return jsonify({
                'error': 'cookies.txt not found or invalid',
                'message': 'Please add a valid cookies.txt file to the app directory'
            }), 500
        
        # Download the file
        success, result = download_with_cookies(video_id, media_type)
        
        if success:
            stream_url = f"{request.host_url.rstrip('/')}/stream/{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
            return jsonify({
                'status': 'success',
                'message': 'Download completed successfully',
                'stream_url': stream_url,
                'video_id': video_id,
                'type': media_type
            })
        else:
            return jsonify({
                'error': 'Download failed',
                'details': result,
                'video_id': video_id,
                'type': media_type
            }), 500
            
    except Exception as e:
        logger.error(f"🔥 Endpoint error: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/stream/<filename>', methods=['GET'])
def stream_file(filename):
    """Serve downloaded files"""
    try:
        # Security check
        if '..' in filename or '/' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
        
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        # Check file size
        file_size = os.path.getsize(filepath)
        if file_size < 10000:  # Less than 10KB
            return jsonify({'error': 'File is too small or corrupted'}), 500
        
        # Determine content type
        if filename.endswith('.mp3'):
            mimetype = 'audio/mpeg'
            as_attachment = False
        elif filename.endswith('.mp4'):
            mimetype = 'video/mp4'
            as_attachment = True
        else:
            mimetype = 'application/octet-stream'
            as_attachment = True
        
        logger.info(f"📤 Serving file: {filename} ({file_size/1024:.1f} KB)")
        
        return send_file(
            filepath,
            mimetype=mimetype,
            as_attachment=as_attachment,
            download_name=filename if as_attachment else None
        )
        
    except Exception as e:
        logger.error(f"💥 Stream error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    cookies_status = "present" if os.path.exists(COOKIES_PATH) else "missing"
    cookies_size = os.path.getsize(COOKIES_PATH) if os.path.exists(COOKIES_PATH) else 0
    
    # Count files in download directory
    file_count = len([f for f in os.listdir(DOWNLOAD_DIR) if os.path.isfile(os.path.join(DOWNLOAD_DIR, f))])
    
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube Downloader with Cookies',
        'timestamp': datetime.now().isoformat(),
        'cookies': {
            'status': cookies_status,
            'size_bytes': cookies_size
        },
        'storage': {
            'download_dir': DOWNLOAD_DIR,
            'file_count': file_count
        }
    })

@app.route('/cookies/status', methods=['GET'])
def cookies_status():
    """Check cookies.txt status"""
    if os.path.exists(COOKIES_PATH):
        file_size = os.path.getsize(COOKIES_PATH)
        with open(COOKIES_PATH, 'r', encoding='utf-8', errors='ignore') as f:
            first_line = f.readline().strip()
        
        return jsonify({
            'status': 'found',
            'size_bytes': file_size,
            'size_kb': round(file_size / 1024, 2),
            'first_line': first_line[:100] if first_line else '',
            'path': COOKIES_PATH
        })
    else:
        return jsonify({
            'status': 'not_found',
            'message': 'cookies.txt not found in app directory',
            'expected_path': COOKIES_PATH
        })

@app.route('/cleanup', methods=['POST'])
def cleanup():
    """Clean old files"""
    try:
        deleted = []
        total_freed = 0
        current_time = time.time()
        
        for filename in os.listdir(DOWNLOAD_DIR):
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 86400:  # Older than 24 hours
                    file_size = os.path.getsize(filepath)
                    os.remove(filepath)
                    deleted.append({
                        'filename': filename,
                        'size_kb': round(file_size / 1024, 2),
                        'age_hours': round(file_age / 3600, 1)
                    })
                    total_freed += file_size
        
        return jsonify({
            'status': 'success',
            'deleted_files': deleted,
            'deleted_count': len(deleted),
            'freed_kb': round(total_freed / 1024, 2),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Initialize on startup
if __name__ == '__main__':
    # Check cookies on startup
    if check_cookies():
        logger.info("✅ Cookies.txt is ready!")
    else:
        logger.warning("⚠️  Cookies.txt not found or invalid")
        logger.info("💡 Please add cookies.txt file in the same directory as app.py")
    
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 YouTube Downloader with Cookies starting on port {port}")
    logger.info(f"📁 Download directory: {DOWNLOAD_DIR}")
    logger.info(f"🍪 Cookies path: {COOKIES_PATH}")
    
    app.run(host='0.0.0.0', port=port, debug=False)
