#!/usr/bin/env python3
# Run: python3 scripts/atlas-auth.py
"""
ATLAS OpenAI OAuth — Connect your ChatGPT subscription.

Run this to authenticate ATLAS with your OpenAI account:
    python scripts/atlas-auth.py

What happens:
1. Opens your browser to OpenAI's login page
2. You log in with your ChatGPT Plus/Pro/Team account
3. Browser redirects back to localhost:1455
4. Token is encrypted and stored at ~/.atlas/auth.json
5. ATLAS can now route T1 (Nano) and T2 (GPT 5.4) through your subscription

To check status:    python scripts/atlas-auth.py --status
To re-authenticate: python scripts/atlas-auth.py --force
"""

import sys
import os
import asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'atlas'))

def check_status():
    """Check if we're authenticated."""
    from core.auth.manager import AuthManager
    mgr = AuthManager()
    if mgr.is_authenticated('openai_oauth'):
        data = mgr.storage.load('openai_oauth')
        import time
        expires_in = data['expires_at'] - int(time.time())
        if expires_in > 0:
            mins = expires_in // 60
            print(f"AUTHENTICATED — Token valid for {mins} minutes")
            print(f"Stored at: ~/.atlas/auth.json (encrypted)")
        else:
            print("TOKEN EXPIRED — Refresh will happen automatically on next request")
            print("Or re-authenticate: python scripts/atlas-auth.py --force")
    else:
        print("NOT AUTHENTICATED")
        print("Run: python scripts/atlas-auth.py")
    return mgr.is_authenticated('openai_oauth')

async def authenticate(force=False):
    """Run the OAuth flow."""
    from core.auth.manager import AuthManager
    mgr = AuthManager()

    if not force and mgr.is_authenticated('openai_oauth'):
        print("Already authenticated. Use --force to re-authenticate.")
        check_status()
        return True

    print("=" * 60)
    print("ATLAS — Connect Your OpenAI Account")
    print("=" * 60)
    print()
    print("This will open your browser to sign in with OpenAI.")
    print("Log in with the account that has your ChatGPT subscription.")
    print()
    print("After login, the browser will redirect to localhost:1455")
    print("and ATLAS will capture the auth token automatically.")
    print()
    print("Waiting for browser authentication (5 min timeout)...")
    print()

    result = await mgr.authenticate_openai_oauth()

    if result:
        print()
        print("=" * 60)
        print("SUCCESS — ATLAS is now connected to your OpenAI account")
        print("=" * 60)
        print()
        print("Routing config:")
        print("  T1: GPT 5.4 Nano  (via your subscription)")
        print("  T2: GPT 5.4       (via your subscription)")
        print()
        print("Token stored at: ~/.atlas/auth.json (encrypted)")
        print("Tokens auto-refresh — you shouldn't need to do this again.")
    else:
        print()
        print("FAILED — Check the error above and try again.")
        print("Make sure you have an active ChatGPT subscription.")

    return result

def main():
    args = sys.argv[1:]

    if '--status' in args:
        check_status()
        return

    force = '--force' in args
    asyncio.run(authenticate(force=force))

if __name__ == '__main__':
    main()
