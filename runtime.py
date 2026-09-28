"""Local service startup and readable handling of duplicate launches."""
import errno
import json
import socket
import urllib.request
from http.server import ThreadingHTTPServer

class LocalServer(ThreadingHTTPServer):
    allow_reuse_address=False

    def server_bind(self):
        # Windows SO_REUSEADDR permits two processes to bind the same port.
        # Explicit exclusivity prevents old and new instances sharing requests.
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()

def is_flight_service(port):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in ['/api/health','/api/airports?q=SHA']:
        try:
            with opener.open(f'http://127.0.0.1:{port}'+path,timeout=1) as response:
                payload=json.loads(response.read(100000))
            if path=='/api/health' and payload.get('app')=='flight-ticket-radar':return True
            # Recognize the already-running version before the health endpoint existed.
            if path.startswith('/api/airports') and payload.get('stats',{}).get('source')=='airportsdata / MIT' and isinstance(payload.get('items'),list):return True
        except (OSError,ValueError,TypeError,AttributeError):
            continue
    return False

def bind_server(handler,port):
    try:return LocalServer(('127.0.0.1',port),handler)
    except OSError as e:
        conflict=e.errno in (errno.EADDRINUSE,10048) or getattr(e,'winerror',None)==10048
        exclusive_conflict=e.errno==10013 or getattr(e,'winerror',None)==10013
        if not conflict and not exclusive_conflict:raise
        url=f'http://127.0.0.1:{port}'
        if is_flight_service(port):
            print(f'机票雷达已在运行，无需重复启动。\n请在浏览器打开：{url}',flush=True)
            return None
        if not conflict:raise
        raise ValueError(f'端口 {port} 已被占用，未启动新服务。\n请先关闭占用端口的程序，或在 PowerShell 中设置 $env:PORT="8766" 后重新运行。') from None
