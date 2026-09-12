"""Async port of hidrate.py's verified production Parse protocol."""
from __future__ import annotations
import asyncio
import json
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
import aiohttp

BASE = "https://www.hidrateapp.com/parse"

class CloudError(Exception):
    """Sanitized error: never include response bodies or credentials."""

class AuthenticationError(CloudError):
    pass

class PermanentError(CloudError):
    pass

class RetryError(CloudError):
    def __init__(self, message="Cloud temporarily unavailable", retry_after=30):
        super().__init__(message)
        self.retry_after = retry_after

class UncertainError(CloudError):
    """A write may have committed: reconcile, never blindly repeat it."""

class HidrateSparkCloudClient:
    def __init__(self, session, data):
        self.session, self.data = session, data
        self.token = data.get("session_token")
        self.user_id = data.get("user_id")

    def headers(self, rest=False, login=False):
        h = {"Content-Type": "application/json" if rest else "application/json; charset=utf-8",
             "Accept": "*/*", "Accept-Encoding": "gzip, deflate",
             "Accept-Language": "zh-CN,zh-Hans;q=0.9",
             "User-Agent": "Hidrate/6208.26033 CFNetwork/3896.100.1.2.1 Darwin/27.0.0",
             "X-Parse-Application-Id": "a5Il6d0n6WWkLQwBzlxvpF5P7PEkUYkX045CRgwM"}
        if rest:
            h.update({"X-Parse-Rest-Api-Key": "z8ZVqRq1sKuXCyAKXlW1ENsmAzMNR03JHwnfmgk7", "Priority": "u=3", "Connection": "keep-alive"})
        else:
            h.update({"X-Parse-Client-Version": "i4.1.2", "X-Parse-Client-Key": "mWasknCNtr9dSQGPwUBWb5u4Ilf8Qkeqkwz9Q4eL",
                      "X-Parse-Installation-Id": self.data["installation_id"], "X-Parse-Os-Version": "27.0 (24A435)",
                      "X-Parse-Request-Id": str(uuid.uuid4()).upper(), "Priority": "u=3, i",
                      "X-Parse-App-Display-Version": "4.9.0", "X-Parse-App-Build-Version": "6208.26033"})
        if login:
            h["X-Parse-Revocable-Session"] = "1"
        elif self.token:
            h["X-Parse-Session-Token"] = self.token
        return h

    async def request(self, path, body, *, rest=False, login=False, method="POST", retry=True):
        try:
            async with self.session.request(method, BASE + path, headers=self.headers(rest, login),
                    data=json.dumps(body, ensure_ascii=False).replace("/", "\\/"),
                    timeout=aiohttp.ClientTimeout(total=30)) as response:
                status = response.status
                try:
                    result = await response.json(content_type=None)
                except (ValueError, aiohttp.ClientError):
                    raise RetryError("Invalid cloud response") from None
                delay = response.headers.get("Retry-After", "30")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise RetryError() from None
        error = result[0].get("error", {}) if isinstance(result, list) and result and isinstance(result[0], dict) else result
        code = error.get("code") if isinstance(error, dict) else None
        if status in (401, 403) or code in (209, 101):
            if not login and retry:
                await self.async_authenticate()
                return await self.request(path, body, rest=rest, method=method, retry=False)
            raise AuthenticationError("Authentication failed")
        if status == 429:
            try:
                seconds = float(delay)
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(delay) - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError):
                    seconds = 30
            raise RetryError("Rate limited", max(1, seconds))
        if status >= 500:
            raise RetryError()
        if status >= 400 or (isinstance(error, dict) and "error" in error):
            raise PermanentError(f"Cloud rejected request ({code or status})")
        return result

    async def async_authenticate(self):
        result = await self.request("/login", {"username": self.data["username"], "password": self.data["password"]}, login=True)
        if not isinstance(result, dict) or not result.get("sessionToken") or not result.get("objectId"):
            raise AuthenticationError("Invalid login response")
        self.token, self.user_id = result["sessionToken"], result["objectId"]

    def pointer(self, cls, object_id):
        return {"className": cls, "__type": "Pointer", "objectId": object_id}

    async def query(self, cls, where):
        result = await self.request(f"/classes/{cls}", {"_method": "GET", "where": where, "limit": "100"})
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            raise RetryError("Invalid query response")
        return result["results"]

    async def async_upload_sip(self, event, save):
        """Persist write intent before POST; recover uncertain writes by clientSipId."""
        if not self.token:
            await self.async_authenticate()
        user = self.pointer("_User", self.user_id)
        rows = await self.query("Sip", {"user": user, "clientSipId": event["event_id"]})
        if rows:
            event["sip_id"] = rows[0]["objectId"]
            event["day_id"] = rows[0].get("day", {}).get("objectId") or event.get("day_id")
        if event.get("attempted") and not event.get("sip_id"):
            raise UncertainError("Upload outcome unknown; manual review required")
        if not event.get("day_id"):
            date = datetime.fromisoformat(event["timestamp"]).astimezone(ZoneInfo(self.data["time_zone"])).strftime("%Y-%m-%d")
            days = await self.query("Day", {"user": user, "date": {"$in": [date]}})
            day = days[0] if days else await self.request("/classes/Day", {"user": user, "date": date, "hidrateDateString": date})
            if not isinstance(day, dict) or not day.get("objectId"):
                raise RetryError("Day creation not confirmed")
            event["day_id"] = day["objectId"]
        if not event.get("sip_id"):
            timestamp = datetime.fromisoformat(event["timestamp"]).astimezone(timezone.utc)
            stop = event["clock"]
            sip = {"time": {"iso": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"), "__type": "Date"},
                   "max": stop + 1000, "day": self.pointer("Day", event["day_id"]), "start": stop + 80,
                   "hydrationImpact": 1, "clientSipId": event["event_id"], "bottleSerialNumber": self.data["bottle_serial"],
                   "amount": float(event["volume_ml"]), "user": user,
                   "liquidTypeInfo": self.pointer("LiquidTypeInfo", "fOXhTEpCFI"), "min": stop - 70,
                   "healthKitUUIDString": event["health_id"], "stop": stop, "timeZone": self.data["time_zone"]}
            event["attempted"] = True
            await save()
            try:
                result = await self.request("/batch", {"requests": [{"method": "POST", "path": "/parse/classes/Sip", "body": sip}]}, rest=True)
            except (AuthenticationError, PermanentError) :
                event["attempted"] = False
                await save()
                raise
            except RetryError as err:
                if str(err) == "Rate limited":
                    event["attempted"] = False
                    await save()
                raise
            if not isinstance(result, list) or not result or not result[0].get("success", {}).get("objectId"):
                raise UncertainError("Sip save not confirmed")
            event["sip_id"] = result[0]["success"]["objectId"]
            await save()
        result = await self.request("/functions/calculatedaytotal", {"dayId": event["day_id"]})
        if not isinstance(result, dict) or "result" not in result:
            raise RetryError("Day recalculation not confirmed")
