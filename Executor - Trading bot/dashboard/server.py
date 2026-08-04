"""
dashboard/server.py — Servidor HTTP para el Dashboard en vivo
═══════════════════════════════════════════════════════════════
Implementación completa (no re-export) del servidor dashboard.

Cada entorno ejecuta su propio servidor en un puerto dedicado.
"""
from __future__ import annotations

import http.server
import socketserver
import threading
from support.logger import get_logger

log = get_logger("dashboard_server")


class SilentHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler silencioso (sin logs de cada request)."""

    def log_message(self, format, *args):
        """Silencia los logs de cada request HTTP individual."""
        pass

    def end_headers(self):
        """Agrega cabeceras CORS y de caché antes de finalizar la respuesta HTTP."""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        super().end_headers()

    def do_OPTIONS(self):
        """Responde a solicitudes CORS preflight (OPTIONS) con los encabezados permitidos."""
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Requested-With, Content-Type')
        self.end_headers()

    def do_HEAD(self):
        """Responde a solicitudes HEAD con los encabezados CORS y tipo de contenido JSON."""
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

    def do_GET(self):
        """Soporta /api/status además de archivos estáticos."""
        if self.path == '/api/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{"status":"running"}')
            return
        return super().do_GET()

    def handle(self):
        """Maneja la solicitud HTTP entrante, ignorando errores de conexión abortada."""
        try:
            super().handle()
        except ConnectionAbortedError:
            pass


class DashboardServer:
    """Servidor HTTP para el dashboard en vivo. Corre en hilo daemon."""

    # Mapeo del modo que recibe de LiveEngine a las claves del .env
    _SECRETS_KEY_MAP = {
        "papper":              "PAPPER_DASHBOARD_PORT",
        "hyperliquid_mainnet":   "HYPERLIQUID_PERPS_DASHBOARD_PORT",
        "hyperliquid_testnet": "HYPERLIQUID_TESTNET_DASHBOARD_PORT",
    }

    _DEFAULT_PORT_MAP = {
        "papper":              8001,
        "hyperliquid_mainnet":   8004,
        "hyperliquid_testnet": 8005,
    }

    def __init__(self, environment: str):
        """Inicializa el servidor dashboard para el entorno indicado, leyendo el puerto desde .env."""
        self._environment = environment
        # Leer el puerto desde .env (via secrets), con fallback al default map
        from support.secrets import secrets
        secrets_key = self._SECRETS_KEY_MAP.get(environment, "PAPPER_DASHBOARD_PORT")
        default_port = str(self._DEFAULT_PORT_MAP.get(environment, 8001))
        self._port = int(secrets(secrets_key, default_port))

    @property
    def port(self) -> int:
        """Retorna el puerto en el que el servidor HTTP está escuchando."""
        return self._port

    def start(self) -> None:
        """Inicia el servidor HTTP en un hilo daemon."""
        def server_thread():
            socketserver.TCPServer.allow_reuse_address = True
            try:
                with socketserver.TCPServer(("", self._port), SilentHTTPHandler) as httpd:
                    httpd.serve_forever()
            except OSError as e:
                log.warning("No se pudo iniciar el servidor del Dashboard",
                            port=self._port, error=str(e))

        t = threading.Thread(target=server_thread, daemon=True)
        t.start()
        log.info("Dashboard HTTP iniciado", port=self._port, environment=self._environment)
