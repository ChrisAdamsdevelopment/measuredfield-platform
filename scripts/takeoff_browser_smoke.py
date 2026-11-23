"""Run REAL Chrome against a local MeasuredField FastAPI instance with simulated
owner identity. Never connects to production, contacts customer data or uses
private plans. Invoked manually; not part of CI (Chrome not guaranteed there).
"""
from __future__ import annotations
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace

import uvicorn

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]

def main():
    from app.main import create_main_app
    from app.deps import get_principal

    org=uuid.uuid4()
    os.environ["MF_TAKEOFF_PILOT_ENABLED"]="true"
    os.environ["MF_TAKEOFF_PILOT_ORG_IDS"]=str(org)
    app=create_main_app()
    app.dependency_overrides[get_principal]=lambda: SimpleNamespace(
        user=SimpleNamespace(id=uuid.uuid5(uuid.NAMESPACE_DNS,"takeoff.synthetic.browser"),name="Andrew Test"),
        org=SimpleNamespace(id=org,name="Synthetic Contractor Business"),
        can=lambda permission:permission=="read")
    port=free_port()
    server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,log_level="error",lifespan="off"))
    worker=threading.Thread(target=server.run,daemon=True)
    worker.start()
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/takeoff",timeout=1) as res:
                if res.status==200:
                    break
        except Exception:
            time.sleep(.1)
    else:
        raise RuntimeError("Takeoff test server not ready")
    try:
        env=dict(os.environ,MF_TAKEOFF_BROWSER_URL=f"http://127.0.0.1:{port}/takeoff")
        result=subprocess.run(
            ["node",str(ROOT/"scripts"/"takeoff_browser_smoke.cjs")],
            cwd=ROOT,env=env,text=True,capture_output=True,timeout=75,check=False)
        print(result.stdout)
        if result.returncode:
            print(result.stderr)
            raise RuntimeError("Takeoff browser integration failed")
    finally:
        server.should_exit=True
        worker.join(timeout=8)

if __name__=="__main__":
    main()
