import os, asyncio
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

async def main():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Set OPENROUTER_API_KEY")
        return
        
    provider = OpenRouterProvider(api_key=api_key, model="qwen/qwen3.5-397b-a17b", timeout=10)
    
    test_providers = ["AtlasCloud", "Atlas Cloud", "atlas-cloud", "AtlasCloud/FP8", "atlas-cloud/fp8", "AtlasCloud/fp8"]
    
    for p in test_providers:
        try:
            print(f"\\nTesting provider: {p}")
            res = await provider.complete(
                messages=[Message(role=Role.USER, content="Say 'foo'.")],
                max_tokens=5,
                provider={"order": [p], "allow_fallbacks": False},
                models=["qwen/qwen3.5-397b-a17b"]
            )
            print(f"SUCCESS with {p} -> {res.content}")
        except Exception as e:
            print(f"FAILED with {p}: {repr(e)}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/Users/abenton333/Desktop/ABLE/able/.env")
    asyncio.run(main())
