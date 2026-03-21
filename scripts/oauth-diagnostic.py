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

print("\nAll OAuth checks passed")
