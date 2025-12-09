import os
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
TEMP_DIR = os.path.join(BASE_DIR, 'temp')

# Create directories
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Cleanup settings
MAX_FILE_AGE_HOURS = 24  # Keep files for 24 hours
MAX_STORAGE_MB = 1024  # 1GB max storage (adjust based on your Heroku plan)
CLEANUP_INTERVAL = 3600  # Cleanup every 1 hour

# Status tracking
download_status = {}

def get_video_id(url):
    """Extract video ID from various YouTube URL formats"""
    url = url.strip()
    
    # Direct video ID
    if len(url) in [11, 12] and '/' not in url and ' ' not in url:
        return url
    
    # YouTube URL patterns
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)'
        r'([^&?/\s]+)'
    ]
    
    import re
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    # If no pattern matches, assume it's a video ID
    return url.split('/')[-1].split('?')[0]

def download_with_ytdlp(video_id, media_type):
    """Download using yt-dlp with proper format handling"""
    
    # YouTube URL
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Build command based on media type
    if media_type == 'audio':
        cmd = [
            'yt-dlp',
            '-x',  # Extract audio
            '--audio-format', 'mp3',
            '--audio-quality', '0',  # Best quality
            '--embed-thumbnail',
            '--add-metadata',
            '--ppa', 'EmbedThumbnail+ffmpeg_o:-c:v mjpeg -vf crop=\'if(gt(ih,iw),iw,ih)\':\'if(gt(iw,ih),ih,iw)\'',
            '--output', os.path.join(TEMP_DIR, f'{video_id}.%(ext)s'),
            '--no-warnings',
            '--quiet',
            youtube_url
        ]
        expected_ext = '.mp3'
    else:  # video
        cmd = [
            'yt-dlp',
            '-f', 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            '--merge-output-format', 'mp4',
            '--embed-thumbnail',
            '--add-metadata',
            '--output', os.path.join(TEMP_DIR, f'{video_id}.%(ext)s'),
            '--no-warnings',
            '--quiet',
            youtube_url
        ]
        expected_ext = '.mp4'
    
    try:
        logger.info(f"Downloading {media_type} for video {video_id}")
        
        # Run yt-dlp
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            # Find the downloaded file
            for file in os.listdir(TEMP_DIR):
                if file.startswith(video_id):
                    temp_path = os.path.join(TEMP_DIR, file)
                    final_path = os.path.join(DOWNLOAD_DIR, f"{video_id}{expected_ext}")
                    
                    # Move to final location
                    shutil.move(temp_path, final_path)
                    
                    # Verify file exists and has content
                    if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
                        logger.info(f"Successfully downloaded: {final_path}")
                        return True, final_path
                    else:
                        return False, "Downloaded file is empty"
            
            return False, "File not found after download"
        else:
            error_msg = result.stderr if result.stderr else "Unknown error"
            logger.error(f"yt-dlp error: {error_msg}")
            return False, error_msg
            
    except subprocess.TimeoutExpired:
        return False, "Download timeout (5 minutes)"
    except Exception as e:
        logger.error(f"Download exception: {str(e)}")
        return False, str(e)

def get_file_info():
    """Get information about stored files"""
    files = []
    total_size = 0
    
    for filename in os.listdir(DOWNLOAD_DIR):
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        if os.path.isfile(filepath):
            size = os.path.getsize(filepath)
            modified = datetime.fromtimestamp(os.path.getmtime(filepath))
            age = datetime.now() - modified
            
            files.append({
                'filename': filename,
                'size_mb': round(size / (1024 * 1024), 2),
                'age_hours': round(age.total_seconds() / 3600, 1),
                'modified': modified.isoformat()
            })
            total_size += size
    
    return {
        'file_count': len(files),
        'total_size_mb': round(total_size / (1024 * 1024), 2),
        'files': sorted(files, key=lambda x: x['modified'], reverse=True)
    }

def cleanup_old_files():
    """Remove old files to free up space"""
    try:
        deleted_files = []
        freed_space = 0
        current_time = time.time()
        
        for filename in os.listdir(DOWNLOAD_DIR):
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                
                # Delete if older than MAX_FILE_AGE_HOURS
                if file_age > MAX_FILE_AGE_HOURS * 3600:
                    file_size = os.path.getsize(filepath)
                    os.remove(filepath)
                    deleted_files.append(filename)
                    freed_space += file_size
                    logger.info(f"Cleaned up old file: {filename}")
        
        # Also clean temp directory
        for filename in os.listdir(TEMP_DIR):
            filepath = os.path.join(TEMP_DIR, filename)
            if os.path.isfile(filepath):
                os.remove(filepath)
        
        logger.info(f"Cleanup completed: {len(deleted_files)} files deleted, "
                   f"{freed_space / (1024*1024):.2f} MB freed")
        
        return deleted_files, freed_space
        
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")
        return [], 0

def start_cleanup_thread():
    """Start background cleanup thread"""
    def cleanup_worker():
        while True:
            try:
                cleanup_old_files()
                time.sleep(CLEANUP_INTERVAL)
            except Exception as e:
                logger.error(f"Cleanup thread error: {str(e)}")
                time.sleep(300)  # Wait 5 minutes on error
    
    thread = threading.Thread(target=cleanup_worker, daemon=True)
    thread.start()
    logger.info("Cleanup thread started")

@app.route('/download', methods=['GET'])
def download():
    """Main download endpoint"""
    try:
        # Get parameters
        video_url = request.args.get('url', '').strip()
        media_type = request.args.get('type', 'audio').strip().lower()
        
        # Validate
        if not video_url:
            return jsonify({'error': 'Missing video URL/ID'}), 400
        
        if media_type not in ['audio', 'video']:
            return jsonify({'error': 'Type must be "audio" or "video"'}), 400
        
        # Extract video ID
        video_id = get_video_id(video_url)
        if not video_id or len(video_id) < 11:
            return jsonify({'error': 'Invalid YouTube URL or video ID'}), 400
        
        # Check if file already exists
        ext = '.mp3' if media_type == 'audio' else '.mp4'
        existing_file = os.path.join(DOWNLOAD_DIR, f"{video_id}{ext}")
        
        if os.path.exists(existing_file):
            logger.info(f"File already exists: {video_id}{ext}")
            stream_url = f"{request.host_url.rstrip('/')}/stream/{video_id}{ext}"
            return jsonify({
                'status': 'success',
                'message': 'File already downloaded',
                'stream_url': stream_url,
                'video_id': video_id,
                'type': media_type
            })
        
        # Download the file
        success, result = download_with_ytdlp(video_id, media_type)
        
        if success:
            # Get filename from path
            filename = os.path.basename(result)
            stream_url = f"{request.host_url.rstrip('/')}/stream/{filename}"
            
            return jsonify({
                'status': 'success',
                'message': 'Download completed',
                'stream_url': stream_url,
                'video_id': video_id,
                'type': media_type,
                'filename': filename
            })
        else:
            return jsonify({
                'error': 'Download failed',
                'details': result
            }), 500
            
    except Exception as e:
        logger.error(f"Download endpoint error: {str(e)}")
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
        if file_size == 0:
            return jsonify({'error': 'File is empty'}), 500
        
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
        
        # Serve file
        return send_file(
            filepath,
            mimetype=mimetype,
            as_attachment=as_attachment,
            download_name=filename if as_attachment else None
        )
        
    except Exception as e:
        logger.error(f"Stream error: {str(e)}")
        return jsonify({'error': 'File serving error'}), 500

@app.route('/status', methods=['GET'])
def get_status():
    """Get server status and file information"""
    try:
        file_info = get_file_info()
        
        return jsonify({
            'status': 'online',
            'server_time': datetime.now().isoformat(),
            'storage': {
                'download_dir': DOWNLOAD_DIR,
                'total_files': file_info['file_count'],
                'total_size_mb': file_info['total_size_mb'],
                'max_storage_mb': MAX_STORAGE_MB,
                'max_age_hours': MAX_FILE_AGE_HOURS
            },
            'files': file_info['files'][:20]  # First 20 files
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/cleanup', methods=['POST'])
def manual_cleanup():
    """Manually trigger cleanup"""
    try:
        deleted_files, freed_space = cleanup_old_files()
        
        return jsonify({
            'status': 'success',
            'deleted_files': deleted_files,
            'deleted_count': len(deleted_files),
            'freed_space_mb': round(freed_space / (1024 * 1024), 2),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/search', methods=['GET'])
def search_files():
    """Search for files by video ID"""
    try:
        search_term = request.args.get('q', '').strip().lower()
        if not search_term:
            return jsonify({'error': 'Missing search term'}), 400
        
        matching_files = []
        for filename in os.listdir(DOWNLOAD_DIR):
            if search_term in filename.lower():
                filepath = os.path.join(DOWNLOAD_DIR, filename)
                if os.path.isfile(filepath):
                    size = os.path.getsize(filepath)
                    matching_files.append({
                        'filename': filename,
                        'size_mb': round(size / (1024 * 1024), 2),
                        'url': f"{request.host_url.rstrip('/')}/stream/{filename}"
                    })
        
        return jsonify({
            'status': 'success',
            'search_term': search_term,
            'results': matching_files,
            'count': len(matching_files)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/delete/<filename>', methods=['DELETE'])
def delete_file(filename):
    """Delete specific file"""
    try:
        if '..' in filename or '/' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
        
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        file_size = os.path.getsize(filepath)
        os.remove(filepath)
        
        return jsonify({
            'status': 'success',
            'message': 'File deleted',
            'filename': filename,
            'size_mb': round(file_size / (1024 * 1024), 2),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube Downloader API',
        'version': '2.0',
        'timestamp': datetime.now().isoformat(),
        'endpoints': {
            'download': '/download?url=VIDEO_ID&type=audio|video',
            'stream': '/stream/FILENAME',
            'status': '/status',
            'search': '/search?q=SEARCH_TERM',
            'cleanup': '/cleanup (POST)',
            'delete': '/delete/FILENAME (DELETE)'
        }
    })

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# Initialize
if __name__ == '__main__':
    # Start cleanup thread
    start_cleanup_thread()
    
    # Initial cleanup
    cleanup_old_files()
    
    logger.info("YouTube Downloader API starting...")
    logger.info(f"Download directory: {DOWNLOAD_DIR}")
    logger.info(f"Max storage: {MAX_STORAGE_MB} MB")
    logger.info(f"Max file age: {MAX_FILE_AGE_HOURS} hours")
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
