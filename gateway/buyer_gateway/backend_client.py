"""Authenticated calls from the gateway to the Exchange backend's internal API (loopback + shared token)."""

from __future__ import annotations

import os

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=float(os.getenv("GATEWAY_BACKEND_TIMEOUT_S", "90")))


class BackendUnavailable(RuntimeError):
    pass


class BackendClient:
    def __init__(self, base_url: str, token: str):
        if not token:
            raise ValueError("GATEWAY_BACKEND_TOKEN is required")
        self.base_url = base_url.rstrip("/")
        self.headers = {"x-gateway-token": token, "content-type": "application/json"}

    async def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT, headers=self.headers) as session:
                async with session.request(method, f"{self.base_url}{path}", json=body) as resp:
                    if resp.status >= 400:
                        raise BackendUnavailable(f"HTTP {resp.status}")
                    return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BackendUnavailable(type(exc).__name__) from exc

    async def intent(self, envelope: dict) -> dict:
        return await self._call("POST", "/internal/chat/intent", envelope)

    async def outbox(self, gateway_address: str) -> list[dict]:
        return (await self._call("GET", f"/internal/chat/outbox?gateway={gateway_address}"))["items"]

    async def ack(self, outbox_id: int, status: str, detail: str = "") -> None:
        await self._call("POST", f"/internal/chat/outbox/{outbox_id}/ack", {"status": status, "detail": detail})

    async def payment_event(self, envelope: dict) -> dict:
        return await self._call("POST", "/internal/payment/event", envelope)
