"""Global, persistent Apple Health delivery bridge."""
from __future__ import annotations

import asyncio
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .const import (
    BRIDGE_KEY, BRIDGE_SIGNAL, BRIDGE_STORE_KEY, DOMAIN, EVENT_BATCH_READY,
    EVENT_PENDING,
)


class HealthBridge:
    """One bridge shared by every bottle config entry."""

    def __init__(self, hass):
        self.hass = hass
        self.store = Store(hass, 1, BRIDGE_STORE_KEY)
        self.token = ""
        self.enabled = True
        self.debounce_seconds = 30
        self.records = {}
        self.created_at = None
        self.updated_at = None
        self._lock = asyncio.Lock()
        self._batch_cancel = None
        self.sensor_owner = None

    async def async_load(self):
        data = await self.store.async_load() or {}
        self.token = data.get("token") or secrets.token_urlsafe(32)
        self.enabled = data.get("enabled", True)
        self.debounce_seconds = max(1, min(300, int(data.get("debounce_seconds", 30))))
        self.records = data.get("records", {})
        self.created_at = data.get("created_at") or self._now()
        self.updated_at = data.get("updated_at") or self.created_at
        await self._prune_and_save()

    def authorized(self, header):
        if not self.enabled or not isinstance(header, str) or not header.startswith("Bearer "):
            return False
        supplied = header[7:]
        return bool(supplied) and hmac.compare_digest(supplied, self.token)

    async def async_configure(self, token, enabled, debounce_seconds):
        async with self._lock:
            self.token = token.strip()
            if len(self.token) < 32:
                raise ValueError("Token must be at least 32 characters")
            self.enabled = bool(enabled)
            self.debounce_seconds = max(1, min(300, int(debounce_seconds)))
            await self._save()
        self.changed()

    async def async_regenerate_token(self):
        token = secrets.token_urlsafe(32)
        await self.async_configure(token, self.enabled, self.debounce_seconds)
        return token

    async def async_enqueue(self, item, entry):
        event_id = item["event_id"]
        async with self._lock:
            if event_id in self.records:
                return False
            occurred = datetime.fromisoformat(item["timestamp"])
            local = occurred.astimezone(ZoneInfo(entry.data["time_zone"]))
            now = self._now()
            self.records[event_id] = {
                "id": event_id, "occurred_at": local.isoformat(timespec="milliseconds"),
                "unix_ms": round(occurred.timestamp() * 1000),
                "volume_ml": item["volume_ml"], "unit": "mL",
                "health_type": "dietaryWater", "source": "ha_hidratespark",
                "bottle": entry.title, "bottle_serial": entry.data["bottle_serial"],
                "entry_id": entry.entry_id,
                "source_entities": dict(entry.options), "captured_at": now,
                "apple_health_status": "pending", "apple_health_synced_at": None,
                "apple_health_attempts": 0, "apple_health_last_error": None,
                "cloud_status": "pending", "cloud_object_id": None,
            }
            await self._save()
        self.hass.bus.async_fire(EVENT_PENDING, {"id": event_id, "bottle": entry.title})
        self._schedule_batch()
        self.changed()
        return True

    async def async_update_cloud(self, item, status):
        async with self._lock:
            record = self.records.get(item["event_id"])
            if record:
                record["cloud_status"] = status
                record["cloud_object_id"] = item.get("sip_id")
                await self._save()
        self.changed()

    async def async_ack(self, ids):
        result = {"acked": [], "already_acked": [], "not_found": []}
        async with self._lock:
            now = self._now()
            for event_id in ids:
                record = self.records.get(event_id)
                if record is None:
                    result["not_found"].append(event_id)
                elif record["apple_health_status"] == "synced":
                    result["already_acked"].append(event_id)
                else:
                    record.update(apple_health_status="synced", apple_health_synced_at=now,
                                  apple_health_last_error=None)
                    result["acked"].append(event_id)
            await self._prune_and_save()
        self.changed()
        return result

    async def async_fail(self, event_id, error):
        async with self._lock:
            record = self.records.get(event_id)
            if record is None:
                return False
            record["apple_health_status"] = "pending"
            record["apple_health_attempts"] += 1
            record["apple_health_last_error"] = str(error)[:500]
            await self._save()
        self.changed()
        return True

    async def async_requeue(self, start=None, end=None, bottle=None):
        count = 0
        async with self._lock:
            for record in self.records.values():
                occurred = datetime.fromisoformat(record["occurred_at"])
                if start and occurred < start or end and occurred > end:
                    continue
                if bottle and bottle not in (record["bottle"], record["bottle_serial"], record["entry_id"]):
                    continue
                if record["apple_health_status"] == "synced":
                    record.update(apple_health_status="pending", apple_health_synced_at=None,
                                  apple_health_last_error=None)
                    count += 1
            await self._save()
        if count:
            self._schedule_batch()
            self.changed()
        return count

    def query(self, status="pending", since=None, until=None, limit=100):
        records = sorted(self.records.values(), key=lambda value: value["unix_ms"])
        return [record for record in records
                if (status == "all" or record["apple_health_status"] == status)
                and (since is None or datetime.fromisoformat(record["occurred_at"]) >= since)
                and (until is None or datetime.fromisoformat(record["occurred_at"]) <= until)][:limit]

    def status_data(self):
        pending = self.query("pending", limit=500000)
        synced = self.query("synced", limit=500000)
        return {"enabled": self.enabled, "status": self.status,
                "pending": len(pending), "synced": len(synced),
                "oldest_pending": pending[0]["occurred_at"] if pending else None,
                "last_successful_sync": max((r["apple_health_synced_at"] for r in synced), default=None)}

    @property
    def status(self):
        if not self.enabled:
            return "disabled"
        if any(r["apple_health_last_error"] for r in self.records.values() if r["apple_health_status"] == "pending"):
            return "error"
        return "pending" if any(r["apple_health_status"] == "pending" for r in self.records.values()) else "ready"

    def changed(self):
        async_dispatcher_send(self.hass, BRIDGE_SIGNAL)

    def _schedule_batch(self):
        if self._batch_cancel is not None:
            return
        def ready(_now):
            self._batch_cancel = None
            pending = len(self.query("pending", limit=500000))
            if pending:
                self.hass.bus.async_fire(EVENT_BATCH_READY, {"pending": pending})
        self._batch_cancel = async_call_later(self.hass, self.debounce_seconds, ready)

    async def _prune_and_save(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=180)
        synced = sorted((r for r in self.records.values() if r["apple_health_status"] == "synced"),
                        key=lambda value: value.get("apple_health_synced_at") or "")
        excess = max(0, len(synced) - 5000)
        remove = {r["id"] for index, r in enumerate(synced)
                  if index < excess or datetime.fromisoformat(r["apple_health_synced_at"]) < cutoff}
        for event_id in remove:
            self.records.pop(event_id, None)
        await self._save()

    async def _save(self):
        self.updated_at = self._now()
        await self.store.async_save({"token": self.token, "enabled": self.enabled,
            "debounce_seconds": self.debounce_seconds, "records": self.records,
            "created_at": self.created_at, "updated_at": self.updated_at})

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()


async def async_get_bridge(hass):
    domain_data = hass.data.setdefault(DOMAIN, {})
    if BRIDGE_KEY not in domain_data:
        bridge = HealthBridge(hass)
        await bridge.async_load()
        domain_data[BRIDGE_KEY] = bridge
    return domain_data[BRIDGE_KEY]

