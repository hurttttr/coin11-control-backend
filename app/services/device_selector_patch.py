"""设备选择器补丁模块 — Launcher 注入方案

在 task_engine 执行 coin11-tb 脚本前，通过 launcher 脚本 patch utils 模块，
避免直接修改 coin11-tb 源码（git clone/update 会覆盖本地修改）。
"""

import os
import sys
import tempfile

# 启动器脚本模板
LAUNCHER_TEMPLATE = r'''"""coin11-tb 启动器 — 自动注入设备选择和用户选择"""
import os
import sys

# 将 coin11-tb 目录加入 Python 路径
sys.path.insert(0, {coin11_tb_dir!r})

# 加载 utils 模块
import utils

# 从环境变量读取设备 serial
device_serial = os.environ.get("COIN11_TB_DEVICE_SERIAL", "")

if device_serial:
    # Patch 1: 替换 select_device，直接返回指定设备
    def _select_device_patched():
        import os as _os
        print(f"[设备选择] 使用后端指定的设备: {{device_serial}}")
        utils.set_terminal_title(device_serial)
        return device_serial
    utils.select_device = _select_device_patched
    
    # Patch 2: 替换 _detect_and_select_user，自动选择第一个用户（机主）
    def _detect_user_patched(d):
        print("[启动器] 后端模式已启用，自动选择用户（机主）")
        try:
            output = d.shell("pm list users").output
            import re
            users = re.findall(r'UserInfo\{{(\d+):([^:]*):', output)
            if users:
                print(f"[启动器] 自动选择用户: 用户{{users[0][0]}}")
                return users[0][0]
        except Exception as e:
            print(f"[启动器] 检测用户失败: {{e}}")
        return None
    utils._detect_and_select_user = _detect_user_patched
    
    print(f"[启动器] 已注入设备序列号: {{device_serial}}")
else:
    print("[启动器] 未指定设备 serial，将使用原 select_device 逻辑")

# 执行目标脚本
script_path = {target_script!r}
print(f"[启动器] 执行脚本: {{script_path}}")

with open(script_path, "r", encoding="utf-8") as f:
    script_code = f.read()

globals_dict = {{
    "__name__": "__main__",
    "__file__": script_path,
    "__builtins__": __builtins__,
}}

exec(compile(script_code, script_path, "exec"), globals_dict)
'''


def create_launcher_script(coin11_tb_dir: str, target_script: str) -> str:
    """创建启动器脚本并写入临时文件"""
    code = LAUNCHER_TEMPLATE.format(
        coin11_tb_dir=coin11_tb_dir,
        target_script=target_script,
    )
    fd, launcher_path = tempfile.mkstemp(
        suffix="_coin11_launcher.py",
        prefix="tb_",
        # 使用系统临时目录，避免在项目内创建 .py 触发热重载
        dir=None,
        text=True,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(code)
    return launcher_path


def cleanup_launcher(launcher_path: str):
    """清理临时启动器脚本"""
    try:
        if os.path.isfile(launcher_path):
            os.unlink(launcher_path)
    except OSError:
        pass
