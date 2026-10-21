#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCU 采集上位机 - 本地串口桥接服务

用途：
  - 用 pyserial 打开真实 COM 口（可按 COM 号选择、显示端口描述、明确报占用错误）
  - 向 HOST/index.html 提供本地 HTTP 接口：
      GET  /api/ports              列出串口
      POST /api/open               {kind:"data"|"debug", port:"COM5", baudrate:115200}
      POST /api/close              {kind}
      POST /api/write              {kind, data:[79,0]}
      GET  /api/stream?kind=data   原始字节流（chunked）
      GET  /api/status             状态
  - 同时托管 index.html，浏览器访问 http://127.0.0.1:8765/ 即可

依赖：Python 3 + pyserial（pip install pyserial）
测试：设置环境变量 MCU_HOST_FAKE=1 使用内置模拟串口 COM_TEST。
"""

import json
import math
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
BIND_HOST = "127.0.0.1"
PORT = int(os.environ.get("MCU_HOST_PORT", "8765"))
FAKE = os.environ.get("MCU_HOST_FAKE") == "1"
INDEX_PATH = os.path.join(HERE, "index.html")

SERIAL_IMPORT_ERROR = None
try:
    import serial
    from serial.tools import list_ports as serial_list_ports
except Exception as exc:  # pyserial 缺失时仍然托管页面并给出明确错误
    serial = None
    serial_list_ports = None
    SERIAL_IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# 模拟串口（仅 MCU_HOST_FAKE=1 时使用，用于自动化验证）
# ---------------------------------------------------------------------------
class FakeSerial:
    def __init__(self, port, baudrate, timeout=0.05, mode="adc"):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.mode = mode
        self.is_open = True
        self.streaming = False
        self.writes = []
        self.sample = 0
        self.last_t = None
        self.credit = 0.0
        self.since_frame = 0
        self.debug_t = 0.0
        self.lock = threading.Lock()

    def write(self, data):
        data = bytes(data)
        with self.lock:
            self.writes.append(data)
            if data == b"\x4f\x00":
                self.streaming = True
            elif data == b"\x43\x00":
                self.streaming = False
        return len(data)

    def flush(self):
        pass

    def read(self, size=8192):
        if self.mode == "debug":
            now = time.perf_counter()
            if now - self.debug_t < 0.5:
                time.sleep(0.02)
                return b""
            self.debug_t = now
            self.sample += 1024
            line = ("D t=%d st=2 rpm=1500 n=%d q=100 pk=200 ub=0 ue=0 "
                    "p=0 rs=0 dr=0 mn=48 mx=208 ls=128 ov=0 te=0 de=0 fe=0 "
                    "z=4 zp=40000 hw=63\r\n" % (int(now * 1000), self.sample))
            return line.encode("ascii")

        with self.lock:
            streaming = self.streaming
        if not streaming:
            time.sleep(min(self.timeout, 0.03))
            return b""

        now = time.perf_counter()
        if self.last_t is None:
            self.last_t = now
        dt = now - self.last_t
        self.last_t = now
        self.credit += dt * 100000.0
        n = int(self.credit)
        if n <= 0:
            time.sleep(0.002)
            return b""
        n = min(n, size, 8192)
        self.credit -= n

        out = bytearray()
        for _ in range(n):
            k = self.sample
            v = (128.0
                 + 78.0 * math.sin(2.0 * math.pi * 1000.0 * k / 100000.0)
                 + 22.0 * math.sin(2.0 * math.pi * 3000.0 * k / 100000.0))
            out.append(max(0, min(255, int(round(v)))))
            self.sample += 1

        self.since_frame += n
        if self.since_frame >= 50000:
            self.since_frame = 0
            rpm = 1500
            frame = bytearray([0xA5, 0x5A, 0x52, 0x04,
                               rpm & 0xFF, (rpm >> 8) & 0xFF, 0, 0])
            frame.append(sum(frame) & 0xFF)
            out.extend(frame)
        return bytes(out)

    def close(self):
        self.is_open = False


# ---------------------------------------------------------------------------
# 串口槽位
# ---------------------------------------------------------------------------
class SerialSlot:
    def __init__(self, kind):
        self.kind = kind
        self.lock = threading.Lock()
        self.ser = None
        self.gen = 0
        self.port = ""
        self.description = ""
        self.baudrate = 115200
        self.rx_bytes = 0
        self.last_error = ""

    def open(self, port, baudrate):
        with self.lock:
            if self.ser is not None and getattr(self.ser, "is_open", False):
                return {"ok": False, "error": "%s 已打开，请先断开" % port}
            try:
                if FAKE:
                    if port == "COM_TEST":
                        ser = FakeSerial(port, baudrate, mode="adc")
                        desc = "STM32 Virtual ComPort (模拟数据口)"
                    elif port == "COM_DEBUG":
                        ser = FakeSerial(port, baudrate, mode="debug")
                        desc = "USART1 USB-TTL (模拟调试口)"
                    else:
                        raise RuntimeError("模拟模式只支持 COM_TEST / COM_DEBUG")
                else:
                    if serial is None:
                        raise RuntimeError("pyserial 未安装：%s" % SERIAL_IMPORT_ERROR)
                    ser = serial.Serial(port=port, baudrate=baudrate, timeout=0.05)
                    desc = ""
                self.ser = ser
                self.port = port
                self.baudrate = baudrate
                self.description = desc
                self.rx_bytes = 0
                self.last_error = ""
                self.gen += 1
                return {"ok": True, "port": port, "description": desc}
            except Exception as exc:
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
                return {"ok": False, "error": self.last_error}

    def close(self):
        with self.lock:
            ser = self.ser
            self.ser = None
            self.port = ""
            self.description = ""
            self.gen += 1
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def write(self, data):
        with self.lock:
            ser = self.ser
        if ser is None:
            return {"ok": False, "error": "端口未打开"}
        try:
            ser.write(data)
            ser.flush()
            return {"ok": True}
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return {"ok": False, "error": self.last_error}

    def read(self, gen, size=8192):
        with self.lock:
            if self.gen != gen or self.ser is None:
                return None
            ser = self.ser
        data = ser.read(size)
        if data:
            self.rx_bytes += len(data)
        return data

    def status(self):
        with self.lock:
            opened = self.ser is not None and getattr(self.ser, "is_open", False)
            return {
                "kind": self.kind,
                "open": opened,
                "port": self.port,
                "description": self.description,
                "baudrate": self.baudrate,
                "rxBytes": self.rx_bytes,
                "lastError": self.last_error,
            }


SLOTS = {"data": SerialSlot("data"), "debug": SerialSlot("debug")}


def list_serial_ports():
    if FAKE:
        return [
            {
                "device": "COM_TEST",
                "description": "STM32 Virtual ComPort (模拟数据口)",
                "hwid": "USB VID:PID=0483:5740",
                "recommended": True,
            },
            {
                "device": "COM_DEBUG",
                "description": "USART1 USB-TTL (模拟调试口)",
                "hwid": "USB VID:PID=10C4:EA60",
                "recommended": False,
            },
        ]
    if serial_list_ports is None:
        return []
    out = []
    for p in serial_list_ports.comports():
        desc = p.description or ""
        hwid = p.hwid or ""
        recommended = ("0483:5740" in hwid.upper()
                       or "STM32" in desc.upper()
                       or "VIRTUAL COMPORT" in desc.upper())
        out.append({
            "device": p.device,
            "description": desc,
            "hwid": hwid,
            "recommended": bool(recommended),
        })
    return out


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MCUHostBridge/1.0"

    def log_message(self, fmt, *args):
        # 只保留错误信息，避免每秒的轮询刷屏
        pass

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        allowed = {
            "http://127.0.0.1:%d" % PORT,
            "http://localhost:%d" % PORT,
            "null",  # file:// 页面
        }
        return origin in allowed

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin and self._origin_ok():
            self.send_header("Access-Control-Allow-Origin", origin)
        elif not origin:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._json(code, {"ok": False, "error": msg})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._origin_ok():
            self._err(403, "origin not allowed")
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._serve_index()
            return

        if path == "/api/ports":
            self._json(200, {
                "ok": True,
                "ports": list_serial_ports(),
                "pyserial": SERIAL_IMPORT_ERROR is None,
                "fake": FAKE,
                "error": SERIAL_IMPORT_ERROR,
            })
            return

        if path == "/api/status":
            self._json(200, {
                "ok": True,
                "data": SLOTS["data"].status(),
                "debug": SLOTS["debug"].status(),
                "ports": list_serial_ports(),
                "pyserial": SERIAL_IMPORT_ERROR is None,
                "fake": FAKE,
                "error": SERIAL_IMPORT_ERROR,
            })
            return

        if path == "/api/stream":
            kind = parse_qs(parsed.query).get("kind", ["data"])[0]
            self._stream(kind)
            return

        self._err(404, "not found")

    def do_POST(self):
        if not self._origin_ok():
            self._err(403, "origin not allowed")
            return

        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"

        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._err(400, "invalid json")
            return

        kind = body.get("kind", "data")
        slot = SLOTS.get(kind)
        if slot is None:
            self._err(400, "invalid kind")
            return

        if path == "/api/open":
            port = str(body.get("port", "")).strip()
            try:
                baudrate = int(body.get("baudrate", 115200))
            except Exception:
                baudrate = 115200
            if not port:
                self._err(400, "missing port")
                return
            self._json(200, slot.open(port, baudrate))
            return

        if path == "/api/close":
            slot.close()
            self._json(200, {"ok": True})
            return

        if path == "/api/write":
            try:
                data = bytes(int(x) & 0xFF for x in (body.get("data") or []))
            except Exception:
                self._err(400, "invalid data")
                return
            self._json(200, slot.write(data))
            return

        self._err(404, "not found")

    def _serve_index(self):
        try:
            with open(INDEX_PATH, "rb") as f:
                body = f.read()
        except Exception as exc:
            self._err(500, "index.html 读取失败: %s" % exc)
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, kind):
        slot = SLOTS.get(kind)
        if slot is None:
            self._err(400, "invalid kind")
            return

        with slot.lock:
            gen = slot.gen
            opened = slot.ser is not None
        if not opened:
            self._err(409, "端口未打开")
            return

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        try:
            while True:
                data = slot.read(gen)
                if data is None:
                    break
                if data:
                    self.wfile.write(b"%X\r\n" % len(data))
                    self.wfile.write(data)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                else:
                    time.sleep(0.003)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            slot.last_error = "%s: %s" % (type(exc).__name__, exc)
            slot.close()
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass
            self.close_connection = True


def main():
    print("MCU Host Bridge  http://%s:%d/" % (BIND_HOST, PORT), flush=True)
    print("  Select the data/debug COM port in the web page.", flush=True)
    if FAKE:
        print("  [FAKE] using simulated COM_TEST / COM_DEBUG", flush=True)
    if SERIAL_IMPORT_ERROR:
        print("  [WARN] pyserial unavailable: %s" % SERIAL_IMPORT_ERROR, flush=True)

    try:
        httpd = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    except OSError as exc:
        print("Cannot listen on %s:%d: %s" % (BIND_HOST, PORT, exc), flush=True)
        print("Port may be in use; close the old bridge.py / http.server first.", flush=True)
        return 1

    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for slot in SLOTS.values():
            slot.close()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
