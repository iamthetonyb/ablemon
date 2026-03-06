import os, asyncio
import aiohttp
import ssl

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

original_client_session_init = aiohttp.ClientSession.__init__
def new_client_session_init(self, *args, **kwargs):
    kwargs['connector'] = aiohttp.TCPConnector(ssl=ssl_context)
    original_client_session_init(self, *args, **kwargs)
aiohttp.ClientSession.__init__ = new_client_session_init

from atlas.core.providers.base import Message, Role
from atlas.core.providers.openrouter import OpenRouterProvider

async def test_api():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    provider = OpenRouterProvider(api_key=api_key, model="qwen/qwen3.5-397b-a17b", timeout=60)
    
    msg = "Write a python script that prints hello world 50 times in different ways."
    
    try:
        print("Testing long generation on AtlasCloud...")
        res = await provider.complete(
            messages=[Message(role=Role.USER, content=msg)],
            max_tokens=16384,
            provider={"order": ["AtlasCloud"], "allow_fallbacks": True, "data_collection": "deny"},
            models=["qwen/qwen3.5-397b-a17b"]
        )
        print(f"SUCCESS. Length: {len(res.content)}")
        print(f"Response: {res.content[:200]}...")
    except Exception as e:
        print(f"FAILED: {repr(e)}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/Users/abenton333/Desktop/ATLAS/atlas/.env")
    asyncio.run(test_api())
