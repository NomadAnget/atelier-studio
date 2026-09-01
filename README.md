# atelier-studio

**Atelier 全量变体** —— 视频翻译 + 新闻日报 + 全部域,全功能前端。基本是旧 Architecture 在 Atelier 组合形态上的等价重建:一个平台,所有能力全开。

YouTube 外语视频 → 翻译/配音/字幕/封面 → 多平台发布;并行新闻日报采集 → 编辑台选题 → 出刊。频道监控不限量、闲时排产、哨兵活性告警全开。

## 组成
- **内核**:`atelier-core`(uv 依赖)
- **全部域子模块**(`src/<域>`):web · scheduler · task · engines · platform_adapters · publishers(发布,远程域)· daemons(监控+日报采集+闲时排产)· **media**(通用媒体库,纯存储)· **sentinel**(哨兵)
- **双管线**(`src/pipelines/`):**video**(视频翻译)· **digest**(新闻日报)——task build 经插件聚合
- **服务子模块**(`src/services/`):publish-engine(publishers 远程实现)· bgutil-server(YouTube POT 供给器,node)
- **前端**:`frontend/` = **Carnation/atelier-frontend 子模块**(体系前端基线=全功能前端,不裁);全量=基准本体,基准更新即随

## 与其他变体的关系
| 变体 | 域集 | 管线 | 前端 |
|---|---|---|---|
| reel-solo | 精简(publishers 空转) | video | in-repo 单表单 |
| reel-creator | + daemons + publishers | video | in-repo(裁 Digest) |
| **studio** | **全部 9 域** | **video + digest** | **全功能子模块(不裁)** |

## 关键点
- **全量无上限**:`daemons_settings.max_channels` 默认 0=监控源不限量。
- **双管线**:task build 插件聚合 video + digest;前端任务类型下拉纯 snapshot 驱动(后端实际挂载即真相源)。
- **自动发布**:gateway 开(`remote_domains=["publishers"]`),publish-engine 经 ws 接入;需 publish-engine + 平台 OAuth 账号。
- **哨兵**:阈值走 atelier-core `SentinelCfg` 默认,可加 `[sentinel]` 段覆盖。
- **POT**:bgutil 边车(supervisor 动态端口),YouTube 采集防风控。
- 端口:web 8097 / gateway 8793 / publish-engine 8803 / bgutil 动态。

## 跑
```bash
git submodule update --init --recursive
# 边车构建(一次性):bgutil —— server/ 子目录装依赖 + tsc 编译 src→build/main.js
cd src/services/bgutil-server/server && npm ci && npx tsc && cd -
# publish-engine 无需手动:supervisor 用 `uv run python run.py` 首次自建其 env
# 前端构建(全功能子模块)
cd frontend && npm ci && npm run build && cd -
uv sync                              # 或本机借 Architecture 胖 venv(见 reel-solo README B 法)
uv run python -m src.main --config config.toml
```
