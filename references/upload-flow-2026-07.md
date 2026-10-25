# 上传 FIT 流程优化与事故链（2026-07-04 ~ 07-06）

## 用户反复反馈的痛点

| 时间 | 用户原话 | 解读 |
|---|---|---|
| 07-04 | "我去你能干点啥" | refresh token 失败后想走完整 OAuth，绕远路 |
| 07-04 | "墨迹啥" | token 拿到后流程没一口气走完，反复查 duplicate |
| 07-05 | "不是你能续上直接做就完了" | refresh 成功后别废话直接上传 |
| 07-05 | "改标题呀" | 我先分辩"标题乱码"没查就回话，用户打断 |
| 07-06 | "你是不是忘了纠偏了？" | 第二次上传没识别 `.fit.orig` 存在，触发双纠偏 |
| 07-06 | "你说你能干点啥，天天弄得事都能搞错" | 综合批评：流程混乱、出错、还要用户纠正 |

## 一条龙工作流（07-04 修订，强制执行）

**5 步连做，不要中途问用户：**

1. **refresh token**（30 秒，失败才走 OAuth）
2. **查 Strava 该日期活动**（避免 duplicate，已传过的不要重传）
3. **cp QQ 附件**（去掉 `doc_<uuid>_` 前缀）
4. **上传**（带 `--correct-coords`，脚本已修双纠偏 bug）
5. **抓活动详情 + 报告**（距离/时长/起点/链接）

**禁止打断的场景：**
- refresh 失败 → **直接走 OAuth**（不要问"是否走 OAuth"）
- duplicate → **直接报"已存在 activity id"**（不要问"是否删原版"）
- 纠偏 → **自动识别文件状态**（不要让用户判断）
- 标题乱码 → **先查 Strava 实际标题**（不要被 terminal 截断误导）

## 双纠偏 bug 事故复盘（07-06）

**根因：** `strava-upload.py` 第 763-775 行纠偏逻辑只看 `args.correct_coords` 标志，不检查文件是否已经纠偏过。

**触发：**
1. 第一次上传：原始 GCJ-02 → 纠偏 WGS-84 → 备份 `.fit.orig`
2. 第二次上传：QQ 重新发同文件 → `cp` 覆盖 `.fit` 但 `.fit.orig` 还在
3. 脚本看到 `.fit.orig` 存在但**没判断这个标志 = 跳过纠偏**，仍调用 `correct_fit_file()`
4. 把已经 WGS-84 的文件再 GCJ→WGS 反算 → 偏移翻倍叠加

**验证命令（30 秒）：**

```python
import fitdecode, json, requests
def to_deg(s): return s / (2**31/180)
def read_start(p):
    with fitdecode.FitReader(p) as fit:
        for c in fit:
            if type(c).__name__ != 'FitDataMessage' or c.mesg_type.name != 'record': continue
            la = lo = None
            for f in c.fields:
                if f.name == 'position_lat': la = to_deg(f.value)
                if f.name == 'position_long': lo = to_deg(f.value)
            if la and lo: return (la, lo)

qq = read_start('~/cache/documents/doc_*.fit')
fix = read_start('~/strava-uploads/MAGENE_C706_*.fit')

# 单次纠偏 diff_lon ≈ -0.0046° (410m 西)
# 双纠偏 diff_lon ≈ -0.0093° (825m 西)
print(f'diff_lon = {fix[1] - qq[1]:+.5f}°')
if abs(fix[1] - qq[1]) > 0.008:
    print('❌ 双纠偏 bug!')
```

**修复（已 commit 到 strava-upload.py 第 763-783 行）：**

```python
if args.correct_coords:
    orig_marker = fit_path + '.orig'
    already_corrected = os.path.exists(orig_marker)
    if already_corrected:
        print(f"[跳过纠偏] 检测到 .orig 备份")
    else:
        # 原纠偏逻辑
```

**07-06 实战：**
- 旧 activity id `19195542869`（双纠偏版本，起点 lon 108.8381 偏西 825m）
- 新 activity id `19195640259`（单次纠偏，起点 lon 108.8427）
- 路线起点对比：本地纠偏版 = Strava 显示 ✅

## OnelapFit vs Strava 渲染差异（07-02 / 07-06）

**OnelapFit 用 GCJ-02 渲染，Strava 用 WGS-84。**

纠偏后 WGS-84 FIT 传到 Strava → Strava 上路线位置正确
但用户经常用 OnelapFit 看 → OnelapFit 把 WGS-84 当 GCJ-02 解释 → 往东南飘 410m

**用户反馈："纠偏差那么远你搞错了吧"**——实际是 OnelapFit 渲染问题。

**判断方法：**
- 路线核心在"雁塔区/昆明池/斗门街道"等地名 → OnelapFit 和 Strava 都标这些 → **位置对**
- 两条路线平均最近距离 ~487m → **是不同骑行**（不是纠偏问题）

## 03-31 历史数据先查 Strava（07-04）

**用户发 03-31 FIT 后说"331 是刚才发错的，传今天就行了"。**

**正确做法：** 收到 FIT 先查 Strava 该日期：
```python
r = requests.get('https://www.strava.com/api/v3/athlete/activities?per_page=50', headers=H)
for a in r.json():
    if date in a.get('start_date_local',''):
        print(f'已存在: {a["id"]}')
```

查到已存在 → 直接报"已存在"，不要上传。

## QQ 附件 cp 文件名陷阱（07-04/07-06）

**缓存命名：** `~/.hermes/cache/documents/doc_<uuid>_qqdownloadftnv5`

**直接 cp 不改名** → 文件名是 `doc_xxx_MAGENE_C706_*.fit` → 脚本找不到

**正确：**
```bash
cp ~/cache/documents/doc_<uuid>_MAGENE_C706_YYYY-MM-DD_*.fit \
   ~/strava-uploads/MAGENE_C706_YYYY-MM-DD_HHMMSS_<DEVICE_UID>.fit
```

## token 写入文件必须校验长度（07-02 教训 + 07-04 复现）

```python
assert len(tok['access_token']) == 40, f'❌ token 长度不对: {len(tok["access_token"])}'
```

`json.dump` 写入是完整 40 字符，但**展示在 terminal / QQ 时会被截断成 `0cf037...930a`**。如果按展示值写入就废了（07-02 真实坑过）。

## OnelapFit 渲染 GCJ-02 / Strava 渲染 WGS-84（坐标系对照表）

| 平台 | 坐标系 | 看 FIT 原始（GCJ-02）位置 | 看纠偏后（WGS-84）位置 |
|---|---|---|---|
| OnelapFit | GCJ-02 | 真实位置 ✅ | 偏东南 410m（被当 GCJ-02） |
| Strava | WGS-84 | 偏西北 410m（被当 WGS-84）| 真实位置 ✅ |
| Google Maps | WGS-84 | 偏西北 410m | 真实位置 ✅ |
| 高德/百度 | GCJ-02 | 真实位置 ✅ | 偏东南 410m |