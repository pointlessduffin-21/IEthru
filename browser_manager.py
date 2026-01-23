"""Browser session management using Playwright with thread-safe design."""

import io
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any, Callable
from urllib.parse import urlparse

from PIL import Image
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

import config


@dataclass
class BrowserSession:
    """Represents a single browser session with isolated context."""
    session_id: str
    context: BrowserContext
    page: Page
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    viewport_width: int = config.DEFAULT_VIEWPORT_WIDTH
    viewport_height: int = config.DEFAULT_VIEWPORT_HEIGHT
    quality: int = config.DEFAULT_QUALITY
    fps: int = config.DEFAULT_FPS
    last_rtt_ms: float = 0.0
    downloads: list[dict] = field(default_factory=list)
    downloading_count: int = 0
    
    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = datetime.now()
    
    def is_expired(self) -> bool:
        """Check if session has expired."""
        elapsed = (datetime.now() - self.last_activity).total_seconds()
        return elapsed > config.SESSION_TIMEOUT_SECONDS


class BrowserManager:
    """
    Thread-safe browser session manager.
    
    All Playwright operations run on a dedicated thread to avoid
    greenlet switching issues with Flask's multi-threaded server.
    """
    
    def __init__(self):
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = threading.Lock()
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._command_queue: queue.Queue = queue.Queue()
        self._result_queues: dict[str, queue.Queue] = {}
        self._browser_thread: Optional[threading.Thread] = None
        self._running = False
        
        # Create downloads directory
        import os
        os.makedirs('downloads', exist_ok=True)
    
    def start(self):
        """Start the browser manager and its dedicated thread."""
        self._running = True
        self._browser_thread = threading.Thread(target=self._browser_loop, daemon=True)
        self._browser_thread.start()
        
        # Wait for browser to initialize
        result = self._execute_on_browser_thread(self._init_browser)
        if result.get('error'):
            raise RuntimeError(f"Failed to start browser: {result['error']}")
        print("Browser manager started with Chromium")
    
    def stop(self):
        """Shutdown browser and cleanup."""
        self._running = False
        self._command_queue.put(('stop', None, None))
        if self._browser_thread:
            self._browser_thread.join(timeout=5)
        print("Browser manager stopped")
    
    def _browser_loop(self):
        """Main loop running on the dedicated browser thread."""
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=config.BROWSER_ARGS,
            downloads_path='downloads'
        )
        
        while self._running:
            try:
                item = self._command_queue.get(timeout=1)
                if item[0] == 'stop':
                    break
                
                command_id, func, args, kwargs = item
                try:
                    result = func(*args, **kwargs)
                    self._result_queues[command_id].put({'result': result})
                except Exception as e:
                    self._result_queues[command_id].put({'error': str(e)})
            except queue.Empty:
                # Check for expired sessions periodically
                self._cleanup_expired_sessions_unsafe()
        
        # Cleanup
        for session_id in list(self._sessions.keys()):
            self._close_session_unsafe(session_id)
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
    
    def _execute_on_browser_thread(self, func: Callable, *args, **kwargs) -> dict:
        """Execute a function on the browser thread and wait for result."""
        command_id = str(uuid.uuid4())
        result_queue = queue.Queue()
        self._result_queues[command_id] = result_queue
        
        self._command_queue.put((command_id, func, args, kwargs))
        
        try:
            result = result_queue.get(timeout=60)  # 60 second timeout
        finally:
            del self._result_queues[command_id]
        
        return result
    
    def _init_browser(self):
        """Initialize browser (called on browser thread)."""
        return True
    
    def _cleanup_expired_sessions_unsafe(self):
        """Remove expired sessions (called on browser thread)."""
        expired = [
            sid for sid, session in self._sessions.items()
            if session.is_expired()
        ]
        for session_id in expired:
            print(f"Cleaning up expired session: {session_id}")
            self._close_session_unsafe(session_id)
    
    def _create_session_unsafe(
        self,
        viewport_width: int = config.DEFAULT_VIEWPORT_WIDTH,
        viewport_height: int = config.DEFAULT_VIEWPORT_HEIGHT
    ) -> str:
        """Create a new browser session (called on browser thread)."""
        if len(self._sessions) >= config.MAX_SESSIONS:
            # Remove oldest session
            oldest_id = min(
                self._sessions.keys(),
                key=lambda k: self._sessions[k].last_activity
            )
            self._close_session_unsafe(oldest_id)
        
        session_id = str(uuid.uuid4())[:8]
        
        # Clamp viewport dimensions
        viewport_width = max(config.MIN_VIEWPORT_WIDTH, 
                           min(viewport_width, config.MAX_VIEWPORT_WIDTH))
        viewport_height = max(config.MIN_VIEWPORT_HEIGHT,
                            min(viewport_height, config.MAX_VIEWPORT_HEIGHT))
        
        context = self._browser.new_context(
            viewport={'width': viewport_width, 'height': viewport_height},
            user_agent=config.USER_AGENT,
            ignore_https_errors=True,
            accept_downloads=True
        )
        page = context.new_page()
        
        # Block some resource types for faster loading
        page.route("**/*.{woff,woff2,ttf,otf}", lambda route: route.abort())
        
        session = BrowserSession(
            session_id=session_id,
            context=context,
            page=page,
            viewport_width=viewport_width,
            viewport_height=viewport_height
        )
        
        # Handle Downloads
        def handle_download(download):
            import os
            
            # Generate ID and info immediately
            unique_id = uuid.uuid4().hex[:8]
            readable_time = datetime.now().strftime("%H:%M:%S")
            filename = download.suggested_filename or "unknown_file"
            stored_filename = f"{unique_id}_{filename}"
            
            # Initial record
            download_info = {
                'id': unique_id,
                'filename': filename,
                'stored_filename': stored_filename,
                'status': 'downloading',
                'path': "", # Not set yet
                'url': download.url,
                'time': time.time(),
                'readable_time': readable_time
            }
            
            # Determine path
            download_dir = os.path.join('downloads', session_id)
            try:
                os.makedirs(download_dir, exist_ok=True)
                download_info['path'] = os.path.join(download_dir, stored_filename)
                
                # Add to list and increment counter
                session.downloads.insert(0, download_info)
                session.downloading_count += 1
                print(f"Download started: {filename} -> {stored_filename}")

                try:
                    # Blocking save
                    download.save_as(download_info['path'])
                    
                    # Success
                    download_info['status'] = 'ready'
                    print(f"Download completed: {download_info['path']}")
                    
                except Exception as e:
                    # Failure during save
                    download_info['status'] = 'failed'
                    download_info['error'] = str(e)
                    print(f"Download failed during save: {e}")
                    
            except Exception as e:
                # Setup failure
                print(f"Download setup failed: {e}")
                # If we inserted it, mark failed
                if download_info in session.downloads:
                    download_info['status'] = 'failed'
                    download_info['error'] = str(e)
            
            finally:
                # Decrement downloading counter
                if session.downloading_count > 0:
                    session.downloading_count -= 1

        page.on("download", handle_download)
        
        self._sessions[session_id] = session
        print(f"Created session: {session_id}")
        return session_id
    
    def _close_session_unsafe(self, session_id: str) -> bool:
        """Close session (called on browser thread)."""
        session = self._sessions.pop(session_id, None)
        if session:
            try:
                session.context.close()
            except Exception as e:
                print(f"Error closing session {session_id}: {e}")
            
            # Clean up downloads
            import shutil
            import os
            download_dir = os.path.join('downloads', session_id)
            if os.path.exists(download_dir):
                try:
                    shutil.rmtree(download_dir)
                    print(f"Cleaned up downloads for session: {session_id}")
                except Exception as e:
                    print(f"Error cleaning up downloads for {session_id}: {e}")
            
            print(f"Closed session: {session_id}")
            return True
        return False
    
    def create_session(
        self,
        viewport_width: int = config.DEFAULT_VIEWPORT_WIDTH,
        viewport_height: int = config.DEFAULT_VIEWPORT_HEIGHT
    ) -> str:
        """Create a new browser session with isolated context."""
        result = self._execute_on_browser_thread(
            self._create_session_unsafe, viewport_width, viewport_height
        )
        if result.get('error'):
            raise RuntimeError(result['error'])
        return result['result']
    
    def get_session(self, session_id: str) -> Optional[BrowserSession]:
        """Get session by ID (thread-safe read)."""
        session = self._sessions.get(session_id)
        if session:
            session.touch()
        return session
    
    def close_session(self, session_id: str) -> bool:
        """Close a session."""
        result = self._execute_on_browser_thread(
            self._close_session_unsafe, session_id
        )
        return result.get('result', False)
    
    def validate_url(self, url: str) -> tuple[bool, str]:
        """Validate and sanitize URL."""
        if not url:
            return False, "URL is required"
        
        # Add scheme if missing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        try:
            parsed = urlparse(url)
        except Exception:
            return False, "Invalid URL format"
        
        if parsed.scheme not in config.ALLOWED_SCHEMES:
            return False, f"Scheme must be one of: {config.ALLOWED_SCHEMES}"
        
        if parsed.hostname in config.BLOCKED_HOSTS:
            return False, "This host is not allowed"
        
        return True, url
    
    def _navigate_unsafe(self, session_id: str, url: str, timeout: int) -> tuple[bool, str]:
        """Navigate to URL (called on browser thread)."""
        session = self._sessions.get(session_id)
        if not session:
            return False, "Session not found"
        session.touch()
        
        try:
            session.page.goto(url, timeout=timeout, wait_until='domcontentloaded')
            return True, session.page.url
        except Exception as e:
            return False, str(e)
    
    def navigate(self, session_id: str, url: str, timeout: int = 30000) -> tuple[bool, str]:
        """Navigate to URL."""
        valid, result = self.validate_url(url)
        if not valid:
            return False, result
        
        nav_result = self._execute_on_browser_thread(
            self._navigate_unsafe, session_id, result, timeout
        )
        if nav_result.get('error'):
            return False, nav_result['error']
        return nav_result['result']
    
    def _go_back_unsafe(self, session_id: str) -> bool:
        """Navigate back (called on browser thread)."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.touch()
        try:
            session.page.go_back(timeout=10000)
            return True
        except Exception:
            return False
    
    def go_back(self, session_id: str) -> bool:
        """Navigate back."""
        result = self._execute_on_browser_thread(self._go_back_unsafe, session_id)
        return result.get('result', False)
    
    def _go_forward_unsafe(self, session_id: str) -> bool:
        """Navigate forward (called on browser thread)."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.touch()
        try:
            session.page.go_forward(timeout=10000)
            return True
        except Exception:
            return False
    
    def go_forward(self, session_id: str) -> bool:
        """Navigate forward."""
        result = self._execute_on_browser_thread(self._go_forward_unsafe, session_id)
        return result.get('result', False)
    
    def _reload_unsafe(self, session_id: str) -> bool:
        """Reload page (called on browser thread)."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.touch()
        try:
            session.page.reload(timeout=30000)
            return True
        except Exception:
            return False
    
    def reload(self, session_id: str) -> bool:
        """Reload page."""
        result = self._execute_on_browser_thread(self._reload_unsafe, session_id)
        return result.get('result', False)
    
    def _stop_loading_unsafe(self, session_id: str) -> bool:
        """Stop page loading (called on browser thread)."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.touch()
        try:
            session.page.evaluate("window.stop()")
            return True
        except Exception:
            return False
    
    def stop_loading(self, session_id: str) -> bool:
        """Stop page loading."""
        result = self._execute_on_browser_thread(self._stop_loading_unsafe, session_id)
        return result.get('result', False)
    
    def _screenshot_unsafe(self, session_id: str, quality: Optional[int]) -> Optional[bytes]:
        """Take screenshot (called on browser thread)."""
        session = self._sessions.get(session_id)
        if not session:
            return None
        session.touch()
        
        quality = quality or session.quality
        quality = max(config.MIN_QUALITY, min(quality, config.MAX_QUALITY))
        
        try:
            # Take PNG screenshot (Playwright default)
            png_bytes = session.page.screenshot(type='png')
            
            # Convert to JPEG with specified quality
            img = Image.open(io.BytesIO(png_bytes))
            if img.mode in ('RGBA', 'LA', 'P'):
                # Convert to RGB for JPEG
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            
            jpeg_buffer = io.BytesIO()
            img.save(jpeg_buffer, format='JPEG', quality=quality, optimize=True)
            return jpeg_buffer.getvalue()
        except Exception as e:
            print(f"Screenshot error: {e}")
            return None
    
    def screenshot(self, session_id: str, quality: Optional[int] = None) -> Optional[bytes]:
        """Take screenshot and return as JPEG bytes."""
        result = self._execute_on_browser_thread(
            self._screenshot_unsafe, session_id, quality
        )
        return result.get('result')
    
    def _click_unsafe(self, session_id: str, x: int, y: int) -> bool:
        """Click at coordinates (called on browser thread)."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.touch()
        try:
            session.page.mouse.click(x, y)
            return True
        except Exception as e:
            print(f"Click error: {e}")
            return False
    
    def click(self, session_id: str, x: int, y: int) -> bool:
        """Click at coordinates."""
        result = self._execute_on_browser_thread(self._click_unsafe, session_id, x, y)
        return result.get('result', False)
    
    def _scroll_unsafe(self, session_id: str, delta_x: int, delta_y: int) -> bool:
        """Scroll the page (called on browser thread)."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.touch()
        try:
            session.page.mouse.wheel(delta_x, delta_y)
            return True
        except Exception as e:
            print(f"Scroll error: {e}")
            return False
    
    def scroll(self, session_id: str, delta_x: int = 0, delta_y: int = 0) -> bool:
        """Scroll the page."""
        result = self._execute_on_browser_thread(
            self._scroll_unsafe, session_id, delta_x, delta_y
        )
        return result.get('result', False)
    
    def _type_text_unsafe(self, session_id: str, text: str) -> bool:
        """Type text (called on browser thread)."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.touch()
        try:
            session.page.keyboard.type(text)
            return True
        except Exception as e:
            print(f"Type error: {e}")
            return False
    
    def type_text(self, session_id: str, text: str) -> bool:
        """Type text."""
        result = self._execute_on_browser_thread(
            self._type_text_unsafe, session_id, text
        )
        return result.get('result', False)
    
    def _press_key_unsafe(self, session_id: str, key: str) -> bool:
        """Press a key (called on browser thread)."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.touch()
        try:
            session.page.keyboard.press(key)
            return True
        except Exception as e:
            print(f"Key press error: {e}")
            return False
    
    def press_key(self, session_id: str, key: str) -> bool:
        """Press a key."""
        result = self._execute_on_browser_thread(
            self._press_key_unsafe, session_id, key
        )
        return result.get('result', False)
    
    def get_page_info(self, session_id: str) -> Optional[dict]:
        """Get current page info (thread-safe)."""
        session = self._sessions.get(session_id)
        if not session:
            return None
        
        def _get_info():
            s = self._sessions.get(session_id)
            if not s:
                return None
            try:
                return {
                    'url': s.page.url,
                    'title': s.page.title(),
                    'viewport_width': s.viewport_width,
                    'viewport_height': s.viewport_height,
                    'quality': s.quality,
                    'fps': s.fps,
                    'quality': s.quality,
                    'fps': s.fps,
                    'fps': s.fps,
                    'downloads_count': len([d for d in s.downloads if d.get('status') == 'ready']),
                    'downloading_count': s.downloading_count
                }
            except Exception:
                return None
        
        result = self._execute_on_browser_thread(_get_info)
        return result.get('result')
    
    def set_quality(self, session_id: str, quality: int) -> bool:
        """Set JPEG quality for session."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.quality = max(config.MIN_QUALITY, min(quality, config.MAX_QUALITY))
        return True
    
    def set_fps(self, session_id: str, fps: int) -> bool:
        """Set FPS for session."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.fps = max(config.MIN_FPS, min(fps, config.MAX_FPS))
        return True
    
    def update_rtt(self, session_id: str, rtt_ms: float):
        """Update round-trip time measurement."""
        session = self._sessions.get(session_id)
        if session:
            session.last_rtt_ms = rtt_ms
            # Apply adaptive quality
            for level in ['fast', 'medium', 'slow', 'very_slow']:
                threshold = config.QUALITY_THRESHOLDS[level]
                if rtt_ms <= threshold['max_rtt']:
                    session.fps = threshold['fps']
                    session.quality = threshold['quality']
                    break
    
    def get_active_sessions_count(self) -> int:
        """Get count of active sessions."""
        return len(self._sessions)


# Global instance
browser_manager = BrowserManager()
