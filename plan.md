# 计划：将 pytdx 改为本地 whl 安装

## 目标
把 `pytdx>=1.72` 的依赖安装方式改为从本地 wheel 文件安装：  
`uv pip install E:\mw3\wspy\2026\pytdx\dist\pytdx-1.72-py3-none-any.whl`

## 步骤

### 1. 调整依赖声明
- [ ] 修改 `pyproject.toml` 中 `dependencies` 的 `pytdx>=1.72`：
  - 改为使用本地 wheel 的直接引用写法（`pytdx @ file:///...`）。
  - 路径使用 Windows 可识别且 `uv` 可解析的 file URL 形式。

### 2. 同步锁文件
- [ ] 执行 `uv lock`（或等效同步流程）更新 `uv.lock`：
  - 确保锁文件中 `pytdx` 来源为本地 wheel，而不是镜像源。

### 3. 安装验证
- [ ] 使用 `uv` 在当前项目环境执行安装验证：
  - 运行 `uv sync` 或 `uv pip install .`，确认依赖可解析并安装成功。
  - 验证 `pytdx` 版本为 `1.72`，且来源为本地 wheel。

## 说明
- 该方案可避免镜像源中 `pytdx` 包不可用或版本漂移问题。
- 保留其他依赖与镜像配置不变，仅替换 `pytdx` 安装来源。
