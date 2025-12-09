import os
import json
import subprocess
import tempfile
import time
from datetime import datetime
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import logging

app = Flask(__name__)
CORS(app)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Disable SSL verification
os.environ['PYTHONHTTPSVERIFY'] = '0'

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

def get_stream_url(video_id, media_type):
    """Get direct stream URL from YouTube"""
    try:
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Get stream URL using yt-dlp
        cmd = [
            'yt-dlp',
            '--no-check-certificates',
            '--force-ipv4',
            '--geo-bypass',
            '--get-url',
            '--format', 'bestaudio/best' if media_type == 'audio' else 'best[ext=mp4]',
            '--quiet',
            '--no-warnings',
            youtube_url
        ]
        
        logger.info(f"Getting stream URL for {video_id} ({media_type})")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            stream_url = result.stdout.strip()
            if stream_url and stream_url.startswith('http'):
                logger.info(f"Got stream URL: {stream_url[:100]}...")
                return True, stream_url
        
        return False, result.stderr or "Failed to get stream URL"
        
    except Exception as e:
        return False, str(e)

def stream_youtube_data(video_id, media_type):
    """Stream YouTube data directly to client"""
    try:
        # Get stream URL
        success, stream_url = get_stream_url(video_id, media_type)
        
        if not success:
            yield json.dumps({'error': stream_url}).encode()
            return
        
        # Use ffmpeg to stream directly
        ffmpeg_cmd = [
            'ffmpeg',
            '-i', stream_url,
            '-c', 'copy',
            '-f', 'mp3' if media_type == 'audio' else 'mp4',
            'pipe:1'
        ]
        
        # Start ffmpeg process
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10**8
        )
        
        # Stream data in chunks
        chunk_size = 1024 * 1024  # 1MB chunks
        
        try:
            while True:
                chunk = process.stdout.read(chunk_size)
                if not chunk:
                    break
                yield chunk
                
                # Check if process is still alive
                if process.poll() is not None:
                    break
                    
        finally:
            # Cleanup
            try:
                process.terminate()
                process.wait(timeout=5)
            except:
                process.kill()
                
    except Exception as e:
        logger.error(f"Stream error: {str(e)}")
        yield json.dumps({'error': str(e)}).encode()

# ============== STREAM ENDPOINTS ==============

@app.route('/stream', methods=['GET'])
def stream_direct():
    """
    Stream YouTube video/audio directly without saving
    
    Parameters:
        url: YouTube URL or Video ID
        type: "audio" or "video"
    
    Returns:
        Direct stream of the media
    """
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
        
        logger.info(f"Direct stream request: {video_id} ({media_type})")
        
        # Set appropriate headers
        headers = {
            'Content-Type': 'audio/mpeg' if media_type == 'audio' else 'video/mp4',
            'Transfer-Encoding': 'chunked',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
            'Accept-Ranges': 'bytes'
        }
        
        # Add Content-Disposition for download
        if request.args.get('download'):
            filename = f"{video_id}.{'mp3' if media_type == 'audio' else 'mp4'}"
            headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        # Stream the response
        return Response(
            stream_with_context(stream_youtube_data(video_id, media_type)),
            headers=headers,
            direct_passthrough=True
        )
        
    except Exception as e:
        logger.error(f"Stream endpoint error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/stream/audio', methods=['GET'])
def stream_audio():
    """Stream audio only"""
    video_url = request.args.get('url', '').strip()
    if not video_url:
        return jsonify({'error': 'Missing URL'}), 400
    
    return stream_direct()

@app.route('/stream/video', methods=['GET'])
def stream_video():
    """Stream video only"""
    video_url = request.args.get('url', '').strip()
    if not video_url:
        return jsonify({'error': 'Missing URL'}), 400
    
    return stream_direct()

@app.route('/stream/mp3', methods=['GET'])
def stream_mp3():
    """Stream as MP3"""
    video_url = request.args.get('url', '').strip()
    if not video_url:
        return jsonify({'error': 'Missing URL'}), 400
    
    video_id = get_video_id(video_url)
    if not video_id:
        return jsonify({'error': 'Invalid URL'}), 400
    
    # Convert to MP3 while streaming
    try:
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Get audio stream URL
        cmd = [
            'yt-dlp',
            '--no-check-certificates',
            '--get-url',
            '--format', 'bestaudio',
            '--quiet',
            youtube_url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            return jsonify({'error': 'Failed to get audio stream'}), 500
        
        stream_url = result.stdout.strip()
        
        # Stream with ffmpeg conversion to MP3
        def generate():
            ffmpeg_cmd = [
                'ffmpeg',
                '-i', stream_url,
                '-c:a', 'libmp3lame',
                '-q:a', '2',
                '-f', 'mp3',
                'pipe:1'
            ]
            
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            try:
                while True:
                    chunk = process.stdout.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    yield chunk
            finally:
                process.terminate()
        
        headers = {
            'Content-Type': 'audio/mpeg',
            'Content-Disposition': f'attachment; filename="{video_id}.mp3"',
            'Cache-Control': 'no-cache'
        }
        
        return Response(
            stream_with_context(generate()),
            headers=headers
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/stream/mp4', methods=['GET'])
def stream_mp4():
    """Stream as MP4"""
    video_url = request.args.get('url', '').strip()
    if not video_url:
        return jsonify({'error': 'Missing URL'}), 400
    
    video_id = get_video_id(video_url)
    if not video_id:
        return jsonify({'error': 'Invalid URL'}), 400
    
    # Stream as MP4
    try:
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Get video stream URL
        cmd = [
            'yt-dlp',
            '--no-check-certificates',
            '--get-url',
            '--format', 'best[ext=mp4]',
            '--quiet',
            youtube_url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            return jsonify({'error': 'Failed to get video stream'}), 500
        
        stream_url = result.stdout.strip()
        
        # Stream directly
        def generate():
            ffmpeg_cmd = [
                'ffmpeg',
                '-i', stream_url,
                '-c', 'copy',
                '-f', 'mp4',
                'pipe:1'
            ]
            
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            try:
                while True:
                    chunk = process.stdout.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                process.terminate()
        
        headers = {
            'Content-Type': 'video/mp4',
            'Content-Disposition': f'attachment; filename="{video_id}.mp4"',
            'Cache-Control': 'no-cache'
        }
        
        return Response(
            stream_with_context(generate()),
            headers=headers
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============== COMPATIBILITY ENDPOINTS ==============

@app.route('/download', methods=['GET'])
def download_compat():
    """
    Compatibility endpoint - returns stream URL
    (For clients expecting download endpoint)
    """
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
        
        # Return stream URL
        stream_url = f"{request.host_url.rstrip('/')}/stream?url={video_id}&type={media_type}"
        
        # Check client for response format
        user_agent = request.headers.get('User-Agent', '').lower()
        
        if 'python' in user_agent or 'requests' in user_agent:
            return jsonify({
                'status': 'success',
                'stream_url': stream_url,
                'video_id': video_id,
                'type': media_type
            })
        else:
            return jsonify({
                'link': stream_url,
                'stream_url': stream_url
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============== UTILITY ENDPOINTS ==============

@app.route('/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube Stream-Only API',
        'timestamp': datetime.now().isoformat(),
        'mode': 'stream-only',
        'endpoints': {
            'stream': '/stream?url=VIDEO_ID&type=audio|video',
            'stream_audio': '/stream/audio?url=VIDEO_ID',
            'stream_video': '/stream/video?url=VIDEO_ID',
            'stream_mp3': '/stream/mp3?url=VIDEO_ID',
            'stream_mp4': '/stream/mp4?url=VIDEO_ID',
            'download_compat': '/download?url=VIDEO_ID&type=audio|video'
        }
    })

@app.route('/info/<video_id>', methods=['GET'])
def video_info(video_id):
    """Get video information"""
    try:
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        
        cmd = [
            'yt-dlp',
            '--no-check-certificates',
            '--dump-json',
            '--quiet',
            youtube_url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            info = json.loads(result.stdout)
            return jsonify({
                'status': 'success',
                'video_id': video_id,
                'title': info.get('title'),
                'duration': info.get('duration_string'),
                'thumbnail': info.get('thumbnail'),
                'formats': len(info.get('formats', [])),
                'stream_urls': {
                    'audio': f"{request.host_url.rstrip('/')}/stream/audio?url={video_id}",
                    'video': f"{request.host_url.rstrip('/')}/stream/video?url={video_id}",
                    'mp3': f"{request.host_url.rstrip('/')}/stream/mp3?url={video_id}",
                    'mp4': f"{request.host_url.rstrip('/')}/stream/mp4?url={video_id}"
                }
            })
        else:
            return jsonify({'error': 'Failed to get video info'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============== TEST ENDPOINTS ==============

@app.route('/test/stream', methods=['GET'])
def test_stream():
    """Test stream endpoint"""
    video_id = request.args.get('id', 'YSWMbwQuWAY')
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Stream Test - {video_id}</title>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 20px; }}
            .player {{ max-width: 800px; margin: 0 auto; }}
            audio, video {{ width: 100%; }}
            .links {{ margin-top: 20px; }}
            .link {{ margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="player">
            <h2>Stream Test: {video_id}</h2>
            
            <div class="links">
                <div class="link">
                    <h3>Audio Stream</h3>
                    <audio controls>
                        <source src="/stream?url={video_id}&type=audio" type="audio/mpeg">
                    </audio>
                    <p><a href="/stream?url={video_id}&type=audio" target="_blank">Direct Link</a></p>
                </div>
                
                <div class="link">
                    <h3>Video Stream</h3>
                    <video controls width="100%">
                        <source src="/stream?url={video_id}&type=video" type="video/mp4">
                    </video>
                    <p><a href="/stream?url={video_id}&type=video" target="_blank">Direct Link</a></p>
                </div>
            </div>
        </div>
    </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🎵 Stream-Only YouTube API starting on port {port}")
    logger.info("📡 Streaming directly from YouTube (no files saved)")
    app.run(host='0.0.0.0', port=port, debug=False)
