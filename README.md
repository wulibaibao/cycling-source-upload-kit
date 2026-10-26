# cycling-source-upload-kit

多平台骑行 FIT 同步工具集 —— 把 **迈金/顽鹿(OneLap)**、**IGPSPORT**、**Strava**、**行者(XOSS)** 四个平台的骑行数据对齐的完整实现。

目前 **5 条同步链路**：

| 链路 | 方向 | 服务端去重 | 坐标系 |
|---|---|---|---|
| A | Strava → 行者 | ✅ 行者有 | WGS-84 → GCJ-02 |
| B | 迈金 → IGPSPORT | ✅ IGPS 有 | GCJ-02（不转） |
| C | 本地 FIT → Strava | ✅ Strava 有 | WGS-84 |
| **D** | **Strava → 迈金** | ❌ **迈金无** | WGS-84 → GCJ-02 |
| **E** | **Strava → IGPSPORT** | ✅ IGPS 有 | WGS-84 → GCJ-02 |

为 [Hermes Agent](https://hermes-agent.nousresearch.com/docs) 编写，也可作为独立脚本使用。

> ⚠️ **本仓库不含任何密钥。** 所有凭据运行时从环境变量或本地文件读取，见 [CREDENTIALS.md](CREDENTIALS.md)。

## 它解决什么问题

骑行数据散落在三个平台，各自的 API 都是逆向出来的，且**坐标系处理各不相同** —— 搞错就是 400-500m 偏移。本工具集把三条链路的完整实现和踩过的坑打包在一起：

| 链路 | 方向 | 坐标系 | 说明 |
|---|---|---|---|
| **A** | Strava → 行者 | **需转** GCJ-02 | Strava 无原始文件下载（付费墙），只能拉 streams 重建 FIT |
| **B** | 迈金 → IGPSPORT | **不转** | 两边都按 GCJ-02 存，实测坐标差 0.00 m |
| **C** | 本地 FIT → Strava | **需转** GCJ-02→WGS-84 | `--correct-coords` |

**坐标系是三条链路最大的差异。** 不要因为"国内平台都用 GCJ-02"就一刀切 —— IGPSPORT 是个例外。

## 快速开始

```bash
# 1. 依赖
pip install -r requirements.txt

# 2. 凭据（见 CREDENTIALS.md）
mkdir -p ~/.hermes/igp_sync
echo -n '<顽鹿签名密钥>' > ~/.hermes/igp_sync/onelap_sign_key
echo -n '<IGPSPORT HMAC 密钥>' > ~/.hermes/igp_sync/hmac_key
chmod 600 ~/.hermes/igp_sync/onelap_sign_key ~/.hermes/igp_sync/hmac_key

# 3a. 手动上传 FIT 到 Strava
python3 scripts/strava-upload.py /path/to/file.fit Ride --correct-coords

# 3b. 迈金 → IGPSPORT 同步
python3 scripts/magene-to-igp.py --since 2026-09-25 --until 2026-10-25 --limit 50

# 3c. Strava → 行者 同步
python3 scripts/strava-to-xingzhe-sync.py --since 2026-10-01 --until 2026-10-20 --limit 30
```

## 核心特性

### 坐标系投票判定

Strava 活动的坐标系**不固定**（取决于用户用什么 App 上传），所以不能写死。本工具用**投票法**判定：采样多个点，分别算到 GCJ-02 / WGS-84 参考系的偏移，取多数票。

> ⚠️ 参考实现 [ridefitsync](https://github.com/sizaif/ridefitsync) 的判定方向是**反的**（实测 10/10 全票判错）。本工具已修正。

### 三层去重闸门

**服务端索引是真相，本地 state 只是缓存。**

- 链路 A：行者私有活动索引（实时）→ uploads MD5 索引 → 才上传
- 链路 B：IGPSPORT 全量活动索引（`begin_time=None`）按「日期 + 距离容差」匹配

> 行者 OpenAPI 对相同 MD5 **不返回 9006**，而是直接创建新活动。MD5 索引闸门是必需的。
> IGPSPORT 索引必须拉全量 —— 用时间过滤会漏更早记录，导致补传时产生重复。

### 坏记录自动维护

拉到坏记录（坐标明显漂移、经纬度互换）时，通过平台私有删除接口**自动清理后重传**，不依赖手动处理。

### Token 自动续期

三个平台的凭据都会过期：

| 平台 | 有效期 | 续期方式 |
|---|---|---|
| 顽鹿 | JWT | 密码自动重登 |
| IGPSPORT | 7 天 | `refreshToken`（**必须保存轮换后的新值**） |
| Strava | 6 小时 | `grant_type=refresh_token` |

提前 6 小时刷新（IGPSPORT 的刷新端点要求旧 Bearer 仍有效，不能等彻底过期）。刷新失败会告警，不会静默失效。

## 目录结构

```
cycling-source-upload-kit/
├── SKILL.md                    # 主文档：接口契约、坐标系矩阵、18 条踩坑清单
├── CREDENTIALS.md              # 凭据配置指南
├── README.md                   # 本文件
├── requirements.txt
├── scripts/
│   ├── strava-to-xingzhe-sync.py   # 链路 A 主脚本
│   ├── run-xingzhe-sync.py         # 链路 A cron 入口
│   ├── onelap_client.py            # 顽鹿/迈金 API 客户端
│   ├── igp_client.py               # IGPSPORT API 客户端
│   ├── magene-to-igp.py            # 链路 B 主脚本
│   ├── run-magene-igp-sync.py      # 链路 B cron 入口
│   ├── strava-upload.py            # 链路 C 主脚本
│   ├── strava-to-onelap.py         # 链路 D 主脚本（Strava → 迈金）
│   ├── run-onelap-sync.py          # 链路 D cron 入口
│   ├── strava-to-igp.py            # 链路 E 主脚本（Strava → IGPSPORT）
│   ├── run-strava-igp-sync.py      # 链路 E cron 入口
│   ├── export-skill.sh             # 导出/发布（含密钥扫描）
│   └── correct_*.py                # 坐标纠偏诊断工具
└── references/                 # 24 篇详细文档
```

## 定时任务

cron 在**上午 11 点**依次跑，窗口统一是 **day-2 ~ day-0**（前天+昨天+今天）：

| 时间 | 脚本 | 方向 | 输出 |
|---|---|---|---|
| 11:00 | `run-github-trending-refresh.py` | 博客 Trending（非骑行） | 有变化才输出 |
| 11:05 | `run-magene-igp-sync.py` | 迈金 → IGPSPORT | 有新同步才输出 |
| 11:10 | `run-xingzhe-sync.py` | Strava → 行者 | 有新同步才输出 |
| 11:15 | `run-onelap-sync.py` | Strava → 迈金 | 有新同步才输出 |
| 11:20 | `run-ai-article-scrape.py` | AI 文章抓取（非骑行） | 有新文章才输出 |
| 11:25 | `run-strava-igp-sync.py` | Strava → IGPSPORT | 有新同步才输出 |

> **为什么是 day-2~day-0 而不是 day-2 单日？**
> - `day-1` 会漏掉「今天早上」骑的（一天两练时必然漏一条）
> - `day-0` 覆盖今天
> - `day-2` 起步是容忍源平台数据延迟入库（迈金→Strava 这条链路实测有延迟，
>   当天骑的常常中午才同步到 Strava）

> **cron 入口必须是 `~/.hermes/scripts/` 下的真文件** —— Hermes 的 cron 守卫会 `resolve()` 软链接并要求路径落在 scripts 目录内，软链出去会被拦。

> **⚠️ `scripts/run-*-sync.py` 不是给独立使用者直接跑的。**
> 它们是 [Hermes Agent](https://hermes-agent.nousresearch.com/docs) 的 cron 入口，依赖：
> - `~/.hermes/scripts/_serial_lock.py`（全局串行锁，与其它任务共用）
> - `~/.hermes/skills/cycling-data-upload/scripts/` 下的主脚本
> - 本机的 token / 凭据文件
>
> 独立使用请直接跑主脚本（见「快速开始」），别用 `run-*.py`。

### 全局串行锁

所有 cron 任务共用 `~/.hermes/run/serial.lock`（`flock` 独占锁）：
**抢不到锁就等一小段时间**（各任务 wait_s 不同，120~240 秒），等不到才放弃本轮。
抢不到时**会明确报告**「本轮未执行：全局锁被占用」—— 不会静默消失。

```bash
cat ~/.hermes/run/serial.lock   # 查看当前持锁者
```

## 参考文档

| 文档 | 内容 |
|---|---|
| `references/strava-xingzhe-sync-detail.md` | 链路 A 完整细节 |
| `references/magene-igp-sync-detail.md` | 链路 B 完整细节（OneLap 签名、三步 OSS 上传） |
| `references/strava-upload-detail.md` | 链路 C 完整细节（OAuth、标题算法、偏移五分类） |
| `references/strava-streams-to-fit.md` | Strava Streams → FIT 重建 |
| `references/fit-method-comparison.md` | 三种 FIT 修复方法决策树 |
| `references/fitdecode-encoder-pitfalls.md` | fitdecode + Encoder 实操坑 |
| `references/xingzhe-api-mechanics.md` | 行者 API 细节（OpenAPI vs 私有接口） |
| `references/magene-c706-gcj02-offset.md` | GCJ-02 系统偏移识别 |
| `references/oauth-application-swap-2026-07-02.md` | Strava Application 切换恢复 API |
| `references/skill-export-recipe.md` | Skill 打包导出流程 |

## 致谢

- [ridefitsync](https://github.com/sizaif/ridefitsync) —— IGPSPORT 上传流程与坐标系判定思路（判定方向已修正）
- [SyncOnelapToXoss](https://github.com/moruoxian/SyncOnelapToXoss) —— 顽鹿/OneLap API 逆向

## License

MIT
