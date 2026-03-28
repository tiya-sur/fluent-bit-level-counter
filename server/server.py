from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class LogHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length)

        print("\n===== RECEIVED LOG =====")
        try:
            data = json.loads(body)
            print(json.dumps(data, indent=2))
        except Exception:
            print(body.decode())

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
        
def run():
    server_address = ('0.0.0.0', 8080)  
    httpd = HTTPServer(server_address, LogHandler)
    print("Server running on http://0.0.0.0:8080")
    httpd.serve_forever()

if __name__ == "__main__":
    run()
