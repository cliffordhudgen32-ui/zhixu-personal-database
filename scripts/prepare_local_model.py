import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.local_ai import pull_model, stop_runtime
last = ''
def progress(value):
    global last
    total=value.get('total',0); completed=value.get('completed',0)
    summary=value.get('status','') + (' ' + str(round(completed/total*100,1)) + '%' if total else '')
    if summary != last:
        print(summary,flush=True);last=summary
try:
    pull_model('qwen3:1.7b',progress,{'provider':'ollama','base_url':'http://127.0.0.1:11435'})
finally:
    stop_runtime()
