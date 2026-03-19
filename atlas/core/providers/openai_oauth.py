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

from core.providers.base import (
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
    ORIGINATOR: str = "atlas"

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
            <p>You can close this window and return to ATLAS.</p>
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
    """OpenAI OAuth provider for ATLAS (PKCE)"""
    
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
    """ATLAS-compatible provider using ChatGPT OAuth (Subscription BYOK)"""
    
    def __init__(self, config: ProviderConfig, auth_manager=None):
        super().__init__(config)
        from core.auth.manager import AuthManager
        self.auth_manager = auth_manager or AuthManager()
        self.base_url = "https://chatgpt.com/backend-api/wham"
        
    @property
    def name(self) -> str:
        return "openai_oauth"
    
    async def complete(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs
    ) -> CompletionResult:
        token = self.auth_manager.get_provider_token('openai_oauth')
        if not token:
            raise ProviderError(self.name, "Not authenticated with OpenAI OAuth", retryable=False)
            
        # Simplified WHAM API format
        payload = {
            "model": self.model or "gpt-4o",
            "messages": [
                {
                    "role": m.role.value,
                    "content": m.content if isinstance(m.content, str) else json.dumps(m.content)
                } for m in messages
            ],
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    f"{self.base_url}/responses",
                    json=payload,
                    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                    timeout=60
                )
            )
            response.raise_for_status()
            data = response.json()
            
            # Note: This is a placeholder for the actual WHAM backend response parsing
            content = data.get('output', [{}])[0].get('content', [{}])[0].get('text', '')
            
            return CompletionResult(
                content=content,
                finish_reason="stop",
                usage=UsageStats(0, 0, 0), # OAuth doesn't report traditional token usage usually
                provider=self.name,
                model=self.model,
                raw_response=data
            )
        except Exception as e:
            raise ProviderError(self.name, str(e))

    async def stream(self, messages: List[Message], **kwargs) -> AsyncIterator[str]:
        # Basic streaming fallback: generate then yield
        # Actually implementing WHAM streaming requires SSE handling
        result = await self.complete(messages, **kwargs)
        yield result.content

    def count_tokens(self, text: str) -> int:
        return len(text) // 4
