import os
import urllib.request
import json

TOKEN = os.environ.get("GH_TOKEN", "")
USER = "HuRTTTTR"

if not TOKEN:
    print("FAIL: GH_TOKEN environment variable not set")
    exit(1)

repos = [
    ("coin11-control-backend", "Coin11-TB 控制平台后端"),
    ("coin11-control-frontend", "Coin11-TB 控制平台前端"),
]

for name, desc in repos:
    data = json.dumps({
        "name": name,
        "description": desc,
        "private": False,
        "auto_init": False
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.github.com/user/repos",
        data=data,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "coin11-control-script"
        },
        method="POST"
    )

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        print(f"OK: {name} -> {result.get('html_url', '')}")
    except urllib.error.HTTPError as e:
        err_body = e.read()
        try:
            err = json.loads(err_body)
            print(f"FAIL: {name} -> {err.get('message', str(e))}")
        except:
            print(f"FAIL: {name} -> {err_body.decode()}")
    except Exception as e:
        print(f"FAIL: {name} -> {e}")
