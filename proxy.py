#!/usr/bin/env python3
"""
Proxy para el Asesor de Maxim Domenech - Versión Cloud Run
La API key se lee de la variable de entorno ANTHROPIC_API_KEY
El puerto se lee de la variable PORT (Cloud Run lo asigna automáticamente)
"""

import os
import json
import http.server
import urllib.request
import urllib.error
from pathlib import Path

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", 8080))
CARPETA = Path(__file__).parent


class ProxyHandler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CARPETA), **kwargs)

    # Redirigir / a la página de inicio
    def do_GET(self):
        if self.path == "/":
            self.send_response(301)
            self.send_header("Location", "/landing.html")
            self.end_headers()
            return
        super().do_GET()

    def do_OPTIONS(self):
        self.send_cors()

    def do_POST(self):
        if self.path == "/api/chat":
            self.handle_chat()
        elif self.path == "/api/webhook":
            self.handle_webhook()
        else:
            self.send_error(404)

    def handle_chat(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                self.send_cors(200)
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            err_data = e.read()
            self.send_cors(e.code)
            self.wfile.write(err_data)

    # ── WEBHOOK (Make/Zapier → HubSpot) ────────────────────────
    def handle_webhook(self):
        if not WEBHOOK_URL:
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": False, "msg": "WEBHOOK_URL no configurado"}).encode())
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            req = urllib.request.Request(
                WEBHOOK_URL,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                print(f"[Webhook] Enviado OK")
                self.send_cors(200)
                self.wfile.write(json.dumps({"ok": True}).encode())
        except urllib.error.HTTPError as e:
            print(f"[Webhook] Error: {e.code}")
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": False, "msg": f"Webhook error {e.code}"}).encode())
        except Exception as e:
            print(f"[Webhook] Error: {e}")
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": False, "msg": str(e)}).encode())

    def send_cors(self, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def log_message(self, fmt, *args):
        status = args[1] if len(args) > 1 else ""
        path = args[0].split()[1] if args else ""
        print(f"[{status}] {path}")


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Variable de entorno ANTHROPIC_API_KEY no configurada")
        print("  En Cloud Run: se configura como secreto")
        print("  En local:     export ANTHROPIC_API_KEY=sk-ant-...")
        exit(1)

    # Escuchar en 0.0.0.0 (requerido por Cloud Run)
    server = http.server.HTTPServer(("0.0.0.0", PORT), ProxyHandler)
    print(f"Proxy Maxim Domenech arrancado en puerto {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Proxy parado.")
