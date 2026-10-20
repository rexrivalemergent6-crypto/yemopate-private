import os
"""
Bifrost v1 — full mirror / passthrough proxy for Veil (veilit.xyz).

Routes (all under /api/bifrost/v1):
  /{target}/{path}     any HTTP method, target in TARGETS
  ws /{target}/{path}  websocket passthrough (Solana RPC subscriptions etc.)
  /_meta               upstream map + health

Every upstream absolute URL inside HTML / JS / CSS / JSON is rewritten to point
back at this proxy, so every button / fetch / RPC call of the original site is
routed through our backend -> Veil's real backends.
"""
import asyncio
import json
import logging
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import httpx
from bifrost_brand import head_inject, brand_asset, brand_icon_for
import websockets
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger("bifrost")

PREFIX = "/api/bifrost/v1"

TARGETS = {
    "site": "https://veilit.xyz",
    "app": "https://app.veilit.xyz",
    "indexer": "https://indexer.veilit.xyz",
    "relayer": "https://relayer.veilit.xyz",
    "rpc": "https://rpc.veilit.xyz",
}
# order matters: subdomains before apex
HOST_ORDER = ["app", "indexer", "relayer", "rpc", "site"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")

REWRITE_TYPES = ("text/html", "javascript", "text/css", "application/json",
                 "manifest+json", "text/plain", "image/svg+xml", "text/x-component")

DROP_RESP_HEADERS = {
    "content-encoding", "content-length", "transfer-encoding", "connection",
    "content-security-policy", "content-security-policy-report-only",
    "x-frame-options", "strict-transport-security", "alt-svc", "via",
    "access-control-allow-origin", "access-control-allow-credentials",
    "access-control-allow-methods", "access-control-allow-headers",
    "access-control-expose-headers", "keep-alive", "server",
    "cross-origin-opener-policy", "cross-origin-embedder-policy",
    "cross-origin-resource-policy", "permissions-policy", "report-to", "nel",
}
DROP_REQ_HEADERS = {
    "host", "content-length", "connection", "accept-encoding", "cookie",
    "origin", "referer", "x-forwarded-for", "x-forwarded-host",
    "x-forwarded-proto", "x-forwarded-port", "x-real-ip", "x-request-id",
    "x-scheme", "forwarded", "via", "cf-connecting-ip", "true-client-ip",
    "upgrade", "te", "keep-alive", "x-original-forwarded-for",
}

_client: Optional[httpx.AsyncClient] = None

# --------------------------------------------------------------------------
# tiny in-memory cache for immutable static assets (raw upstream bytes)
# --------------------------------------------------------------------------
_CACHE: "OrderedDict[str, tuple]" = OrderedDict()
_CACHE_BYTES = 0
_CACHE_MAX = 400 * 1024 * 1024
_CACHE_TTL = 6 * 3600


def _cacheable(target: str, path: str, method: str) -> bool:
    if method != "GET":
        return False
    p = "/" + path
    if target == "indexer" and p.startswith("/v1/logo/"):
        return True
    if target not in ("site", "app"):
        return False
    return (p.startswith("/_next/static/") or p.startswith("/circuits/")
            or p.startswith("/fonts/") or p.startswith("/vendor/")
            or bool(re.search(r"\.(webp|png|jpg|jpeg|svg|ico|woff2?|wasm|zkey|gif)$", p)))


def _cache_get(key):
    v = _CACHE.get(key)
    if not v:
        return None
    if time.time() - v[0] > _CACHE_TTL:
        _cache_del(key)
        return None
    _CACHE.move_to_end(key)
    return v


def _cache_del(key):
    global _CACHE_BYTES
    v = _CACHE.pop(key, None)
    if v:
        _CACHE_BYTES -= len(v[3])


def _cache_put(key, status, headers, body):
    global _CACHE_BYTES
    if len(body) > 120 * 1024 * 1024:
        return
    _cache_del(key)
    _CACHE[key] = (time.time(), status, headers, body)
    _CACHE_BYTES += len(body)
    while _CACHE_BYTES > _CACHE_MAX and _CACHE:
        k, _ = next(iter(_CACHE.items()))
        _cache_del(k)


IMG_HOSTS = ("api.houdiniswap.com", "cdn.jsdelivr.net", "raw.githubusercontent.com",
             "assets.coingecko.com", "coin-images.coingecko.com", "static.jup.ag", "arweave.net")


_IMG_MISS = {}
# Neutral placeholder coin served (200) instead of a 404 so missing token logos
# don't flood the browser console with failed image loads.
_PLACEHOLDER_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="0 0 64 64">'
                    b'<circle cx="32" cy="32" r="32" fill="#e6e8de"/>'
                    b'<circle cx="32" cy="32" r="13" fill="none" stroke="#b9bdae" stroke-width="5"/></svg>')


def _placeholder():
    return Response(_PLACEHOLDER_SVG, media_type="image/svg+xml",
                    headers={"cache-control": "public, max-age=3600", "x-placeholder": "1"})
_LOGO = {}  # path -> (ctype, bytes) downscaled token logos


def _shrink(body: bytes, size: int = 96):
    """Downscale a token logo to <=size px WebP (upstream logos can be ~1 MB each)."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(body))
        if getattr(im, "is_animated", False):
            im.seek(0)
        im = im.convert("RGBA")
        im.thumbnail((size, size), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "WEBP", quality=82, method=4)
        b = out.getvalue()
        return ("image/webp", b) if len(b) < len(body) else None
    except Exception:
        return None


# Logos also persist on disk so a restart never makes visitors wait for ~100 cold fetches.
_LOGO_DIR = Path(__file__).parent / ".cache" / "logos"
try:
    _LOGO_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def _logo_disk_key(path: str) -> Path:
    import hashlib
    return _LOGO_DIR / hashlib.sha1(path.encode()).hexdigest()


def _logo_disk_get(path: str):
    try:
        f = _logo_disk_key(path)
        if f.exists():
            raw = f.read_bytes()
            ctype, _, body = raw.partition(b"\n")
            return (ctype.decode(), body)
    except Exception:
        return None
    return None


def _logo_disk_put(path: str, entry):
    try:
        _logo_disk_key(path).write_bytes(entry[0].encode() + b"\n" + entry[1])
    except Exception:
        pass


async def _logo_small(path: str, request: Request):
    hit = _LOGO.get(path) or _logo_disk_get(path)
    if hit:
        _LOGO[path] = hit
        return Response(hit[1], media_type=hit[0], headers={"cache-control": "public, max-age=604800", "x-bifrost": "v1-hit"})
    url = f"{TARGETS['indexer']}/{path}"
    try:
        r = await get_client().get(url, headers={"user-agent": UA, "origin": TARGETS["app"], "referer": TARGETS["app"] + "/"})
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=502)
    if r.status_code != 200:
        return _placeholder()
    body, ctype = r.content, r.headers.get("content-type", "image/png")
    small = await asyncio.get_event_loop().run_in_executor(None, _shrink, body) if ctype.startswith("image/") and "svg" not in ctype else None
    entry = small or (ctype, body)
    if len(_LOGO) > 3000:
        _LOGO.clear()
    _LOGO[path] = entry
    _logo_disk_put(path, entry)
    return Response(entry[1], media_type=entry[0], headers={"cache-control": "public, max-age=604800"})


async def _idx_fetch(path: str, qs: str):
    url = f"{TARGETS['indexer']}/{path}" + (f"?{qs}" if qs else "")
    return await get_client().get(url, headers={"user-agent": UA, "origin": TARGETS["app"], "referer": TARGETS["app"] + "/"})


async def warm_logos():
    """Pre-fetch every pool token logo + the catalog once at startup (disk cache makes this cheap)."""
    await asyncio.sleep(3)
    try:
        r = await _idx_fetch("v1/assets", "")
        import json as _json
        mints = []
        for a in (_json.loads(r.content).get("assets") or []):
            m = a.get("mint") or a.get("address") or a.get("id")
            if isinstance(m, str):
                mints.append(m)
        sem = asyncio.Semaphore(6)

        class _R:  # minimal stand-in for Request
            pass

        async def one(m):
            async with sem:
                try:
                    await _logo_small(f"v1/logo/{m}", _R())
                except Exception:
                    pass
        await asyncio.gather(*(one(m) for m in mints[:400]))
        logger.info(f"bifrost: warmed {len(mints)} logos")
    except Exception as e:
        logger.warning(f"bifrost: logo warm failed: {e}")


async def _image_proxy(u: str):
    """Same-origin image relay for swap token icons (upstream CDNs are blocked cross-origin by ORB)."""
    try:
        from urllib.parse import urlparse
        pu = urlparse(u)
    except Exception:
        return JSONResponse({"error": "bad url"}, status_code=400)
    host = (pu.hostname or "").lower()
    if pu.scheme != "https" or not any(host == h or host.endswith("." + h) for h in IMG_HOSTS):
        return JSONResponse({"error": "host not allowed"}, status_code=400)
    miss = _IMG_MISS.get(u)
    if miss and time.time() - miss < 3600:
        return _placeholder()
    ckey = ("_img", u)
    hit = _cache_get(ckey)
    if hit:
        return Response(hit[3], media_type=hit[2].get("content-type", "image/png"),
                        headers={"cache-control": "public, max-age=86400"})
    try:
        r = await get_client().get(u, headers={"user-agent": UA, "accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8"})
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=502)
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not (ctype.startswith("image/") or ctype == "application/octet-stream"):
        _IMG_MISS[u] = time.time()
        return _placeholder()
    body = r.content
    if ctype == "application/octet-stream":
        ctype = "image/png"
    _cache_put(ckey, 200, {"content-type": ctype}, body)
    return Response(body, media_type=ctype, headers={"cache-control": "public, max-age=86400"})


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=False,
            http2=False,
            limits=httpx.Limits(max_keepalive_connections=40, max_connections=100,
                                keepalive_expiry=30),
        )
    return _client


async def close_client():
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()


# --------------------------------------------------------------------------
# URL rewriting
# --------------------------------------------------------------------------
def public_origin(request) -> str:
    h = request.headers
    host = h.get("x-forwarded-host") or h.get("host") or ""
    host = host.split(",")[0].strip()
    proto = (h.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    if not host.startswith(("localhost", "127.", "0.0.0.0")):
        proto = "https"
    return f"{proto}://{host}"


def _inject_script(target: str) -> str:
    return head_inject(target, PREFIX)


# Project token of the upstream (removed from the mirror; only swap tokens remain)
PROJECT_TOKEN_MINT = "HgTGdc29D5bDMrDvsCLnorxz899VyDj7ZN7FPa6vpump"
# valid base58 pubkey that is not a mint -> upstream code treats the project token as absent
PROJECT_TOKEN_STUB = "11111111111111111111111111111111"
# every upstream project token is hidden (only neutral swap tokens remain)
HIDDEN_MINTS = {PROJECT_TOKEN_MINT, "7KEPApdbBMByrmqihz3bht2uMhFQcatjfSFQCKq66kH3"}


def _strip_project_token(obj):
    """Recursively drop list entries that reference the project token mint."""
    def refs(o):
        if isinstance(o, str):
            return o in HIDDEN_MINTS
        if isinstance(o, dict):
            return any(refs(v) for v in o.values())
        if isinstance(o, list):
            return any(refs(v) for v in o)
        return False

    if isinstance(obj, list):
        return [_strip_project_token(v) for v in obj if not refs(v)]
    if isinstance(obj, dict):
        return {k: _strip_project_token(v) for k, v in obj.items() if k not in HIDDEN_MINTS}
    return obj


def strip_project_token_json(text: str) -> str:
    if not any(m in text for m in HIDDEN_MINTS):
        return text
    try:
        return json.dumps(_strip_project_token(json.loads(text)), separators=(",", ":"))
    except Exception:
        return text


def null_prefix_js(text: str) -> str:
    """Show NullCash prefixes (nullsk2…, nullvk1…, null2…) for keys/addresses.
    Same length as the upstream prefixes so all slicing/decoding stays intact;
    checksum domain strings ("veil/recovery", "veil/address") are untouched, and
    parsers accept BOTH prefixes so older keys/addresses keep working."""
    if "veil" not in text:
        return text
    # generators
    text = text.replace('"veilsk2"+', '"nullsk2"+').replace('"veilvk1"+', '"nullvk1"+').replace('"veil2"+', '"null2"+')
    # parsers: normalise null-prefixed input back to the upstream prefix before decoding
    text = re.sub(r'function\((\w)\)\{try\{let (\w)=\1\.startsWith\("veil2"\)',
                  r'function(\1){try{\1=String(\1).replace(/^null(?=[12])/,"veil");let \2=\1.startsWith("veil2")', text)
    text = re.sub(r'let (\w)=(\w)\.trim\(\)\.replace\(/\\s\+/g,""\);try\{if\(\1\.startsWith\("veilsk2"\)',
                  r'let \1=\2.trim().replace(/\\s+/g,"").replace(/^null(?=sk[12])/,"veil");try{if(\1.startsWith("veilsk2")', text)
    # validators / extractors
    text = text.replace(r'/^\s*veilsk[12]/', r'/^\s*(?:veil|null)sk[12]/')
    text = text.replace('/^veil[12]/', '/^(?:veil|null)[12]/')
    text = text.replace('/veil[12][1-9A-HJ-NP-Za-km-z]', '/(?:veil|null)[12][1-9A-HJ-NP-Za-km-z]')
    # human-readable hints / placeholders
    text = re.sub(r'veil(sk[12]|vk1|[12])…', r'null\1…', text)
    text = text.replace('starts with veilsk2', 'starts with nullsk2')
    return text

CHUNK_VER = "nc2"
_CHUNK_REF_RE = re.compile(r'(static/chunks/[A-Za-z0-9_-]+)\.js(?![A-Za-z0-9_.])')
_CHUNK_ALIAS_RE = re.compile(r'\.nc\d+\.js$')
_NC_RETRY = ("(window.__ncR||(window.__ncR=function(f,n,d){return Promise.race([Promise.resolve().then(f),"
             "new Promise(function(z){setTimeout(function(){z(null)},15000)})]).catch(function(){return null})"
             ".then(function(r){return r==null&&n>0?new Promise(function(z){setTimeout(z,d)})"
             ".then(function(){return window.__ncR(f,n-1,d)}):r})}))")
_SEND_PLAN_RE = re.compile(r'(\w+)\.planSend\((\w+)\.key,(\w+)\)\.then\((\w+)=>\{(\w+)&&(\w+)\(\4\)\}\)')
_TRADE_QUOTE_RE = re.compile(r'(\w+)\.quote\((\w+)\.key,(\w+)\.key,(\w+)\)\.then\((\w+)\)\.finally\(\(\)=>(\w+)\(!1\)\)')


def app_js_fixes(text: str) -> str:
    """Stop the in-app Send / Swap screens from hanging on a spinner.
    - Send: planSend() returns null when the vault has no notes to cover the amount,
      which left the button on "Working out the fee..." forever. If the private
      balance can't cover it, resolve a zero-fee plan so the UI says
      "Not enough private <TOKEN>"; otherwise retry the planner (2x, 1.5s apart).
    - Swap: a relayer quote that hangs kept "quoting..." on screen forever. Each try
      is capped at 15s and a null/failed quote is retried twice before giving up."""
    if "planSend" in text:
        def _send(m):
            st, tk, amt, r, alive, setter = m.groups()
            return (f"((({st}.priv[{tk}.key]??0n)<{amt})?Promise.resolve({{fee:0n,feeKey:{tk}.key,notes:1}}):"
                    f"{_NC_RETRY}(()=>{st}.planSend({tk}.key,{amt}),2,1500)).then({r}=>{{{alive}&&{setter}({r})}})")
        text = _SEND_PLAN_RE.sub(_send, text)
    if ".quote(" in text:
        def _quote(m):
            st, a, b, amt, setter, busy = m.groups()
            return (f"{_NC_RETRY}(()=>{st}.quote({a}.key,{b}.key,{amt}),2,1200)"
                    f".then({setter}).finally(()=>{busy}(!1))")
        text = _TRADE_QUOTE_RE.sub(_quote, text)
    return text



def rewrite_text(text: str, target: str, origin: str, ctype: str) -> str:
    base = f"{origin}{PREFIX}"
    if target in ("indexer", "relayer") and "json" in ctype:
        text = strip_project_token_json(text)
    if target == "app" and "javascript" in ctype:
        text = text.replace(f'"{PROJECT_TOKEN_MINT}"', f'"{PROJECT_TOKEN_STUB}"')
    for t in HOST_ORDER:
        up = TARGETS[t]
        host = up.split("://", 1)[1]
        text = text.replace(up, f"{base}/{t}")
        text = text.replace("wss://" + host, base.replace("https://", "wss://", 1) + f"/{t}")
        # JSON-escaped variants (Next flight data)
        text = text.replace(up.replace("/", "\\/"), f"{base}/{t}".replace("/", "\\/"))
    p = f"{PREFIX}/{target}"
    if target == "app":
        # versioned chunk names so CDN/browser caches never serve a pre-fix chunk
        text = _CHUNK_REF_RE.sub(rf"\1.{CHUNK_VER}.js", text)
    if target in ("site", "app"):
        if "text/html" in ctype or "text/x-component" in ctype:
            # root-relative attribute urls
            text = re.sub(r'((?:href|src|action|poster|data-src|content)=["\'])/(?!/|api/bifrost/)',
                          rf"\1{p}/", text)
            text = re.sub(r'(srcset=["\'])/(?!/|api/bifrost/)', rf"\1{p}/", text)
            text = re.sub(r'(,\s*)/(?=_next/)', rf"\1{p}/", text)
            text = re.sub(r'url\((["\']?)/(?!/|api/bifrost/)', rf"url(\1{p}/", text)
            text = text.replace("'veil-theme'", "'cb-theme'").replace('"veil-theme"', '"cb-theme"')
            if "text/html" in ctype:
                text = text.replace(f"{p}/veil-mark.webp", f"{PREFIX}/_brand/mark.png")
                text = text.replace("Pulling the veil over", "Pulling the nullcash over")
                text = re.sub(r"(?<![A-Za-z$])Veil(?![A-Za-z])", "NullCash", text)
                # document-relative urls (page URL is shown as /shield/... so resolve them here)
                text = re.sub(r'((?:\s)(?:src|poster|srcset)=["\'])(?![/#?"\']|[a-zA-Z][a-zA-Z0-9+.-]*:)',
                              rf"\1{p}/", text)
                text = re.sub(r'(<link\b[^>]*?\shref=["\'])(?![/#?"\']|[a-zA-Z][a-zA-Z0-9+.-]*:)',
                              rf"\1{p}/", text)
            # next flight / json payload references
            text = re.sub(r'(?<![A-Za-z0-9_/.])/_next/', f"{p}/_next/", text)
            if "text/html" in ctype:
                inj = _inject_script(target)
                if "<head>" in text:
                    text = text.replace("<head>", "<head>" + inj, 1)
                elif re.search(r"<head[^>]*>", text):
                    text = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + inj, text, count=1)
                else:
                    text = inj + text
        elif "css" in ctype:
            text = re.sub(r'url\((["\']?)/(?!/|api/bifrost/)', rf"url(\1{p}/", text)
        elif "javascript" in ctype:
            text = text.replace('"/circuits"', f'"{p}/circuits"')
            text = text.replace("'/circuits'", f"'{p}/circuits'")
            text = text.replace('TURBOPACK_CHUNK_BASE_PATH:"/_next/"', f'TURBOPACK_CHUNK_BASE_PATH:"{p}/_next/"')
            if target == "app":
                text = null_prefix_js(text)
                text = app_js_fixes(text)
        elif "json" in ctype:
            text = re.sub(r'("(?:src|start_url|scope|url|href)"\s*:\s*")/(?!/|api/bifrost/)',
                          rf"\1{p}/", text)
    return text


def _rewrite_location(loc: str, target: str, origin: str) -> str:
    for t in HOST_ORDER:
        if loc.startswith(TARGETS[t]):
            return f"{origin}{PREFIX}/{t}" + loc[len(TARGETS[t]):]
    if loc.startswith("/") and not loc.startswith("//"):
        return f"{PREFIX}/{target}{loc}"
    return loc


def _resp_headers(up_headers, target: str, origin: str) -> dict:
    out = {}
    for k, v in up_headers.multi_items() if hasattr(up_headers, "multi_items") else up_headers.items():
        lk = k.lower()
        if lk in DROP_RESP_HEADERS:
            continue
        if lk == "location":
            v = _rewrite_location(v, target, origin)
        if lk == "set-cookie":
            v = re.sub(r";\s*domain=[^;]+", "", v, flags=re.I)
            continue  # cookies are not needed by the upstream SPA; drop for safety
        out[k] = v
    return out


# --------------------------------------------------------------------------
# router
# --------------------------------------------------------------------------
router = APIRouter(prefix="/api/bifrost/v1")


@router.get("/_meta")
async def meta():
    return {"proxy": "bifrost", "version": "v1", "targets": TARGETS,
            "cache_items": len(_CACHE), "cache_bytes": _CACHE_BYTES}


@router.get("/_live")
async def live_status():
    return JSONResponse({"relay": "on", "every_s": LIVE_RELAY_EVERY, "ok": _LIVE["ok"], "errors": _LIVE["err"],
                         "age_s": round(time.time() - _LIVE["at"], 1) if _LIVE["at"] else None},
                        headers={"cache-control": "no-store"})


@router.get("/_health")
async def health():
    out = {}
    c = get_client()

    async def ping(name, url, method="GET", **kw):
        t0 = time.time()
        try:
            r = await c.request(method, url, headers={"user-agent": UA}, **kw)
            out[name] = {"status": r.status_code, "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa
            out[name] = {"status": 0, "error": str(e)[:120]}

    await asyncio.gather(
        ping("site", TARGETS["site"] + "/"),
        ping("app", TARGETS["app"] + "/"),
        ping("indexer", TARGETS["indexer"] + "/"),
        ping("relayer", TARGETS["relayer"] + "/"),
        ping("rpc", TARGETS["rpc"], method="POST",
             json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"}),
    )
    return out


@router.api_route("/{target}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_root(target: str, request: Request):
    if target in ("site", "app") and request.method == "GET":
        q = ("?" + request.url.query) if request.url.query else ""
        return Response(status_code=307, headers={"location": f"{PREFIX}/{target}/{q}"})
    return await proxy(target, "", request)


@router.api_route("/{target}/{path:path}",
                  methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(target: str, path: str, request: Request):
    if target == "_img":
        u = request.query_params.get("u", "")
        k = request.query_params.get("k", "")
        o = request.query_params.get("o", "")
        if o:
            u = img_key_decode(o)
        elif k:
            try:
                import base64 as _b
                u = _b.urlsafe_b64decode(k + "=" * (-len(k) % 4)).decode()
            except Exception:
                u = ""
        return await _image_proxy(u)
    if target == "indexer" and path.startswith("v1/logo/") and request.method == "GET":
        return await _logo_small(path, request)
    if target == "_brand":
        asset = brand_asset(path)
        if not asset:
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(asset[0], media_type=asset[1],
                        headers={"cache-control": "no-cache"})
    upstream = TARGETS.get(target)
    if not upstream:
        return JSONResponse({"error": "unknown bifrost target", "targets": list(TARGETS)},
                            status_code=404)
    if target == "app" and request.method in ("GET", "HEAD") and path.strip("/") == "api":
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/docs", status_code=302)
    if target in ("site", "app") and request.method in ("GET", "HEAD"):
        icon = brand_icon_for(path)
        if icon:
            asset = brand_asset(icon)
            if asset:
                return Response(asset[0], media_type=asset[1],
                                headers={"cache-control": "public, max-age=300"})
    method = request.method
    origin = public_origin(request)
    if path.startswith("cdn-cgi/"):
        return Response(status_code=204)

    if method == "OPTIONS" and target not in ("site", "app"):
        return Response(status_code=204, headers={
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
            "access-control-allow-headers": "*",
            "access-control-max-age": "86400",
        })

    if target == "app" and path.startswith("_next/static/"):
        path = _CHUNK_ALIAS_RE.sub(".js", path)  # cache-busted alias -> real upstream chunk
    url = f"{upstream}/{path}"
    if request.url.query:
        url += "?" + request.url.query

    fwd = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQ_HEADERS}
    fwd["user-agent"] = request.headers.get("user-agent") or UA
    page_origin = TARGETS["app"] if target != "site" else TARGETS["site"]
    fwd["origin"] = page_origin
    ref = request.headers.get("referer")
    if ref and PREFIX in ref:
        tail = ref.split(PREFIX, 1)[1]
        seg = tail.strip("/").split("/", 1)
        if seg and seg[0] in TARGETS:
            fwd["referer"] = TARGETS[seg[0]] + "/" + (seg[1] if len(seg) > 1 else "")
    else:
        fwd["referer"] = page_origin + "/"
    fwd["accept-encoding"] = "gzip, deflate"

    body = await request.body() if method not in ("GET", "HEAD") else None

    ckey = url
    use_cache = _cacheable(target, path, method)
    if use_cache:
        hit = _cache_get(ckey)
        if hit:
            _, status, headers, raw = hit
            return _build_response(status, headers, raw, target, origin, cached=True, path=path)

    if _micro_ok(target, path, method):
        now = time.time()
        m = _MICRO.get(url)
        if m and now - m[0] < _MICRO_TTL:
            return _build_response(m[1], m[2], m[3], target, origin, cached=True)
        fut = _INFLIGHT.get(url)
        if fut is not None:
            try:
                st, hd, rw = await asyncio.wait_for(asyncio.shield(fut), 20)
                return _build_response(st, hd, rw, target, origin, cached=True)
            except Exception:
                pass
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        _INFLIGHT[url] = fut
        try:
            r = await get_client().get(url, headers=fwd)
            if "text/html" in r.headers.get("content-type", ""):
                await asyncio.sleep(0.8)
                r = await get_client().get(url, headers=fwd)
            raw = r.content
            hd = dict(r.headers)
            if "text/html" in hd.get("content-type", ""):
                stale = _MICRO.get(url)
                if stale:
                    fut.set_result((stale[1], stale[2], stale[3]))
                    return _build_response(stale[1], stale[2], stale[3], target, origin, cached=True)
                fut.set_result((503, {"content-type": "application/json"}, b'{"error":"Upstream is busy. Please retry in a few seconds.","busy":true}'))
                return JSONResponse({"error": "Upstream is busy. Please retry in a few seconds.", "busy": True}, status_code=503,
                                    headers={"access-control-allow-origin": "*", "retry-after": "3"})
            if r.status_code == 200:
                _MICRO[url] = (time.time(), r.status_code, hd, raw)
                if len(_MICRO) > 2000:
                    for k in list(_MICRO)[:500]:
                        _MICRO.pop(k, None)
            fut.set_result((r.status_code, hd, raw))
            return _build_response(r.status_code, hd, raw, target, origin)
        except Exception as e:
            if not fut.done():
                fut.set_exception(e)
            return JSONResponse({"error": "bifrost upstream unreachable", "detail": str(e)[:200]}, status_code=502)
        finally:
            _INFLIGHT.pop(url, None)
            if fut.done() and fut.exception() if fut.done() and not fut.cancelled() else False:
                pass

    client = get_client()
    attempts = 3 if method in ("GET", "HEAD") else 2
    if target == "relayer" and method == "GET" and path.startswith(("v1/quote", "v1/fee")):
        attempts = 4  # relayer's shared Jupiter quota 429s in bursts; ride it out
        qm = _QUOTE_MICRO.get(url)
        if qm and time.time() - qm[0] < 3.0:
            return _build_response(qm[1], qm[2], qm[3], target, origin, cached=True)
    last_err = None
    for i in range(attempts):
        try:
            if attempts == 4:  # relayer quote/fee: fail fast so the UI never hangs
                req = client.build_request(method, url, headers=fwd, content=body,
                                           timeout=httpx.Timeout(10.0, connect=6.0))
            else:
                req = client.build_request(method, url, headers=fwd, content=body)
            r = await client.send(req, stream=True)
            ctype = r.headers.get("content-type", "")
            big_binary = (not any(t in ctype for t in REWRITE_TYPES)) and not use_cache
            if big_binary:
                headers = _resp_headers(r.headers, target, origin)
                if target not in ("site", "app"):
                    headers["access-control-allow-origin"] = "*"

                async def gen(resp=r):
                    try:
                        async for chunk in resp.aiter_raw():
                            yield chunk
                    finally:
                        await resp.aclose()
                # aiter_raw yields encoded bytes; keep upstream encoding header then
                ce = r.headers.get("content-encoding")
                if ce:
                    headers["content-encoding"] = ce
                return StreamingResponse(gen(), status_code=r.status_code, headers=headers)
            raw = await r.aread()
            await r.aclose()
            if target not in ("site", "app") and ("text/html" in ctype or r.headers.get("cf-mitigated") == "challenge"):
                logger.warning(f"bifrost upstream challenge {target}/{path} (attempt {i + 1})")
                if i < attempts - 1:
                    await asyncio.sleep(0.6 * (2 ** i))
                    continue
                return JSONResponse({"error": "Upstream is busy. Please retry in a few seconds.", "busy": True},
                                    status_code=503, headers={"access-control-allow-origin": "*", "retry-after": "3"})
            if r.status_code in (502, 503, 504, 429) and i < attempts - 1:
                rate_limited = b"429" in raw[:200] or r.status_code == 429
                # Never blindly re-send a POST (relay/submit) unless upstream clearly
                # rejected it before doing any work (rate limit / busy).
                if method in ("GET", "HEAD") or rate_limited or r.status_code == 503:
                    await asyncio.sleep((0.9 if rate_limited else 0.4) * (2 ** i))
                    continue
            if use_cache and r.status_code == 200:
                _cache_put(ckey, r.status_code, dict(r.headers), raw)
            if attempts == 4 and r.status_code == 200:
                _QUOTE_MICRO[url] = (time.time(), r.status_code, dict(r.headers), raw)
                if len(_QUOTE_MICRO) > 1000:
                    for k in list(_QUOTE_MICRO)[:300]:
                        _QUOTE_MICRO.pop(k, None)
            return _build_response(r.status_code, r.headers, raw, target, origin, path=path)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last_err = e
            await asyncio.sleep(0.4 * (2 ** i))
    logger.error(f"bifrost {method} {url} failed: {last_err}")
    return JSONResponse({"error": "bifrost upstream unreachable", "detail": str(last_err)[:200]},
                        status_code=502)


_RCACHE: "OrderedDict" = None  # rendered (rewritten) bodies for immutable assets
_RCACHE_BYTES = 0
_RCACHE_MAX = 300 * 1024 * 1024
_MICRO = {}          # url -> (ts, status, headers, raw) short-TTL cache for live JSON
_MICRO_TTL = 4.0
_INFLIGHT = {}       # url -> asyncio.Future (singleflight)
_QUOTE_MICRO = {}    # relayer quote/fee url -> (ts, status, headers, raw), 3s
MICRO_PATHS = ("v1/stats", "v1/root", "v1/feed", "v1/assets", "v1/info", "v1/health",
               "v1/bridge/routes", "v1/positions", "v1/flows", "v1/nullifiers")


LIVE_RELAY_PATHS = ("v1/stats", "v1/feed?limit=60", "v1/feed?limit=20")
LIVE_RELAY_EVERY = 3.0
_LIVE = {"ok": 0, "err": 0, "at": 0.0}


async def live_relay():
    """Constant relay: keep the shared indexer snapshot (pool stats + activity feed) fresh every few
    seconds so every visitor always reads the newest on-chain state instantly, even under load."""
    await asyncio.sleep(2)
    up = TARGETS["indexer"]
    while True:
        for path in LIVE_RELAY_PATHS:
            url = f"{up}/{path}"
            try:
                r = await get_client().get(url, headers={"user-agent": UA, "accept": "application/json",
                                                         "origin": TARGETS["app"], "referer": TARGETS["app"] + "/"})
                if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                    _MICRO[url] = (time.time(), 200, dict(r.headers), r.content)
                    _LIVE["ok"] += 1
                    _LIVE["at"] = time.time()
                else:
                    _LIVE["err"] += 1
            except Exception:
                _LIVE["err"] += 1
        await asyncio.sleep(LIVE_RELAY_EVERY)


def _micro_ok(target, path, method):
    return method == "GET" and target in ("indexer", "relayer") and path.startswith(MICRO_PATHS)


def _immutable(path: str) -> bool:
    return path.startswith(("_next/static/", "circuits/", "wallets/", "icons/", "fonts/")) or \
        path.endswith((".webp", ".png", ".svg", ".woff2", ".ico", ".jpg"))


def _build_response(status, up_headers, raw: bytes, target, origin, cached=False, path=""):
    global _RCACHE, _RCACHE_BYTES
    if cached and path and _immutable(path):
        from collections import OrderedDict
        if _RCACHE is None:
            _RCACHE = OrderedDict()
        rk = (target, path, origin)
        hit = _RCACHE.get(rk)
        if hit:
            _RCACHE.move_to_end(rk)
            return Response(content=hit[1], status_code=status, headers=dict(hit[0]))
        resp = _build_response(status, up_headers, raw, target, origin, cached=False, path=path)
        hdrs = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
        _RCACHE[rk] = (hdrs, resp.body)
        _RCACHE_BYTES += len(resp.body)
        while _RCACHE_BYTES > _RCACHE_MAX and _RCACHE:
            _, (h0, b0) = _RCACHE.popitem(last=False)
            _RCACHE_BYTES -= len(b0)
        return resp
    if isinstance(up_headers, dict):
        hdrs = httpx.Headers(up_headers)
    else:
        hdrs = up_headers
    ctype = hdrs.get("content-type", "")
    headers = _resp_headers(hdrs, target, origin)
    if any(t in ctype for t in REWRITE_TYPES):
        try:
            text = raw.decode("utf-8")
            text = rewrite_text(text, target, origin, ctype)
            raw = text.encode("utf-8")
        except UnicodeDecodeError:
            pass
    if "text/html" in ctype:
        headers["cache-control"] = "no-store"
    if target not in ("site", "app"):
        headers["access-control-allow-origin"] = "*"
    if path and _immutable(path) and status == 200 and target in ("site", "app"):
        headers["cache-control"] = "public, max-age=31536000, immutable" if path.startswith(("_next/static/", "circuits/")) \
            else "public, max-age=86400"
    elif target in ("indexer", "relayer") and status == 200 and "json" in ctype:
        headers["cache-control"] = "public, max-age=3"
    headers["x-bifrost"] = "v1" + ("-hit" if cached else "")
    return Response(content=raw, status_code=status, headers=headers)


# --------------------------------------------------------------------------
# websocket passthrough
# --------------------------------------------------------------------------
@router.websocket("/{target}")
async def ws_proxy_root(ws: WebSocket, target: str):
    await ws_proxy(ws, target, "")


@router.websocket("/{target}/{path:path}")
async def ws_proxy(ws: WebSocket, target: str, path: str):
    upstream = TARGETS.get(target)
    if not upstream:
        await ws.close(code=4404)
        return
    await ws.accept()
    up_url = upstream.replace("https://", "wss://", 1) + "/" + path
    if ws.url.query:
        up_url += "?" + ws.url.query
    try:
        async with websockets.connect(up_url, origin=TARGETS["app"], user_agent_header=UA,
                                      max_size=None, open_timeout=15) as up:
            async def c2u():
                try:
                    while True:
                        msg = await ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            break
                        if msg.get("text") is not None:
                            await up.send(msg["text"])
                        elif msg.get("bytes") is not None:
                            await up.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass
                finally:
                    await up.close()

            async def u2c():
                async for m in up:
                    if isinstance(m, (bytes, bytearray)):
                        await ws.send_bytes(m)
                    else:
                        await ws.send_text(m)

            await asyncio.gather(c2u(), u2c(), return_exceptions=True)
    except Exception as e:  # noqa
        logger.warning(f"bifrost ws {up_url} error: {e}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


_IMG_URL_RE = re.compile(r'https://(?:api\.houdiniswap\.com|cdn\.jsdelivr\.net|raw\.githubusercontent\.com|assets\.coingecko\.com|coin-images\.coingecko\.com)/[^"\\<>]+')


def _img_stream(n: int) -> bytes:
    import hashlib
    seed = (os.environ.get("IMG_RELAY_SALT") or "nullcash-img").encode()
    out, i = b"", 0
    while len(out) < n:
        out += hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
        i += 1
    return out[:n]


def img_key_encode(u: str) -> str:
    import base64 as _b
    raw = u.encode()
    x = bytes(a ^ b for a, b in zip(raw, _img_stream(len(raw))))
    return _b.urlsafe_b64encode(x).decode().rstrip("=")


def img_key_decode(k: str) -> str:
    import base64 as _b
    try:
        x = _b.urlsafe_b64decode(k + "=" * (-len(k) % 4))
        return bytes(a ^ b for a, b in zip(x, _img_stream(len(x)))).decode()
    except Exception:
        return ""


def opaque_image_urls(text: str) -> str:
    """Replace third-party image URLs with opaque same-origin relay keys (no upstream host visible)."""
    def _sub(m):
        u = m.group(0).rstrip().replace(" ", "%20")  # some upstream icon names contain spaces
        return f"{PREFIX}/_img?o={img_key_encode(u)}"
    return _IMG_URL_RE.sub(_sub, text)
