from fastapi import FastAPI, APIRouter, Request, Response
from dotenv import load_dotenv
import httpx
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import asyncio
import time
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List
import uuid
from bifrost import router as bifrost_router, close_client as bifrost_close, opaque_image_urls, warm_logos, live_relay
from datetime import datetime, timezone


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# Define Models
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Ignore MongoDB's _id field
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StatusCheckCreate(BaseModel):
    client_name: str

# Add your routes to the router instead of directly to app
@api_router.get("/")
async def root():
    return {"message": "Hello World"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)
    
    # Convert to dict and serialize datetime to ISO string for MongoDB
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    
    _ = await db.status_checks.insert_one(doc)
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    # Exclude MongoDB's _id field from the query results
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    
    # Convert ISO string timestamps back to datetime objects
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    
    return status_checks

# ---------------------------------------------------------------------------
# DarkSwap passthrough proxy (server-side -> avoids browser CORS / auth issues)
# Maps  /api/ds/<path>  ->  https://darkswap.app/api/swap/<path>
#
# Hardened for stability so the upstream does not rate-limit / block us:
#  * one shared keep-alive connection pool (no per-request socket churn)
#  * automatic retry with exponential backoff on 429 / 5xx / network errors
#  * short TTL cache for the near-static token & chain lists (cuts upstream hits)
# ---------------------------------------------------------------------------
DS_BASE = "https://darkswap.app/api/swap"
DS_HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://darkswap.app",
    "referer": "https://darkswap.app/swap",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
# Only these path prefixes are allowed to be proxied.
DS_ALLOWED = ("tokens", "chains", "quotes", "orders", "near")
RETRY_STATUS = {429, 500, 502, 503, 504}

# Shared async HTTP client with a bounded keep-alive pool (created lazily).
_ds_client: httpx.AsyncClient | None = None
# Tiny in-memory TTL cache for idempotent list endpoints: key -> (ts, status, body)
_ds_cache: dict = {}


def _get_ds_client() -> httpx.AsyncClient:
    global _ds_client
    if _ds_client is None or _ds_client.is_closed:
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=50,
                              keepalive_expiry=30.0)
        _ds_client = httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=10.0),
            limits=limits,
            headers=DS_HEADERS,
            follow_redirects=True,
            http2=False,
        )
    return _ds_client


def _ds_allowed(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "?")
               for p in DS_ALLOWED) or path.split("/")[0] in DS_ALLOWED


def _cache_ttl(path: str) -> int:
    """How long (seconds) a GET on this path may be cached. 0 = never cache."""
    base = path.split("?")[0]
    if base.endswith("tokens") or base == "tokens":
        return 60
    if base.endswith("chains") or base == "chains":
        return 300
    return 0  # quotes / orders / status must always be live


def _is_challenge(resp) -> bool:
    ct = resp.headers.get("content-type", "")
    if resp.headers.get("cf-mitigated") == "challenge":
        return True
    return "text/html" in ct or resp.content[:1] == b"<"


async def _ds_request(method: str, url: str, params=None, content=None, retries: int = 3):
    """Perform the upstream request with retry + exponential backoff."""
    global _ds_client
    last_exc = None
    resp = None
    for attempt in range(retries + 1):
        client = _get_ds_client()
        try:
            resp = await client.request(method, url, params=params, content=content)
            if _is_challenge(resp):
                logger.warning(f"ds upstream challenge on {url} (attempt {attempt + 1})")
                if attempt < retries:
                    await client.aclose()
                    _ds_client = None
                    await asyncio.sleep(0.6 * (2 ** attempt))
                    continue
                return httpx.Response(503, json={"error": "Upstream is busy. Please retry in a few seconds.", "busy": True})
            if resp.status_code in RETRY_STATUS and attempt < retries:
                await asyncio.sleep(0.4 * (2 ** attempt))
                continue
            return resp
        except httpx.HTTPError as e:
            last_exc = e
            if attempt < retries:
                await asyncio.sleep(0.4 * (2 ** attempt))
                continue
            raise
    if resp is not None:
        return resp
    raise last_exc


@api_router.get("/ds/_post/{path:path}")
async def ds_proxy_post_tunnel(path: str, request: Request):
    """GET tunnel for POSTs, used by the client only when an edge bot-check intercepts a POST."""
    import base64 as _b
    if not _ds_allowed(path):
        return Response(content='{"error":"path not allowed"}', status_code=403, media_type="application/json")
    b = request.query_params.get("b", "")
    try:
        body = _b.urlsafe_b64decode(b + "=" * (-len(b) % 4))
    except Exception:
        return Response(content='{"error":"bad body"}', status_code=400, media_type="application/json")
    try:
        r = await _ds_request("POST", f"{DS_BASE}/{path}", content=body)
        return Response(content=_scrub(r.content), status_code=r.status_code, media_type="application/json",
                        headers={"cache-control": "no-store"})
    except httpx.HTTPError as e:
        logger.error(f"ds tunnel POST {path} failed: {e}")
        return Response(content='{"error":"upstream unreachable"}', status_code=502, media_type="application/json")


@api_router.get("/ds/{path:path}")
async def ds_proxy_get(path: str, request: Request):
    if not _ds_allowed(path):
        return Response(content='{"error":"path not allowed"}', status_code=403,
                        media_type="application/json")
    params = dict(request.query_params)
    ttl = _cache_ttl(path)
    key = f"GET {path}?{sorted(params.items())}"
    now = time.time()
    if ttl and key in _ds_cache:
        ts, status, body = _ds_cache[key]
        if now - ts < ttl:
            return Response(content=body, status_code=status, media_type="application/json")
        # stale-while-revalidate: answer instantly with the last good copy, refresh in background
        if now - ts < ttl * 20:
            if key not in _ds_refreshing:
                _ds_refreshing.add(key)
                asyncio.create_task(_ds_refresh(path, params, key))
            return Response(content=body, status_code=status, media_type="application/json")
    url = f"{DS_BASE}/{path}"
    try:
        r = await _ds_request("GET", url, params=params)
    except httpx.HTTPError as e:
        logger.error(f"ds proxy GET {path} failed: {e}")
        # serve stale cache if we have any, rather than erroring out
        if key in _ds_cache:
            _, status, body = _ds_cache[key]
            return Response(content=body, status_code=status, media_type="application/json")
        return Response(content='{"error":"upstream unreachable"}', status_code=502,
                        media_type="application/json")
    if r.status_code == 503 and key in _ds_cache:
        _, status, body = _ds_cache[key]
        return Response(content=body, status_code=status, media_type="application/json")
    body = _scrub(r.content)
    if ttl and r.status_code == 200:
        _ds_cache[key] = (now, r.status_code, body)
    return Response(content=body, status_code=r.status_code,
                    media_type="application/json")


_ds_refreshing: set = set()


async def _ds_refresh(path: str, params: dict, key: str):
    """Background refresh of one cached list endpoint."""
    try:
        r = await _ds_request("GET", f"{DS_BASE}/{path}", params=params, retries=2)
        if r.status_code == 200:
            _ds_cache[key] = (time.time(), 200, _scrub(r.content))
    except Exception as e:  # keep serving the stale copy
        logger.warning(f"ds refresh {path} failed: {e}")
    finally:
        _ds_refreshing.discard(key)


# Lists every visitor needs first: keep them hot so the first load is instant.
_WARM = [("chains", {}), ("tokens", {"side": "source"}), ("tokens", {"side": "destination"}),
         ("near/tokens", {"side": "source"}), ("near/tokens", {"side": "destination"})]


async def _ds_warm_loop():
    await asyncio.sleep(2)
    while True:
        for path, params in _WARM:
            key = f"GET {path}?{sorted(params.items())}"
            ts = _ds_cache.get(key, (0,))[0]
            if time.time() - ts > _cache_ttl(path) * 0.8 and key not in _ds_refreshing:
                _ds_refreshing.add(key)
                await _ds_refresh(path, params, key)
        await asyncio.sleep(20)


@app.on_event("startup")
async def _start_warm():
    asyncio.create_task(_ds_warm_loop())
    asyncio.create_task(warm_logos())
    asyncio.create_task(live_relay())


def _scrub(body: bytes) -> bytes:
    """Hide upstream identity in JSON sent to the browser (image hosts -> opaque relay, display names)."""
    try:
        t = body.decode("utf-8")
    except Exception:
        return body
    t = opaque_image_urls(t)
    t = t.replace("DarkSwap", "NullCash").replace("darkswap.app", "nullcash")
    t = t.replace('"houdiniId"', '"orderId"').replace("HoudiniSwap", "NullCash").replace("houdiniswap", "nullcash")
    return t.encode("utf-8")


@api_router.post("/ds/{path:path}")
async def ds_proxy_post(path: str, request: Request):
    if not _ds_allowed(path):
        return Response(content='{"error":"path not allowed"}', status_code=403,
                        media_type="application/json")
    url = f"{DS_BASE}/{path}"
    body = await request.body()
    try:
        r = await _ds_request("POST", url, content=body)
        return Response(content=_scrub(r.content), status_code=r.status_code,
                        media_type="application/json")
    except httpx.HTTPError as e:
        logger.error(f"ds proxy POST {path} failed: {e}")
        return Response(content='{"error":"upstream unreachable"}', status_code=502,
                        media_type="application/json")


# Include the router in the main app
app.include_router(api_router)
app.include_router(bifrost_router)

class GatewayStatusMiddleware:
    """The public ingress replaces any 502/504 body with its own HTML error page,
    which destroys the upstream JSON error (e.g. relayer "jupiter 429", order
    validation errors) and breaks JSON parsing in the browser. Re-map those two
    statuses to 503 (passed through untouched) and keep the original code in a header."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def _send(msg):
            if msg["type"] == "http.response.start" and msg.get("status") in (502, 504):
                orig = msg["status"]
                msg = dict(msg)
                msg["status"] = 503
                msg["headers"] = list(msg.get("headers", [])) + [
                    (b"x-upstream-status", str(orig).encode()),
                    (b"retry-after", b"2"),
                ]
            await send(msg)

        return await self.app(scope, receive, _send)


app.add_middleware(GatewayStatusMiddleware)
# compress JSON/HTML/JS (the token catalog alone is ~380 KB raw)
app.add_middleware(GZipMiddleware, minimum_size=1400, compresslevel=5)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # live relay polls every 3s; keep logs readable

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
    await bifrost_close()
    global _ds_client
    if _ds_client is not None and not _ds_client.is_closed:
        await _ds_client.aclose()