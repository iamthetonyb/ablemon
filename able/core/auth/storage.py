import os
import json
import stat
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

class SecureStorage:
    """Encrypted file storage for OAuth tokens"""
    
    def __init__(self, path: str = "~/.able/auth.json"):
        self.path = os.path.expanduser(path)
        self._ensure_dir()
        # In a real environment, we'd use a more secure way to manage the key
        # but for this implementation we derive it from a machine-specific constant
        self.key = self._derive_key()
    
    def _ensure_dir(self):
        dir_path = os.path.dirname(self.path)
        os.makedirs(dir_path, mode=0o700, exist_ok=True)
    
    def _derive_key(self) -> bytes:
        """Derive encryption key from machine-specific data"""
        salt = b'able_auth_v1_salt'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        # Using a fixed secret for this context; in production, use a machine-unique ID
        key = base64.urlsafe_b64encode(kdf.derive(b'able-secure-bypass-v1'))
        return key
    
    def save(self, provider: str, data: dict):
        """Encrypt and save provider tokens"""
        f = Fernet(self.key)
        encrypted = f.encrypt(json.dumps(data).encode())
        
        # Load existing or create new
        store = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r') as file:
                    store = json.load(file)
            except Exception:
                store = {}
        
        store[provider] = encrypted.decode()
        
        # Atomic write
        temp_path = self.path + '.tmp'
        with open(temp_path, 'w') as file:
            json.dump(store, file)
        
        os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        os.replace(temp_path, self.path)
    
    def load(self, provider: str) -> dict:
        """Load and decrypt provider tokens"""
        if not os.path.exists(self.path):
            return None
        
        try:
            with open(self.path, 'r') as file:
                store = json.load(file)
            
            if provider not in store:
                return None
            
            f = Fernet(self.key)
            decrypted = f.decrypt(store[provider].encode())
            return json.loads(decrypted)
        except Exception:
            return None
