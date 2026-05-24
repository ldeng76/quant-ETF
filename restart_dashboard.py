"""
一键重启量化ETF看板（Dashboard）服务

Usage:
    uv run python restart_dashboard.py

功能：
    1. 查找并终止监听指定端口的旧 Dashboard 进程
    2. 等待端口释放
    3. 后台启动新的 Dashboard 服务
    4. 输出每个步骤的执行状态
"""

import os
import sys
import time
import signal
import subprocess
from pathlib import Path

# 从环境变量获取端口，默认 8522（与 config.py 保持一致）
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8522"))
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")


def find_processes_on_port(port: int) -> list[dict]:
    """查找监听指定端口的所有进程（Windows 兼容）"""
    processes = []
    try:
        # 使用 netstat 查找监听指定端口的进程 PID
        result = subprocess.run(
            ["netstat", "-aon", "-p", "TCP"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            # 匹配 LISTENING 状态且端口匹配的行
            if "LISTENING" not in line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local_addr = parts[1]
            # 解析地址格式如 127.0.0.1:8522 或 0.0.0.0:8522
            if local_addr.endswith(f":{port}"):
                pid = int(parts[4])
                processes.append({"pid": pid, "addr": local_addr})
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  [警告] 查询端口进程时出错: {e}")
    return processes


def get_process_name(pid: int) -> str:
    """获取进程名称"""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip():
            # CSV 格式: "python.exe","12345","Console","1","25,600 K"
            parts = result.stdout.strip().split(",")
            if parts:
                return parts[0].strip('"')
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return "<未知>"


def stop_old_service(port: int) -> None:
    """停止旧服务"""
    print(f"[1/3] 正在查找监听端口 {port} 的旧服务...")

    procs = find_processes_on_port(port)
    if not procs:
        print(f"  端口 {port} 上没有运行中的服务，无需停止。")
        return

    for proc in procs:
        pid = proc["pid"]
        name = get_process_name(pid)
        print(f"  发现进程: PID={pid} ({name}), 监听地址={proc['addr']}")

        try:
            # 发送终止信号（Windows 上 SIGTERM 等同于 taskkill）
            os.kill(pid, signal.SIGTERM)
            print(f"  已发送终止信号 -> PID {pid}")
        except PermissionError:
            print(f"  [警告] 无权限终止进程 PID {pid}，尝试使用 taskkill...")
            try:
                subprocess.run(["taskkill", "/PID", str(pid)], timeout=5)
                print(f"  已通过 taskkill 终止 PID {pid}")
            except Exception as e2:
                print(f"  [错误] 终止进程失败: {e2}")
        except ProcessLookupError:
            print(f"  进程 PID {pid} 已不存在，跳过。")
        except Exception as e:
            print(f"  [错误] 终止进程 PID {pid} 失败: {e}")

    # 等待端口释放
    print(f"[2/3] 等待端口 {port} 释放...")
    for i in range(10):
        time.sleep(0.5)
        remaining = find_processes_on_port(port)
        if not remaining:
            print(f"  旧服务已停止，端口 {port} 已释放。（耗时 {(i+1)*0.5:.1f}s）")
            return

    print(f"  [警告] 端口 {port} 在 5s 后仍未释放，继续启动新服务...")


def start_new_service() -> subprocess.Popen:
    """后台启动新的 Dashboard 服务"""
    project_root = Path(__file__).parent
    script_path = project_root / "run_dashboard.py"

    print(f"[3/3] 正在启动新服务...")
    print(f"  命令: uv run python {script_path}")

    # 使用 subprocess.Popen 后台启动，不阻塞当前终端
    # CREATE_NEW_PROCESS_GROUP 使子进程在独立进程组中，不受 Ctrl+C 影响
    proc = subprocess.Popen(
        ["uv", "run", "python", str(script_path)],
        cwd=str(project_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    # 短暂等待确认进程启动成功
    time.sleep(1)
    if proc.poll() is not None:
        print(f"  [错误] 新服务启动失败，进程已退出 (exit code: {proc.returncode})")
        print(f"  请尝试手动运行诊断: uv run python run_dashboard.py")
        sys.exit(1)

    return proc


def main():
    print("=" * 50)
    print("  量化ETF看板 - 一键重启服务")
    print("=" * 50)
    print()

    # Step 1 & 2: 停止旧服务 + 等待端口释放
    stop_old_service(DASHBOARD_PORT)
    print()

    # Step 3: 启动新服务
    proc = start_new_service()
    print()

    # 状态反馈
    url = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"
    print("-" * 50)
    print(f"  新服务已启动!")
    print(f"  访问地址: {url}")
    print(f"  进程 PID: {proc.pid}")
    print(f"  如需停止服务，请运行:")
    print(f"    taskkill /PID {proc.pid}")
    print("-" * 50)


if __name__ == "__main__":
    main()
