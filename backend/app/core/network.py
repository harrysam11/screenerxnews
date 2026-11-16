import httpx
from typing import Optional
from app.core.config import settings

class OfflineModeBlockedException(Exception):
    """
    Raised whenever an outbound network request (HTTP, RPC, WebSocket)
    is attempted while the system is operating in offline mode.
    Guarantees that no network sockets are opened.
    """
    pass

class OfflineEnforcingAsyncTransport(httpx.AsyncBaseTransport):
    """
    Centralized HTTP transport enforcing deterministic offline network isolation.
    Intercepts all requests at the transport boundary before socket creation.
    """
    def __init__(self, inner_transport: Optional[httpx.AsyncBaseTransport] = None):
        self.inner_transport = inner_transport or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if settings.is_offline or settings.OPERATING_MODE == "offline_research":
            # Attempt to record audit event without breaking on circular import
            try:
                from app.services.provider_manager import provider_manager
                provider_manager._record_audit_event(
                    "OFFLINE_BLOCKED",
                    details={"url": str(request.url), "method": request.method}
                )
            except Exception:
                pass
            raise OfflineModeBlockedException(
                f"Outbound network request blocked in offline mode: {request.method} {request.url}"
            )
        return await self.inner_transport.handle_async_request(request)

    async def aclose(self):
        if hasattr(self.inner_transport, "aclose"):
            await self.inner_transport.aclose()

def get_http_client(timeout: float = 10.0, **kwargs) -> httpx.AsyncClient:
    """
    Factory returning an httpx.AsyncClient equipped with the OfflineEnforcingAsyncTransport.
    All external HTTP and JSON-RPC integrations must use this client.
    """
    transport = kwargs.pop("transport", None) or OfflineEnforcingAsyncTransport()
    return httpx.AsyncClient(transport=transport, timeout=timeout, **kwargs)

def assert_online_mode(target_url: str = ""):
    """
    Synchronous / pre-socket assertion ensuring the system is not in offline mode.
    """
    if settings.is_offline or settings.OPERATING_MODE == "offline_research":
        raise OfflineModeBlockedException(
            f"Outbound network call blocked in offline mode: {target_url}"
        )
