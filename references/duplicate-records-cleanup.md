# 历史重复记录的识别与清理（2026-10-26）

四个平台的历史数据都检查过，重复情况**不一样**：

| 平台 | 总数 | 重复 | 能否自助清理 |
|---|---|---|---|
| 迈金 / OneLap | 456 | **0** | — |
| 行者 | 308 → **301** | 7 组 | ✅ `DELETE /api/v1/pgworkout/{id}` |
| IGPSPORT | 516 | **74**（`dataStatus=4`） | ❌ 无删除 API，只能找客服 |
| Strava | 361 | 1 组（0.00km 空活动） | ✅ `DELETE /api/v3/activities/{id}` |

## 1. 行者：可以删

```http
DELETE https://www.imxingzhe.com/api/v1/pgworkout/{workout_id}
Authorization: Bearer <access_token>
→ 200 OK
```

`openapi/v1/activities/` 只有 GET（405），删除必须走**私有接口** `api/v1/pgworkout/`。

### 识别影子副本：看 `credits`

同一活动被重复上传时，行者会建两条：
- `credits > 0` → 正式记录（正常显示、有积分）
- `credits == 0` → 影子副本（重复上传产生的）

实测：行者 308 条里有 7 条 `credits=0`，每条都有一条同日期同距离的 `credits>0` 副本。
删掉 7 条影子副本后 → 301 条，同日期同距离组数 **0**。

### 清理脚本（一次性，慎用）

```python
import requests, datetime
H = {"Authorization": f"Bearer {access_token}"}

def key(a):
    t = datetime.datetime.fromtimestamp(a.get("start_time", 0) / 1000)
    return (t.strftime("%Y-%m-%d"), round((a.get("distance") or 0) / 1000, 2))

for a in [x for x in xz if x.get("credits") == 0]:
    partners = [x for x in xz if key(x) == key(a) and x.get("id") != a["id"] and x.get("credits")]
    if not partners:
        print(f"❌ {a['id']} 没有配对记录，跳过"); continue
    r = requests.delete(f"https://www.imxingzhe.com/api/v1/pgworkout/{a['id']}", headers=H, timeout=25)
    print(f"{'✅' if r.status_code == 200 else '❌'} 删 {a['id']} → 保留 {partners[0]['id']}")
```

**必须先验证配对存在**（同日期 + 同距离 + `credits>0`），否则会删掉唯一的一条。

### 根因

`openapi/v1/uploads/` 上传记录里同一 md5 出现 2–3 次（实测 198 条记录里 18 组重复），
来源是 **2026-07 第一版 Strava→行者脚本还没做去重闸门**的时候。
现在的三层闸门（服务端活动 / md5 / state）已能挡住。

## 2. 迈金：本来就干净

`www.onelap.cn` 456 条，同日期同距离组数 **0**。原生数据源，没有历史导入问题。

迈金**有**删除接口：`DELETE /api/otm/ride_record/delete/{id}` ✅（实测有效，
清理过一条探测产生的重复 `6adee216b48d416dc20c9a94`）。

## 3. IGPSPORT：删不掉

`queryMyActivity` 返回的 516 条里有 **74 条 `dataStatus=4`**，每条都能找到一条
`dataStatus=1` 的**同日期同距离**副本（例：`57995298` 与 `57952733` 都是 2026-01-29 / 26812.8m）。

- `dataStatus=1`：正常条目，`title` 是有意义的名字（`MATCH_470592-2026-10-26-07-03-27-log`）。
- `dataStatus=4`：文件级副本，`title` 是原始文件名（`18615857830.fit`），但 `rideDistance`/`totalAscent`/`avgSpeed`/`analysisStatus=1` **数据完全正常**。
- 这是早期去重还没做好时批量上传留下的。

**服务端无法删除**：`deleteActivity` / `delete` / `remove` / `delActivity` / `removeActivity` 等
端点全部返回 `{"code":1003,"message":"服务器异常"}`（带签名也一样），不存在可用的删除 API。
列表接口也不提供 `dataStatus` 过滤参数（`totalRows` 恒为 516）。

**推测**：App 端按 `dataStatus` 过滤，只显示 `dataStatus=1`。**未经用户确认**。

**如果 App 里确实看到重复**：只能找 IGPSPORT 客服让他们后台清理，没有自助路径。

**不影响同步**：去重闸门比对的是 `dataStatus=1` 的记录，重复副本不会干扰匹配。

## 4. Strava

361 条里只有 1 组重复：`2025-04-27 0.00km`（`14301402123` / `14301402306`）—— 两条空活动。
可删：`DELETE https://www.strava.com/api/v3/activities/{id}`。
