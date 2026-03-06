import asyncio
import os
import json
import logging
import sys
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

from atlas.core.providers.base import Message, Role, ToolCall
from atlas.core.providers.openrouter import OpenRouterProvider

logging.basicConfig(level=logging.INFO)

async def test_openrouter():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Set OPENROUTER_API_KEY")
        return

    provider = OpenRouterProvider(api_key=api_key, model="qwen/qwen3.5-397b-a17b", timeout=30)
    
    # Simulate a loop:
    # 1. User says "list repos"
    # 2. Assistant calls "github_list_repos"
    # 3. Tool returns the repos
    
    messages = [
        Message(role=Role.SYSTEM, content="You are ATLAS..."),
        Message(role=Role.ASSISTANT, content="⚠️ Security check failed: HIGH threat detected - suspicious patterns require human review"),
        Message(role=Role.USER, content="Using my GitHub repo ;\nInside—\niamthetonyb/atlas-mission-control\n\nbuild the following:\nWork on building the atlas dashboard..."),
        Message(role=Role.USER, content="- Clean, well-organized code with comments for each section\n- CSS variables for all colors/spacing (easy to re-theme)\n- Smooth transitions on all interactive elements\n- Keyboard shortcut: Cmd/Ctrl+K to focus search\n\nBuild the complete file. Make it production quality — this should look like a premium SaaS dashboard, not a hobby project."),
        Message(role=Role.ASSISTANT, content="", tool_calls=[
            ToolCall(id="call_73b2f8de2a3c4e33b93fd179", name="github_list_repos", arguments={})
        ]),
        Message(role=Role.TOOL, content="**Your repositories:**\n• AIDE — public", tool_call_id="call_73b2f8de2a3c4e33b93fd179", name="github_list_repos")
    ]
    
    try:
        print("Sending request...")
        res = await provider.complete(
            messages=messages,
            max_tokens=16384,
            temperature=0.60,
            top_p=0.95,
            top_k=20,
            presence_penalty=0,
            repetition_penalty=1,
            provider={
                "order": ["OpenRouter"],
                "allow_fallbacks": True,
                "require_parameters": True,
                "data_collection": "deny"
            },
            models=["qwen/qwen-2.5-72b-instruct"],
            route="fallback",
            extra_body={
                "chat_template_kwargs": {"enable_thinking": True}
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "github_list_repos",
                        "description": "Lists repos",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                }
            ]
        )
        print("Success!", res.content)
    except Exception as e:
        print("FAILED:", repr(e))
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/Users/abenton333/Desktop/ATLAS/atlas/.env")
    asyncio.run(test_openrouter())
