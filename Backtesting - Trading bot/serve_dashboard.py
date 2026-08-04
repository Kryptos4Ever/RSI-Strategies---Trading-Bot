"""
serve_dashboard.py — Servidor HTTP local con compresión gzip para el Dashboard
═══════════════════════════════════════════════════════════════════════════════════
Reemplaza ``python -m http.server`` añadiendo compresión gzip automática
para archivos .json (el backtest_results.json de ~15 MB se reduce a ~3 MB).

Uso:
    python serve_dashboard.py [puerto]
    (puerto por defecto: 7999)
"""
from __future__ import annotations

import gzip
import io
import os
import sys
import webbrowser
from functools import lru_cache
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import IO

# Puerto por defecto (mismo que Iniciar_Dashboard.bat)
DEFAULT_PORT = 7999

# Extensiones que comprimimos con gzip
GZIP_TYPES = {
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".svg":  "image/svg+xml",
}

CACHE_MAX_AGE = 300  # 5 minutos para contenido estático


class GzipHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Extiende SimpleHTTPRequestHandler con compresión gzip."""

    def __init__(self, *args, **kwargs):
        self._gzipped_cache: dict[str, bytes] = {}
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        """Sirve el archivo con compresión gzip si el navegador lo acepta."""
        path = self.translate_path(self.path)

        if not os.path.isfile(path):
            self.send_error(404, f"Archivo no encontrado: {self.path}")
            return

        ext = os.path.splitext(path)[1].lower()
        content_type = GZIP_TYPES.get(ext, "application/octet-stream")
        accepts_gzip = self._accepts_gzip()

        # Leer el archivo una sola vez
        try:
            raw = self._read_file(path)
        except OSError:
            self.send_error(500, f"Error leyendo archivo: {self.path}")
            return

        if accepts_gzip and ext in GZIP_TYPES:
            # Comprimir con gzip y cachear
            compressed = self._compress(raw)

            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(compressed)))
            self.send_header("Cache-Control", f"public, max-age={CACHE_MAX_AGE}")
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            self.wfile.write(compressed)
        else:
            # Servir sin comprimir
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", f"public, max-age={CACHE_MAX_AGE}")
            self.end_headers()
            self.wfile.write(raw)

    @staticmethod
    def _read_file(path: str) -> bytes:
        """Lee el archivo completo en memoria."""
        with open(path, "rb") as f:
            return f.read()

    @staticmethod
    def _compress(data: bytes) -> bytes:
        """Comprime datos con gzip al máximo nivel."""
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9) as f:
            f.write(data)
        return buf.getvalue()

    def _accepts_gzip(self) -> bool:
        """Verifica si el navegador acepta gzip."""
        accept = self.headers.get("Accept-Encoding", "")
        return "gzip" in accept

    def log_message(self, format: str, *args) -> None:
        """Log más compacto: solo método, ruta y tamaño."""
        msg = format % args
        # Extraer: "GET /path HTTP/1.1" 200 -
        parts = msg.split()
        if len(parts) >= 2:
            method = parts[0] if parts[0] in ("GET", "POST", "PUT") else "?"
            path = parts[1] if len(parts) > 1 else "?"
            status = parts[-2] if len(parts) > 2 else "?"
            print(f"  {method} {path} -> {status}")
        else:
            print(f"  {msg}")


def find_project_root() -> Path:
    """Busca la raíz del proyecto (donde está este script o el .bat).

    Empieza desde el PADRE del directorio del script para evitar
    encontrar .gitignore anidado dentro de Backtesting - Trading bot/.
    """
    script_dir = Path(__file__).resolve().parent
    # Empezar desde el padre para evitar .gitignore de subdirectorios
    for parent in [script_dir.parent, script_dir.parent.parent]:
        if (parent / ".gitignore").exists() or (parent / ".git").exists():
            return parent
    # Fallback
    return script_dir.parent


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    # Determinar directorio raíz para servir archivos
    root = find_project_root()
    os.chdir(root)
    print(f"  Directorio raiz: {root}")
    print(f"  Buscando JSON:   {root / 'Backtesting - Trading bot' / 'backtest_results.json'}")
    print(f"  Existe?          {(root / 'Backtesting - Trading bot' / 'backtest_results.json').exists()}")

    server = HTTPServer(("0.0.0.0", port), GzipHTTPRequestHandler)

    print()
    print("=" * 60)
    print(f"  Dashboard Server - con compresion gzip")
    print(f"  Puerto : {port}")
    print(f"  Raiz   : {root}")
    print("=" * 60)
    print()
    print(f"  => http://localhost:{port}/Backtesting%20-%20Trading%20bot/backtest_dashboard.html")
    print()
    print("  Ctrl+C para detener el servidor.")
    print()

    # Abrir navegador automáticamente
    url = f"http://localhost:{port}/Backtesting%20-%20Trading%20bot/backtest_dashboard.html"
    try:
        webbrowser.open(url)
    except Exception:
        print(f"  Abre manualmente: {url}")
        print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor detenido.")
        server.server_close()


if __name__ == "__main__":
    main()