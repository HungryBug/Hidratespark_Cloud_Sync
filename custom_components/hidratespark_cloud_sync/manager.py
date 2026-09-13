"""Persistent, per-entry serial upload queue."""
from __future__ import annotations
import asyncio
import logging
import math
import uuid
from datetime import datetime, timezone
from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from datetime import timedelta
from .api import AuthenticationError, PermanentError, RetryError, UncertainError
from .const import DOMAIN
from .source import get_source

_LOGGER = logging.getLogger(__name__)


def drink_event(timestamp, volume, serial, clock):
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError("Invalid sip volume")
    stamp = datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds")
    identity = f"{serial}|{stamp}|{volume:.6f}"
    return {"timestamp": stamp, "volume_ml": volume,
            "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, identity)).upper(),
            "health_id": str(uuid.uuid5(uuid.NAMESPACE_OID, identity)).upper(), "clock": clock}


class SyncManager:
    def __init__(self, hass, entry, client, health_bridge=None):
        self.hass, self.entry, self.client = hass, entry, client
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.signal = f"{DOMAIN}_{entry.entry_id}"
        self.pending, self.synced, self.failed = [], [], []
        self.last_success = None
        self.status, self.last_error = "idle", None
        self.clock = 33000
        self.wake = asyncio.Event()
        self.unsubs = []
        self.worker = None
        self.source = None
        self.source_unsub = None
        self.seen = set()
        self.auth_failed = False
        self.stopping = False
        self.health_bridge = health_bridge

    async def save(self):
        await self.store.async_save({"pending": self.pending, "synced_event_ids": self.synced[-2000:],
            "failed": self.failed, "last_success": self.last_success, "clock": self.clock})

    def changed(self):
        async_dispatcher_send(self.hass, self.signal)

    async def start(self):
        data = await self.store.async_load()
        if data:
            self.pending = data.get("pending", [])
            self.synced = data.get("synced_event_ids", [])
            self.failed = data.get("failed", [])
            self.last_success = data.get("last_success")
            self.clock = data.get("clock", 33000)
        # Baseline the current ledger only on first installation. Existing queues recover.
        self.attach(baseline=data is None)
        if data is None:
            await self.save()
        self.unsubs.append(async_track_state_change_event(self.hass,
            [self.entry.options["time_entity"], self.entry.options["volume_entity"]], self.capture))
        self.unsubs.append(async_track_time_interval(self.hass, self.check_source, timedelta(seconds=5)))
        self.worker = self.hass.async_create_background_task(self.run(), f"{DOMAIN}-{self.entry.entry_id}")
        self.wake.set()

    @callback
    def attach(self, baseline=False):
        source = get_source(self.hass, self.entry.options)
        if source is self.source:
            return
        if self.source_unsub:
            self.source_unsub()
        self.source = source
        self.source_unsub = async_dispatcher_connect(self.hass, source.signal, self.capture)
        if baseline:
            for sip in tuple(source.state.sips):
                event = drink_event(sip.timestamp, float(sip.volume_ml), self.entry.data["bottle_serial"], self.clock)
                self.synced.append(event["event_id"])
        self.capture()

    @callback
    def check_source(self, now):
        try:
            self.attach()
        except ValueError:
            self.status, self.last_error = "error", "BLE source unavailable"
            self.changed()

    @callback
    def capture(self, event=None):
        if self.stopping:
            return
        # Snapshot immutable pairs in the callback, before another BLE notification.
        known = set(self.synced) | {e["event_id"] for e in self.pending + self.failed} | self.seen
        for sip in tuple(self.source.state.sips):
            try:
                item = drink_event(sip.timestamp, float(sip.volume_ml), self.entry.data["bottle_serial"], self.clock)
            except (ValueError, OverflowError):
                self.status, self.last_error = "error", "Invalid source sip"
                self.changed()
                continue
            if self.health_bridge:
                self.hass.async_create_task(self.health_bridge.async_enqueue(dict(item), self.entry))
            if item["event_id"] in known:
                continue
            self.clock += 1500
            self.seen.add(item["event_id"])
            self.pending.append(item)
            _LOGGER.debug("Queued sip %s: %s ml", item["event_id"], item["volume_ml"])
        self.wake.set()
        self.changed()

    async def run(self):
        attempts = 0
        while True:
            await self.wake.wait()
            self.wake.clear()
            try:
                await self.save()  # No network write before durable capture.
                while self.pending and not self.auth_failed:
                    self.pending.sort(key=lambda e: e["timestamp"])
                    item = self.pending[0]
                    self.status = "uploading"
                    self.changed()
                    try:
                        await self.client.async_upload_sip(item, self.save)
                    except AuthenticationError:
                        self.auth_failed = True
                        self.status = "authentication_error"
                        self.entry.async_start_reauth(self.hass)
                        break
                    except PermanentError as err:
                        self.pending.remove(item)
                        item["error"] = str(err)
                        self.failed.append(item)
                        self.last_error = str(err)
                        await self.save()
                        if self.health_bridge:
                            await self.health_bridge.async_update_cloud(item, "failed")
                        continue
                    except UncertainError as err:
                        self.pending.remove(item)
                        item["error"] = str(err)
                        self.failed.append(item)
                        self.last_error = str(err)
                        await self.save()
                        if self.health_bridge:
                            await self.health_bridge.async_update_cloud(item, "uncertain")
                        continue
                    except RetryError as err:
                        attempts += 1
                        self.status, self.last_error = "retrying", str(err)
                        self.changed()
                        # Unknown commits remain pending; query again but never repeat POST.
                        await asyncio.sleep(max(getattr(err, "retry_after", 30), min(300, 30 * 2 ** min(attempts - 1, 4))))
                        continue
                    self.pending.remove(item)
                    self.synced = (self.synced + [item["event_id"]])[-2000:]
                    self.seen.discard(item["event_id"])
                    self.last_success = datetime.now(timezone.utc).isoformat()
                    attempts = 0
                    self.last_error = None
                    await self.save()
                    if self.health_bridge:
                        await self.health_bridge.async_update_cloud(item, "synced")
                if not self.auth_failed:
                    self.status = "error" if self.failed else "idle"
                self.changed()
            except Exception:
                self.status, self.last_error = "error", "Queue processing failed; see persisted pending records"
                self.changed()
                await asyncio.sleep(30)
                self.wake.set()

    async def stop(self):
        self.stopping = True
        for unsub in self.unsubs:
            unsub()
        if self.source_unsub:
            self.source_unsub()
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        await self.save()
