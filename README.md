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
- **PR 宪法检查与子模块提交事件验证**:PR 工作流检出组装所需的 `src/` 域/管线声明，调用 `atelier-core` 的 `atelier_core.discipline src` 静态扫描和 `python -m src.main --check` 组装自检；静态扫描结果目前作为非阻塞告警，组装自检失败仍会阻塞工作流。组装自检加载各域 header/schema，验证总线契约闭环及存储声明；CI 仅额外安装 schema 导入所需的轻量 Ollama 客户端，不安装引擎推理栈、不加载模型或执行推理。统一使用 Actions secret `GH_SYNC_TOKEN` 读取内核及组装子仓，并由 sync 转发事件到本仓；该 token 需有这些仓库的 Contents: read 权限，以及对本仓发送 repository dispatch 所需权限。静态扫描不覆盖 B1–B7 行为规范；组装自检不检查 M12 配置或 M13 真实数据库漂移。主仓按 `.gitmodules` 白名单验证子仓提交并运行可识别的测试、构建/lint 或 Python 语法检查；无测试配置时明确标记未验证，不以构建/语法检查代替契约审查。可手动触发全量检查,不做定时轮询。
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
