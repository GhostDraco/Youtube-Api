import os
import json
import subprocess
import shutil
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import logging
import mimetypes

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

def ensure_audio_compatibility(filepath):
    """Ensure audio file is properly encoded for streaming"""
    try:
        # Check if file exists and has content
        if not os.path.exists(filepath):
            return False
        
        file_size = os.path.getsize(filepath)
        if file_size < 10000:  # Less than 10KB
            logger.warning(f"File too small: {filepath} ({file_size} bytes)")
            return False
        
        # Use ffmpeg to check and fix audio if needed
        temp_path = filepath + ".temp.mp3"
        
        # Re-encode audio to ensure compatibility
        cmd = [
            'ffmpeg',
            '-i', filepath,
            '-c:a', 'libmp3lame',
            '-q:a', '2',  # High quality
            '-ar', '44100',  # Standard sample rate
            '-ac', '2',  # Stereo
            '-id3v2_version', '3',
            '-write_id3v1', '1',
            '-y',  # Overwrite output
            temp_path
        ]
        
        logger.info(f"Ensuring audio compatibility: {filepath}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0 and os.path.exists(temp_path):
            # Replace original with fixed version
            os.remove(filepath)
            shutil.move(temp_path, filepath)
            logger.info(f"Audio file optimized: {filepath}")
            return True
        else:
            logger.error(f"Audio optimization failed: {result.stderr[:200]}")
            # Try simpler conversion
            return simple_audio_fix(filepath)
            
    except Exception as e:
        logger.error(f"Audio compatibility error: {str(e)}")
        return False

def simple_audio_fix(filepath):
    """Simple audio fix for compatibility"""
    try:
        temp_path = filepath + ".fixed.mp3"
        
        cmd = [
            'ffmpeg',
            '-i', filepath,
            '-c:a', 'copy',  # Just copy without re-encoding if possible
            '-y',
            temp_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        
        if result.returncode == 0 and os.path.exists(temp_path):
            os.remove(filepath)
            shutil.move(temp_path, filepath)
            return True
        
        return False
    except:
        return False

def download_media(video_id, media_type):
    """Download audio or video with proper encoding"""
    
    output_file = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
    output_path = os.path.join(DOWNLOAD_DIR, output_file)
    
    # Check if file already exists and is valid
    if os.path.exists(output_path):
        file_size = os.path.getsize(output_path)
        if file_size > 50000:  # At least 50KB
            logger.info(f"Using cached: {output_file} ({file_size/1024:.1f} KB)")
            
            # For audio files, ensure compatibility
            if media_type == 'audio':
                ensure_audio_compatibility(output_path)
            
            return True, output_path
        else:
            os.remove(output_path)  # Remove corrupted file
    
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Build optimized yt-dlp command for streaming compatibility
    cmd = ['yt-dlp', '--quiet', '--no-warnings']
    
    if os.path.exists(COOKIES_PATH):
        cmd.extend(['--cookies', COOKIES_PATH])
    
    # Common options for better compatibility
    cmd.extend([
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        '--retries', '5',
        '--fragment-retries', '5',
        '--no-check-certificates',
        '--force-ipv4',
        '--geo-bypass',
    ])
    
    if media_type == 'audio':
        # Optimized audio download for streaming
        cmd.extend([
            '-x',  # Extract audio
            '--audio-format', 'mp3',
            '--audio-quality', '0',  # Best quality
            '--postprocessor-args', '-ar 44100 -ac 2',  # Force standard format
            '--output', os.path.join(DOWNLOAD_DIR, f'{video_id}.%(ext)s'),
        ])
    else:  # video
        cmd.extend([
            '-f', 'best[ext=mp4]',  # Simple format for compatibility
            '--output', os.path.join(DOWNLOAD_DIR, f'{video_id}.%(ext)s'),
        ])
    
    cmd.append(youtube_url)
    
    try:
        logger.info(f"Downloading {media_type}: {video_id}")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            # Check if file was created
            if os.path.exists(output_path):
                # Post-process audio for streaming compatibility
                if media_type == 'audio':
                    ensure_audio_compatibility(output_path)
                
                return True, output_path
            
            # Try to find the file
            for filename in os.listdir(DOWNLOAD_DIR):
                if filename.startswith(video_id):
                    found_path = os.path.join(DOWNLOAD_DIR, filename)
                    if filename != output_file:
                        shutil.move(found_path, output_path)
                    
                    # Post-process audio
                    if media_type == 'audio':
                        ensure_audio_compatibility(output_path)
                    
                    return True, output_path
        
        return False, result.stderr[:500] if result.stderr else "Download failed"
        
    except Exception as e:
        return False, str(e)

# ============== API ENDPOINTS ==============

@app.route('/download', methods=['GET'])
def download():
    """Download endpoint"""
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
        
        success, result = download_media(video_id, media_type)
        
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
    """Stream file with proper headers for playback"""
    try:
        # Security check
        if '..' in filename or '/' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
        
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        # Check file
        file_size = os.path.getsize(filepath)
        if file_size < 10000:
            return jsonify({'error': 'File too small or corrupted'}), 500
        
        # Determine content type
        if filename.endswith('.mp3'):
            content_type = 'audio/mpeg'
            # Ensure audio is playable
            if not ensure_audio_compatibility(filepath):
                logger.warning(f"Audio compatibility check failed for {filename}")
        elif filename.endswith('.mp4'):
            content_type = 'video/mp4'
        else:
            content_type = 'application/octet-stream'
        
        # Get file stats
        stat = os.stat(filepath)
        last_modified = datetime.fromtimestamp(stat.st_mtime)
        
        # Check for Range header (for streaming)
        range_header = request.headers.get('Range', None)
        
        if range_header and filename.endswith('.mp3'):
            # Handle byte range requests for audio streaming
            return send_range_request(filepath, content_type, range_header, file_size)
        
        # Regular file serve
        response = send_file(
            filepath,
            mimetype=content_type,
            as_attachment=False,
            last_modified=last_modified
        )
        
        # Add headers for proper streaming
        response.headers['Accept-Ranges'] = 'bytes'
        response.headers['Content-Length'] = str(file_size)
        response.headers['Cache-Control'] = 'public, max-age=3600'
        
        # For audio files, add additional headers
        if filename.endswith('.mp3'):
            response.headers['Content-Type'] = 'audio/mpeg'
            response.headers['X-Content-Type-Options'] = 'nosniff'
        
        return response
        
    except Exception as e:
        logger.error(f"Stream error: {str(e)}")
        return jsonify({'error': str(e)}), 500

def send_range_request(filepath, content_type, range_header, file_size):
    """Handle byte range requests for streaming"""
    try:
        range_start, range_end = parse_range_header(range_header, file_size)
        
        if range_start >= file_size or range_end > file_size:
            return Response('Range Not Satisfiable', status=416)
        
        length = range_end - range_start
        
        with open(filepath, 'rb') as f:
            f.seek(range_start)
            data = f.read(length)
        
        response = Response(
            data,
            status=206,
            mimetype=content_type,
            direct_passthrough=True
        )
        
        response.headers['Content-Range'] = f'bytes {range_start}-{range_end-1}/{file_size}'
        response.headers['Content-Length'] = str(length)
        response.headers['Accept-Ranges'] = 'bytes'
        response.headers['Cache-Control'] = 'public, max-age=3600'
        
        return response
        
    except Exception as e:
        logger.error(f"Range request error: {str(e)}")
        return Response(str(e), status=500)

def parse_range_header(range_header, file_size):
    """Parse Range header"""
    try:
        range_type, range_spec = range_header.split('=')
        if range_type.strip() != 'bytes':
            return 0, file_size
        
        range_parts = range_spec.strip().split('-')
        range_start = int(range_parts[0]) if range_parts[0] else 0
        
        if len(range_parts) > 1 and range_parts[1]:
            range_end = int(range_parts[1]) + 1
        else:
            range_end = file_size
        
        return range_start, min(range_end, file_size)
    except:
        return 0, file_size

@app.route('/play/<video_id>', methods=['GET'])
def play_audio(video_id):
    """Direct play endpoint for audio"""
    try:
        filename = f"{video_id}.mp3"
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        if not os.path.exists(filepath):
            # Try to download it first
            success, result = download_media(video_id, 'audio')
            if not success:
                return jsonify({'error': 'Audio not available'}), 404
        
        # Ensure audio is playable
        ensure_audio_compatibility(filepath)
        
        # Return HTML player
        html = f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Play Audio - {video_id}</title>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 20px; }}
                .player {{ max-width: 500px; margin: 0 auto; text-align: center; }}
                audio {{ width: 100%; }}
                .info {{ margin-top: 20px; color: #666; }}
            </style>
        </head>
        <body>
            <div class="player">
                <h2>Audio Player</h2>
                <audio controls autoplay>
                    <source src="/stream/{filename}" type="audio/mpeg">
                    Your browser does not support the audio element.
                </audio>
                <div class="info">
                    <p>Video ID: {video_id}</p>
                    <p><a href="/stream/{filename}" download>Download MP3</a></p>
                </div>
            </div>
        </body>
        </html>
        '''
        
        return html
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube Audio/Video Streamer',
        'timestamp': datetime.now().isoformat(),
        'endpoints': {
            'download': '/download?url=VIDEO_ID&type=audio|video',
            'stream': '/stream/FILENAME',
            'play': '/play/VIDEO_ID',
            'health': '/health'
        }
    })

@app.route('/test/audio/<video_id>', methods=['GET'])
def test_audio(video_id):
    """Test audio download and streaming"""
    try:
        # Download audio
        success, result = download_media(video_id, 'audio')
        
        if success:
            file_size = os.path.getsize(result)
            
            # Test audio file with ffprobe
            cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', result]
            probe_result = subprocess.run(cmd, capture_output=True, text=True)
            
            audio_info = {}
            if probe_result.returncode == 0:
                try:
                    info = json.loads(probe_result.stdout)
                    if info.get('streams'):
                        for stream in info['streams']:
                            if stream.get('codec_type') == 'audio':
                                audio_info = {
                                    'codec': stream.get('codec_name'),
                                    'sample_rate': stream.get('sample_rate'),
                                    'channels': stream.get('channels'),
                                    'duration': stream.get('duration')
                                }
                                break
                except:
                    pass
            
            return jsonify({
                'status': 'success',
                'video_id': video_id,
                'file_path': result,
                'file_size_kb': round(file_size / 1024, 2),
                'stream_url': f"{request.host_url.rstrip('/')}/stream/{video_id}.mp3",
                'play_url': f"{request.host_url.rstrip('/')}/play/{video_id}",
                'audio_info': audio_info
            })
        else:
            return jsonify({
                'status': 'error',
                'video_id': video_id,
                'error': result
            }), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Background cleanup
def cleanup_old_files():
    """Clean old files periodically"""
    import threading
    
    def cleanup():
        while True:
            try:
                current_time = time.time()
                for filename in os.listdir(DOWNLOAD_DIR):
                    filepath = os.path.join(DOWNLOAD_DIR, filename)
                    if os.path.isfile(filepath):
                        file_age = current_time - os.path.getmtime(filepath)
                        if file_age > 86400:  # 24 hours
                            os.remove(filepath)
                            logger.info(f"Cleaned up old file: {filename}")
                time.sleep(3600)  # Run every hour
            except Exception as e:
                logger.error(f"Cleanup error: {str(e)}")
                time.sleep(300)
    
    thread = threading.Thread(target=cleanup, daemon=True)
    thread.start()
    logger.info("Cleanup thread started")

# Initialize
if __name__ == '__main__':
    cleanup_old_files()
    
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🎵 Audio Streaming API starting on port {port}")
    logger.info(f"📁 Downloads: {DOWNLOAD_DIR}")
    app.run(host='0.0.0.0', port=port, debug=False)
