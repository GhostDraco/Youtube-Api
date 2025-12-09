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
    
    # Direct video ID
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
    """Check if cookies.txt exists"""
    if not os.path.exists(COOKIES_PATH):
        logger.warning("cookies.txt not found - some videos may require login")
        return False
    
    file_size = os.path.getsize(COOKIES_PATH)
    if file_size > 100:
        logger.info(f"Using cookies.txt: {file_size} bytes")
        return True
    
    return False

def download_media(video_id, media_type):
    """Download audio or video"""
    
    output_file = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
    output_path = os.path.join(DOWNLOAD_DIR, output_file)
    
    # Check if file already exists
    if os.path.exists(output_path):
        file_size = os.path.getsize(output_path)
        if file_size > 50000:
            logger.info(f"Using cached file: {output_file}")
            return True, output_path
    
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Build command
    cmd = ['yt-dlp', '--quiet', '--no-warnings']
    
    # Add cookies if available
    if os.path.exists(COOKIES_PATH):
        cmd.extend(['--cookies', COOKIES_PATH])
    
    # Add common options
    cmd.extend([
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        '--retries', '10',
        '--fragment-retries', '10',
        '--skip-unavailable-fragments',
        '--no-check-certificates',
        '--force-ipv4',
        '--geo-bypass',
    ])
    
    # Add media type options
    if media_type == 'audio':
        cmd.extend([
            '-x',
            '--audio-format', 'mp3',
            '--audio-quality', '0',
            '--embed-thumbnail',
            '--add-metadata',
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
        logger.info(f"Downloading {media_type}: {video_id}")
        
        # Run download
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        if result.returncode == 0:
            if os.path.exists(output_path):
                return True, output_path
            else:
                # Find any file with this video ID
                for filename in os.listdir(DOWNLOAD_DIR):
                    if filename.startswith(video_id):
                        found_path = os.path.join(DOWNLOAD_DIR, filename)
                        if filename != output_file:
                            shutil.move(found_path, output_path)
                        return True, output_path
        
        return False, result.stderr[:500] if result.stderr else "Download failed"
        
    except Exception as e:
        return False, str(e)

# ============== LIGHTWEIGHT API ENDPOINTS ==============
# Compatible with: https://shrutibots.site format

@app.route('/download', methods=['GET'])
def download():
    """
    Lightweight API endpoint (compatible with shrutibots.site)
    
    Parameters:
        url: YouTube URL or Video ID
        type: "audio" or "video"
    
    Returns:
        Format 1: {"link": "stream_url"}  # for lightweight clients
        Format 2: {"status": "success", "stream_url": "url"}  # for full clients
    """
    try:
        video_url = request.args.get('url', '').strip()
        media_type = request.args.get('type', 'audio').strip().lower()
        
        # Validate
        if not video_url:
            return jsonify({'error': 'Missing URL'}), 400
        
        if media_type not in ['audio', 'video']:
            return jsonify({'error': 'Invalid type'}), 400
        
        # Get video ID
        video_id = get_video_id(video_url)
        if not video_id:
            return jsonify({'error': 'Invalid YouTube URL'}), 400
        
        logger.info(f"Lightweight download request: {video_id} ({media_type})")
        
        # Download file
        success, result = download_media(video_id, media_type)
        
        if success:
            stream_url = f"{request.host_url.rstrip('/')}/stream/{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
            
            # Check client type from User-Agent or Accept header
            user_agent = request.headers.get('User-Agent', '').lower()
            accept_header = request.headers.get('Accept', '')
            
            # Determine response format
            if 'python' in user_agent or 'requests' in user_agent or 'application/json' in accept_header:
                # Full JSON response (for Python clients)
                return jsonify({
                    'status': 'success',
                    'stream_url': stream_url,
                    'video_id': video_id,
                    'type': media_type
                })
            else:
                # Lightweight response (compatible with shrutibots.site)
                return jsonify({
                    'link': stream_url
                })
        else:
            return jsonify({'error': result}), 500
            
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/stream/<filename>', methods=['GET'])
def stream_file(filename):
    """Stream/download file"""
    try:
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        return send_file(
            filepath,
            as_attachment=False,
            mimetype='audio/mpeg' if filename.endswith('.mp3') else 'video/mp4'
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============== FULL-FEATURED API ENDPOINTS ==============
# Compatible with ShrutiMusic YouTube class

@app.route('/api/v1/search', methods=['GET'])
def search_videos():
    """Search videos (compatible with YouTube.search())"""
    try:
        query = request.args.get('q', '').strip()
        limit = int(request.args.get('limit', 5))
        
        if not query:
            return jsonify({'error': 'Missing query'}), 400
        
        # Use yt-dlp to search
        cmd = [
            'yt-dlp', 'ytsearch{}:{}'.format(limit, query),
            '--get-id',
            '--get-title',
            '--get-duration',
            '--get-thumbnail',
            '--quiet',
            '--no-warnings'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            videos = []
            
            for i in range(0, len(lines), 4):
                if i + 3 < len(lines):
                    videos.append({
                        'id': lines[i],
                        'title': lines[i+1],
                        'duration': lines[i+2],
                        'thumbnail': lines[i+3]
                    })
            
            return jsonify({
                'status': 'success',
                'query': query,
                'results': videos
            })
        else:
            return jsonify({'error': 'Search failed'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/v1/details', methods=['GET'])
def video_details():
    """Get video details (compatible with YouTube.details())"""
    try:
        video_url = request.args.get('url', '').strip()
        
        if not video_url:
            return jsonify({'error': 'Missing URL'}), 400
        
        video_id = get_video_id(video_url)
        if not video_id:
            return jsonify({'error': 'Invalid URL'}), 400
        
        # Get video info using yt-dlp
        cmd = [
            'yt-dlp',
            f'https://www.youtube.com/watch?v={video_id}',
            '--get-title',
            '--get-duration',
            '--get-thumbnail',
            '--get-id',
            '--dump-json',
            '--quiet',
            '--no-warnings'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            try:
                info = json.loads(result.stdout)
                return jsonify({
                    'status': 'success',
                    'details': {
                        'title': info.get('title'),
                        'duration': info.get('duration_string'),
                        'thumbnail': info.get('thumbnail'),
                        'id': info.get('id'),
                        'description': info.get('description'),
                        'uploader': info.get('uploader'),
                        'view_count': info.get('view_count')
                    }
                })
            except:
                # Fallback to simple parsing
                lines = result.stdout.strip().split('\n')
                if len(lines) >= 4:
                    return jsonify({
                        'status': 'success',
                        'details': {
                            'title': lines[0],
                            'duration': lines[1],
                            'thumbnail': lines[2],
                            'id': lines[3]
                        }
                    })
        
        return jsonify({'error': 'Failed to get details'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/v1/playlist', methods=['GET'])
def playlist_items():
    """Get playlist items (compatible with YouTube.playlist())"""
    try:
        playlist_url = request.args.get('url', '').strip()
        limit = int(request.args.get('limit', 20))
        
        if not playlist_url:
            return jsonify({'error': 'Missing playlist URL'}), 400
        
        # Get playlist items
        cmd = [
            'yt-dlp',
            '--flat-playlist',
            '--get-id',
            '--playlist-end', str(limit),
            '--quiet',
            '--no-warnings',
            playlist_url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            video_ids = [vid for vid in result.stdout.strip().split('\n') if vid]
            
            return jsonify({
                'status': 'success',
                'playlist_url': playlist_url,
                'videos': video_ids,
                'count': len(video_ids)
            })
        else:
            return jsonify({'error': 'Failed to get playlist'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/v1/formats', methods=['GET'])
def available_formats():
    """Get available formats (compatible with YouTube.formats())"""
    try:
        video_url = request.args.get('url', '').strip()
        
        if not video_url:
            return jsonify({'error': 'Missing URL'}), 400
        
        video_id = get_video_id(video_url)
        if not video_id:
            return jsonify({'error': 'Invalid URL'}), 400
        
        # Get formats info
        cmd = [
            'yt-dlp',
            f'https://www.youtube.com/watch?v={video_id}',
            '--list-formats',
            '--quiet',
            '--no-warnings'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            formats = []
            lines = result.stdout.strip().split('\n')
            
            for line in lines:
                if 'mp4' in line or 'webm' in line or 'audio only' in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        formats.append({
                            'format_id': parts[0],
                            'extension': parts[1],
                            'resolution': parts[2] if len(parts) > 2 else '',
                            'note': parts[3] if len(parts) > 3 else ''
                        })
            
            return jsonify({
                'status': 'success',
                'formats': formats,
                'video_id': video_id
            })
        else:
            return jsonify({'error': 'Failed to get formats'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============== UTILITY ENDPOINTS ==============

@app.route('/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube Dual-Format API',
        'formats_supported': ['lightweight', 'full-featured'],
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/v1/cleanup', methods=['POST'])
def api_cleanup():
    """Clean old files"""
    try:
        deleted = []
        current_time = time.time()
        
        for filename in os.listdir(DOWNLOAD_DIR):
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 86400:  # 24 hours
                    os.remove(filepath)
                    deleted.append(filename)
        
        return jsonify({
            'status': 'success',
            'deleted': deleted,
            'count': len(deleted)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============== COMPATIBILITY ENDPOINTS ==============
# For direct compatibility with existing code

@app.route('/api/download', methods=['GET'])
def api_download():
    """API endpoint with consistent response format"""
    return download()  # Use same logic as /download

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 YouTube Dual-Format API starting on port {port}")
    logger.info("📱 Lightweight format: /download?url=ID&type=audio|video")
    logger.info("💻 Full-featured format: /api/v1/ endpoints")
    app.run(host='0.0.0.0', port=port, debug=False)
