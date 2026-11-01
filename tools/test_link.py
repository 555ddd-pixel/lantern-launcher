# -*- coding: utf-8 -*-
"""联机模块自检：用一个假的路由器（本地 HTTP）跑通 UPnP 全流程。

真路由器不方便反复折腾，但 SOAP 报文、描述文件解析、控制地址拼接这些
恰恰是最容易写错的地方，所以这里起一个假的 IGD 来验证。
"""
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import link  # noqa: E402

DESC = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>
    <friendlyName>Fake Router</friendlyName>
    <deviceList>
      <device>
        <deviceType>urn:schemas-upnp-org:device:WANDevice:1</deviceType>
        <deviceList>
          <device>
            <deviceType>urn:schemas-upnp-org:device:WANConnectionDevice:1</deviceType>
            <serviceList>
              <service>
                <serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
                <serviceId>urn:upnp-org:serviceId:WANIPConn1</serviceId>
                <controlURL>/ctl/IPConn</controlURL>
                <eventSubURL>/evt/IPConn</eventSubURL>
                <SCPDURL>/WANIPCn.xml</SCPDURL>
              </service>
            </serviceList>
          </device>
        </deviceList>
      </device>
    </deviceList>
  </device>
</root>
"""

STATE = {"ext": "203.0.113.7", "maps": {}}
CALLS = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        return

    def _send(self, body: bytes, ctype="text/xml; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/desc.xml"):
            self._send(DESC.encode())
        else:
            self._send(b"nope", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "ignore")
        soap = self.headers.get("SOAPACTION", "")
        CALLS.append((soap, raw))

        env = ('<?xml version="1.0"?><s:Envelope '
               'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>%s'
               '</s:Body></s:Envelope>')

        if "AddPortMapping" in soap:
            ext = _tag(raw, "NewExternalPort")
            inner = _tag(raw, "NewInternalClient")
            ipt = _tag(raw, "NewInternalPort")
            STATE["maps"][int(ext)] = {"port": int(ext), "internal_ip": inner,
                                  "internal_port": int(ipt or 0),
                                  "protocol": "TCP", "enabled": True,
                                  "description": _tag(raw, "NewPortMappingDescription")}
            self._send((env % "<u:AddPortMappingResponse/>").encode())
        elif "DeletePortMapping" in soap:
            STATE["maps"].pop(int(_tag(raw, "NewExternalPort") or 0), None)
            self._send((env % "<u:DeletePortMappingResponse/>").encode())
        elif "GetExternalIPAddress" in soap:
            body = (env % f"<u:GetExternalIPAddressResponse>"
                           f"<NewExternalIPAddress>{STATE['ext']}"
                           f"</NewExternalIPAddress></u:GetExternalIPAddressResponse>")
            self._send(body.encode())
        elif "GetGenericPortMappingEntry" in soap:
            idx = int(_tag(raw, "NewPortMappingIndex") or 0)
            rows = list(STATE["maps"].values())
            if idx >= len(rows):
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            m = rows[idx]
            body = (env % "<u:GetGenericPortMappingEntryResponse>"
                           f"<NewRemoteHost></NewRemoteHost>"
                           f"<NewExternalPort>{m['port']}</NewExternalPort>"
                           f"<NewProtocol>{m['protocol']}</NewProtocol>"
                           f"<NewInternalPort>{m['internal_port']}</NewInternalPort>"
                           f"<NewInternalClient>{m['internal_ip']}</NewInternalClient>"
                           f"<NewEnabled>1</NewEnabled>"
                           f"<NewPortMappingDescription>{m['description']}"
                           f"</NewPortMappingDescription>"
                           f"<NewLeaseDuration>0</NewLeaseDuration>"
                           "</u:GetGenericPortMappingEntryResponse>")
            self._send(body.encode())
        else:
            self._send((env % "<u:Unknown/>").encode())


def _tag(xml: str, name: str) -> str:
    start = xml.find(f"<{name}>")
    if start < 0:
        return ""
    start += len(name) + 2
    end = xml.find(f"</{name}>", start)
    return xml[start:end].strip() if end > start else ""


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name + (f"  ({detail})" if detail else ""))
        if not cond:
            ok = False

    print("[1] 解析设备描述、拼控制地址")
    svc = link._find_wan_service(DESC.encode(), f"{base}/desc.xml")
    check("找到 WANIPConnection", bool(svc), svc and svc["type"])
    check("控制地址拼对", bool(svc) and svc["control"] == f"{base}/ctl/IPConn",
          svc and svc["control"])

    gw = link.Gateway(svc["control"], svc["type"], svc["base"])
    check("读取公网地址", gw.external_ip() == "203.0.113.7", gw.external_ip())

    print("[2] 添加 / 删除映射")
    gw.add_mapping(25566, "192.168.1.8")
    check("路由器已记录", 25566 in STATE["maps"], str(list(STATE["maps"])))
    check("内网地址正确", STATE["maps"][25566]["internal_ip"] == "192.168.1.8")
    rows = gw.mappings()
    check("能列出来", any(r["port"] == 25566 for r in rows), str(rows))
    gw.delete_mapping(25566)
    check("删除成功", 25566 not in STATE["maps"])

    print("[3] map_port / unmap_port（接管 discover）")
    link.discover = lambda timeout=2.2: gw          # 跳过 SSDP，直接用假网关
    res = link.map_port(25567)
    check("返回完整地址", res["address"] == "203.0.113.7:25567", res["address"])
    check("内网地址取自本机", link.is_private_ip(res["internal_ip"]), res["internal_ip"])
    check("非 CGNAT", res["cgnat"] is False)
    link.unmap_port(25567)
    check("端口已回收", 25567 not in STATE["maps"])

    print("[4] 运营商内网识别")
    STATE["ext"] = "100.64.3.9"
    st = link.status()
    check("识别为 CGNAT", st["gateway"]["cg_nat"] is True, st["gateway"]["external_ip"])
    check("给出提示语", "CGNAT" in st["gateway"]["error"], st["gateway"]["error"][:30])
    STATE["ext"] = "203.0.113.7"

    print("[5] 房间参数")
    room = link.make_room()
    check("网络名格式", room["network"].startswith("lantern-")
          and len(room["network"]) == len("lantern-") + 6, room["network"])
    check("密码长度", len(room["secret"]) == 10)
    check("邀请文字含网络名", room["network"] in room["invite"])

    srv.shutdown()
    print("\n结论：" + ("全部通过 ✅" if ok else "有失败项 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
