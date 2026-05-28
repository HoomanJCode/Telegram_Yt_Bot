#!/usr/bin/env python3
"""
Simple HTTP server to serve downloaded files
"""

import http.server
import socketserver
import os

PORT = 8000
DIRECTORY = "downloads"

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

if __name__ == "__main__":
    os.makedirs(DIRECTORY, exist_ok=True)
    
    with socketserver.TCPServer(("0.0.0.0", PORT), CustomHandler) as httpd:
        print(f"Serving files from '{DIRECTORY}' at http://0.0.0.0:{PORT}")
        print("Press Ctrl+C to stop the server")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")