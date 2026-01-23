"""
IEthru - Flask Application

A web app that lets legacy browsers (IE5/6) browse modern websites
through remote rendering using Playwright.
"""

import time
from flask import (
    Flask, 
    render_template, 
    request, 
    redirect, 
    url_for, 
    Response,
    jsonify,
    send_file
)

import config
from browser_manager import browser_manager

app = Flask(__name__)


@app.route('/downloads/<session_id>/<filename>')
def download_file(session_id, filename):
    """Serve a downloaded file."""
    # Security check: generic path traversal prevention
    if '..' in session_id or '..' in filename:
        return "Invalid path", 400
        
    import os
    file_path = os.path.join('downloads', session_id, filename)
    
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return "File not found", 404


@app.before_request
def record_request_time():
    """Record request start time for RTT calculation."""
    request.start_time = time.time()


@app.route('/')
def index():
    """Landing page with URL input form."""
    return render_template('index.html')


@app.route('/browse', methods=['GET', 'POST'])
def browse():
    """Navigate to URL and display browser view."""
    url = request.form.get('url') or request.args.get('url', '')
    session_id = request.form.get('session_id') or request.args.get('session_id')
    
    # Get viewport settings from form
    viewport_width = request.form.get('viewport_width', type=int) or config.DEFAULT_VIEWPORT_WIDTH
    viewport_height = request.form.get('viewport_height', type=int) or config.DEFAULT_VIEWPORT_HEIGHT
    fps = request.form.get('fps', type=int) or request.args.get('fps', type=int) or config.DEFAULT_FPS
    
    # Create new session if needed
    if not session_id or not browser_manager.get_session(session_id):
        session_id = browser_manager.create_session(viewport_width, viewport_height)
    
    # Set FPS on session
    browser_manager.set_fps(session_id, fps)
    
    # Navigate if URL provided
    error = None
    if url:
        success, result = browser_manager.navigate(session_id, url)
        if not success:
            error = result
    
    # Get page info
    page_info = browser_manager.get_page_info(session_id) or {
        'url': '',
        'title': 'New Session',
        'viewport_width': viewport_width,
        'viewport_height': viewport_height,
        'fps': fps
    }
    
    # Add timestamp for cache-busting
    import time as time_module
    timestamp = int(time_module.time() * 1000)
    
    return render_template(
        'browser.html',
        session_id=session_id,
        page_info=page_info,
        error=error,
        fps=page_info.get('fps', fps),
        timestamp=timestamp
    )


@app.route('/frame/<session_id>')
def frame(session_id):
    """Get current frame as JPEG image."""
    quality = request.args.get('q', type=int)
    
    # Update RTT measurement
    if hasattr(request, 'start_time'):
        rtt = (time.time() - request.start_time) * 1000
        browser_manager.update_rtt(session_id, rtt)
    
    jpeg_data = browser_manager.screenshot(session_id, quality)
    
    if jpeg_data:
        response = Response(jpeg_data, mimetype='image/jpeg')
        # No caching for fresh frames
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    else:
        # Return a 1x1 white pixel as fallback
        return Response(
            b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
            b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t'
            b'\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a'
            b'\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9teletyp:teletyp>teletyp6teletyp\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456teletyp:teletyp>teletyp6teletypDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5\x1ff\xbf\xff\xd9',
            mimetype='image/jpeg'
        )


# ============================================
# Simplified routes with session_id in URL path
# These ensure the session is NEVER lost
# ============================================

@app.route('/scroll/<session_id>')
def scroll_action(session_id):
    """Scroll the page and refresh view."""
    dx = request.args.get('dx', type=int, default=0)
    dy = request.args.get('dy', type=int, default=0)
    
    browser_manager.scroll(session_id, dx, dy)
    time.sleep(0.15)  # Brief wait for scroll
    
    return redirect(f'/view/{session_id}')


@app.route('/click/<session_id>')
def click_action(session_id):
    """Click at coordinates and refresh view."""
    x = request.args.get('x', type=int, default=0)
    y = request.args.get('y', type=int, default=0)
    
    browser_manager.click(session_id, x, y)
    time.sleep(0.3)  # Wait for any JS/navigation
    
    return redirect(f'/view/{session_id}')


@app.route('/type/<session_id>')
def type_action(session_id):
    """Type text and refresh view."""
    text = request.args.get('text', '')
    
    if text:
        browser_manager.type_text(session_id, text)
    time.sleep(0.1)
    
    return redirect(f'/view/{session_id}')


@app.route('/key/<session_id>')
def key_action(session_id):
    """Press a key and refresh view."""
    key = request.args.get('key', '')
    
    if key:
        browser_manager.press_key(session_id, key)
    time.sleep(0.15)
    
    return redirect(f'/view/{session_id}')


@app.route('/refresh/<session_id>')
def refresh_action(session_id):
    """Just refresh the view (no action)."""
    return redirect(f'/view/{session_id}')


@app.route('/view/<session_id>')
def view_session(session_id):
    """View session without navigating (just show current state)."""
    session = browser_manager.get_session(session_id)
    if not session:
        return redirect(url_for('index'))
    
    page_info = browser_manager.get_page_info(session_id) or {
        'url': '',
        'title': 'Session',
        'viewport_width': 800,
        'viewport_height': 600,
        'fps': 5
    }
    
    import time as time_module
    timestamp = int(time_module.time() * 1000)
    
    return render_template(
        'browser.html',
        session_id=session_id,
        page_info=page_info,
        error=None,
        fps=page_info.get('fps', 5),
        timestamp=timestamp
    )


@app.route('/stream/<session_id>')
def stream(session_id):
    """MJPEG stream for continuous frame updates."""
    session = browser_manager.get_session(session_id)
    if not session:
        return "Session not found", 404
    
    def generate():
        while True:
            # Check if session still exists
            s = browser_manager.get_session(session_id)
            if not s:
                break
            
            jpeg_data = browser_manager.screenshot(session_id)
            if jpeg_data:
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n'
                    b'Content-Length: ' + str(len(jpeg_data)).encode() + b'\r\n'
                    b'\r\n' + jpeg_data + b'\r\n'
                )
            
            # Wait based on FPS setting
            time.sleep(1.0 / s.fps)
    
    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route('/input/<session_id>', methods=['GET', 'POST'])
def handle_input(session_id):
    """Handle mouse/keyboard input events."""
    action = request.form.get('action') or request.args.get('action')
    
    if action == 'click':
        x = request.form.get('x', type=int) or request.args.get('x', type=int, default=0)
        y = request.form.get('y', type=int) or request.args.get('y', type=int, default=0)
        browser_manager.click(session_id, x, y)
    
    elif action == 'scroll':
        dx = request.form.get('dx', type=int, default=0)
        dy = request.form.get('dy', type=int, default=0)
        browser_manager.scroll(session_id, dx, dy)
    
    elif action == 'scroll_up':
        browser_manager.scroll(session_id, 0, -100)
    
    elif action == 'scroll_down':
        browser_manager.scroll(session_id, 0, 100)
    
    elif action == 'type':
        keys = request.form.get('keys', '')
        if keys:
            browser_manager.type_text(session_id, keys)
    
    elif action == 'key':
        key = request.form.get('key', '')
        if key:
            browser_manager.press_key(session_id, key)
    
    elif action == 'enter':
        browser_manager.press_key(session_id, 'Enter')
    
    elif action == 'tab':
        browser_manager.press_key(session_id, 'Tab')
    
    elif action == 'escape':
        browser_manager.press_key(session_id, 'Escape')
    
    # Small delay to let page update
    time.sleep(0.1)
    
    # Redirect back to browser view
    page_info = browser_manager.get_page_info(session_id)
    current_url = page_info['url'] if page_info else ''
    return redirect(url_for('browse', session_id=session_id, url=current_url))


@app.route('/action/<session_id>', methods=['POST'])
def handle_action(session_id):
    """Handle navigation actions (back, forward, reload, stop)."""
    action = request.form.get('action', '')
    
    if action == 'back':
        browser_manager.go_back(session_id)
    elif action == 'forward':
        browser_manager.go_forward(session_id)
    elif action == 'reload':
        browser_manager.reload(session_id)
    elif action == 'stop':
        browser_manager.stop_loading(session_id)
    
    # Small delay to let page update
    time.sleep(0.2)
    
    # Redirect back to browser view
    page_info = browser_manager.get_page_info(session_id)
    current_url = page_info['url'] if page_info else ''
    return redirect(url_for('browse', session_id=session_id, url=current_url))


@app.route('/input/<session_id>/async', methods=['POST'])
def handle_input_async(session_id):
    """Handle mouse/keyboard input events asynchronously (no redirect)."""
    action = request.form.get('action') or request.args.get('action')
    
    if action == 'click':
        x = request.form.get('x', type=int) or request.args.get('x', type=int, default=0)
        y = request.form.get('y', type=int) or request.args.get('y', type=int, default=0)
        browser_manager.click(session_id, x, y)
    
    elif action == 'scroll':
        dx = request.form.get('dx', type=int, default=0)
        dy = request.form.get('dy', type=int, default=0)
        browser_manager.scroll(session_id, dx, dy)
    
    elif action == 'type':
        keys = request.form.get('keys', '')
        if keys:
            browser_manager.type_text(session_id, keys)
    
    elif action == 'key':
        key = request.form.get('key', '')
        if key:
            browser_manager.press_key(session_id, key)
    
    return jsonify({'success': True, 'action': action})


@app.route('/action/<session_id>/async', methods=['POST'])
def handle_action_async(session_id):
    """Handle navigation actions asynchronously (no redirect)."""
    action = request.form.get('action', '')
    
    if action == 'back':
        browser_manager.go_back(session_id)
    elif action == 'forward':
        browser_manager.go_forward(session_id)
    elif action == 'reload':
        browser_manager.reload(session_id)
    elif action == 'stop':
        browser_manager.stop_loading(session_id)
    
    return jsonify({'success': True, 'action': action})


@app.route('/browse/async', methods=['POST'])
def browse_async():
    """Navigate to URL asynchronously (no redirect)."""
    url = request.form.get('url', '')
    session_id = request.form.get('session_id')
    
    if session_id and url:
        success, result = browser_manager.navigate(session_id, url)
        return jsonify({'success': success, 'url': result})
    
    return jsonify({'success': False, 'error': 'Missing session_id or url'})


# ============================================
# GET-based endpoints for IE5/6 compatibility
# ============================================

@app.route('/input/<session_id>/get', methods=['GET'])
def handle_input_get(session_id):
    """Handle mouse/keyboard input via GET (for IE5/6)."""
    action = request.args.get('action', '')
    
    if action == 'click':
        x = request.args.get('x', type=int, default=0)
        y = request.args.get('y', type=int, default=0)
        browser_manager.click(session_id, x, y)
    
    elif action == 'scroll':
        dx = request.args.get('dx', type=int, default=0)
        dy = request.args.get('dy', type=int, default=0)
        browser_manager.scroll(session_id, dx, dy)
    
    elif action == 'type':
        keys = request.args.get('keys', '')
        if keys:
            browser_manager.type_text(session_id, keys)
    
    elif action == 'key':
        key = request.args.get('key', '')
        if key:
            browser_manager.press_key(session_id, key)
    
    # Small delay
    time.sleep(0.1)
    
    # Redirect back to browser view (GET-based)
    page_info = browser_manager.get_page_info(session_id)
    current_url = page_info['url'] if page_info else ''
    fps = page_info.get('fps', 5) if page_info else 5
    return redirect(f'/browse?session_id={session_id}&url={current_url}&fps={fps}')


@app.route('/action/<session_id>/get', methods=['GET'])
def handle_action_get(session_id):
    """Handle navigation actions via GET (for IE5/6)."""
    action = request.args.get('action', '')
    
    if action == 'back':
        browser_manager.go_back(session_id)
    elif action == 'forward':
        browser_manager.go_forward(session_id)
    elif action == 'reload':
        browser_manager.reload(session_id)
    elif action == 'stop':
        browser_manager.stop_loading(session_id)
    
    time.sleep(0.2)
    
    # Redirect back to browser view
    page_info = browser_manager.get_page_info(session_id)
    current_url = page_info['url'] if page_info else ''
    fps = page_info.get('fps', 5) if page_info else 5
    return redirect(f'/browse?session_id={session_id}&url={current_url}&fps={fps}')


@app.route('/session/close/<session_id>/get', methods=['GET'])
def close_session_get(session_id):
    """Close session via GET (for IE5/6)."""
    browser_manager.close_session(session_id)
    return redirect(url_for('index'))


@app.route('/session/new', methods=['POST'])
def new_session():
    """Create a new browser session."""
    viewport_width = request.form.get('viewport_width', type=int, default=config.DEFAULT_VIEWPORT_WIDTH)
    viewport_height = request.form.get('viewport_height', type=int, default=config.DEFAULT_VIEWPORT_HEIGHT)
    
    session_id = browser_manager.create_session(viewport_width, viewport_height)
    
    # Redirect to browser view
    return redirect(url_for('browse', session_id=session_id))


@app.route('/session/close/<session_id>', methods=['POST'])
def close_session(session_id):
    """Close a browser session."""
    browser_manager.close_session(session_id)
    return redirect(url_for('index'))


@app.route('/api/status')
def api_status():
    """API endpoint for status (for modern browsers)."""
    return jsonify({
        'sessions': browser_manager.get_active_sessions_count(),
        'max_sessions': config.MAX_SESSIONS
    })


@app.route('/api/session/<session_id>/info')
def api_session_info(session_id):
    """API endpoint for session info."""
    info = browser_manager.get_page_info(session_id)
    if info:
        return jsonify(info)
    return jsonify({'error': 'Session not found'}), 404


@app.route('/api/session/<session_id>/quality', methods=['POST'])
def api_set_quality(session_id):
    """API endpoint to set quality."""
    quality = request.json.get('quality', config.DEFAULT_QUALITY)
    browser_manager.set_quality(session_id, quality)
    return jsonify({'success': True, 'quality': quality})


@app.route('/api/session/<session_id>/fps', methods=['POST'])
def api_set_fps(session_id):
    """API endpoint to set FPS."""
    fps = request.json.get('fps', config.DEFAULT_FPS)
    browser_manager.set_fps(session_id, fps)
    return jsonify({'success': True, 'fps': fps})


def main():
    """Main entry point."""
    print(f"IEthru started with Chromium")
    print(f"Server will be available at http://{config.HOST}:{config.PORT}")
    
    # Start browser manager
    browser_manager.start()
    
    try:
        app.run(
            host=config.HOST,
            port=config.PORT,
            debug=config.DEBUG,
            threaded=True,
            use_reloader=False  # Disable reloader to avoid double browser init
        )
    finally:
        browser_manager.stop()


if __name__ == '__main__':
    main()
