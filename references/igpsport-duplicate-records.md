# IGPSPORT 历史遗留：74 条重复记录（2026-10-26 发现）

`queryMyActivity` 返回的 516 条里，有 **74 条 `dataStatus=4`** 的记录，每条都能找到一条
`dataStatus=1` 的**同日期同距离**副本（例：`57995298` 与 `57952733` 都是 2026-01-29 / 26812.8m）。

- `dataStatus=1`：正常条目，`title` 是有意义的名字（`MATCH_470592-2026-10-26-07-03-27-log`）。
- `dataStatus=4`：文件级副本，`title` 是原始文件名（`18615857830.fit`），但 `rideDistance`/`totalAscent`/`avgSpeed`/`analysisStatus=1` **数据完全正常**。
- 这是早期去重还没做好时批量上传留下的。

**服务端无法删除**：`deleteActivity` / `delete` / `remove` / `delActivity` 等端点全部返回
`{"code":1003,"message":"服务器异常"}`（带签名也一样），不存在可用的删除 API。
列表接口也不提供 `dataStatus` 过滤参数（`totalRows` 恒为 516）。

**推测**：App 端按 `dataStatus` 过滤，只显示 `dataStatus=1`。**未经用户确认**。

**如果 App 里确实看到重复**：只能找 IGPSPORT 客服让他们后台清理，没有自助路径。

**不影响同步**：去重闸门比对的是 `dataStatus=1` 的记录，重复副本不会干扰匹配。
