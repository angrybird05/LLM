import subprocess
import time
import sys
import urllib.request

code = """
from fastapi import FastAPI
import uvicorn
app = FastAPI()
@app.get('/health')
def health():
    return {'status': 'ok'}
uvicorn.run(app, host='127.0.0.1', port=8006, log_level='info')
"""

proc = subprocess.Popen([sys.executable, '-c', code])
time.sleep(3)
print('Server started, testing...')
try:
    resp = urllib.request.urlopen('http://127.0.0.1:8006/health', timeout=5)
    print('Response:', resp.read())
except Exception as e:
    print('Error:', e)
proc.terminate()