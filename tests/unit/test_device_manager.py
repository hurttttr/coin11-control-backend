"""
device_manager 单元测试 — 不调用真实 adb，全部通过 monkeypatch 伪造输出。
覆盖: adb devices -l 解析、TLS 服务发现过滤、wifi/usb 判断、pair_device、get_devices 缓存。
"""

from app.services.device_manager import DeviceManager


# ---------- _parse_devices_output: 纯函数解析 ----------


def test_parse_devices_normal_output():
    """正常 adb devices -l 输出：USB 与 WiFi 设备都被解析"""
    out = (
        "List of devices attached\n"
        "ABC123 device product:realme model:RMX3706 device:RMX3706 transport_id:1\n"
        "192.168.1.5:5555 device product:xiaomi model:Mi10 device:Mi10 transport_id:2\n"
    )
    devices = DeviceManager._parse_devices_output(out)
    assert len(devices) == 2
    usb, wifi = devices
    assert usb["serial"] == "ABC123"
    assert usb["connection_type"] == "usb"
    assert usb["model"] == "RMX3706"
    assert wifi["serial"] == "192.168.1.5:5555"
    assert wifi["connection_type"] == "wifi"
    assert wifi["model"] == "Mi10"
    # 所有设备解析后状态均为 online
    assert all(d["status"] == "online" for d in devices)


def test_parse_devices_filters_tls_service_entries():
    """ADB TLS 服务发现条目（无 model 信息）应被跳过，有 model 的 adb- 前缀设备应保留"""
    out = (
        "List of devices attached\n"
        "adb-1f2e._adb-tls-connect._tcp device\n"
        "ABC123 device product:realme model:RMX3706 device:RMX3706 transport_id:1\n"
        "adb-ABCDEF device product:xiaomi model:Mi10 device:Mi10 transport_id:3\n"
    )
    devices = DeviceManager._parse_devices_output(out)
    serials = [d["serial"] for d in devices]
    assert serials == ["ABC123", "adb-ABCDEF"]
    assert devices[1]["model"] == "Mi10"


def test_parse_devices_ignores_noise_and_unauthorized():
    """空输出 / 表头 / unauthorized 状态不应被解析为在线设备"""
    assert DeviceManager._parse_devices_output("") == []
    assert DeviceManager._parse_devices_output("List of devices attached\n\n") == []
    out = "List of devices attached\nABC123 unauthorized\n"
    assert DeviceManager._parse_devices_output(out) == []


def test_parse_devices_no_model_unknown():
    """未携带 model 信息的设备 model 回退为 Unknown"""
    out = "List of devices attached\nABC123 device\n"
    devices = DeviceManager._parse_devices_output(out)
    assert len(devices) == 1
    assert devices[0]["model"] == "Unknown"


# ---------- pair_device: 公开配对方法 ----------


async def test_pair_device_success(monkeypatch):
    m = DeviceManager()
    async def fake_run_adb(*args, **kwargs):
        assert args[:2] == ("pair", "192.168.1.5:41339")
        assert args[2] == "123456"
        return ("Successfully paired to 192.168.1.5:41339 [guid=adb-xxx]", "", 0)
    monkeypatch.setattr(m, "_run_adb", fake_run_adb)
    result = await m.pair_device("192.168.1.5:41339", "123456")
    assert result["success"] is True
    assert "paired" in result["message"].lower()


async def test_pair_device_failure(monkeypatch):
    m = DeviceManager()
    async def fake_run_adb(*args, **kwargs):
        return ("", "Failed to pair", 1)
    monkeypatch.setattr(m, "_run_adb", fake_run_adb)
    result = await m.pair_device("192.168.1.5:41339", "000000")
    assert result["success"] is False
    assert "Failed" in result["message"]


# ---------- get_devices: TTL 缓存 ----------


async def test_get_devices_cache_ttl(monkeypatch):
    m = DeviceManager()
    m.clear_devices_cache()  # 确保干净
    calls = {"n": 0}

    async def fake_run_adb(*args, **kwargs):
        calls["n"] += 1
        return (
            "List of devices attached\n"
            "ABC123 device product:realme model:R device:R transport_id:1\n",
            "",
            0,
        )

    monkeypatch.setattr(m, "_run_adb", fake_run_adb)

    d1 = await m.get_devices()
    d2 = await m.get_devices()
    assert calls["n"] == 1  # 相同 TTL 内第二次命中缓存，未重复调 adb
    assert d1 == d2

    # 调用方修改返回列表不应污染缓存
    d1.append({
        "serial": "FAKE",
        "model": "X",
        "status": "online",
        "connection_type": "usb",
        "android_version": "Unknown",
    })
    d3 = await m.get_devices()
    assert len(d3) == 1

    # 清缓存后再次调用会重新执行 adb
    m.clear_devices_cache()
    await m.get_devices()
    assert calls["n"] == 2


async def test_get_devices_cache_ttl_zero_disables(monkeypatch):
    """把 TTL 设为 0（或负值）等效关闭缓存"""
    import app.services.device_manager as dm
    monkeypatch.setattr(dm, "DEVICES_CACHE_TTL", 0)
    m = DeviceManager()
    m.clear_devices_cache()
    calls = {"n": 0}

    async def fake_run_adb(*args, **kwargs):
        calls["n"] += 1
        return ("List of devices attached\nABC123 device\n", "", 0)

    monkeypatch.setattr(m, "_run_adb", fake_run_adb)
    await m.get_devices()
    await m.get_devices()
    assert calls["n"] == 2
