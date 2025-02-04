import os
import zipfile
from pathlib import Path
import winreg
import shutil
import json
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
        """线程执行方法"""
        try:
            response = self.request_manager.get(self.url, stream=True)
            if isinstance(response, str):  # 如果返回的是错误信息
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

class GoPlugin(Plugin):
    def __init__(self, packages_dir):
        super().__init__(packages_dir)
        self.go_dir = self.packages_dir / "go"
        self.go_dir.mkdir(parents=True, exist_ok=True)
        self.request_manager = RequestManager()
        self.download_thread = None  # 初始化下载线程
        self.go_bin_path_str = None  # 定义 go_bin_path_str

    def get_name(self):
        """获取插件名称"""
        return "go"

    def get_available_versions(self) -> list[str]:
        """获取可用的 Go 版本列表"""
        url = "https://golang.org/dl/?mode=json"
        result = self.request_manager.get(url)
        if isinstance(result, list):
            return [item["version"] for item in result]
        else:
            return []

    def get_installed_versions(self) -> list[str]:
        """获取已安装的 Go 版本列表"""
        installed_versions = []
        for item in self.go_dir.iterdir():
            if item.is_dir() and item.name.startswith("go"):  # 检查是否是 Go 版本目录
                version = item.name
                installed_versions.append(version)
        return installed_versions

    def get_current_version(self) -> str|None:
        """从系统用户变量中获取当前使用的版本"""
        with winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER) as registry:
            with winreg.OpenKey(registry, r"Environment", 0, winreg.KEY_READ) as key:
                try:
                    current_path, _ = winreg.QueryValueEx(key, "Path")
                    for part in current_path.split(";"):
                        if "go" in part.lower() and "bin" in part.lower():
                            # 从路径中提取版本号
                            version = part.split("go\\")[1].split("\\bin")[0]
                            return f"{version}"
                except FileNotFoundError:
                    pass
        return None

    def install(self, version: str) -> str:
        """安装指定版本的 Go"""
        # 定义压缩文件名、版本号、解压的目标文件夹
        zip_filename = f"{version}.windows-amd64.zip"  # 压缩文件名
        download_url = f"https://golang.org/dl/{zip_filename}"  # 下载 URL
        download_path = self.go_dir / zip_filename  # 下载路径
        extract_dir = self.go_dir / version  # 解压的目标文件夹

        # 开始下载
        if not self._download_file(download_url, download_path):
            return "下载失败"

        # 等待下载完成
        if self.download_thread:
            self.download_thread.wait()

        # 检查 ZIP 文件是否有效
        if not self._is_valid_zip(download_path):
            return "下载的文件无效"

        # 解压 ZIP 文件
        if not self._unzip_file(download_path, extract_dir):
            return "解压失败"

        # 定义 go_bin_path_str 并传递给 _set_go_path
        self.go_bin_path_str = str(extract_dir / "go" / "bin")  # 将路径转换为字符串
        if self._set_go_path(self.go_bin_path_str):
            print(f"已将 {self.go_bin_path_str} 添加到 PATH 环境变量")
        return f"Go {version} 安装完成"

    def uninstall(self, version: str) -> str:
        """卸载指定版本的 Go"""
        install_dir = self.go_dir / version
        print('install_dir', install_dir)
        if install_dir.exists():
            try:
                # 使用 shutil.rmtree 删除目录
                shutil.rmtree(install_dir)
                return f"已卸载 Go {version}"
            except PermissionError as e:
                return f"卸载失败: {e}"
        return f"未找到 Go {version}"

    def use_version(self, version: str) -> str:
        """切换到指定版本"""
        install_dir = self.go_dir / f"{version}"
        print('install_dir', install_dir)
        if install_dir.exists():
            # 删除原来的 Go 路径
            self._remove_all_go_paths()

            # 添加新的 Go 路径
            go_bin_dir = install_dir / "bin"
            self._update_environment_variable(go_bin_dir)

            return f"已切换到 Go {version}"
        return f"未找到 Go {version}"

    def set_default(self, version: str) -> str:
        """将指定版本设为默认版本"""
        return self.use_version(version)

    def _download_file(self, url, path):
        """下载文件"""
        self.download_thread = DownloadThread(url, path, self.request_manager)
        self.download_thread.start()
        return True  # 立即返回，表示下载已开始

    def _is_valid_zip(self, path):
        """检查 ZIP 文件是否有效"""
        try:
            with zipfile.ZipFile(path, "r") as zip_ref:
                return zip_ref.testzip() is None  # 如果文件有效，返回 True
        except zipfile.BadZipFile:
            return False

    def _unzip_file(self, zip_path, extract_dir):
        """解压 ZIP 文件到指定目录"""
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)
            return True
        except Exception as e:
            print(f"解压失败: {e}")
            return False

    def _set_go_path(self, go_bin_path_str):
        """设置 Go 的环境变量路径"""
        if os.path.exists(go_bin_path_str):  # 检查路径是否存在
            self._remove_all_go_paths()
            # 将 Go 的 bin 目录添加到系统 PATH 环境变量
            os.environ["PATH"] = f"{go_bin_path_str}{os.pathsep}{os.environ.get('PATH', '')}"
            return True  # 返回成功标志
        return False  # 如果路径不存在，返回失败标志

    def _update_environment_variable(self, go_bin_dir):
        """更新系统环境变量"""
        go_path = str(go_bin_dir)
        with winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER) as registry:
            with winreg.OpenKey(registry, r"Environment", 0, winreg.KEY_ALL_ACCESS) as key:
                try:
                    current_path, _ = winreg.QueryValueEx(key, "Path")
                    if go_path not in current_path:
                        new_path = f"{current_path};{go_path}" if current_path else go_path
                        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
                except FileNotFoundError:
                    winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, go_path)
        # 通知系统环境变量已更新
        self._broadcast_environment_change()

    def _remove_all_go_paths(self):
        """从系统用户变量中删除所有 Go 路径"""
        with winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER) as registry:
            with winreg.OpenKey(registry, r"Environment", 0, winreg.KEY_ALL_ACCESS) as key:
                try:
                    current_path, _ = winreg.QueryValueEx(key, "Path")
                    new_path = ";".join([path for path in current_path.split(";") if "go" not in path.lower() or "bin" not in path.lower()])
                    winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
                except FileNotFoundError:
                    pass
        # 通知系统环境变量已更新
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