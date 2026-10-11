# atelier-studio

**Atelier 全量变体** —— 视频翻译 + 新闻日报 + 全部域,全功能前端。基本是旧 Architecture 在 Atelier 组合形态上的等价重建:一个平台,所有能力全开。

> **宪法在内核仓**:体系的架构/契约/纪律以 [atelier-core/docs/](https://forgejo.hurcaguari.top/Carnation/atelier-core/src/branch/master/docs)(CONSTITUTION + architecture/bus/storage/domains/boot/logging)为准。本 README 只给本变体的组装与运行导引。


YouTube 外语视频 → 翻译/配音/字幕/封面 → 多平台发布;并行新闻日报采集 → 编辑台选题 → 出刊。频道监控不限量、闲时排产、哨兵活性告警全开。

## 组成
- **内核**:`atelier-core`(**Forgejo PyPI 包**,非 git 依赖):`atelier-core>=0.1.0`,经 `[tool.uv.sources]` 指向内部 registry `debian.lan:3257/api/packages/Carnation/pypi`。语义解析、不再钉 commit;内核更新只需 bump+publish,变体 `uv sync` 自动升。认证走本机 `~/.netrc`(需 `machine debian.lan` 与 `machine forgejo.hurcaguari.top` 两条,后者是文件下载 host)。
- **全部域子模块**(`src/<域>`):web · scheduler · task · engines · platform_adapters · publishers(发布,进程域)· daemons(监控+日报采集+闲时排产)· **media**(通用媒体库,纯存储)· **sentinel**(哨兵)
- **依赖模型**:各域自带 `requirements.txt`(依赖声明归域所有);变体 pyproject 用 `dynamic=["dependencies"]` + `hatch-requirements-txt` 聚合(`files=[根 requirements.txt, src/engines/requirements.txt, …]`)。已迁:**引擎推理栈 → `src/engines/requirements.txt`**;其余尚未归域项暂留变体根 `requirements.txt`(后续 phase 分给 web/存储/platform_adapters/publishers)。解析器旋钮(override/index/prerelease)留 `[tool.uv]`。
- **双管线**(`src/pipelines/`):**video**(视频翻译)· **digest**(新闻日报)——task build 经插件聚合
- **服务子模块**(`src/services/`):bgutil-server(YouTube POT 供给器,node)
- **前端**:`frontend/` = **Carnation/atelier-frontend 子模块**(体系前端基线=全功能前端,不裁);全量=基准本体,基准更新即随

## 与其他变体的关系
| 变体 | 域集 | 管线 | 前端 |
|---|---|---|---|
| reel-solo | 精简(publishers 空转) | video | in-repo 单表单 |
| reel-creator | + daemons + publishers | video | in-repo(裁 Digest) |
| **studio** | **全部 9 域** | **video + digest** | **全功能子模块(不裁)** |

## 关键点
- **全量无上限**:`daemons_settings.max_channels` 默认 0=监控源不限量。
- **子仓 PR 与主仓集成**:子仓 PR 只运行 `atelier-core` 的机器化纪律检查与该仓语言的语法检查，不在子仓阶段跑组装/集成测试。域仓 checkout 根目录含 `header.py` 时，机器检查命令为 `python -m atelier_core.discipline .`；该扫描只覆盖可见规则，不等同完整行为契约审查。子仓合并后以实际合并 SHA 事件化通知主仓，主仓按 `.gitmodules` 找出受影响路径，在 `atelier-studio`、`atelier-reel-solo`、`atelier-reel-creator` 创建或更新指针 PR；不再将分支 `--remote` 结果直接推到父仓 master。父仓 PR 才是组装与项目测试的集成边界，验证候选 gitlink SHA、运行 `python -m src.main --check` 和可移植测试子集；依赖推理栈或本机 fixture 的测试会明确标记未覆盖，不伪报通过。当前先落实这些机器门禁，暂不接入 Codex PR 审查。
- **双向镜像与凭据**:GitHub 是唯一的 PR 审查/合并入口；Forgejo 与 GitHub 通过 `atelier-sync` 双向同步 Git refs，不同步 PR 审批。继续使用现有 `GH_SYNC_TOKEN`，不创建新 token。当前 Actions/Forgejo Secret 无法显示实际 token scope；已知用途与最低权限需求见 [`docs/submodule-automation-plan.md`](docs/submodule-automation-plan.md)。该文档不含 token 值，并将本地 CLI 登录权限与 Actions secret 明确区分。机器纪律扫描不覆盖 B1–B7 行为规范；组装自检不检查 M12 配置或 M13 真实数据库漂移。可手动触发全量组装检查，不做定时轮询。
- **双管线**:task build 插件聚合 video + digest;前端任务类型下拉纯 snapshot 驱动(后端实际挂载即真相源)。
- **自动发布**:publishers = 进程域(`KIND=process`·lazy),网关按需拉起子进程执行、闲时回收;需平台 OAuth 账号。
- **哨兵**:阈值走 atelier-core `SentinelCfg` 默认,可加 `[sentinel]` 段覆盖。
- **POT**:bgutil 边车(supervisor 动态端口),YouTube 采集防风控。
- 端口:web 8097 / gateway 8793 / bgutil 动态。

## 跑
```bash
git submodule update --init --recursive
# 边车构建(一次性):bgutil —— server/ 子目录装依赖 + tsc 编译 src→build/main.js
cd src/services/bgutil-server/server && npm ci && npx tsc && cd -
# 前端构建(全功能子模块)
cd frontend && npm ci && npm run build && cd -
uv sync                              # 或本机借 Architecture 胖 venv(见 reel-solo README B 法)
uv run python -m src.main --config config.toml
```
