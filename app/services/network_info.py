"""
局域网信息探测 — 为 GET /api/devices/network-info 提供 IP/网段。

探测策略：
- 用 UDP socket connect 到一个外部地址（如 8.8.8.8:80，仅为触发路由，不真正发包）
  让操作系统返回本机出网网卡 IP。
- 优先选择 IPv4，过滤回环 127.x / 链路本地 169.254.x / 以及明显非局域网的
  虚拟网卡（VPN、Docker、虚拟交换机等，见 _is_private_lan）。
- subnet 取 IP 前三段（如 192.168.1.10 → "192.168.1"）。
- 探测失败或探测到非局域网地址时回退到 settings.LAN_SUBNET_OVERRIDE
  （此时 host_ip 置空或取该网段 .1 占位，交由前端拼 IP 建议）。
"""
from __future__ import annotations

import ipaddress
import logging
import socket

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# UDP connect 的目标（仅触发路由选择，不发送任何数据）
_UDP_PROBE_HOST = "8.8.8.8"
_UDP_PROBE_PORT = 80


def _new_udp_socket():
    """创建探测用 UDP socket（独立工厂，便于测试 mock 而不污染全局 socket.socket）。"""
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _is_private_lan(ip: str) -> bool:
    """判断 IP 是否属于可被手机直连的局域网私网段。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if not addr.version == 4:
        return False
    return addr.is_private and not addr.is_loopback and not addr.is_link_local


def detect_local_ip() -> str | None:
    """用 UDP connect 取本机 IPv4 出网 IP；失败返回 None。"""
    s = _new_udp_socket()
    try:
        s.connect((_UDP_PROBE_HOST, _UDP_PROBE_PORT))
        ip = s.getsockname()[0]
        return ip if _is_private_lan(ip) else None
    except OSError as e:  # 无网络 / 无默认路由
        logger.debug("探测本机 IP 失败: %s", e)
        return None
    finally:
        s.close()


def _subnet_of(ip: str) -> str:
    """取 IPv4 前三段作为网段标识；非合法 IPv4 返回空串。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if addr.version != 4:
        return ""
    return ".".join(str(ip).split(".")[:3])


def get_network_info() -> dict:
    """返回 {"subnet": str, "host_ip": str}。

    自动探测成功且命中局域网 → 返回 {subnet, host_ip}；
    否则回退 settings.LAN_SUBNET_OVERRIDE（host_ip 置空，由前端自行建议）。
    """
    settings = get_settings()
    ip = detect_local_ip()
    if ip:
        subnet = _subnet_of(ip)
        if subnet:
            return {"subnet": subnet, "host_ip": ip}

    override = (settings.LAN_SUBNET_OVERRIDE or "").strip()
    if override:
        return {"subnet": override, "host_ip": ""}

    return {"subnet": "", "host_ip": ""}
