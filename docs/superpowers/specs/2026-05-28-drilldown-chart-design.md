# Strategy Result Drilldown Design

Date: 2026-05-28
Status: Approved

## Goal

Enable clicking a value in the p5/p10/p20/p60 columns of the strategy results table to open a modal showing a cumulative return line chart for that stock over the corresponding period.

## Approach

**Method A: in-memory data + JSON API + ECharts modal**

Reuse the `_loaded_data` dict already held by `BaseTask` after strategy execution. A new API endpoint extracts the relevant price slice and computes cumulative returns on the fly. The frontend renders an ECharts line chart in a Bootstrap modal.

No new data storage, no PG re-query, no precomputation.

## Data Flow

```
User clicks <td data-code="510050" data-field="p20">
  → JS: fetch /api/strategy/drilldown/{run_id}?code=510050&field=p20
    → route: lookup _running_tasks[run_id]["task_ref"]
      → task_ref._loaded_data["510050"] → DataFrame
        → slice last (bars_for_days(20, interval) + 1) bars
          → cum_ret[i] = (close[i] - close[0]) / close[0]
            → return JSON {code, field, label, dates[], values[]}
  → JS: ECharts line chart in Bootstrap modal
```

## Backend Changes

### 1. strategy_runner.py

- In `_execute()`, after `task.run()`, store the task reference:
  ```python
  _running_tasks[run_id]["task_ref"] = task
  ```
- New public function `get_drilldown_data(run_id, code, field)`:
  - Validate run_id exists and is "complete"
  - Get `task_ref` from `_running_tasks[run_id]`
  - Get DataFrame from `task_ref._loaded_data[code]`
  - Map field name to day count: p60→60, p20→20, p10→10, p5→5
  - Get `task_ref._bar_interval` (BarInterval object)
  - Compute `n = bars_for_days(day_count, interval)`
  - Slice `df.iloc[-(n+1):]`
  - Compute cumulative return series: `(close - close[0]) / close[0]`
  - Return dict: `{code, field, label, dates: [...], values: [...]}`
  - Error cases: run_id not found → 404, task has no loaded data → 404, code not found → 404

### 2. routes/strategy.py

- New endpoint:
  ```
  GET /api/strategy/drilldown/{run_id}?code=510050&field=p20
  ```
- Auth: `get_current_user` (same as results page)
- Returns JSON response with the drilldown data
- 404 if run_id / code not available

## Frontend Changes

### 3. _results.html

**Clickable cells:**

In the `<tbody>` rendering loop, detect p5/p10/p20/p60 columns and render them as clickable:
- Add `class="drilldown-cell"` with inline style `cursor:pointer; text-decoration:underline dotted;`
- Add `data-code`, `data-field`, `data-run-id` attributes
- `onclick="showDrilldown(this)"`

**Modal markup** (added once at template bottom):
```html
<div class="modal fade" id="drilldownModal" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="drilldownTitle"></h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <div id="drilldownChart" style="width:100%;height:350px;"></div>
      </div>
    </div>
  </div>
</div>
```

**JavaScript:**

- `showDrilldown(el)`: reads data attributes, fetches API, calls `renderDrilldownChart`
- `renderDrilldownChart(data)`:
  - ECharts line chart
  - x-axis: date/time labels from `data.dates`
  - y-axis: cumulative return in % (formatted as `{value}%`)
  - Tooltip showing date + cumulative return
  - Title: `"{code} {name} - {label} 累计涨幅"`

**ECharts CDN:**

Added via `<script>` tag in the template, only loaded when status is "complete". Uses the public CDN (e.g., `https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js`).

## Files Changed

| File | Change |
|---|---|
| `src/quant_etf/dashboard/services/strategy_runner.py` | Store `task_ref`; add `get_drilldown_data()` |
| `src/quant_etf/dashboard/routes/strategy.py` | Add `GET /drilldown/{run_id}` endpoint |
| `src/quant_etf/dashboard/templates/strategy/_results.html` | Clickable cells + modal + ECharts + JS |

**Unchanged:** strategy.py, tasks.py, data_source.py, bar_interval.py, conf.py, all other templates.

## Lifecycle

Drilldown data lives in `_running_tasks` memory alongside strategy results. Service restart clears it — same behavior as strategy results themselves.

## Edge Cases

- **Task completed but `_loaded_data` missing**: Return 404 with message "Drilldown data not available"
- **Code not in loaded data**: Return 404
- **Insufficient bars for requested period**: Return empty series with a note
- **Multiple clicks**: Reuse existing chart instance (dispose + reinit), no accumulation
