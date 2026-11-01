# -*- coding: utf-8 -*-
"""联机：局域网信息 + UPnP 端口映射。

Minecraft 的联机无非两条路：
  1. 同一个 WiFi 下，朋友直接用「局域网 IP:端口」连；
  2. 不在同一个网络，就得让路由器把端口映射到公网（UPnP），
     朋友用「公网 IP:端口」连。

这里实现第 2 条：用标准库做 SSDP 发现 + SOAP 调用，不装任何第三方组件、
不需要管理员权限（路由器支持 UPnP 就行）。拿不到公网地址时会明确告诉用户
是路由器没开 UPnP，还是运营商给了内网地址（CGNAT）——这两种情况换谁都救不了。
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import time
import urllib.request

from .net import open_local
import xml.etree.ElementTree as ET
from typing import Optional

from .config import LOG_DIR, config

SSDP_ADDR = ("239.255.255.250", 1900)
PRIVATE_NETS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)
DEFAULT_PORT = 25565
DESC = "老张的minecraft启动器"


# ---------------------------------------------------------------- 本机地址


def local_ips() -> list:
    """本机的局域网 IPv4（去掉回环、虚拟网卡那种没用的）。"""
    found = []

    def add(ip: str, name: str = "") -> None:
        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            return
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            return
        if not any(addr in n for n in PRIVATE_NETS):
            return
        if any(x[0] == ip for x in found):
            return
        found.append((ip, name))

    # 主页不是靠 DNS，而是「拨号路由」：connect UDP 不实际发包，只让系统选出口网卡
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 80))
            add(s.getsockname()[0], "默认出口")
        finally:
            s.close()
    except OSError:
        pass

    try:
        host = socket.gethostname()
        for item in socket.getaddrinfo(host, None, socket.AF_INET):
            add(item[4][0], host)
    except OSError:
        pass

    # 有 192.168.x.x 的放前面，用户更容易认出自己那张网卡
    found.sort(key=lambda x: (not x[0].startswith("192.168."), x[0]))
    return [{"ip": ip, "name": name} for ip, name in found]


def is_private_ip(ip: str) -> bool:
    """只认 RFC1918。

    注意不能用 ipaddress 的 is_private：Python 3.13 起它把部分保留段也算成
    private，会把正常的公网地址误判成内网。
    """
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return any(addr in n for n in PRIVATE_NETS)


def is_cgnat(ip: str) -> bool:
    """100.64.0.0/10 是运营商级 NAT 段：看着像公网，其实外面进不来。"""
    try:
        return ipaddress.IPv4Address(ip) in ipaddress.IPv4Network("100.64.0.0/10")
    except ValueError:
        return False


def guess_game_port() -> dict:
    """从最近一次游戏日志里猜「对局域网开放」用的端口。"""
    best = {"port": 0, "source": ""}
    try:
        files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:5]
    except OSError:
        files = []
    pats = (
        re.compile(r"Started serving on (\d{2,5})"),
        re.compile(r"Local game hosted on port (\d{2,5})"),
        re.compile(r"在端口\s*(\d{2,5})\s*上"),
    )
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in pats:
            hits = pat.findall(text)
            if hits:
                return {"port": int(hits[-1]), "source": f.name}
    return best


# ---------------------------------------------------------------- SSDP 发现


MSEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
    "ssdp:all",
)


def _msearch(st: str, mx: int = 1) -> bytes:
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {mx}\r\n"
        f"ST: {st}\r\n"
        "\r\n"
    ).encode("ascii")


def _ssdp_search(timeout: float = 1.0, rounds: int = 2) -> list:
    """发 M-SEARCH 找 IGD 的描述文件地址。

    各家路由器只回应其中某几个 ST（有的只答 ssdp:all，有的只答
    InternetGatewayDevice），单发一个经常扑空，所以每轮把所有 ST 都发一遍，
    多来几轮，谁先答就收谁的。
    """
    seen, locations = set(), []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.25)
        for _ in range(max(1, rounds)):
            for st in MSEARCH_TARGETS:
                try:
                    sock.sendto(_msearch(st), SSDP_ADDR)
                except OSError:
                    pass
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data, _addr = sock.recvfrom(65507)
                except socket.timeout:
                    continue
                except OSError:
                    break
                text = data.decode("utf-8", "ignore")
                m = re.search(r"(?im)^location://s*(//S+)", text)
                if not m:
                    continue
                loc = m.group(1).strip()
                if loc in seen:
                    continue
                seen.add(loc)
                locations.append(loc)
            if locations:
                break
    finally:
        sock.close()
    return locations


def _service_url(base: str, control: str) -> str:
    if control.startswith("http://") or control.startswith("https://"):
        return control
    if base.endswith("/"):
        base = base[:-1]
    if not control.startswith("/"):
        control = "/" + control
    return base + control


def _find_wan_service(xml_bytes: bytes, location: str) -> Optional[dict]:
    """在描述文件里找 WANIPConnection / WANPPPConnection 服务。"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    base = location
    m = re.match(r"(https?://[^/]+)", location)
    if m:
        base = m.group(1)
    for el in root.iter():
        if el.tag.endswith("URLBase") and (el.text or "").strip():
            base = el.text.strip()

    for svc in root.iter():
        if not svc.tag.endswith("service"):
            continue
        fields = {}
        for child in svc:
            tag = child.tag.split("}")[-1]
            fields[tag] = (child.text or "").strip()
        stype = fields.get("serviceType", "")
        if "WANIPConnection" not in stype and "WANPPPConnection" not in stype:
            continue
        if not fields.get("controlURL"):
            continue
        return {
            "type": stype,
            "control": _service_url(base, fields["controlURL"]),
            "base": base,
        }
    return None


def _http_post(url: str, service_type: str, action: str, args: dict,
               timeout: float = 5.0) -> str:
    body_args = "".join(
        f"<{k}>{v}</{k}>" for k, v in args.items()
    )
    envelope = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f"<s:Body><u:{action} xmlns:u=\"{service_type}\">"
        f"{body_args}"
        f"</u:{action}></s:Body></s:Envelope>"
    ).encode("utf-8")
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPACTION": f'"{service_type}#{action}"',
        "Connection": "close",
    }
    with open_local(url, data=envelope, headers=headers, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _http_get(url: str, timeout: float = 4.0) -> bytes:
    with open_local(url, headers={"Connection": "close"}, timeout=timeout) as r:
        return r.read()


class Gateway:
    """路由器上的 UPnP 网关（只保留用得到的动作）。"""

    def __init__(self, control: str, service_type: str, base: str) -> None:
        self.control = control
        self.service_type = service_type
        self.base = base

    # ---- 动作
    def external_ip(self) -> str:
        xml = _http_post(self.control, self.service_type, "GetExternalIPAddress", {})
        m = re.search(r"<NewExternalIPAddress>([^<]*)<", xml, re.I)
        return (m.group(1).strip() if m else "")

    def add_mapping(self, port: int, internal_ip: str, protocol: str = "TCP",
                    lease: int = 0, description: str = DESC) -> None:
        args = {
            "NewRemoteHost": "",
            "NewExternalPort": str(port),
            "NewProtocol": protocol,
            "NewInternalPort": str(port),
            "NewInternalClient": internal_ip,
            "NewEnabled": "1",
            "NewPortMappingDescription": description,
            "NewLeaseDuration": str(lease),
        }
        _http_post(self.control, self.service_type, "AddPortMapping", args)

    def delete_mapping(self, port: int, protocol: str = "TCP") -> None:
        args = {"NewRemoteHost": "", "NewExternalPort": str(port),
                "NewProtocol": protocol}
        _http_post(self.control, self.service_type, "DeletePortMapping", args)

    def mappings(self) -> list:
        """列出已有映射（路由器不支持时返回空表，不报错）。"""
        out = []
        for i in range(0, 40):
            try:
                xml = _http_post(self.control, self.service_type,
                                 "GetGenericPortMappingEntry",
                                 {"NewPortMappingIndex": str(i)})
            except Exception:  # noqa: BLE001 - 索引越界就是解析失败
                break
            fields = {}
            for key in ("NewRemoteHost", "NewExternalPort", "NewProtocol",
                        "NewInternalPort", "NewInternalClient", "NewEnabled",
                        "NewPortMappingDescription"):
                m = re.search(rf"<{key}>([^<]*)<", xml, re.I)
                fields[key] = (m.group(1).strip() if m else "")
            if not fields["NewExternalPort"]:
                break
            out.append({
                "port": int(fields["NewExternalPort"] or 0),
                "protocol": fields["NewProtocol"],
                "internal_ip": fields["NewInternalClient"],
                "internal_port": int(fields["NewInternalPort"] or 0),
                "enabled": fields["NewEnabled"] == "1",
                "description": fields["NewPortMappingDescription"],
            })
        return out


def default_gateway() -> str:
    """取默认网关地址（Windows 上读 ipconfig，其它平台读 /proc/net/route）。"""
    import re
    import subprocess

    try:
        if os.name == "nt":
            out = subprocess.run(["ipconfig"], capture_output=True, timeout=6,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            text = out.stdout.decode("gbk", "ignore") + out.stdout.decode("utf-8", "ignore")
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if "网关" not in line and "Gateway" not in line:
                    continue
                # IPv4 网关有时写在下一行的续行里（上面那行是 IPv6）
                for probe in lines[i:i + 3]:
                    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", probe)
                    if m and m.group(1) != "0.0.0.0":
                        return m.group(1)
        else:
            with open("/proc/net/route", encoding="utf-8") as f:
                for line in f.readlines()[1:]:
                    cols = line.split()
                    if len(cols) > 2 and cols[1] == "00000000":
                        hexip = cols[2]
                        return ".".join(str(int(hexip[i:i + 2], 16))
                                        for i in (6, 4, 2, 0))
    except Exception:  # noqa: BLE001
        pass
    return ""


def _cache_file():
    from .paths import DATA_DIR

    return DATA_DIR / ".cache" / "upnp.json"


def _load_saved() -> Optional[dict]:
    import json

    try:
        data = json.loads(_cache_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not data.get("control") or not data.get("type"):
        return None
    return data


def _save(gw: "Gateway") -> None:
    import json

    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"control": gw.control, "type": gw.service_type,
                                    "base": gw.base}, ensure_ascii=False),
                        encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _description_candidates() -> list:
    """SSDP 不应答时，直接按常见地址去敲网关。"""
    gw = default_gateway()
    if not gw:
        return []
    urls = []
    for port in (5000, 49152, 80):
        for path in ("rootDesc.xml", "description.xml"):
            urls.append(f"http://{gw}:{port}/{path}")
    return urls


_CACHE: dict = {"gw": None, "at": 0.0}
CACHE_TTL = 600.0


def discover(timeout: float = 1.0, use_cache: bool = True) -> Optional[Gateway]:
    """找路由器上的 UPnP 网关；找不到返回 None。

    组播应答时有时无（路由器限速、无线丢包都会影响），所以结果缓存 10 分钟：
    加映射、查公网地址、删映射连着做的时候不会因为"这一次没应答"就失败。
    """
    gw = _CACHE.get("gw")
    if use_cache and gw is not None and time.time() - _CACHE.get("at", 0) < CACHE_TTL:
        return gw

    # 1) 上次成功过的控制地址（最稳，不依赖组播）
    saved = _load_saved()
    if saved:
        gw = Gateway(saved["control"], saved["type"], saved.get("base") or "")
        try:
            gw.external_ip()
            _CACHE["gw"], _CACHE["at"] = gw, time.time()
            return gw
        except Exception:  # noqa: BLE001 - 换了网络 / 换了路由器就重新找
            pass

    # 2) SSDP 组播发现
    for loc in _ssdp_search(timeout):
        try:
            xml = _http_get(loc)
        except Exception:  # noqa: BLE001
            continue
        svc = _find_wan_service(xml, loc)
        if svc:
            found = Gateway(svc["control"], svc["type"], svc["base"])
            _CACHE["gw"], _CACHE["at"] = found, time.time()
            _save(found)
            return found

    # 3) 直接按常见地址敲网关（有些路由器懒得回组播）
    for url in _description_candidates():
        try:
            xml = _http_get(url, timeout=0.9)
        except Exception:  # noqa: BLE001
            continue
        svc = _find_wan_service(xml, url)
        if svc:
            found = Gateway(svc["control"], svc["type"], svc["base"])
            try:
                found.external_ip()
            except Exception:  # noqa: BLE001
                continue
            _CACHE["gw"], _CACHE["at"] = found, time.time()
            _save(found)
            return found
    return None


# ---------------------------------------------------------------- 对外组合


def _lan_ip_for(internal_ip: str = "") -> str:
    if internal_ip:
        return internal_ip
    ips = local_ips()
    return ips[0]["ip"] if ips else "127.0.0.1"


def status(with_public: bool = True) -> dict:
    """联机页面要的一整套信息。"""
    ips = local_ips()
    out = {
        "lan_ips": ips,
        "suggest_port": guess_game_port(),
        "default_port": DEFAULT_PORT,
        "gateway": {"found": False, "error": "", "external_ip": "", "cg_nat": False,
                    "mappings": []},
        "updated": time.strftime("%H:%M:%S"),
    }
    if not with_public:
        return out

    try:
        gw = discover()
    except Exception as e:  # noqa: BLE001
        gw, out["gateway"]["error"] = None, f"发现网关失败：{e}"
        return out

    if gw is None:
        out["gateway"]["error"] = (
            "没找到支持 UPnP 的路由器。可能是路由器关了 UPnP（登录路由器后台，"
            "在「安全 / 端口映射 / UPnP」里打开），或者当前网络不支持。"
        )
        return out

    out["gateway"]["found"] = True
    try:
        ext = gw.external_ip()
    except Exception as e:  # noqa: BLE001
        ext, out["gateway"]["error"] = "", f"读取公网地址失败：{e}"
    out["gateway"]["external_ip"] = ext
    out["gateway"]["cg_nat"] = bool(ext) and (is_cgnat(ext) or is_private_ip(ext))
    if out["gateway"]["cg_nat"]:
        out["gateway"]["error"] = (
            f"公网地址是 {ext}，属于运营商内网（CGNAT），外面连不进来。"
            "这种情况只能用内网穿透或组网工具，端口映射救不了。"
        )
    try:
        out["gateway"]["mappings"] = [
            m for m in gw.mappings() if m["description"].startswith(DESC[:12])
        ]
    except Exception:  # noqa: BLE001
        out["gateway"]["mappings"] = []
    return out


def map_port(port: int, internal_ip: str = "") -> dict:
    """加一条端口映射，并把公网地址一起回给前端。"""
    if not (1 <= int(port) <= 65535):
        raise ValueError("端口要在 1~65535 之间")
    gw = discover()
    if gw is None:
        raise RuntimeError(
            "没找到支持 UPnP 的路由器，先在路由器后台打开 UPnP 再来试"
        )
    ip = _lan_ip_for(internal_ip)
    gw.add_mapping(int(port), ip)
    ext = ""
    try:
        ext = gw.external_ip()
    except Exception:  # noqa: BLE001
        pass
    return {
        "port": int(port),
        "internal_ip": ip,
        "external_ip": ext,
        "cgnat": bool(ext) and (is_cgnat(ext) or is_private_ip(ext)),
        "address": f"{ext}:{port}" if ext else "",
    }


def unmap_port(port: int) -> dict:
    gw = discover()
    if gw is None:
        raise RuntimeError("没找到支持 UPnP 的路由器")
    gw.delete_mapping(int(port))
    return {"port": int(port), "removed": True}


def game_dir_hint() -> str:
    return str(config.game_dir)


# ---------------------------------------------------------------- 房间（组网工具用）

ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉 0/O/1/I 这类看错的
PUBLIC_NODE = "tcp://public.easytier.cn:11010"
EASYTIER_REPO = "EasyTier/EasyTier"
EASYTIER_HOME = "https://easytier.cn/"


def make_room() -> dict:
    """生成一组房间参数：网络名 + 密码，双方填一样的即可互相访问。

    组网工具对等，不分主机客户端；谁先连上都能被找到。这里只负责「起名字」，
    真正的连接交给 EasyTier / 陶瓦联机这类客户端。
    """
    import random
    import secrets

    code = "".join(random.choice(ROOM_ALPHABET) for _ in range(6))
    secret = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(10))
    return {
        "code": code,
        "secret": secret,
        "network": f"lantern-{code.lower()}",
        "node": PUBLIC_NODE,
        "invite": (
            f"一起来玩 Minecraft！\n"
            f"网络名：lantern-{code.lower()}\n"
            f"密码：{secret}\n"
            f"共享节点：public.easytier.cn:11010\n"
            f"（双方装上 EasyTier 后填一样的网络名和密码，然后在游戏里"
            f"用对方的虚拟 IP 加「直接连接」）"
        ),
    }


def easytier_latest() -> dict:
    """查一下 EasyTier 最新版 Windows 包的地址（查不到就用官网兜底）。"""
    import json

    out = {"version": "", "url": "", "size": 0, "error": "", "home": EASYTIER_HOME}
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{EASYTIER_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "LanternLauncher"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        out["version"] = data.get("tag_name", "")
        for asset in data.get("assets") or []:
            name = (asset.get("name") or "").lower()
            if "windows" in name and "x86_64" in name and name.endswith(".zip"):
                out["url"] = asset["browser_download_url"]
                out["size"] = asset.get("size") or 0
                break
        if not out["url"]:
            out["error"] = "这个版本没有 Windows x64 包"
    except Exception as e:  # noqa: BLE001
        out["error"] = f"查不到最新版（{e}），可以点「打开发布页」手动下载"
    return out


def easytier_local(base_dir=None) -> dict:
    """本机是否已经下载过 EasyTier。"""
    from .paths import DATA_DIR

    root = base_dir or (DATA_DIR / "runtime" / "easytier")
    gui, core = None, None
    if root.exists():
        for p in root.rglob("*.exe"):
            low = p.name.lower()
            if low.startswith("easytier-gui"):
                gui = p
            elif low.startswith("easytier-core"):
                core = p
    return {
        "dir": str(root),
        "installed": bool(gui or core),
        "gui": str(gui) if gui else "",
        "core": str(core) if core else "",
    }


def download_easytier(emit=None, progress=None, cancel=None) -> dict:
    """下载并解压 EasyTier 客户端到 runtime/easytier/。"""
    import zipfile
    from .paths import DATA_DIR

    say = emit or (lambda *_: None)
    tick = progress or (lambda *_: None)
    info = easytier_latest()
    if not info.get("url"):
        raise RuntimeError(info.get("error") or "拿不到下载地址")

    root = DATA_DIR / "runtime" / "easytier"
    root.mkdir(parents=True, exist_ok=True)
    zip_path = root / "easytier.zip"

    say(f"下载 EasyTier {info.get('version')}（约 "
        f"{round((info.get('size') or 0) / 1048576, 1)} MB）…")
    req = urllib.request.Request(info["url"],
                                 headers={"User-Agent": "LanternLauncher"})
    total = info.get("size") or 0
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(zip_path, "wb") as f:
        total = int(r.headers.get("Content-Length") or total or 0)
        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("已取消")
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            tick({"done_bytes": done, "total_bytes": total or done})

    say("解压…")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(root)
    try:
        zip_path.unlink()
    except OSError:
        pass

    local = easytier_local(root)
    if not local["installed"]:
        raise RuntimeError("解压完了但没找到 exe，可能包结构变了")
    say(f"已就绪：{local['gui'] or local['core']}")
    return {**local, "version": info.get("version", "")}
