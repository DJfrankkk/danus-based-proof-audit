from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import load_config
from .models import public_state
from .state import current_item, load_state


def _dashboard_html() -> bytes:
    return files("proofaudit").joinpath("assets/dashboard.html").read_bytes()


def _api_payload(root: Path) -> dict:
    state = load_state(root)
    payload = public_state(state)
    total = len(state["items"])
    closed = sum(item["status"] == "closed" for item in state["items"])
    skipped = sum(item["status"] == "skipped" for item in state["items"])
    active = current_item(state)
    payload["progress"] = {
        "total": total,
        "closed": closed,
        "skipped": skipped,
        "terminal": closed + skipped,
        "percent": round((closed + skipped) * 100 / total, 1) if total else 100,
    }
    payload["current_item_id"] = active["id"] if active else None
    payload["evidence_notice"] = "Independent AI review; not formal verification."
    return payload


def _report_payload(root: Path, item_id: str, reviewer_id: str) -> dict | None:
    state = load_state(root)
    for item in state["items"]:
        if item["id"] != item_id:
            continue
        review = item.get("reviews", {}).get(reviewer_id)
        if not review:
            return None
        relative = review.get("report_path")
        if not relative:
            return None
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            return None
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def make_handler(root: Path):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", _dashboard_html())
                return
            if parsed.path == "/api/state":
                body = json.dumps(_api_payload(root), ensure_ascii=False).encode("utf-8")
                self._send(HTTPStatus.OK, "application/json; charset=utf-8", body)
                return
            if parsed.path == "/api/report":
                query = parse_qs(parsed.query)
                item_id = query.get("item", [""])[0]
                reviewer_id = query.get("reviewer", [""])[0]
                report = _report_payload(root, item_id, reviewer_id)
                if report is None:
                    self._send(
                        HTTPStatus.NOT_FOUND,
                        "application/json; charset=utf-8",
                        b'{"error":"report not found"}',
                    )
                    return
                body = json.dumps(report, ensure_ascii=False).encode("utf-8")
                self._send(HTTPStatus.OK, "application/json; charset=utf-8", body)
                return
            self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Not found")

        def log_message(self, fmt: str, *args) -> None:
            return

    return Handler


def serve(project: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    config = load_config(project)
    load_state(config.root)
    server = ThreadingHTTPServer((host, port), make_handler(config.root))
    print(f"Danus Proof Audit dashboard: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
