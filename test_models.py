import urllib.request
import json
import ssl

ssl._create_default_https_context = ssl._create_unverified_context
url = "https://openrouter.ai/api/v1/models"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode('utf-8'))
    models = data['data']
    for m in models:
        name = m.get('id', '').lower()
        if 'qwen' in name and '397' in name:
            print(f"FOUND 397B MODEL: {m['id']}")
        elif 'qwen' in name and '72b' in name:
            print(f"FOUND 72B MODEL: {m['id']}")
        elif 'qwen' in name and '110' in name:
            print(f"FOUND 110B MODEL: {m['id']}")
        elif '397' in name:
            print(f"FOUND ANY 397 MODEL: {m['id']}")
