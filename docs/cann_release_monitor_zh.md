# CANN 版本监控

[English](cann_release_monitor.md) | [简体中文](cann_release_monitor_zh.md)

`tools/cann_release/monitor.py` 监控 CANN 社区公告，报告本仓库尚未覆盖的版本。每日工作流会把每个可发版的版本变成一个通知 issue，由维护者处理。监控器只读：不写仓库，也不构建任何镜像。

## 什么算作新版本

一个版本必须同时通过三项检查才会被报告。

1. **尚未覆盖。** 只要任一已知 tag、publish 路径、workflow 选项行或 `supported_tags.md` 标题带有*完全相同的版本段*，就算已覆盖。`9.2.0-beta.2-910b-...` 覆盖 `9.2.0-beta.2`，但绝不能覆盖之后的 `9.2.0` 正式版——否则只发过 beta 的仓库会静默吞掉正式版公告。
2. **足够新。** 两个守卫取"或"，既让有意跳过的历史保持安静，又不漏掉早于正式版发布的版本：
   * 版本号高于最新已覆盖版本；和/或
   * 公告发布时间晚于已覆盖版本中最新的发布时间。
   前者捕获 `9.2.0-beta.1`（公告 2026/08/11），而此时最新的已覆盖版本是 `9.1.1`（公告 2026/09/01）；后者捕获版本号更低但发布更晚的版本。
3. **包已齐备。** OBS 上必须存在完整包集合：toolkit、nnal，以及当前矩阵中每个芯片的 ops 包，且两个架构齐全（`tools/cann_release/availability.py`）。已公告但包不全的版本（例如 `9.1.0-beta.2`，其 910b/310p/A3/910 的 ops 包从未上传）只会列在运行摘要里，不会开 issue。

## 报告与 issue 生命周期

`--check` 向 stdout 打印 JSON：

```jsonc
{
  "new_versions": [
    {"version": "9.3.0", "publishTime": "2026/09/16", "oss_dir": ""}
  ],
  "announced_not_buildable": [
    {"version": "9.1.0-beta.2", "publishTime": "2026/07/09",
     "reason": "package set incomplete under 9.1.T7 (8/14 files missing)",
     "missing": ["Ascend-cann-310p-ops_9.1.0-beta.2_linux-aarch64.run"]}
  ]
}
```

* 只有 `new_versions` 会创建 issue；`announced_not_buildable` 写入工作流运行摘要。
* issue 标题固定为 `New CANN release detected: <version>`。
* 去重要求标题完全匹配，不能只靠 token 搜索（仅用 `in:title` 时 `9.3.0` 会前缀匹配到 `9.3.0-beta.1`）。
* issue 正文每个版本只写一次（版本、公告时间、使用提示，以及 beta 自动探测到的 OBS 目录），已存在的 issue 不会被每天改写。
* 版本一旦被覆盖，对应 issue 自动关闭。
* 公告返回空是错误，绝不当作"没有新版本"——否则 API 变更会把未关闭的通知误关掉。

## 可用性探测

`tools/cann_release/availability.py` 把匿名 HEAD 响应分为：

| 响应 | 含义 |
|---|---|
| 200 | 存在 |
| 403 / 404（可配置） | 缺失 |
| 其它状态码、超时、连接重置 | 未知——按指数退避重试；只有所有尝试都未知才判定失败 |

未知绝不能当成"缺失"：否则网关抖动会静默压掉真实的发布。探测策略（超时、并发、重试、退避、`unknown_budget`）位于 `tools/cann_release/release_profiles.json`。

Beta 版本没有可推导的 OBS 目录（`CANN x.y.Tn`），因此按 `workers` 分批扫描候选目录，命中第一个完整目录即停止；一旦有 `unknown_budget` 个候选无法判定就中止——网关不可达时几分钟内失败，而不是把 600 个候选全部重试一遍。

## 工作流

`.github/workflows/check_cann_release.yml`

* 触发：每日 cron `23 1 * * *`（01:23 UTC）和 `workflow_dispatch`。
* 权限：`contents: read`、`issues: write`。
* 由 `cann-release-check` 并发组串行化。
* issue 就是通知渠道：GitHub 会按 watch 设置通知本仓库的订阅者。已公告但包不全的版本不会开 issue，只写入运行摘要。

仓库必须**启用 Issues**，否则创建 issue 会失败并报错。

## 本地运行

```bash
pip install -r requirements.txt
python3 tools/cann_release/monitor.py --check    # 打印 JSON 报告
pytest -q tools/cann_release/tests               # 单元 + 契约测试
```

测试会打桩所有 HTTP 调用，离线几秒内跑完。

## 已知限制

* 公告列表接口是内部网关（需要 `Referer`，payload 无公开文档）。结构变化会抛 `BulletinAPIError` 并让运行失败——这是刻意的大声失败，但需要更新代码。
* 包可用性通过公共 OBS 桶的 HEAD 请求判断；`x.y.Tn` 扫描假设历史目录结构不变，且上限为 `beta_scan_max`（600）。
* 监控器只校验*打包完整性*，不验证 NPU 上的运行时行为。
