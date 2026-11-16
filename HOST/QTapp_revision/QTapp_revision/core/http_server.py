# -*- coding: utf-8 -*-
"""
HTTP服务器线程模块

在后台线程中启动HTTP服务器，用于提供output文件夹的Web访问

## 服务说明
- 服务地址：http://<局域网IP>:<端口>（默认8000）
- 服务目录：output文件夹
- 支持文件下载、预览、批量下载等功能

## 输出文件命名规则

### 成像结果文件
- `latest_image.png` - 最新的成像结果（固定名称，每次运行覆盖）
- `<base_name>.png` - 自动保存的成像结果（基于txt文件名）
- `<base_name>.txt` - 成像参数和元数据
- `<base_name>.xyz` - 3D点云数据（如果生成）

### 分割检测结果文件
每次运行检测后自动保存：
- `latest_seg_result.png` - 最新的2D可视化结果（固定名称，每次运行覆盖）
- `latest_seg_mask.png` - 最新的缺陷掩膜
- `latest_seg_heatmap.png` - 最新的热力图
- `latest_seg_info.txt` - 最新的统计信息

手动导出时保存（带时间戳）：
- `seg_YYYYMMDD_HHMMSS_result.png` - 2D可视化结果
- `seg_YYYYMMDD_HHMMSS_mask.png` - 缺陷掩膜
- `seg_YYYYMMDD_HHMMSS_heatmap.png` - 热力图
- `seg_YYYYMMDD_HHMMSS_info.txt` - 统计信息

## API端点

### 文件列表
- `GET /api/files` - 获取所有文件列表（JSON格式）
  返回：[{name, size, modified}, ...]

- `GET /api/txt-files` - 获取所有txt文件及配套文件信息
  返回：[{name, base_name, size, modified, has_png, has_xyz}, ...]

### 文件预览
- `GET /api/preview/<filename>` - 预览文本文件内容
  示例：/api/preview/latest_seg_info.txt

### 文件下载
- `GET /<filename>` - 直接下载单个文件
  示例：/latest_seg_result.png

- `GET /api/download-set/<base_name>` - 下载配套文件集（zip格式）
  示例：/api/download-set/seg_20260131_143025
  包含：result.png, mask.png, heatmap.png, info.txt

- `POST /api/download-batch` - 批量下载文件（zip格式）
  请求体：{"files": ["file1.png", "file2.txt", ...]}
  限制：最多50个文件

## 使用示例

### Python调用示例
```python
import requests

# 获取最新分割结果
response = requests.get('http://192.168.1.100:8000/latest_seg_result.png')
with open('result.png', 'wb') as f:
    f.write(response.content)

# 获取文件列表
response = requests.get('http://192.168.1.100:8000/api/files')
files = response.json()

# 下载配套文件集
response = requests.get('http://192.168.1.100:8000/api/download-set/seg_20260131_143025')
with open('results.zip', 'wb') as f:
    f.write(response.content)
```

### JavaScript调用示例
```javascript
// 获取最新分割结果
fetch('http://192.168.1.100:8000/latest_seg_result.png')
  .then(response => response.blob())
  .then(blob => {
    const url = URL.createObjectURL(blob);
    document.getElementById('img').src = url;
  });

// 获取文件列表
fetch('http://192.168.1.100:8000/api/files')
  .then(response => response.json())
  .then(files => console.log(files));
```

### curl调用示例
```bash
# 下载最新分割结果
curl -O http://192.168.1.100:8000/latest_seg_result.png

# 获取文件列表
curl http://192.168.1.100:8000/api/files

# 下载配套文件集
curl -O http://192.168.1.100:8000/api/download-set/seg_20260131_143025
```
"""
import os
import logging
import threading
import socket
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from PyQt5 import QtCore
from utils.resource_utils import resource_path

logger = logging.getLogger(__name__)


def get_local_ip():
    """
    获取本机局域网IP地址

    Returns:
        str: 局域网IP地址，如果获取失败返回'127.0.0.1'
    """
    try:
        # 创建一个UDP socket连接到外部地址（不会真正发送数据）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"


class HttpServerWorker(QtCore.QThread):
    """
    HTTP服务器工作线程

    在后台线程中启动HTTP服务器，提供文件访问服务
    """
    # 信号：服务器启动成功，传递服务器URL和实际端口号
    server_started = QtCore.pyqtSignal(str, int)
    # 信号：服务器启动失败，传递错误信息
    server_error = QtCore.pyqtSignal(str)

    def __init__(self, directory, port=8000, parent=None):
        """
        初始化HTTP服务器工作线程

        Args:
            directory: 要提供服务的目录路径
            port: 服务器端口号，默认8000
            parent: 父对象
        """
        super().__init__(parent)
        self._directory = os.path.abspath(directory)  # 使用绝对路径
        self._port = port
        self._server = None
        self._running = False
        self._actual_port = None

    def run(self):
        """线程执行体：启动HTTP服务器，支持端口自动选择"""
        try:
            # 确保目录存在
            if not os.path.exists(self._directory):
                os.makedirs(self._directory, exist_ok=True)
                logger.info(f"[HTTP服务器] 创建服务目录: {self._directory}")

            # 创建专业的index.html页面，动态显示文件列表
            index_file = os.path.join(self._directory, "index.html")
            # 每次启动都重新生成，确保是最新版本
            # 获取实际的输出目录绝对路径
            output_path = os.path.abspath(self._directory)

            # 读取HTML模板
            template_path = resource_path('templates/preview.html')
            try:
                with open(template_path, 'r', encoding='utf-8') as template_file:
                    html_template = template_file.read()
            except FileNotFoundError:
                logger.error(f"[HTTP服务器] 模板文件不存在: {template_path}")
                raise

            # 替换模板中的变量
            html_content = html_template.replace('{output_path}', output_path)

            # 写入index.html
            with open(index_file, 'w', encoding='utf-8') as f:
                f.write(html_content)

            logger.info(f"[HTTP服务器] 已生成index.html: {index_file}")
            # 创建自定义的请求处理器，指定服务目录
            # 使用闭包捕获 directory 变量
            directory = self._directory

            class CustomHandler(SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    # 设置服务目录为指定目录
                    super().__init__(*args, directory=directory, **kwargs)

                def end_headers(self):
                    # 为特定文件类型添加下载头
                    # 检查路径是否为需要强制下载的文件类型
                    if hasattr(self, 'path') and self.path:
                        # 排除API端点
                        if not self.path.startswith('/api/'):
                            # 获取文件扩展名
                            import os
                            from urllib.parse import unquote
                            file_path = unquote(self.path.lstrip('/'))
                            _, ext = os.path.splitext(file_path)
                            ext = ext.lower()

                            # 对于PNG、JSON等文件，添加Content-Disposition头强制下载
                            if ext in ['.png', '.jpg', '.jpeg', '.json', '.txt', '.xyz', '.bin']:
                                filename = os.path.basename(file_path)
                                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')

                    super().end_headers()

                def do_GET(self):
                    # 添加点云预览API端点（优化版）
                    if self.path.startswith('/api/pointcloud/'):
                        from urllib.parse import unquote
                        import json
                        import os
                        import sys

                        # 提取文件名
                        filename = unquote(self.path[16:])  # 16 = len('/api/pointcloud/')
                        filepath = os.path.join(directory, filename)

                        if not os.path.exists(filepath):
                            self.send_response(404)
                            self.send_header('Content-type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(json.dumps({'error': '文件不存在'}).encode('utf-8'))
                            return

                        try:
                            # 导入点云处理函数
                            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                            from algorithms.cylinder_3d import generate_3d_for_web
                            from config.config_manager import get_config_manager

                            # 读取配置
                            config = get_config_manager()
                            max_points = config.get("web_preview", {}).get("max_points", 50000)

                            # 使用优化的加载函数
                            data = generate_3d_for_web(filepath, max_points=max_points)

                            self.send_response(200)
                            self.send_header('Content-type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(json.dumps(data).encode('utf-8'))

                            logger.info(f"[HTTP] 点云预览: {filename} ({data['sampled_points']}/{data['total_points']} 点)")
                        except Exception as e:
                            import traceback
                            logger.error(f"[HTTP] 点云加载失败: {e}")
                            logger.error(traceback.format_exc())
                            self.send_response(500)
                            self.send_header('Content-type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))
                        return

                    # 添加API端点返回文件列表JSON
                    if self.path == '/api/files' or self.path.startswith('/api/files?'):
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()

                        import json
                        import os

                        files = []
                        try:
                            for filename in os.listdir(directory):
                                filepath = os.path.join(directory, filename)
                                if os.path.isfile(filepath) and filename != 'index.html':
                                    stat = os.stat(filepath)
                                    files.append({
                                        'name': filename,
                                        'size': stat.st_size,
                                        'modified': stat.st_mtime
                                    })
                        except Exception as e:
                            logger.error(f"[HTTP] 获取文件列表失败: {e}")

                        self.wfile.write(json.dumps(files).encode('utf-8'))

                    # 添加API端点返回所有txt文件列表
                    elif self.path == '/api/txt-files' or self.path.startswith('/api/txt-files?'):
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()

                        import json
                        import os

                        txt_files = []
                        try:
                            for filename in os.listdir(directory):
                                if filename.endswith('.txt'):
                                    filepath = os.path.join(directory, filename)
                                    if os.path.isfile(filepath):
                                        stat = os.stat(filepath)
                                        base_name = filename[:-4]  # 去掉.txt扩展名

                                        # 检查配套文件是否存在
                                        png_exists = os.path.isfile(os.path.join(directory, f"{base_name}.png"))
                                        xyz_exists = os.path.isfile(os.path.join(directory, f"{base_name}.xyz"))

                                        txt_files.append({
                                            'name': filename,
                                            'base_name': base_name,
                                            'size': stat.st_size,
                                            'modified': stat.st_mtime,
                                            'has_png': png_exists,
                                            'has_xyz': xyz_exists
                                        })

                            # 按修改时间倒序排序
                            txt_files.sort(key=lambda x: x['modified'], reverse=True)
                        except Exception as e:
                            logger.error(f"[HTTP] 获取txt文件列表失败: {e}")

                        self.wfile.write(json.dumps(txt_files).encode('utf-8'))

                    # 添加API端点预览文件内容
                    elif self.path.startswith('/api/preview/'):
                        import os
                        from urllib.parse import unquote

                        # 提取文件名
                        filename = unquote(self.path[13:])  # 13 = len('/api/preview/')
                        filepath = os.path.join(directory, filename)

                        if not os.path.isfile(filepath):
                            self.send_response(404)
                            self.send_header('Content-type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(json.dumps({{'error': 'File not found'}}).encode('utf-8'))
                            return

                        try:
                            # 读取文件内容（限制大小）
                            max_size = 100 * 1024  # 100KB
                            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read(max_size)

                            self.send_response(200)
                            self.send_header('Content-type', 'text/plain; charset=utf-8')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(content.encode('utf-8'))

                            logger.info(f"[HTTP] 预览文件: {{filename}}")
                        except Exception as e:
                            logger.error(f"[HTTP] 预览文件失败: {{e}}")
                            self.send_response(500)
                            self.send_header('Content-type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(json.dumps({{'error': str(e)}}).encode('utf-8'))

                    # 添加API端点下载指定txt及配套文件（zip格式）
                    elif self.path.startswith('/api/download-set/'):
                        import json
                        import os
                        import zipfile
                        import io
                        from urllib.parse import unquote

                        # 提取文件名（去掉/api/download-set/前缀）
                        filename = unquote(self.path[18:])  # 18 = len('/api/download-set/')

                        # 如果文件名包含.txt，去掉它获取base_name
                        if filename.endswith('.txt'):
                            base_name = filename[:-4]
                        else:
                            base_name = filename

                        txt_file = os.path.join(directory, f"{base_name}.txt")
                        png_file = os.path.join(directory, f"{base_name}.png")
                        xyz_file = os.path.join(directory, f"{base_name}.xyz")

                        # 检查txt文件是否存在
                        if not os.path.isfile(txt_file):
                            self.send_response(404)
                            self.send_header('Content-type', 'application/json')
                            self.end_headers()
                            self.wfile.write(json.dumps({'error': 'txt文件不存在'}).encode('utf-8'))
                            return

                        try:
                            # 创建内存中的zip文件
                            zip_buffer = io.BytesIO()
                            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                                # 添加txt文件
                                zip_file.write(txt_file, f"{base_name}.txt")

                                # 添加png文件（如果存在）
                                if os.path.isfile(png_file):
                                    zip_file.write(png_file, f"{base_name}.png")

                                # 添加xyz文件（如果存在）
                                if os.path.isfile(xyz_file):
                                    zip_file.write(xyz_file, f"{base_name}.xyz")

                            # 发送zip文件
                            zip_buffer.seek(0)
                            self.send_response(200)
                            self.send_header('Content-type', 'application/zip')
                            self.send_header('Content-Disposition', f'attachment; filename="{base_name}.zip"')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(zip_buffer.read())

                            logger.info(f"[HTTP] 下载文件集: {base_name}")
                        except Exception as e:
                            logger.error(f"[HTTP] 创建zip文件失败: {e}")
                            self.send_response(500)
                            self.send_header('Content-type', 'application/json')
                            self.end_headers()
                            self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))

                    else:
                        # 默认处理
                        super().do_GET()

                def do_POST(self):
                    # 批量下载API端点
                    if self.path == '/api/download-batch':
                        import json
                        import os
                        import zipfile
                        import io

                        try:
                            # 读取POST数据
                            content_length = int(self.headers['Content-Length'])
                            post_data = self.rfile.read(content_length)
                            data = json.loads(post_data.decode('utf-8'))

                            files = data.get('files', [])

                            if not files:
                                self.send_response(400)
                                self.send_header('Content-type', 'application/json')
                                self.send_header('Access-Control-Allow-Origin', '*')
                                self.end_headers()
                                self.wfile.write(json.dumps({'error': 'No files specified'}).encode('utf-8'))
                                return

                            if len(files) > 50:
                                self.send_response(400)
                                self.send_header('Content-type', 'application/json')
                                self.send_header('Access-Control-Allow-Origin', '*')
                                self.end_headers()
                                self.wfile.write(json.dumps({'error': 'Too many files (max 50)'}).encode('utf-8'))
                                return

                            # 创建内存中的zip文件
                            zip_buffer = io.BytesIO()
                            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                                for filename in files:
                                    filepath = os.path.join(directory, filename)
                                    if os.path.isfile(filepath):
                                        zip_file.write(filepath, filename)
                                    else:
                                        logger.warning(f"[HTTP] 文件不存在，跳过: {filename}")

                            # 发送zip文件
                            zip_buffer.seek(0)
                            self.send_response(200)
                            self.send_header('Content-type', 'application/zip')
                            self.send_header('Content-Disposition', f'attachment; filename="selected_files.zip"')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(zip_buffer.read())

                            logger.info(f"[HTTP] 批量下载 {len(files)} 个文件")
                        except Exception as e:
                            logger.error(f"[HTTP] 批量下载失败: {e}")
                            self.send_response(500)
                            self.send_header('Content-type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))
                    else:
                        self.send_response(404)
                        self.send_header('Content-type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps({'error': 'Not found'}).encode('utf-8'))

                def log_message(self, format, *args):
                    # 自定义日志输出
                    logger.debug(f"[HTTP] {{format % args}}")

            # 尝试启动服务器，如果端口被占用则尝试其他端口
            port_to_try = self._port
            max_attempts = 10

            for attempt in range(max_attempts):
                try:
                    # 创建HTTP服务器
                    # 绑定到0.0.0.0允许外部访问
                    self._server = ThreadingHTTPServer(
                        ('0.0.0.0', port_to_try),
                        CustomHandler
                    )

                    self._running = True
                    self._actual_port = port_to_try

                    # 获取局域网IP
                    local_ip = get_local_ip()

                    # 发送启动成功信号（传递IP和端口）
                    server_url = f"http://{local_ip}:{self._actual_port}"
                    self.server_started.emit(server_url, self._actual_port)
                    logger.info(f"[HTTP服务器] 已启动: {server_url}")
                    logger.info(f"[HTTP服务器] 服务目录: {self._directory}")

                    # 启动服务器（阻塞调用）
                    self._server.serve_forever()
                    break

                except OSError as e:
                    if attempt < max_attempts - 1:
                        # 端口被占用，尝试下一个端口
                        logger.warning(f"[HTTP服务器] 端口 {port_to_try} 被占用，尝试端口 {port_to_try + 1}")
                        port_to_try += 1
                    else:
                        # 所有端口都尝试失败
                        error_msg = f"无法找到可用端口（尝试了 {self._port}-{port_to_try}）"
                        self.server_error.emit(error_msg)
                        logger.error(f"[HTTP服务器] {error_msg}")

        except Exception as e:
            # 其他未预期的错误
            error_msg = f"启动失败: {e}"
            self.server_error.emit(error_msg)
            logger.error(f"[HTTP服务器] {error_msg}")

    def stop(self):
        """停止HTTP服务器"""
        if self._server and self._running:
            self._running = False

            # 在单独的线程中关闭服务器，避免阻塞
            def shutdown_server():
                try:
                    self._server.shutdown()
                    logger.info("[HTTP服务器] 已停止")
                except Exception as e:
                    logger.error(f"[HTTP服务器] 停止时出错: {e}")

            shutdown_thread = threading.Thread(target=shutdown_server)
            shutdown_thread.daemon = True
            shutdown_thread.start()
