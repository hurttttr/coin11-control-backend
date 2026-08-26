"""
network-info 局域网探测单元测试。

覆盖：UDP socket 探测取本机 IP、网段提取、非局域网/失败回退 LAN_SUBNET_OVERRIDE。
不真正发包，全部通过 mock socket 与 get_settings 实现。
"""
from unittest import mock

from app.core.config import get_settings
from app.services import network_info as ni


class _FakeSocket:
    """模拟一个 connect 到外部地址后 get sockname 的本机 socket。"""

    def __init__(self, sockname):
        self._sockname = sockname

    def connect(self, addr):  # noqa: D401
        pass

    def getsockname(self):
        return self._sockname

    def close(self):
        pass


def test_detect_local_ip_private_lan():
    """UDP connect 返回局域网私网 IP → 命中并返回该 IP。"""
    fake = _FakeSocket(("192.168.1.10", 0))
    with mock.patch.object(ni, "_new_udp_socket", return_value=fake):
        assert ni.detect_local_ip() == "192.168.1.10"


def test_detect_local_ip_rejects_loopback_and_link_local():
    """回环 127.x 与链路本地 169.254.x 不属于可被手机直连的局域网，应过滤。"""
    for bad in ("127.0.0.1", "169.254.1.1"):
        fake = _FakeSocket((bad, 0))
        with mock.patch.object(ni, "_new_udp_socket", return_value=fake):
            assert ni.detect_local_ip() is None


def test_detect_local_ip_failure_returns_none():
    """无网络（connect 抛异常）→ 返回 None。"""

    class FailingSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self, addr):
            raise OSError("network unreachable")

        def close(self):
            pass

    with mock.patch.object(ni, "_new_udp_socket", FailingSocket):
        assert ni.detect_local_ip() is None


def test_subnet_of_takes_first_three_octets():
    assert ni._subnet_of("192.168.1.10") == "192.168.1"
    assert ni._subnet_of("10.0.0.5") == "10.0.0"
    assert ni._subnet_of("172.16.3.9") == "172.16.3"
    # 非 IPv4 / 非法 → 空
    assert ni._subnet_of("") == ""
    assert ni._subnet_of("::1") == ""
    assert ni._subnet_of("not-an-ip") == ""


def test_get_network_info_success(monkeypatch):
    """探测成功 → {subnet, host_ip}。"""
    fake = _FakeSocket(("192.168.1.10", 0))
    monkeypatch.setattr(ni, "_new_udp_socket", lambda *a, **k: fake)
    info = ni.get_network_info()
    assert info == {"subnet": "192.168.1", "host_ip": "192.168.1.10"}


def test_get_network_info_fallback_to_override(monkeypatch):
    """探测失败 → 回退 LAN_SUBNET_OVERRIDE。"""

    class FailingSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self, addr):
            raise OSError("no route")

        def close(self):
            pass

    monkeypatch.setattr(ni, "_new_udp_socket", FailingSocket)
    monkeypatch.setattr(
        get_settings(),
        "LAN_SUBNET_OVERRIDE",
        "192.168.5",
    )
    info = ni.get_network_info()
    assert info == {"subnet": "192.168.5", "host_ip": ""}


def test_get_network_info_nonlan_fallback_to_override(monkeypatch):
    """探测到非局域网（如虚拟网卡/NAT 出口的公网 IP）→ 回退 override。"""
    fake = _FakeSocket(("8.8.8.8", 0))  # 公网 DNS，is_private=False
    monkeypatch.setattr(ni, "_new_udp_socket", lambda *a, **k: fake)
    monkeypatch.setattr(
        get_settings(),
        "LAN_SUBNET_OVERRIDE",
        "10.10.10",
    )
    info = ni.get_network_info()
    assert info == {"subnet": "10.10.10", "host_ip": ""}


def test_get_network_info_no_override_all_empty(monkeypatch):
    """探测失败且无 override → 全空。"""

    class FailingSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self, addr):
            raise OSError("no route")

        def close(self):
            pass

    monkeypatch.setattr(ni, "_new_udp_socket", FailingSocket)
    monkeypatch.setattr(get_settings(), "LAN_SUBNET_OVERRIDE", "")
    assert ni.get_network_info() == {"subnet": "", "host_ip": ""}
