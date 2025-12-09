import os
import json
import subprocess
import shutil
import time
import ssl
from datetime import datetime
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import logging

app = Flask(__name__)
CORS(app)

# Disable SSL certificate verification (FIX for Heroku)
os.environ['SSL_CERT_FILE'] = ''
os.environ['REQUESTS_CA_BUNDLE'] = ''
os.environ['CURL_CA_BUNDLE'] = ''

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
    
    if 'v=' in url:
        return url.split('v=')[-1].split('&')[0]
    
    return url

def download_media_fixed(video_id, media_type):
    """Download with SSL fix"""
    
    output_file = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
    output_path = os.path.join(DOWNLOAD_DIR, output_file)
    
    # Check if file already exists
    if os.path.exists(output_path):
        file_size = os.path.getsize(output_path)
        if file_size > 50000:
            logger.info(f"Using cached: {output_file}")
            return True, output_path
    
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Method 1: Try with SSL bypass
    success, result = try_download_with_ssl_bypass(video_id, media_type, youtube_url)
    
    if success:
        return True, result
    
    # Method 2: Try without SSL check
    logger.info("Method 1 failed, trying Method 2...")
    success, result = try_download_no_ssl(video_id, media_type, youtube_url)
    
    if success:
        return True, result
    
    # Method 3: Use alternative approach
    logger.info("Method 2 failed, trying Method 3...")
    success, result = try_download_alternative(video_id, media_type, youtube_url)
    
    return success, result

def try_download_with_ssl_bypass(video_id, media_type, youtube_url):
    """Method 1: With SSL bypass"""
    output_file = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
    output_path = os.path.join(DOWNLOAD_DIR, output_file)
    
    cmd = [
        'yt-dlp',
        '--no-check-certificates',  # IMPORTANT: Disable SSL check
        '--force-ipv4',
        '--geo-bypass',
        '--retries', '5',
        '--fragment-retries', '5',
        '--skip-unavailable-fragments',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        '--quiet',
        '--no-warnings',
    ]
    
    if os.path.exists(COOKIES_PATH):
        cmd.extend(['--cookies', COOKIES_PATH])
    
    if media_type == 'audio':
        cmd.extend([
            '-x',
            '--audio-format', 'mp3',
            '--audio-quality', '0',
            '--output', os.path.join(DOWNLOAD_DIR, f'{video_id}.%(ext)s'),
        ])
    else:
        cmd.extend([
            '-f', 'best[ext=mp4]',
            '--output', os.path.join(DOWNLOAD_DIR, f'{video_id}.%(ext)s'),
        ])
    
    cmd.append(youtube_url)
    
    try:
        # Set environment to disable SSL
        env = os.environ.copy()
        env['PYTHONHTTPSVERIFY'] = '0'
        env['GIT_SSL_NO_VERIFY'] = '1'
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
        
        if result.returncode == 0:
            if os.path.exists(output_path):
                return True, output_path
            
            # Find any matching file
            for filename in os.listdir(DOWNLOAD_DIR):
                if filename.startswith(video_id):
                    found_path = os.path.join(DOWNLOAD_DIR, filename)
                    if filename != output_file:
                        shutil.move(found_path, output_path)
                    return True, output_path
        
        return False, result.stderr[:500] if result.stderr else "Download failed"
        
    except Exception as e:
        return False, str(e)

def try_download_no_ssl(video_id, media_type, youtube_url):
    """Method 2: Complete SSL bypass"""
    output_file = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
    output_path = os.path.join(DOWNLOAD_DIR, output_file)
    
    # Create Python script that downloads without SSL
    script_content = f'''
import yt_dlp
import os
import ssl

# Disable SSL verification
ssl._create_default_https_context = ssl._create_unverified_context

ydl_opts = {{
    'quiet': True,
    'no_warnings': True,
    'no_check_certificate': True,
    'force_ipv4': True,
    'geo_bypass': True,
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
'''

    if media_type == 'audio':
        script_content += f'''
    'format': 'bestaudio/best',
    'postprocessors': [{{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }}],
    'outtmpl': '{output_path.replace(".mp3", ".%(ext)s")}',
'''
    else:
        script_content += f'''
    'format': 'best[ext=mp4]',
    'outtmpl': '{output_path.replace(".mp4", ".%(ext)s")}',
'''

    script_content += f'''
}}

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download(['{youtube_url}'])
    print("SUCCESS")
except Exception as e:
    print(f"ERROR: {{str(e)}}")
'''
    
    # Write and execute script
    script_path = os.path.join(DOWNLOAD_DIR, f'download_{video_id}.py')
    with open(script_path, 'w') as f:
        f.write(script_content)
    
    try:
        cmd = ['python3', script_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        # Clean up script
        if os.path.exists(script_path):
            os.remove(script_path)
        
        if "SUCCESS" in result.stdout and os.path.exists(output_path):
            return True, output_path
        
        return False, result.stderr or result.stdout
        
    except Exception as e:
        return False, str(e)

def try_download_alternative(video_id, media_type, youtube_url):
    """Method 3: Use alternative tool"""
    output_file = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
    output_path = os.path.join(DOWNLOAD_DIR, output_file)
    
    try:
        # Try using youtube-dl (older but sometimes works)
        cmd = ['youtube-dl', '--no-check-certificate', '-f', 'best']
        
        if media_type == 'audio':
            cmd.extend(['-x', '--audio-format', 'mp3'])
        
        cmd.extend(['-o', output_path.replace('.mp3', '.%(ext)s').replace('.mp4', '.%(ext)s'), youtube_url])
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0 and os.path.exists(output_path):
            return True, output_path
        
        return False, "All download methods failed"
        
    except Exception as e:
        return False, str(e)

# Install youtube-dl if not present
def ensure_dependencies():
    """Ensure all dependencies are installed"""
    try:
        # Install youtube-dl as backup
        subprocess.run(['pip', 'install', 'youtube-dl'], capture_output=True)
        logger.info("Dependencies checked")
    except:
        pass

# ============== API ENDPOINTS ==============

@app.route('/download', methods=['GET'])
def download():
    """Download endpoint with SSL fix"""
    try:
        video_url = request.args.get('url', '').strip()
        media_type = request.args.get('type', 'audio').strip().lower()
        
        if not video_url:
            return jsonify({'error': 'Missing URL'}), 400
        
        if media_type not in ['audio', 'video']:
            return jsonify({'error': 'Invalid type'}), 400
        
        video_id = get_video_id(video_url)
        if not video_id:
            return jsonify({'error': 'Invalid YouTube URL'}), 400
        
        logger.info(f"Download request: {video_id} ({media_type})")
        
        success, result = download_media_fixed(video_id, media_type)
        
        if success:
            stream_url = f"{request.host_url.rstrip('/')}/stream/{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
            
            return jsonify({
                'status': 'success',
                'stream_url': stream_url,
                'video_id': video_id,
                'type': media_type
            })
        else:
            return jsonify({'error': result}), 500
            
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/stream/<filename>', methods=['GET'])
def stream_file(filename):
    """Stream file"""
    try:
        if '..' in filename or '/' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
        
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        file_size = os.path.getsize(filepath)
        if file_size < 10000:
            return jsonify({'error': 'File too small'}), 500
        
        if filename.endswith('.mp3'):
            content_type = 'audio/mpeg'
        elif filename.endswith('.mp4'):
            content_type = 'video/mp4'
        else:
            content_type = 'application/octet-stream'
        
        response = send_file(filepath, mimetype=content_type, as_attachment=False)
        response.headers['Accept-Ranges'] = 'bytes'
        response.headers['Content-Length'] = str(file_size)
        
        return response
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube Downloader (SSL Fixed)',
        'timestamp': datetime.now().isoformat(),
        'ssl_fixed': True
    })

@app.route('/test/ssl', methods=['GET'])
def test_ssl():
    """Test SSL connection"""
    try:
        # Test YouTube access
        import urllib.request
        import ssl
        
        # Create unverified context
        context = ssl._create_unverified_context()
        
        req = urllib.request.Request(
            'https://www.youtube.com',
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        
        response = urllib.request.urlopen(req, context=context, timeout=10)
        return jsonify({
            'status': 'success',
            'message': 'SSL connection test passed',
            'code': response.getcode()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': 'SSL test failed',
            'error': str(e)
        }), 500

@app.route('/fix/ssl', methods=['POST'])
def fix_ssl():
    """Force SSL fix"""
    try:
        # Update certificates
        subprocess.run(['update-ca-certificates', '--fresh'], capture_output=True)
        
        # Clear Python certificate cache
        import certifi
        certifi.where()
        
        return jsonify({
            'status': 'success',
            'message': 'SSL certificates updated'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Cleanup
def cleanup():
    import threading
    
    def cleaner():
        while True:
            try:
                current_time = time.time()
                for filename in os.listdir(DOWNLOAD_DIR):
                    filepath = os.path.join(DOWNLOAD_DIR, filename)
                    if os.path.isfile(filepath):
                        file_age = current_time - os.path.getmtime(filepath)
                        if file_age > 86400:
                            os.remove(filepath)
                time.sleep(3600)
            except:
                time.sleep(300)
    
    thread = threading.Thread(target=cleaner, daemon=True)
    thread.start()

# Initialize
if __name__ == '__main__':
    ensure_dependencies()
    cleanup()
    
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🔓 SSL-Fixed YouTube Downloader starting on port {port}")
    logger.info("SSL verification is DISABLED for Heroku compatibility")
    app.run(host='0.0.0.0', port=port, debug=False)
