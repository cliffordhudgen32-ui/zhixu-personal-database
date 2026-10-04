"""Windows-friendly launcher, no terminal commands needed by the user."""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

if __name__ == '__main__':
    import uvicorn
    from app.database import migrate
    port = int(os.getenv('APP_PORT','8000'))
    host = os.getenv('APP_HOST','127.0.0.1')
    if host not in ('127.0.0.1','localhost','::1'):
        print('当前版本为本地个人数据库，请设置 APP_HOST=127.0.0.1。')
        sys.exit(1)
    url=f'http://127.0.0.1:{port}'
    with socket.socket() as check:
        occupied = check.connect_ex(('127.0.0.1',port)) == 0
    if occupied:
        import urllib.request, json
        try:
            with urllib.request.urlopen(url+'/api/session',timeout=2) as r:
                existing=json.load(r)
            if 'token' not in existing or 'settings' not in existing:
                raise ValueError()
        except Exception:
            print(f'端口 {port} 被其他程序占用。请在 .env 修改 APP_PORT。')
            sys.exit(1)
        webbrowser.open(url)
        print('数据库已在运行，已打开网页。')
        sys.exit(0)
    def open_when_ready():
        import urllib.request
        for _ in range(120):
            try:
                urllib.request.urlopen(url+'/api/session',timeout=1).close()
                webbrowser.open(url)
                return
            except Exception:
                time.sleep(.5)
    threading.Thread(target=open_when_ready,daemon=True).start()
    print('个人知识、经验、案例与数字记忆数据库')
    print(url+'  |  关闭此窗口或按 Ctrl+C 停止服务')
    uvicorn.run('app.main:app',host=host,port=port,workers=1,access_log=False)
