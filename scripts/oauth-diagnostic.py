#!/usr/bin/env python3
"""Quick diagnostic to verify OAuth auth.json works on the server."""
import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'atlas'))
os.chdir(os.path.expanduser('~/.atlas'))

from core.auth.storage import SecureStorage
from core.auth.manager import AuthManager

auth_path = os.path.expanduser('~/.atlas/auth.json')

# 1. Can we read the file?
print(f"auth.json path: {auth_path}")
print(f"auth.json exists: {os.path.exists(auth_path)}")
if not os.path.exists(auth_path):
    print("FAIL: auth.json not found")
    sys.exit(1)

print(f"auth.json size: {os.path.getsize(auth_path)} bytes")

try:
    with open(auth_path) as f:
        raw = json.load(f)
    print(f"1. JSON parse: OK ({len(raw)} keys: {list(raw.keys())})")
except Exception as e:
    print(f"1. JSON parse: FAIL — {e}")
    sys.exit(1)

# 2. Can we decrypt?
s = SecureStorage(auth_path)
data = s.load('openai_oauth')
if data:
    print(f"2. Fernet decrypt: OK (keys: {list(data.keys())})")
    exp = data.get('expires_at', 0)
    now = time.time()
    status = "EXPIRED" if now > exp else "VALID"
    remaining = int(exp - now)
    print(f"   access_token expires_at: {exp} ({status}, {remaining}s)")
    print(f"   refresh_token present: {bool(data.get('refresh_token'))}")
    print(f"   access_token prefix: {data.get('access_token', '')[:30]}...")
else:
    print("2. Fernet decrypt: FAIL — returned None")
    sys.exit(1)

# 3. AuthManager check
am = AuthManager(auth_path)
authed = am.is_authenticated('openai_oauth')
print(f"3. is_authenticated: {authed}")

# 4. Token retrieval (triggers refresh if expired)
token = am.get_provider_token('openai_oauth')
if token:
    print(f"4. get_provider_token: OK (starts with {token[:30]}...)")
else:
    print("4. get_provider_token: FAIL — returned None (refresh failed)")
    sys.exit(1)

print("\nAll auth checks passed. Testing WHAM connectivity...")

# 5. Test actual WHAM API connectivity
import requests

wham_url = "https://chatgpt.com/backend-api/wham/responses"
test_payload = {
    "model": "gpt-5.4-mini",
    "instructions": "You are a test assistant.",
    "input": [{"role": "user", "content": "Say OK"}],
    "stream": True,
    "store": False,
    "reasoning": {"effort": "xhigh"},
}
print(f"   Payload includes reasoning.effort=xhigh (matches runtime config)")

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

print(f"5. WHAM URL: {wham_url}")
print(f"   Token prefix: {token[:40]}...")

try:
    resp = requests.post(
        wham_url,
        json=test_payload,
        headers=headers,
        timeout=30,
        stream=True,
    )
    print(f"   HTTP status: {resp.status_code}")
    print(f"   Response headers: {dict(list(resp.headers.items())[:5])}")
    if resp.status_code == 200:
        # Read first few SSE lines
        lines = []
        for raw_line in resp.iter_lines():
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            lines.append(line)
            if len(lines) >= 5:
                break
        print(f"   First SSE lines: {lines[:3]}")
        print("   WHAM API: OK")
    else:
        body = resp.text[:500]
        print(f"   Response body: {body}")
        print("   WHAM API: FAIL")
except requests.exceptions.ConnectionError as e:
    print(f"   Connection error: {e}")
    print("   WHAM API: BLOCKED (likely Cloudflare or IP block)")
except requests.exceptions.Timeout:
    print("   Timeout after 30s")
    print("   WHAM API: TIMEOUT")
except Exception as e:
    print(f"   Error: {type(e).__name__}: {e}")
    print("   WHAM API: FAIL")

# 6. DNS and basic connectivity check
import socket
print("\n6. Network diagnostics:")
try:
    ip = socket.getaddrinfo("chatgpt.com", 443)[0][4][0]
    print(f"   chatgpt.com resolves to: {ip}")
except Exception as e:
    print(f"   DNS resolution failed: {e}")

try:
    ip = socket.getaddrinfo("api.openai.com", 443)[0][4][0]
    print(f"   api.openai.com resolves to: {ip}")
except Exception as e:
    print(f"   DNS resolution failed: {e}")
