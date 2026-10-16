import os
import shutil
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

DB_FILE = "tasks.db"
ARCHIVE_DIR = "archive"
MAX_BACKUPS = 30  # Ile starych kopii bazy trzymać na HA

os.makedirs(ARCHIVE_DIR, exist_ok=True)

class SyncHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Wysyła bazę do telefonu/PC."""
        if not os.path.exists(DB_FILE):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        with open(DB_FILE, "rb") as f:
            self.wfile.write(f.read())

    def do_POST(self):
        """Odbiera nową bazę i przenosi starą do archiwum."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_response(400)
            self.end_headers()
            return

        new_data = self.rfile.read(content_length)

        if os.path.exists(DB_FILE):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(DB_FILE, os.path.join(ARCHIVE_DIR, f"tasks_{timestamp}.db"))
            backups = sorted([os.path.join(ARCHIVE_DIR, f) for f in os.listdir(ARCHIVE_DIR) if f.endswith(".db")])
            while len(backups) > MAX_BACKUPS:
                os.remove(backups.pop(0))

        with open(DB_FILE, "wb") as f:
            f.write(new_data)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8765), SyncHandler)
    print("Serwer synchronizacji ADHD dziala na porcie 8765...")
    server.serve_forever()