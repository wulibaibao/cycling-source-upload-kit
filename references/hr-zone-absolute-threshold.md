---
title: 心率区间判断——绝对阈值法（06-02 修订）
created: 2026-06-02
related: strava-upload.py::get_hr_zone
---

# 心率区间判断：绝对阈值法 vs max_hr 百分比法

## TL;DR

**用绝对 avg_hr 阈值，不用 `avg_hr / max_hr` 百分比。** 因为 max_hr 在轻松骑时偏低，百分比法会系统性高估强度。

## 阈值表（参考 max_hr≈166）

| 区间 | avg_hr 范围 | 标题 |
|---|---|---|
| Z1 | < 120 bpm | Z1 轻松区 |
| Z2 | 120–139 bpm | Z2 脂肪燃烧区 |
| Z3 | 140–151 bpm | Z3 有氧耐力区 |
| Z4 | 152–163 bpm | Z4 乳酸阈值区 |
| Z5 | ≥ 164 bpm | Z5 最大摄氧区 |

阈值依据：max_hr=166（用户历史最大心率），按运动医学标准的心率储备法百分比换算：
- Z1 < 72% HRmax (120)
- Z2 72–84% (120–140)
- Z3 84–91% (140–152)
- Z4 91–98% (152–163)
- Z5 ≥ 98% (164)

## 为什么不用 max_hr 百分比法（06-02 踩坑记录）

**旧逻辑（06-01 用了两天）：**
```python
pct = avg_hr / max_hr * 100
if pct < 60: Z1
elif pct < 70: Z2
elif pct < 80: Z3
elif pct < 90: Z4
else: Z5
```

**Bug 案例：**

| 日期 | avg_hr | max_hr | 旧算法 pct | 旧判 | 新阈值 | 实际体感 |
|---|---|---|---|---|---|---|
| 06-01 早 | 141 | 166 | 85% | Z3 ✅ | Z3 | 有氧耐力（合理）|
| 06-02 早 | 134 | 154 | **87%** | **Z4 ❌** | **Z2** | 脂肪燃烧（轻松）|

**6-02 那次骑行用户明确反馈："我发现每次都是乳酸阈值区"——其实 6-02 是轻松骑（avg 134），因为 max_hr 只拉到 154，比例被放大到 87% 误判 Z4。**

**根本原因：** max_hr 是一次骑行的**最高瞬时心率**，不是用户的**真实最大心率**。轻松骑时 max_hr 天然偏低，百分比被放大。绝对阈值法用历史已知的 max_hr=166 做分母，规避了这个问题。

## 新代码（`strava-upload.py` line 715-731）

```python
def get_hr_zone(avg_hr, max_hr) -> str:
    """心率区间估算（绝对心率阈值法，参考 max_hr=166）

    之前的 max_hr 百分比法有 bug：当骑行没达到最大心率时（如 max_hr=154），
    avg_hr 134 会被算成 87% → Z4 阈值（错误）。改用绝对心率阈值。
    """
    if max_hr <= 0:
        return "未知"
    if avg_hr < 120:
        return "Z1 轻松区"
    elif avg_hr < 140:
        return "Z2 脂肪燃烧区"
    elif avg_hr < 152:
        return "Z3 有氧耐力区"
    elif avg_hr < 164:
        return "Z4 乳酸阈值区"
    else:
        return "Z5 最大摄氧区"
```

## 已修正的活动（06-02）

| Activity ID | 原标题 | 修正后 | 触发原因 |
|---|---|---|---|
| 18735826730 | Z4 乳酸阈值区（upload 时误判）| Z3 有氧耐力区 | 改脚本后重跑分析 → 实际是 Z3 |
| 18748867877 | Z4 乳酸阈值区 | Z2 脂肪燃烧区 | avg 134，max 154，百分比误判 |

修正代码：
```python
import os
import json, requests
tok = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
headers = {'Authorization': f'Bearer {tok["access_token"]}'}
r = requests.patch(f'https://www.strava.com/api/v3/activities/{aid}',
                   headers=headers, json={'name': new_name})
```

## 何时需要重新校准阈值

- 用户年龄变化（max_hr 公式 220-age）→ 调整阈值
- 体能提升，max_hr 推高 → 用历史 max_hr 重新算百分比
- 阈值表偏离体感（用户说"我现在 Z2 都得 150+"）→ 上调阈值

## 区间名版本历史（避免混乱）

| 日期 | 区间名版本 | 来源 |
|---|---|---|
| 06-01 SKILL.md 旧版 | 热身/轻松/马拉松/乳酸阈值/极限 | 用户口头偏好 |
| 06-01 strava-upload.py 旧版 | 轻松/脂肪燃烧/有氧耐力/乳酸阈值/最大摄氧 | 通用运动医学命名 |
| 06-02 统一版 | **轻松/脂肪燃烧/有氧耐力/乳酸阈值/最大摄氧** | 以脚本实际输出为准 |

**06-02 起以 `strava-upload.py::get_hr_zone()` 输出为准，其他地方全部对齐。**
