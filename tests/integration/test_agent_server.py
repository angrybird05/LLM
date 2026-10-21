import subprocess
import time
import sys
import urllib.request
import json
import os

# Set environment variables for the subprocess
env = os.environ.copy()
env['API_KEYS'] = 'sk-dev-key-1,sk-dev-key-2'
env['RATE_LIMIT_ENABLED'] = 'true'
env['APP_ENV'] = 'development'
env['DATABASE_URL'] = 'sqlite+aiosqlite:///./dev.db'
env['REDIS_URL'] = 'redis://localhost:6379/0'
env['ANTHROPIC_API_KEY'] = ''
env['OPENAI_API_KEY'] = ''
env['OPENROUTER_API_KEY'] = ''
env['DEFAULT_PROVIDER'] = 'anthropic'
env['DEFAULT_MODEL'] = 'claude-3-5-sonnet-20241022'
env['DOCKER_HOST'] = 'unix:///var/run/docker.sock'
env['SANDBOX_TIMEOUT'] = '300'
env['SANDBOX_MEMORY_LIMIT'] = '512m'
env['SANDBOX_CPU_LIMIT'] = '1.0'
env['SANDBOX_IMAGE'] = 'python:3.11-slim'
env['UPLOAD_DIR'] = '/app/uploads'
env['MAX_UPLOAD_SIZE'] = '10485760'
env['ALLOWED_EXTENSIONS'] = '.py,.js,.ts,.json,.yaml,.yml,.md,.txt,.csv,.html,.css'
env['JWT_SECRET'] = 'dev-secret-change-in-production-min-32-chars'
env['JWT_ALGORITHM'] = 'HS256'
env['JWT_EXPIRE_MINUTES'] = '60'

code = """
import os
from coding_agent.config import settings
print('API_KEYS:', os.environ.get('API_KEYS'))
print('Settings API_KEYS:', settings.API_KEYS)
print('Settings api_keys_list:', settings.api_keys_list)
print('Settings RATE_LIMIT_ENABLED:', settings.RATE_LIMIT_ENABLED)
print('Settings APP_ENV:', settings.APP_ENV)

from coding_agent.main import create_app
import uvicorn
app = create_app()
uvicorn.run(app, host='127.0.0.1', port=8006, log_level='info')
"""

proc = subprocess.Popen([sys.executable, '-c', code], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

# Read output for a bit to see the settings
for i, line in enumerate(proc.stdout):
    print(line.rstrip())
    if 'Uvicorn running' in line:
        break

time.sleep(2)
print('Server started, testing...')

# Test health endpoint
try:
    req = urllib.request.Request('http://127.0.0.1:8006/health')
    resp = urllib.request.urlopen(req, timeout=10)
    print('Health Response:', resp.read())
except Exception as e:
    print('Health Error:', e)

time.sleep(1)

# Test chat endpoint (non-streaming) - with valid API key
try:
    data = json.dumps({'message': 'hello', 'stream': False}).encode()
    req = urllib.request.Request('http://127.0.0.1:8006/api/v1/chat', data=data, headers={'Content-Type': 'application/json', 'X-API-Key': 'sk-dev-key-1'})
    resp = urllib.request.urlopen(req, timeout=15)
    print('Chat Response:', resp.read())
except Exception as e:
    print('Chat Error:', e)

proc.terminate()