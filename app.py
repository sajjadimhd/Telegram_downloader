from flask import Flask, request, jsonify
from pyrogram import Client
import os
import uuid
import asyncio
import time
import logging
import base64

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Your Telegram API credentials
api_id = int(os.getenv('TELEGRAM_API_ID', '25954266'))
api_hash = os.getenv('TELEGRAM_API_HASH', 'bf7e335fc5972530524fcaf427185157')

# Phone is optional if session file exists
phone = os.getenv('TELEGRAM_PHONE', None)

# ترکیب کردن قسمت‌های session از environment variables
session_parts = []
i = 1
while True:
    part = os.getenv(f'SESSION_PART_{i}', None)
    if part:
        session_parts.append(part)
        logger.info(f"Found SESSION_PART_{i}")
        i += 1
    else:
        break

if session_parts:
    try:
        session_base64 = ''.join(session_parts)
        session_data = base64.b64decode(session_base64)
        
        # ذخیره در /tmp با دسترسی کامل
        session_path = '/tmp/my_account.session'
        with open(session_path, 'wb') as f:
            f.write(session_data)
        
        # تنظیم permission
        os.chmod(session_path, 0o600)
        
        logger.info(f"✅ Session loaded from {len(session_parts)} parts")
        logger.info(f"Session saved to: {session_path}")
        logger.info(f"Session file size: {len(session_data)} bytes")
        
        # چک کردن فایل
        if os.path.exists(session_path):
            logger.info(f"✅ Session file verified at {session_path}")
        else:
            logger.error(f"❌ Session file not found after creation!")
            
    except Exception as e:
        logger.error(f"❌ Failed to load session: {e}")
        import traceback
        logger.error(traceback.format_exc())
else:
    logger.warning("⚠️ No SESSION_PART variables found")

# Initialize Pyrogram client
# If session file exists, phone is not needed
telegram_client = Client(
    "my_account",
    api_id=api_id,
    api_hash=api_hash,
    phone_number=phone  # Can be None if session exists
)

def cleanup_file(file_path, delay=300):
    def delete_later():
        time.sleep(delay)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up: {file_path}")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
    
    import threading
    threading.Thread(target=delete_later, daemon=True).start()

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "service": "Telegram Video Downloader",
        "version": "2.0",
        "api_id": api_id,
        "session_file": "exists" if os.path.exists("my_account.session") else "missing",
        "phone_configured": bool(phone),
        "endpoints": {
            "/download": "POST - Download video from Telegram",
            "/health": "GET - Health check",
            "/cleanup": "POST - Manual cleanup",
            "/test": "GET - Test connection"
        }
    })

@app.route('/download', methods=['POST'])
def download_video():
    start_time = time.time()
    
    try:
        data = request.json
        file_id = data.get('file_id')
        auto_cleanup = data.get('auto_cleanup', True)
        cleanup_delay = data.get('cleanup_delay', 300)
        
        if not file_id:
            return jsonify({"error": "file_id is required"}), 400
        
        logger.info(f"Download request for file_id: {file_id[:20]}...")
        
        unique_id = str(uuid.uuid4())[:8]
        filename = f"/tmp/telegram_video_{unique_id}.mp4"
        
        async def download():
            async with telegram_client:
                logger.info(f"Starting download to {filename}")
                file_path = await telegram_client.download_media(
                    file_id,
                    file_name=filename
                )
                return file_path
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        file_path = loop.run_until_complete(download())
        loop.close()
        
        if not file_path or not os.path.exists(file_path):
            logger.error("Download failed - file not found")
            return jsonify({"error": "Download failed"}), 500
        
        file_size = os.path.getsize(file_path)
        download_time = time.time() - start_time
        
        logger.info(f"Download successful: {file_size} bytes in {download_time:.2f}s")
        
        if auto_cleanup:
            cleanup_file(file_path, cleanup_delay)
            logger.info(f"Scheduled cleanup in {cleanup_delay}s")
        
        return jsonify({
            "success": True,
            "file_path": file_path,
            "file_size": file_size,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "download_time_seconds": round(download_time, 2),
            "auto_cleanup": auto_cleanup,
            "cleanup_in_seconds": cleanup_delay if auto_cleanup else None
        })
        
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        return jsonify({
            "error": str(e),
            "success": False
        }), 500

@app.route('/health', methods=['GET'])
def health():
    try:
        test_file = "/tmp/health_check.txt"
        with open(test_file, 'w') as f:
            f.write("test")
        os.remove(test_file)
        
        return jsonify({
            "status": "healthy",
            "storage": "ok",
            "session_file": os.path.exists("my_account.session"),
            "timestamp": time.time(),
            "api_id": api_id
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500

@app.route('/test', methods=['GET'])
def test():
    try:
        async def test_connection():
            async with telegram_client:
                me = await telegram_client.get_me()
                return {
                    "connected": True,
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "username": me.username,
                    "session_valid": True
                }
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(test_connection())
        loop.close()
        
        return jsonify({
            "success": True,
            "telegram_connection": result
        })
    except Exception as e:
        logger.error(f"Test connection failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "hint": "Session file might be missing or expired"
        }), 500

@app.route('/cleanup', methods=['POST'])
def manual_cleanup():
    try:
        import glob
        files = glob.glob("/tmp/telegram_video_*.mp4")
        deleted = []
        
        for file_path in files:
            try:
                file_size = os.path.getsize(file_path)
                os.remove(file_path)
                deleted.append({
                    "path": file_path,
                    "size_mb": round(file_size / (1024 * 1024), 2)
                })
                logger.info(f"Cleaned up: {file_path}")
            except:
                pass
        
        return jsonify({
            "success": True,
            "deleted_count": len(deleted),
            "deleted_files": deleted,
            "total_freed_mb": round(sum(f['size_mb'] for f in deleted), 2)
        })
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
        return jsonify({
            "error": str(e),
            "success": False
        }), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({
        "error": "Endpoint not found",
        "available_endpoints": ["/", "/download", "/health", "/test", "/cleanup"]
    }), 404

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal error: {e}")
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting server on port {port}")
    logger.info(f"API ID: {api_id}")
    logger.info(f"Phone configured: {bool(phone)}")
    logger.info(f"Session file exists: {os.path.exists('my_account.session')}")

    app.run(host='0.0.0.0', port=port)

