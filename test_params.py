import os, asyncio
import urllib.request
import json
import ssl
import aiohttp

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

original_client_session_init = aiohttp.ClientSession.__init__
def new_client_session_init(self, *args, **kwargs):
    kwargs['connector'] = aiohttp.TCPConnector(ssl=ssl_context)
    original_client_session_init(self, *args, **kwargs)
aiohttp.ClientSession.__init__ = new_client_session_init

from able.core.providers.base import Message, Role
from able.core.providers.openrouter import OpenRouterProvider

async def test_api():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    provider = OpenRouterProvider(api_key=api_key, model="qwen/qwen3.5-397b-a17b", timeout=10)
    
    # Let's isolate which parameter combination breaks AtlasCloud
    configs = [
        # Base config
        {
            "name": "Base AtlasCloud",
            "provider": {"order": ["AtlasCloud"], "allow_fallbacks": False},
            "extra_body": None
        },
        # With data_collection=deny
        {
            "name": "AtlasCloud + DataDeny",
            "provider": {"order": ["AtlasCloud"], "allow_fallbacks": False, "data_collection": "deny"},
            "extra_body": None
        },
        # With require_parameters=True
        {
            "name": "AtlasCloud + RequireParams",
            "provider": {"order": ["AtlasCloud"], "allow_fallbacks": False, "require_parameters": True},
            "extra_body": None
        },
        # With enable_thinking
        {
            "name": "AtlasCloud + EnableThinking",
            "provider": {"order": ["AtlasCloud"], "allow_fallbacks": False},
            "extra_body": {"chat_template_kwargs": {"enable_thinking": True}}
        },
        # The EXACT failing config from gateway.py
        {
            "name": "EXACT Gateway Config",
            "provider": {"order": ["AtlasCloud"], "allow_fallbacks": False, "require_parameters": True, "data_collection": "deny"},
            "extra_body": {"chat_template_kwargs": {"enable_thinking": True}}
        }
    ]
    
    for cfg in configs:
        try:
            print(f"\\n[{cfg['name']}] Testing...")
            res = await provider.complete(
                messages=[Message(role=Role.USER, content="Hello")],
                max_tokens=10,
                provider=cfg["provider"],
                models=["qwen/qwen3.5-397b-a17b"],
                **({"extra_body": cfg["extra_body"]} if cfg["extra_body"] else {})
            )
            print(f"SUCCESS")
        except Exception as e:
            print(f"FAILED: {repr(e)}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/Users/abenton333/Desktop/ABLE/able/.env")
    asyncio.run(test_api())
