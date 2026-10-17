#!/usr/bin/env python3
"""在本机起一个只读小服务器，让看板里的参考图能在任何浏览器（含预览窗格）里显示。

用法：python3 scripts/serve_dashboard.py [端口]      默认 8765，只监听 127.0.0.1
然后打开  http://127.0.0.1:8765/docs/dashboard.html

只放行 /docs/ 与 /参考图组/ 两个目录，其余（配置、日志、状态、任务卡）一律 403，避免把私有文件暴露出去。
"""
import http.server
import os
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED = ("/docs/", "/参考图组/")


def allowed(path):
    """path 是请求路径（可能带 %XX 编码与 ..）。只允许 ALLOWED 前缀下、且不含 .. 的路径。"""
    p = urllib.parse.unquote(urllib.parse.urlparse(path).path)
    if ".." in p.split("/"):
        return False
    return p.startswith(ALLOWED)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/docs/dashboard.html")
            self.end_headers()
            return
        if not allowed(self.path):
            self.send_error(403, "forbidden")
            return
        super().do_GET()

    do_HEAD = do_GET

    def log_message(self, fmt, *args):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("看板：http://127.0.0.1:%d/docs/dashboard.html（Ctrl+C 停止）" % port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
