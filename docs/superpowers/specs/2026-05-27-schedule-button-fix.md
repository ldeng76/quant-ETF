# 调度表状态按钮语义修复

## 问题

定时策略配置表中，状态列和操作按钮的语义不一致：
- 状态显示"已启用"，但按钮显示"启动"（应该显示"停止"）

## 根因

按钮逻辑只判断了 `running`（是否正在执行），没有判断 `enabled`（是否已启用）：
- `running=True` → 按钮"停止" ✓
- `running=False, enabled=True` → 按钮"启动" ✗（调度已启用，按钮应为"停止"）
- `running=False, enabled=False` → 按钮"启动" ✓

## 修改方案

### 修改文件

`src/quant_etf/dashboard/templates/monitor/_schedule_table.html`

### 修改内容

按钮的 `enabled` 属性判断从 `s.running` 改为 `s.enabled`：

```html
<!-- Before -->
<button class="btn btn-sm btn-outline-{{ 'danger' if s.running else 'success' }}"
        hx-post="/api/market/schedules/{{ s.id }}/toggle">
    {{ '停止' if s.running else '启动' }}
</button>

<!-- After -->
<button class="btn btn-sm btn-outline-{{ 'danger' if s.enabled else 'success' }}"
        hx-post="/api/market/schedules/{{ s.id }}/toggle">
    {{ '停止' if s.enabled else '启动' }}
</button>
```

### 最终效果

| 状态列 | 按钮 | 按钮颜色 |
|--------|------|----------|
| 运行中 | 停止 | 红色边框 |
| 已启用 | 停止 | 红色边框 |
| 已停用 | 启动 | 绿色边框 |

按钮始终反映 `enabled` 字段的值，点击后切换 enabled 状态。
