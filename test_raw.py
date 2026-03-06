import os, asyncio
import json
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

from atlas.core.gateway.gateway import ATLAS_TOOL_DEFS

async def test_api():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    
    payload = {
        "model": "qwen/qwen3.5-397b-a17b",
        "messages": [
            {"role": "system", "content": "You are ATLAS..."},
            {"role": "assistant", "content": "⚠️ Security check failed: HIGH threat detected - suspicious patterns require human review"},
            {"role": "user", "content": "Using my GitHub repo ;\nInside—\niamthetonyb/atlas-mission-control\n\nbuild the following:\nWork on building the atlas dashboard (for like the agent swarm/mixture of agents, following our auditing process, etc.) with an elegant interactive ui/ux but robust tracking app that I can also add client portal so they can monitor the agents work for their client processes but i want to be able to see all files you generate, research history of the relevant articles, sources, etc. everything transparent lots of data can generate from each token spent to be able to track everything. This can be built with whatever tech stack you think is best to help offload some of the context for tracking and long term memories in a visual way (almost like an obsidian  front end with the .md infinite drafting and management for the internal db plus the vector, rag, embeddings, semantic, graph, & Tiered memory systems like short-term, working, episodic, and semantic layers that mirror how human memory actually works and the app will help both the AI + users) so I can edit and interact with them or just have a full suite to help run our AGI system for me as ultimate viewer then a view for clients once I add them in. This system will be dynamic based off how you work and operate for instance if I do a new command about adding a client, clocking in or out etc. that system needs to be dynamically updated but in an efficient manner so tie it all in within your processing from end to end so I have a solid visual app to go with your skills and abilities that we can grow off of reliable yet cost effective.\n\nSample for Mission Control Dashboard —\nI want you to build me a Mission Control dashboard as a single HTML file. This is my personal command center for tracking my life and business.\n\nAbout Me:\n- Name: Tony B.\n- Business/Role: KingCRO — AI Expert\n- Main Goal: $100k/m by 2/11/2028\n- Key Metric: Net revenue\n\nTechnical Requirements:\n- Single self-contained HTML file (inline CSS + JS, no external dependencies except Google Fonts Inter)\n- Dark theme with glassmorphism: primary bg #050508, cards with backdrop-filter blur, subtle borders rgba(255,255,255,0.06)\n- Accent color: Shimmering Gold (use for highlights, active states, glows)\n- Font: Inter from Google Fonts\n- Fully responsive, smooth animations (fadeInUp on cards, pulse on status dot)\n- All data saved to localStorage so nothing is lost on refresh\n\nLayout:\n- Sticky frosted-glass header: logo/title left, tab navigation center, search bar + live status dot right\n- Tabs: 📊 Dashboard, 📋 Projects, 📅 Timeline, a lightweight CRM 📝 Tracking & Reporting/Memories/Notes\n- Main content max-width 1600px, centered, 2.5rem padding\n\nDashboard Tab:\n- Welcome bar: \"Good [morning/afternoon/evening], [NAME]\" with live date/time\n- 4 metric cards in a grid: each with colored top accent bar (3px), icon, label, value, and trend indicator\n  Cards: [GOAL_METRIC] progress, Active Projects count, Tasks Today count, Days to Goal countdown\n- Activity feed: scrollable list of recent items with timestamps (stored in localStorage)\n- Top Priorities section: editable list with checkboxes, add new priority button\n\nProjects Tab:\n- Kanban board with 3 columns: Backlog, In Progress, Done\n- Task cards: title, description preview, priority badge (high/medium/low with colors), created date\n- Add task button per column, click card to edit, delete option\n- All tasks persisted to localStorage\n\nTimeline Tab:\n- Visual roadmap with phases displayed vertically\n- Each phase: title, date range, description, list of milestones with completion checkmarks\n- Current phase highlighted with accent glow\n- Data defined in a JS config object (easy to edit)\n\nTracking + Memory/Notes Tab:\n- Obsidian like with large textarea with formatting saved in markdown\n- Auto-saves to localStorage on every keystroke\n- Character count, last saved timestamp\n- Clean minimal design\n- Open AI SDK like view of tracking every single action, data injected, etc.\n- Tie in the logic graphs for semantic ties, vectors etc.\n\nCode Quality:\n- Clean, well-organized code with comments for each section\n- CSS variables for all colors/spacing (easy to re-theme)\n- Smooth transitions on all interactive elements\n- Keyboard shortcut: Cmd/Ctrl+K to focus search\n\nBuild the complete file. Make it production quality — this should look like a premium SaaS dashboard, not a hobby project."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_73b2f8de2a3c4e33b93fd179", "type": "function", "function": {"name": "github_list_repos", "arguments": "{}"}}]},
            {"role": "tool", "content": "**Your repositories:**\n• [AIDE](https://github.com/iamthetonyb/AIDE) — 🌐 public\n• [atlas-mission-control](https://github.com/iamthetonyb/atlas-mission-control) — 🌐 public\n• [premium-saas-dashboard](https://github.com/iamthetonyb/premium-saas-dashboard) — 🌐 public", "tool_call_id": "call_73b2f8de2a3c4e33b93fd179", "name": "github_list_repos"}
        ],
        "max_tokens": 16384,
        "provider": {"order": ["AtlasCloud"], "allow_fallbacks": True, "data_collection": "deny"},
        "tools": ATLAS_TOOL_DEFS
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        print("Sending raw bypass request to OpenRouter...")
        async with aiohttp.ClientSession() as session:
            async with session.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload) as resp:
                text = await resp.text()
                text_stripped = text.strip()
                print(f"HTTP Status: {resp.status}")
                print(f"Stripped Length: {len(text_stripped)}")
                if len(text_stripped) > 1500:
                    print(f"RAW JSON RESPONSE END:\n...{text_stripped[-1500:]}")
                else:
                    print(f"RAW JSON RESPONSE:\n{text_stripped}")
    except Exception as e:
        print(f"FAILED: {repr(e)}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/Users/abenton333/Desktop/ATLAS/atlas/.env")
    asyncio.run(test_api())
