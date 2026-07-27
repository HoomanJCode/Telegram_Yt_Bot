#!/usr/bin/env python3
"""
Simple standalone HTTP server to serve downloaded files from `downloads/`.

This is a convenience script that can be run independently of the bot when an
operator wants a plain HTTP server (no Telegram polling) just to expose the
download directory. The main bot uses `app.fileserver.FileServer` (aiohttp)
instead, so this file is mostly for local testing or very minimal deployments.

AI RULE: If you modify this file, you must also update and fix the comments,
docstrings, and descriptions to keep them accurate and current.
"""

import http.server       # Standard-library base for simple HTTP servers.
import socketserver      # Wrappers for creating TCP socket servers.
import os                # Used to create the downloads directory.

# Port the server listens on. Hard-coded because this is a standalone helper.
PORT = 8000
# Directory whose contents will be served over HTTP.
DIRECTORY = "downloads"


class CustomHandler(http.server.SimpleHTTPRequestHandler):
    """Request handler that serves files from the configured DIRECTORY."""

    def __init__(self, *args, **kwargs):
        # Pass `directory=DIRECTORY` to the parent class so every request is
        # resolved relative to the downloads folder. This prevents the handler
        # from accidentally exposing files outside that directory.
        super().__init__(*args, directory=DIRECTORY, **kwargs)


if __name__ == "__main__":
    # Ensure the target directory exists before trying to serve it.
    os.makedirs(DIRECTORY, exist_ok=True)

    # Create a TCP server bound to all interfaces on the chosen port.
    with socketserver.TCPServer(("0.0.0.0", PORT), CustomHandler) as httpd:
        print(f"Serving files from '{DIRECTORY}' at http://0.0.0.0:{PORT}")
        print("Press Ctrl+C to stop the server")
        try:
            # Run the server indefinitely, handling one request at a time.
            httpd.serve_forever()
        except KeyboardInterrupt:
            # Graceful shutdown message when the operator hits Ctrl+C.
            print("\nServer stopped.")
