import time
import logging
from typing import Optional, Dict
from core.auth.storage import SecureStorage
# Note: OpenAIOAuthProvider will be imported from providers when needed to avoid circular imports
# or we can define the auth logic here and use it in providers.

logger = logging.getLogger(__name__)

class AuthManager:
    """Manages OAuth authentication for multiple providers"""
    
    def __init__(self, storage_path: str = "~/.atlas/auth.json"):
        self.storage = SecureStorage(path=storage_path)
    
    async def authenticate_openai_oauth(self) -> bool:
        """Interactive OAuth flow for OpenAI"""
        from core.providers.openai_oauth import OpenAIOAuthProvider
        
        print("🔐 Authenticating with OpenAI (ChatGPT Plus/Pro)")
        print("A browser window will open. Please log in with your ChatGPT account.")
        
        oauth = OpenAIOAuthProvider()
        
        try:
            tokens = oauth.authenticate()
            
            # Store securely
            self.storage.save('openai_oauth', {
                'access_token': tokens['access_token'],
                'refresh_token': tokens['refresh_token'],
                'expires_at': int(time.time()) + tokens['expires_in'],
                'id_token': tokens.get('id_token')
            })
            
            print("✅ Successfully authenticated with ChatGPT!")
            return True
            
        except Exception as e:
            print(f"❌ Authentication failed: {e}")
            logger.error(f"OpenAI Authentication failed: {e}")
            return False
    
    def get_provider_token(self, provider_name: str) -> Optional[str]:
        """Get valid access token for a provider, auto-refreshing if needed"""
        data = self.storage.load(provider_name)
        if not data:
            return None
        
        # Check if token is expired or near expiration (within 5 minutes)
        if time.time() > data['expires_at'] - 300:
            if provider_name == 'openai_oauth':
                from core.providers.openai_oauth import OpenAIOAuthProvider
                
                logger.info(f"Refreshing {provider_name} token...")
                oauth = OpenAIOAuthProvider()
                oauth.tokens = {
                    'refresh_token': data['refresh_token'],
                    'access_token': data['access_token']
                }
                
                try:
                    new_tokens = oauth.refresh_access_token()
                    
                    # Update storage
                    data['access_token'] = new_tokens['access_token']
                    data['expires_at'] = int(time.time()) + new_tokens['expires_in']
                    if 'refresh_token' in new_tokens:
                        data['refresh_token'] = new_tokens['refresh_token']
                    
                    self.storage.save(provider_name, data)
                except Exception as e:
                    logger.error(f"Failed to refresh {provider_name} token: {e}")
                    return None
            else:
                logger.warning(f"No refresh logic implemented for provider: {provider_name}")
        
        return data['access_token']

    def is_authenticated(self, provider_name: str) -> bool:
        """Check if we have credentials for the provider"""
        return self.storage.load(provider_name) is not None
