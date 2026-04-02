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
    provider = OpenRouterProvider(api_key=api_key, model="qwen/qwen-2.5-72b-instruct", timeout=10)
    
    # Try different configurations to see what OpenRouter accepts for 1M context
    configs = [
        # Try native 397B without strict provider
        {
            "models": ["qwen/qwen3.5-397b-a17b"],
            "provider": {"allow_fallbacks": True}
        },
        # Try 397B with AtlasCloud
        {
            "models": ["qwen/qwen3.5-397b-a17b"],
            "provider": {"order": ["AtlasCloud"], "allow_fallbacks": False}
        },
        # Try YaRN config on Qwen 2.5 72B
        {
            "models": ["qwen/qwen-2.5-72b-instruct"],
            "provider": {"allow_fallbacks": True}
        },
        # User requested AtlasCloud/FP8, let's see if that's a model routing preference
        {
            "models": ["qwen/qwen-2.5-72b-instruct"],
            "provider": {"order": ["AtlasCloud"], "allow_fallbacks": False}
        }
    ]
    
    for cfg in configs:
        try:
            print(f"\\nTesting: {cfg}")
            res = await provider.complete(
                messages=[Message(role=Role.USER, content="Hello")],
                max_tokens=10,
                provider=cfg["provider"],
                models=cfg["models"]
            )
            print(f"SUCCESS: {res.content}")
        except Exception as e:
            print(f"FAILED: {repr(e)}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/Users/abenton333/Desktop/ABLE/able/.env")
    asyncio.run(test_api())
