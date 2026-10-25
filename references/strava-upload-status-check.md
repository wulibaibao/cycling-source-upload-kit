# Strava API 上传状态查询 + 重复检测 + 自动删除检测

## 查询上传状态

```python
import json, requests

TOKEN_FILE = '~/.hermes/strava_token.json'
tok = json.load(open(TOKEN_FILE))
headers = {'Authorization': f'Bearer {tok["access_token"]}'}

upload_id = '19762628501'
resp = requests.get(f'https://www.strava.com/api/v3/uploads/{upload_id}', headers=headers)
data = resp.json()
print(json.dumps(data, indent=2, ensure_ascii=False))
```

## 典型响应解读

| status | error 字段 | 含义 | 操作 |
|--------|-----------|------|------|
| `Your activity is still being processed.` | null | 上传进行中，等待几秒 | 轮询重查 |
| `There was an error processing your activity.` | null | 处理失败 | 查 error 字段 |
| `There was an error processing your activity.` | `duplicate of /activities/18615808588` | **已成功创建活动**，只是标记为重复 | 无需再次上传，去 Strava 看活动 |
| `The external_id is already associated with an activity you own.` | — | 同上，重复 | 无需再次上传 |

## ⚠️ Strava 自动删除活动（GPS 漂移严重时）

**问题现象**：上传 API 返回 `activity_id`（非报错），但随后查询该活动返回 404，或活动字段 `start_latlng: null`，或 `name` 为空。

**根本原因**：当 FIT 文件 GPS 坐标严重漂移（如纬度飞到 68°，完全失锁），Strava 处理过程中会将该活动**静默删除**，不通知 API。返回了 activity_id 但活动实际已被销毁。

**判断流程**（上传后立即执行）：

```python
import json, requests
TOKEN_FILE = '~/.hermes/strava_token.json'
tok = json.load(open(TOKEN_FILE))
headers = {'Authorization': f'Bearer {tok["access_token"]}'}

upload_resp = requests.post('https://www.strava.com/api/v3/uploads', ...)
upload_data = upload_resp.json()
activity_id = upload_data.get('activity_id')

# 立即验证活动是否存在
verify_resp = requests.get(f'https://www.strava.com/api/v3/activities/{activity_id}', headers=headers)
if verify_resp.status_code == 404 or verify_resp.json().get('start_latlng') is None:
    print("活动被 Strava 自动删除，需 Encoder 重建 FIT")
else:
    act = verify_resp.json()
    print(f"活动正常: {act['name']}, 距离 {act['distance']/1000:.1f}km")
```

**判断标准**：
- `status_code == 404` → 活动已删除
- `start_latlng == null` → 活动已删除
- `name` 为空或 `distance == 0` → 活动异常

**后续处理**（与普通 GPS 漂移相同）：
1. 在 Strava 网页确认活动是否真的已不存在
2. 用 Encoder 重建干净 FIT（见 `references/fit-rebuild-encoder.md`）
3. 重新上传

## 通过活动 ID 查详情

```python
act_id = 18615808588
resp = requests.get(f'https://www.strava.com/api/v3/activities/{act_id}', headers=headers)
act = resp.json()
print(f"Name: {act['name']}")
print(f"Type: {act['type']}")
print(f"Date: {act['start_date']}")
print(f"Distance: {act['distance']/1000:.2f} km")
```

## 找到某个 FIT 文件对应的已上传活动

如果不确定某天的记录是否已上传，可以按文件名里的时间戳 + 距离来搜索：

```python
# 查找 2026-05-23 附近 106km 左右的所有 Ride 活动
import datetime
resp = requests.get(
    'https://www.strava.com/api/v3/activities',
    headers=headers,
    params={'after': 1747872000, 'before': 1748131200, 'per_page': 30}  # 2026-05-22 ~ 2026-05-25
)
# after/before 是 Unix timestamp（UTC）
```