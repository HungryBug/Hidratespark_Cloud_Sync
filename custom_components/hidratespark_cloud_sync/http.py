"""Versioned Apple Health Bridge HTTP API."""
from __future__ import annotations

from datetime import datetime
from aiohttp import web
from homeassistant.components.http import HomeAssistantView

BASE = "/api/hidratespark_cloud_sync/v1/apple_health"


def _error(message, status):
    return web.json_response({"error": message}, status=status)


def _limit(request):
    value = int(request.query.get("limit", 100))
    if value < 1 or value > 500:
        raise ValueError
    return value


def _date(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed


class BridgeView(HomeAssistantView):
    requires_auth = False

    def __init__(self, bridge):
        self.bridge = bridge

    def auth(self, request):
        return self.bridge.authorized(request.headers.get("Authorization"))

    async def body(self, request):
        try:
            value = await request.json()
        except Exception as err:
            raise ValueError from err
        if not isinstance(value, dict):
            raise ValueError
        return value


class PendingView(BridgeView):
    url = f"{BASE}/pending"
    name = "api:hidratespark_cloud_sync:apple_health:pending"

    async def get(self, request):
        if not self.auth(request): return _error("Unauthorized", 401)
        try: records = self.bridge.query("pending", limit=_limit(request))
        except ValueError: return _error("Invalid limit", 400)
        fields = ("id", "occurred_at", "unix_ms", "volume_ml", "unit", "health_type", "bottle")
        return web.json_response({"records": [{key: r[key] for key in fields} for r in records]})


class AckView(BridgeView):
    url = f"{BASE}/ack"
    name = "api:hidratespark_cloud_sync:apple_health:ack"

    async def post(self, request):
        if not self.auth(request): return _error("Unauthorized", 401)
        try:
            body = await self.body(request)
            ids = body.get("ids", [body.get("id")])
            if not isinstance(ids, list) or not ids or not all(isinstance(v, str) and v for v in ids): raise ValueError
        except ValueError: return _error("Provide id or ids", 400)
        return web.json_response(await self.bridge.async_ack(list(dict.fromkeys(ids))))


class FailView(BridgeView):
    url = f"{BASE}/fail"
    name = "api:hidratespark_cloud_sync:apple_health:fail"

    async def post(self, request):
        if not self.auth(request): return _error("Unauthorized", 401)
        try:
            body = await self.body(request)
            if not isinstance(body.get("id"), str) or not isinstance(body.get("error"), str): raise ValueError
        except ValueError: return _error("Provide id and error", 400)
        if not await self.bridge.async_fail(body["id"], body["error"]): return _error("Record not found", 404)
        return web.json_response({"failed": body["id"]})


class StatusView(BridgeView):
    url = f"{BASE}/status"
    name = "api:hidratespark_cloud_sync:apple_health:status"

    async def get(self, request):
        if not self.auth(request): return _error("Unauthorized", 401)
        return web.json_response(self.bridge.status_data())


class HistoryView(BridgeView):
    url = f"{BASE}/history"
    name = "api:hidratespark_cloud_sync:apple_health:history"

    async def get(self, request):
        if not self.auth(request): return _error("Unauthorized", 401)
        try:
            status = request.query.get("status", "all")
            if status not in ("pending", "synced", "all"): raise ValueError
            records = self.bridge.query(status, _date(request.query.get("since")),
                                        _date(request.query.get("until")), _limit(request))
        except (ValueError, TypeError): return _error("Invalid query", 400)
        safe = [{key: value for key, value in record.items()
                 if key not in ("bottle_serial", "entry_id", "source_entities")} for record in records]
        return web.json_response({"records": safe})


def register_views(hass, bridge):
    for view in (PendingView, AckView, FailView, StatusView, HistoryView):
        hass.http.register_view(view(bridge))

