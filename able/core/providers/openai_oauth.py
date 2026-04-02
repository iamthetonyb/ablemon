import secrets
import hashlib
import base64
import urllib.parse
import webbrowser
import http.server
import socketserver
import threading
import requests
import json
import logging
import asyncio
from typing import Optional, Dict, List, AsyncIterator, Union, Any
from dataclasses import dataclass

from able.core.providers.base import (
    LLMProvider,
    ProviderConfig,
    ProviderError,
    Message,
    CompletionResult,
    Role,
    UsageStats,
    ToolCall
)

logger = logging.getLogger(__name__)

@dataclass
class OpenAIOAuthConfig:
    """Configuration for OpenAI OAuth PKCE flow"""
    # OpenAI Codex OAuth endpoints (public client - no secret needed)
    AUTH_URL: str = "https://auth.openai.com/oauth/authorize"
    TOKEN_URL: str = "https://auth.openai.com/oauth/token"
    BASE_URL: str = "https://chatgpt.com/backend-api/wham"
    CLIENT_ID: str = "app_EMoamEEZ73f0CkXaXp7hrann"  # Codex CLI public client ID
    REDIRECT_URI: str = "http://localhost:1455/auth/callback"
    SCOPE: str = "openid profile email offline_access"
    
    # OpenAI-specific required parameters
    ID_TOKEN_ADD_ORGS: str = "true"
    CODEX_CLI_SIMPLIFIED_FLOW: str = "true"
    ORIGINATOR: str = "able"

class PKCEManager:
    """Handles PKCE code generation and verification"""
    
    @staticmethod
    def generate_code_verifier() -> str:
        return base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode('utf-8').rstrip('=')
    
    @staticmethod
    def generate_code_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode('utf-8')).digest()
        return base64.urlsafe_b64encode(digest).decode('utf-8').rstrip('=')

class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback"""
    
    auth_code: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None
    server_instance: Optional[socketserver.TCPServer] = None
    
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        
        if 'code' in params:
            OAuthCallbackHandler.auth_code = params['code'][0]
            OAuthCallbackHandler.state = params.get('state', [None])[0]
            self._send_response(200, "Authorization successful! You can close this window.")
        elif 'error' in params:
            OAuthCallbackHandler.error = params['error'][0]
            self._send_response(400, f"Authorization failed: {params['error'][0]}")
        else:
            self._send_response(400, "Invalid callback")
        
        if OAuthCallbackHandler.server_instance:
            threading.Thread(target=self._shutdown).start()
    
    def _send_response(self, status: int, message: str):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        html = f"""
        <html>
        <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #050508; color: white;">
            <h1 style="color: #D4AF37;">{message}</h1>
            <p>You can close this window and return to ABLE.</p>
        </body>
        </html>
        """
        self.wfile.write(html.encode('utf-8'))
    
    def _shutdown(self):
        import time
        time.sleep(0.5)
        OAuthCallbackHandler.server_instance.shutdown()
    
    def log_message(self, format, *args):
        pass

class OpenAIOAuthProvider:
    """OpenAI OAuth provider for ABLE (PKCE)"""
    
    def __init__(self, config: OpenAIOAuthConfig = None):
        self.config = config or OpenAIOAuthConfig()
        self.pkce = PKCEManager()
        self.tokens: Dict[str, Any] = {}
        self.code_verifier: Optional[str] = None
        
    def get_authorization_url(self) -> str:
        self.code_verifier = self.pkce.generate_code_verifier()
        code_challenge = self.pkce.generate_code_challenge(self.code_verifier)
        state = secrets.token_urlsafe(32)
        
        params = {
            'response_type': 'code',
            'client_id': self.config.CLIENT_ID,
            'redirect_uri': self.config.REDIRECT_URI,
            'scope': self.config.SCOPE,
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
            'id_token_add_organizations': self.config.ID_TOKEN_ADD_ORGS,
            'codex_cli_simplified_flow': self.config.CODEX_CLI_SIMPLIFIED_FLOW,
            'originator': self.config.ORIGINATOR
        }
        
        return f"{self.config.AUTH_URL}?{urllib.parse.urlencode(params)}"
    
    def authenticate(self, timeout: int = 300) -> Dict:
        OAuthCallbackHandler.auth_code = None
        OAuthCallbackHandler.error = None
        
        port = int(urllib.parse.urlparse(self.config.REDIRECT_URI).port or 1455)
        server = socketserver.TCPServer(('localhost', port), OAuthCallbackHandler)
        OAuthCallbackHandler.server_instance = server
        
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        
        auth_url = self.get_authorization_url()
        webbrowser.open(auth_url)
        
        server_thread.join(timeout=timeout)
        
        if OAuthCallbackHandler.error:
            raise Exception(f"OAuth error: {OAuthCallbackHandler.error}")
        
        if not OAuthCallbackHandler.auth_code:
            raise Exception("Authentication timed out")
        
        return self.exchange_code(OAuthCallbackHandler.auth_code)
    
    def exchange_code(self, code: str) -> Dict:
        payload = {
            'grant_type': 'authorization_code',
            'client_id': self.config.CLIENT_ID,
            'code': code,
            'redirect_uri': self.config.REDIRECT_URI,
            'code_verifier': self.code_verifier
        }
        
        response = requests.post(self.config.TOKEN_URL, data=payload, timeout=30)
        response.raise_for_status()
        self.tokens = response.json()
        return self.tokens
    
    def refresh_access_token(self) -> Dict:
        if 'refresh_token' not in self.tokens:
            raise Exception("No refresh token available")
        
        payload = {
            'grant_type': 'refresh_token',
            'client_id': self.config.CLIENT_ID,
            'refresh_token': self.tokens['refresh_token']
        }
        
        response = requests.post(self.config.TOKEN_URL, data=payload, timeout=30)
        response.raise_for_status()
        new_tokens = response.json()
        if 'refresh_token' not in new_tokens:
            new_tokens['refresh_token'] = self.tokens['refresh_token']
        self.tokens = new_tokens
        return self.tokens

class OpenAIChatGPTProvider(LLMProvider):
    """ABLE-compatible provider using ChatGPT OAuth (Subscription BYOK).

    Routes through the WHAM backend (chatgpt.com/backend-api/wham).
    WHAM requires: stream=true, store=false, instructions field.
    Available models: gpt-5.4, gpt-5.4-mini (NOT nano, o-series, or gpt-4o).

    Supports reasoning.effort: none, minimal, low, medium, high, xhigh.
    """

    VALID_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}

    def __init__(self, config: ProviderConfig, auth_manager=None, reasoning_effort: str = "none"):
        super().__init__(config)
        from core.auth.manager import AuthManager
        self.auth_manager = auth_manager or AuthManager()
        self.base_url = "https://chatgpt.com/backend-api/wham"
        self.reasoning_effort = reasoning_effort if reasoning_effort in self.VALID_EFFORTS else "none"

    @property
    def name(self) -> str:
        return "openai_oauth"

    def _build_payload(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> Dict:
        """Build WHAM-compatible Responses API payload."""
        effort = reasoning_effort or self.reasoning_effort
        if effort not in self.VALID_EFFORTS:
            effort = self.reasoning_effort

        # Separate system messages into instructions, rest into input
        system_parts = []
        input_msgs = []
        for m in messages:
            content = m.content if isinstance(m.content, str) else json.dumps(m.content)
            if m.role == Role.SYSTEM:
                system_parts.append(content)
            else:
                input_msgs.append({"role": m.role.value, "content": content})

        payload = {
            "model": self.model or "gpt-5.4",
            "instructions": instructions or "\n".join(system_parts) or "You are a helpful assistant.",
            "input": input_msgs,
            "stream": True,   # WHAM requires streaming
            "store": False,   # WHAM requires store=false
        }

        if effort and effort != "none":
            payload["reasoning"] = {"effort": effort}

        # temperature not supported when reasoning is active
        if effort in ("none", "minimal", "") or not effort:
            payload["temperature"] = temperature

        # Note: WHAM does not support max_output_tokens — omit it
        if tools:
            # Convert Chat Completions tool format to Responses API format.
            # Chat Completions nests under "function" key; Responses API is flat.
            converted = []
            for tool in tools:
                if tool.get("type") == "function" and "function" in tool:
                    fn = tool["function"]
                    converted.append({
                        "type": "function",
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    })
                else:
                    converted.append(tool)
            payload["tools"] = converted
        if tool_choice:
            payload["tool_choice"] = tool_choice

        return payload

    def _consume_sse(self, response) -> tuple:
        """Parse SSE stream from WHAM, return (content, usage_dict, tool_calls)."""
        full_text = ""
        usage = {}
        tool_calls = []
        # Accumulate function call argument deltas keyed by call_id
        _fc_args: Dict[str, str] = {}
        _fc_names: Dict[str, str] = {}

        for raw_line in response.iter_lines():
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                event = json.loads(data_str)
                etype = event.get("type", "")
                if etype == "response.output_text.delta":
                    full_text += event.get("delta", "")
                elif etype == "response.output_item.added":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        cid = item.get("call_id", "")
                        _fc_names[cid] = item.get("name", "")
                        _fc_args[cid] = ""
                elif etype == "response.function_call_arguments.delta":
                    cid = event.get("call_id", "")
                    _fc_args[cid] = _fc_args.get(cid, "") + event.get("delta", "")
                elif etype == "response.completed":
                    resp = event.get("response", {})
                    usage = resp.get("usage", {})
                    # Also extract tool calls from completed response output
                    for item in resp.get("output", []):
                        if item.get("type") == "function_call":
                            cid = item.get("call_id", item.get("id", ""))
                            name = item.get("name", _fc_names.get(cid, ""))
                            args = item.get("arguments", _fc_args.get(cid, "{}"))
                            tool_calls.append(ToolCall(
                                id=cid,
                                name=name,
                                arguments=args if isinstance(args, str) else json.dumps(args),
                            ))
            except (json.JSONDecodeError, KeyError):
                pass

        # If we got function calls from deltas but not from completed, build them
        if not tool_calls and _fc_names:
            for cid, name in _fc_names.items():
                tool_calls.append(ToolCall(
                    id=cid,
                    name=name,
                    arguments=_fc_args.get(cid, "{}"),
                ))

        return full_text, usage, tool_calls

    async def complete(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs
    ) -> CompletionResult:
        token = self.auth_manager.get_provider_token("openai_oauth")
        if not token:
            raise ProviderError(self.name, "Not authenticated with OpenAI OAuth", retryable=False)

        effort = kwargs.pop("reasoning_effort", None)
        instructions = kwargs.pop("instructions", None)
        payload = self._build_payload(
            messages, temperature, max_tokens, tools, tool_choice, effort, instructions
        )

        timeout = kwargs.pop("timeout", 180)

        try:
            loop = asyncio.get_event_loop()
            url = f"{self.base_url}/responses"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            logger.debug(f"WHAM request: POST {url} model={payload.get('model')} timeout={timeout}")
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                    stream=True,
                )
            )
            logger.debug(f"WHAM response: {response.status_code} headers={dict(list(response.headers.items())[:3])}")
            response.raise_for_status()

            content, usage_data, tool_calls = await loop.run_in_executor(
                None, lambda: self._consume_sse(response)
            )

            usage = UsageStats(
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )

            return CompletionResult(
                content=content,
                finish_reason="tool_calls" if tool_calls else "completed",
                usage=usage,
                provider=self.name,
                model=self.model,
                raw_response=usage_data,
                tool_calls=tool_calls or None,
            )
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            body = ""
            try:
                body = e.response.json().get("detail", "")
            except Exception:
                body = e.response.text[:200] if e.response else ""
            retryable = status in (429, 500, 502, 503)
            logger.error(f"WHAM HTTPError: status={status} body={body!r} has_response={e.response is not None}")
            raise ProviderError(self.name, f"HTTP {status}: {body}", retryable=retryable)
        except requests.exceptions.ConnectionError as e:
            logger.error(f"WHAM ConnectionError: {e}")
            raise ProviderError(self.name, f"Connection failed: {e}", retryable=True)
        except requests.exceptions.Timeout as e:
            logger.error(f"WHAM Timeout: {e}")
            raise ProviderError(self.name, f"Timeout: {e}", retryable=True)
        except Exception as e:
            logger.error(f"WHAM unexpected error: {type(e).__name__}: {e}", exc_info=True)
            raise ProviderError(self.name, f"{type(e).__name__}: {e}")

    async def stream(self, messages: List[Message], **kwargs) -> AsyncIterator[str]:
        """Stream tokens from WHAM SSE endpoint."""
        token = self.auth_manager.get_provider_token("openai_oauth")
        if not token:
            raise ProviderError(self.name, "Not authenticated with OpenAI OAuth", retryable=False)

        effort = kwargs.pop("reasoning_effort", None)
        instructions = kwargs.pop("instructions", None)
        payload = self._build_payload(messages, reasoning_effort=effort, instructions=instructions)
        timeout = kwargs.pop("timeout", 180)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(
                f"{self.base_url}/responses",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=timeout,
                stream=True,
            )
        )
        response.raise_for_status()

        for raw_line in response.iter_lines():
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                event = json.loads(data_str)
                if event.get("type") == "response.output_text.delta":
                    delta = event.get("delta", "")
                    if delta:
                        yield delta
            except (json.JSONDecodeError, KeyError):
                pass

    def count_tokens(self, text: str) -> int:
        return len(text) // 4
