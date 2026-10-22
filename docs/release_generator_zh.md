# CANN 发布生成器与触发器

[English](release_generator.md) | [简体中文](release_generator_zh.md)

`tools/cann_release/generator.py` 把一个 CANN 版本转换成完整的发布变更集。`.github/workflows/release_cann_agent.yml` 由 `/release-cann` issue 评论触发，运行生成器、做门禁校验并推送分支。代码拆分为 `generator.py`（CLI 与编排）、`matrix.py`、`repo_files.py`、`docs.py`；`registry.py` 与 `availability.py` 与监控器共用。监控器本身见 `docs/cann_release_monitor.md`。

## 约定

* 生成基于**上游**发布仓库（默认 `Ascend/cann-container-image@main`），而不是本仓库自身状态，因此维护者可以直接从生成的分支向上游提 PR。
* 工作流**止步于分支**：它回帖给出分支名和 `compare` 链接，真正打开上游 PR 是人工动作，不涉及跨仓库 token。
* 生成器不提交任何东西：它只写工作区，由工作流负责提交、推送和汇报。

## 触发方式

```
/release-cann <version>              (正式版)
/release-cann <version> <x.y.Tn>     (beta)
```

同时满足以下条件才会接受：评论位于标题为 `New CANN release detected: <version>` 的 issue 下、评论者是仓库 owner、评论中的版本与 issue 标题一致、两个 token 都通过正则校验。`workflow_dispatch`（带 `version` / `link_id` 输入）可绕过守卫用于演练。

`tools/cann_release/prepare_upstream_state.sh <upstream-url> <ref> <branch>`：

1. 把 `tools/` 复制到 `/tmp/cann-tools`（上游没有这套自动化，工作流继续使用副本，且副本永远不会被提交）；
2. 把上游 ref 抓取到本仓库（完整抓取，保证后续推送到 fork 时对象齐全）；
3. 在该上游提交处强制创建发布分支；
4. 把策略文件恢复到 `tools/cann_release/` 作为未跟踪的辅助文件。

随后的暂存门禁只显式添加**发布相关路径**——绝不用 `git add -A`（那会吞掉未跟踪的策略文件）——并在提交前校验暂存集合非空且完全位于允许列表内。

## 发布矩阵是声明式的

`tools/cann_release/release_matrix.json` 是"一次发布包含什么"的唯一事实来源：

```jsonc
{
  "cann": {
    "chips": ["310p", "910", "910b", "A3", "950"],
    "os": [{"name": "ubuntu", "version": "22.04"},
           {"name": "openeuler", "version": "24.03"}],
    "py_versions": ["3.10", "3.11", "3.12"]
  },
  "manylinux": {
    "chips": ["950", "910b", "310p", "A3"],
    "os": [{"name": "manylinux", "version": "2_34"}],
    "py_versions": ["3.10", "3.11", "3.12"]
  }
}
```

* 生成器按列表顺序构建 `chips x os x py`。增删芯片 / OS / Python 版本只是**一行配置**，不需要改代码，也不需要手工编辑 arg 文件。
* 上一个发布只提供条目顺序与格式模板；笛卡尔积永远来自这个文件。
* 与上一个发布的差异会被打印（新增 / 删除的组合），但从不阻塞——配置就是意图。
* `arches` **不是**矩阵维度。一个 tag 会构建两个架构；可用性策略（`tools/cann_release/release_profiles.json`）列出必须存在于 OBS 的包。
* 每个生成的 tag 在写入前都会对 OBS 探测（toolkit、nnal、各芯片 ops、两个架构）。缺失或无法判定即中止。

## 生成器依赖的文件格式

| 约定 | 位置 | 失败表现 |
|---|---|---|
| `        options:` 后跟 `          - <tag>` | 四个 build/push yml | 报出期望的锚点，并提示更新生成器或 workflow |
| `ALPHA_DICT = { ... }` 以仅含 `}` 的行结尾 | `tools/template.py` | `EXIT_DRIFT`，附带期望形状 |
| JSON 必须字节可复现（`json.dump` indent 2 或 4，可选文件尾换行） | 四个注册 JSON | `EXIT_FORMAT`；绝不静默重排 |
| 每个 OVERVIEW 恰好一对 `<!-- RELEASED:BEGIN/END -->` 和 `<!-- LATEST:BEGIN/END -->` | `OVERVIEW.md`、`OVERVIEW.zh.md` | `EXIT_DRIFT`，提示补回标记或设置 `docs.mode=skip` |
| `### CANN <version>` 标题 | `supported_tags.md` | `EXIT_DRIFT` |

`tools/cann_release/tests/test_contracts.py` 会对真实文件断言以上全部约定，因此格式变化会在 PR 阶段失败，而不是等到发版时。

## 文档区块

`OVERVIEW.md` / `OVERVIEW.zh.md` 是散文式文档，其中只有两个由生成器拥有的围栏区域：

```
<!-- RELEASED:BEGIN --> ... <!-- RELEASED:END -->
<!-- LATEST:BEGIN -->  ... <!-- LATEST:END -->
```

标记之外可以随意重写。标记之内由生成器写入发布语句、LATEST 标题/简介/表格/注释，并使用各语言自己的措辞与分隔符（`toolkit, ops, nnal` 与 `toolkit、ops、nnal`）。tag 命名示例行和 quick-start 中的 `CANN_TAG=` 示例刻意**不**自动化：它们只是文档示例（仍是有效的 tag），因此不会阻塞发版，可以手工修改。

`tools/cann_release/release_profiles.json` 提供 `docs.mode`：

* `block` —— 重写标记块（默认）；
* `skip` —— 完全不动文档（适合文档正在大改的时期）；生成器会明确报告已跳过。

## 试运行

```
python3 tools/cann_release/generator.py <version> [--link-id <x.y.Tn>] --dry-run
```

校验版本、构建矩阵、探测 OBS，并打印每个 tag 以及将要改动的文件——不触碰工作区。适合评审矩阵变更或新增芯片时使用。

## 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 策略拒绝（未知命名、beta 缺 link-id、正式版带 link-id） |
| 3 | 包不可用或探测无法判定 |
| 4 | 版本已被覆盖 |
| 5 | 漂移 / 仓库状态不一致（锚点、标记、矩阵形状） |
| 6 | 注册 JSON 的格式无法被 `json.dump` 复现 |

## 测试

`pytest -q tools/cann_release/tests`（PR 上也会由 `.github/workflows/test_tools.yml` 运行）：

* `test_matrix.py` —— 笛卡尔积、顺序、tag 约定、差异打印。
* `test_overview.py` —— 标记块替换（中英）、EOF 保持、`docs.mode=skip`、标记缺失。
* `test_supported_tags.py` —— 区块渲染、文档顺序。
* `test_guards.py` —— 版本分类、link-id 规则、退出码、无损 JSON。
* `test_contracts.py` —— 上述格式约定，以及中英文文档结构一致性检查。

## 已完成的验证

用声明式生成器重放了真实的 `9.2.0-beta.2` 发布提交（`a36525fb`，父提交 `814c9cfa`）：四个注册 JSON、四个 workflow yml、`supported_tags.md`、`template.py` 以及标记围栏内的 OVERVIEW 区块**全部逐字节一致**。唯一的差异是预期的活数据（Python patch 版本发生了变化）以及上面提到的两个由人工维护的示例区域。

## 已知限制

* Beta 版本需要 OBS 的 `x.y.Tn` 目录，无法从版本号推导；监控器的扫描会发现它并打印在 issue 里，便于复制。
* 生成器只在 Ubuntu（CI）上运行，并依赖上述仓库布局；布局一旦变化，契约测试会立刻暴露。
* 它只校验打包完整性，不验证 NPU 上的运行时行为。
