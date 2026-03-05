import psutil
import subprocess


def get_tdx_pid():
  """通过进程名或路径特征获取通达信进程的PID"""
  for proc in psutil.process_iter(['pid', 'name', 'exe']):
    try:
      # 匹配条件1：精确匹配进程名（考虑Windows大小写不敏感特性）
      if proc.info['name'] and 'tdxw.exe' == proc.info['name'].lower():
        return proc.pid

      # 匹配条件2：路径特征匹配（适配不同安装位置的通达信客户端）
      exe_path = proc.info['exe'] or ''
      if 'new_tdx' in exe_path and 'tdxw.exe' in exe_path:
        return proc.pid

    except (psutil.NoSuchProcess, psutil.AccessDenied):
      # 处理进程已结束或权限不足的情况，继续遍历其他进程
      continue
  return None


def find_ip_by_pid(pid, port):
  """通过PID和端口号查找对应的网络连接信息"""

  # 构建命令：查找指定PID和端口的网络连接
  cmd = f'netstat -ano | findstr "{pid}" | findstr "{port}"'
  try:
    # 执行命令并捕获输出
    result = subprocess.check_output(cmd, shell=True, text=True)
    # 按行分割并解析每部分信息
    lines = [line.split() for line in result.strip().split('\n')]
    return [{
      'proto': parts[0],  # 协议类型（TCP/UDP）
      'local': parts[1],  # 本地地址:端口
      'remote': parts[2],  # 远端地址:端口（即行情服务器地址）
      'state': parts[3],  # 连接状态（ESTABLISHED等）
      'pid': parts[4]  # 进程ID（用于二次验证）
    } for parts in lines]
  except subprocess.CalledProcessError:
    # 未找到匹配结果时返回空列表
    return []


def get_tdx_hq_poc(ip, port=7709):
  from pytdx.hq import TdxHq_API

  # 创建 API 对象
  api = TdxHq_API()

  # 连接服务器
  if api.connect(ip, port):
    print("连接成功！")
    # 获取单只股票的实时行情
    stock_data = api.get_security_quotes(1, '600519')
    print(stock_data)
    # 做一些操作
    api.disconnect()  # 关闭连接
  else:
    print("连接失败！")

def get_tdx_k_poc(ip, port=7709):
  from pytdx.hq import TdxHq_API

  # 创建 API 对象
  api = TdxHq_API()

  """K线种类 
  0 5分钟K线 
  1 15分钟K线 
  2 30分钟K线 
  3 1小时K线 
  4 日K线
  5 周K线
  6 月K线
  7 1分钟
  8 1分钟K线 
  9 日K线
  10 季K线
  11 年K线 
  """
  # 连接服务器
  if api.connect(ip, port):
    print("连接成功！")
    # 获取 K 线数据
    k_data = api.get_security_bars(0, 1, '600519', 0, 10)
    df = api.to_df(k_data)
    print(df)

    api.disconnect()  # 关闭连接
  else:
    print("连接失败！")

def poc1():
  # 步骤1：获取通达信客户端PID
  pid = get_tdx_pid()

  # 步骤2：查找该PID与7709端口（通达信默认行情端口）的连接信息
  ip_port = find_ip_by_pid(pid, 7709)

  # 步骤3：处理查询结果
  if len(ip_port):
    for part in ip_port:
      print("通达信行情服务器为：", part['remote'])
      get_tdx_hq_poc(part['remote'])
  else:
    print("未找到通达信行情服务器")

def poc2():
  from pytdx.util.best_ip import select_best_ip
  # best_ip = select_best_ip()
  # print(f"最优服务器：IP={best_ip['ip']}, 端口={best_ip['port']}")
  # get_tdx_hq_poc(best_ip['ip'], best_ip['port'])
  get_tdx_hq_poc('60.191.117.167')
  get_tdx_k_poc('60.191.117.167')


# 主执行流程
if __name__ == "__main__":
  poc2()

