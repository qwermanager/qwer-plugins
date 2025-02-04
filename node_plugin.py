import os
import zipfile
from pathlib import Path
import winreg
import shutil
from request_manager import RequestManager
from PyQt6.QtCore import QThread, pyqtSignal
from plugin_interface import Plugin

class DownloadThread(QThread):
    finished = pyqtSignal(bool)  # 下载完成信号
    error = pyqtSignal(str)  # 下载错误信号

    def __init__(self, url, path, request_manager):
        super().__init__()
        self.url = url
        self.path = path
        self.request_manager = request_manager

    def run(self):
        try:
            response = self.request_manager.get(self.url, stream=True)
            if isinstance(response, str):
                self.error.emit(response)
                self.finished.emit(False)
                return
            with open(self.path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            self.finished.emit(True)
        except Exception as e:
            self.error.emit(f"下载失败: {e}")
            self.finished.emit(False)

class NodePlugin(Plugin):
    def __init__(self, packages_dir):
        super().__init__(packages_dir)
        self.node_dir = self.packages_dir / "node"
        self.node_dir.mkdir(parents=True, exist_ok=True)
        self.request_manager = RequestManager()
        self.download_thread = None

    def get_name(self):
        """获取插件名称"""
        return "node"

    def get_available_versions(self) -> list[str]:
        """获取可用的 Node.js 版本列表"""
        result = self.request_manager.get("https://nodejs.org/dist/index.json")
        if isinstance(result, list):
            versions = []
            for item in result:
                version = item["version"]
                lts = item.get("lts", False)
                if lts:
                    version += f" (LTS: {lts})"
                versions.append(version)
            return versions
        return []

    def get_installed_versions(self) -> list[str]:
        """获取已安装的 Node.js 版本列表"""
        if not self.node_dir.exists():
            return []
        return [dir.name for dir in self.node_dir.iterdir() if dir.is_dir()]

    def install(self, version: str) -> str:
        """安装指定版本的 Node.js"""
        clean_version = version.split(" ")[0]
        download_url = f"https://nodejs.org/dist/{clean_version}/node-{clean_version}-win-x64.zip"
        download_path = self.node_dir / f"node-{clean_version}.zip"
        
        # 开始下载
        if not self._download_file(download_url, download_path):
            return "下载失败"
        return "下载已开始"

    def _download_file(self, url, path):
        """下载文件"""
        self.download_thread = DownloadThread(url, path, self.request_manager)
        self.download_thread.finished.connect(lambda success: self._handle_download_complete(success, path))
        self.download_thread.start()
        return True

    def _handle_download_complete(self, success: bool, download_path: Path):
        """处理下载完成"""
        if success and self._is_valid_zip(download_path):
            clean_version = download_path.stem.split("-")[1]
            install_dir = self.node_dir / clean_version
            self._extract_zip(download_path, install_dir)
            try:
                download_path.unlink()
            except Exception as e:
                print(f"删除压缩文件失败: {e}")

    def uninstall(self, version: str) -> str:
        """卸载指定版本的 Node.js"""
        clean_version = version.split(" ")[0]
        install_dir = self.node_dir / clean_version
        if install_dir.exists():
            try:
                shutil.rmtree(install_dir)
                return f"已卸载 Node.js {clean_version}"
            except PermissionError as e:
                return f"卸载失败: {e}"
        return f"未找到 Node.js {clean_version}"

    def set_default(self, version: str) -> str:
        """将指定版本设为默认版本"""
        return self.use_version(version)

    def use_version(self, version: str) -> str:
        """切换到指定版本"""
        clean_version = version.split(" ")[0]
        install_dir = self.node_dir / clean_version
        if install_dir.exists():
            self._remove_all_node_paths()
            node_bin_dir = install_dir / f"node-{clean_version}-win-x64"
            self._update_environment_variable(node_bin_dir)
            return f"已切换到 Node.js {clean_version}"
        return f"未找到 Node.js {clean_version}"

    def get_current_version(self) -> str|None:
        """获取当前使用的版本"""
        with winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER) as registry:
            with winreg.OpenKey(registry, r"Environment", 0, winreg.KEY_READ) as key:
                try:
                    current_path, _ = winreg.QueryValueEx(key, "Path")
                    for part in current_path.split(";"):
                        if "node" in part.lower() and "win-x64" in part.lower():
                            version = part.split("node-")[1].split("-win-x64")[0]
                            return version
                except FileNotFoundError:
                    pass
        return None

    def _is_valid_zip(self, path):
        """检查 ZIP 文件是否有效"""
        try:
            with zipfile.ZipFile(path, "r") as zip_ref:
                return zip_ref.testzip() is None
        except zipfile.BadZipFile:
            return False

    def _extract_zip(self, zip_path, extract_dir):
        """解压 ZIP 文件"""
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)

    def _update_environment_variable(self, node_bin_dir):
        """更新系统环境变量"""
        node_path = str(node_bin_dir)
        with winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER) as registry:
            with winreg.OpenKey(registry, r"Environment", 0, winreg.KEY_ALL_ACCESS) as key:
                try:
                    current_path, _ = winreg.QueryValueEx(key, "Path")
                    if node_path not in current_path:
                        new_path = f"{current_path};{node_path}" if current_path else node_path
                        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
                except FileNotFoundError:
                    winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, node_path)
        self._broadcast_environment_change()

    def _remove_all_node_paths(self):
        """从系统用户变量中删除所有 Node.js 路径"""
        with winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER) as registry:
            with winreg.OpenKey(registry, r"Environment", 0, winreg.KEY_ALL_ACCESS) as key:
                try:
                    current_path, _ = winreg.QueryValueEx(key, "Path")
                    new_path = ";".join([path for path in current_path.split(";") 
                                       if "node" not in path.lower() or "win-x64" not in path.lower()])
                    winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
                except FileNotFoundError:
                    pass
        self._broadcast_environment_change()

    def _broadcast_environment_change(self):
        """通知系统环境变量已更新"""
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None
        )