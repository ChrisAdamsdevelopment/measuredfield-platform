"""Run browser fixture against same-host test application. No external provider."""
import os,threading,time,uuid,subprocess,urllib.request
from pathlib import Path
from types import SimpleNamespace
import sys,uvicorn
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from scripts.takeoff_browser_smoke import free_port
from app.main import create_main_app
from app.deps import get_principal
org=uuid.uuid4()
os.environ["MF_TAKEOFF_PILOT_ENABLED"]="true"
os.environ["MF_TAKEOFF_PILOT_ORG_IDS"]=str(org)
app=create_main_app()
app.dependency_overrides[get_principal]=lambda:SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4(),name="QA"),org=SimpleNamespace(id=org,name="QA business"),can=lambda p:True,csrf="test-fixture")
port=free_port()
server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,log_level="error",lifespan="off"))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
url=f"http://127.0.0.1:{port}/takeoff"
try:
 for _ in range(100):
  try:
   if urllib.request.urlopen(url,timeout=1).status==200:break
  except Exception:time.sleep(.1)
 else:raise RuntimeError("Fixture server not ready")
 subprocess.run(["node",str(root/"scripts/takeoff_assistant_browser_fixture.cjs")],env={**os.environ,"MF_TAKEOFF_BROWSER_URL":url},check=True,timeout=75)
finally:
 server.should_exit=True;thread.join(8)
