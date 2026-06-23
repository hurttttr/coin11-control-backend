"""测试任务执行流程"""
import urllib.request
import json
import time

device_id = "adb-3B15B5012FF00000-2f6Jet._adb-tls-connect._tcp"
base = "http://127.0.0.1:8000"

# Enqueue
data = json.dumps({"script_name": "淘宝芭芭农场.py"}).encode()
req = urllib.request.Request(f"{base}/api/devices/{device_id}/queue", data=data,
    headers={"Content-Type": "application/json"}, method="POST")
resp = urllib.request.urlopen(req, timeout=10)
task = json.loads(resp.read())
print(f"ENQUEUED: id={task['id']}")

# Start
req2 = urllib.request.Request(f"{base}/api/devices/{device_id}/queue/start", method="POST")
resp2 = urllib.request.urlopen(req2, timeout=10)
print(f"START: {resp2.read().decode()}")

# Check repeatedly for 30 seconds
for i in range(30):
    time.sleep(1)
    resp3 = urllib.request.urlopen(f"{base}/api/devices/{device_id}/queue", timeout=5)
    tasks = json.loads(resp3.read())
    n = len(tasks)
    if n > 0:
        t = tasks[0]
        logs = t.get("log", "")
        log_lines = logs.split("\n") if logs else []
        print(f"CHECK {i+1}: status={t['status']}, log_lines={len(log_lines)}")
        if log_lines:
            print(f"  FIRST: {log_lines[0][:100]}")
        if t["status"] in ("completed", "failed"):
            for line in log_lines[:30]:
                print(f"  |{line}")
            break
    else:
        print(f"CHECK {i+1}: queue EMPTY")
