"""atelier-studio 组装入口(产品接线)—— 全量变体:视频翻译 + 新闻日报 + 全部域。

域集 = 全部:web / scheduler / task / engines / platform_adapters / publishers(发布,远程域)/
daemons(监控 + 日报采集 + 闲时排产)/ media(通用媒体库,纯存储)/ sentinel(哨兵)。
管线 = video + digest 双条(task build 经插件聚合)。gateway 开;supervisor 起 bgutil + publish-engine。
监控源不限量(max_channels 默认 0)。基本为旧 Architecture 在 Atelier 上的等价重建。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from atelier_core.boot.assembly import (
    Handlers,
    _acquire_single_instance_lock,
    _housekeeping_loop,
    build_hub,
    build_store,
    self_test,
)
from atelier_core.boot.composition import BuildContext
from atelier_core.core import config, logger

DOMAIN_ROOT = __package__
_SHUTDOWN_DEADLINE = 20.0


async def serve(config_path: str | None = None) -> None:
    cfg = config.load(config_path)
    logger.set_level(cfg.logger.level)
    logger.info("基建配置来源:%s", cfg.source, layer="CORE", component="Main")

    lock_fd = _acquire_single_instance_lock(cfg.paths.store_db)
    if lock_fd is None:
        logger.error("已有 hub 独占数据目录 %s,拒绝启动", cfg.paths.store_db,
                     layer="CORE", component="Main")
        return
    import os
    os.environ.setdefault("GCP_MODELS_DIR", cfg.paths.models_dir)

    svc: dict = {}
    scheduler_handlers: Handlers = {
        "on_job_requested":        lambda e: svc["scheduler"].on_job_requested(e),
        "on_job_cancel":           lambda e: svc["scheduler"].on_job_cancel(e),
        "on_job_delete":           lambda e: svc["scheduler"].on_job_delete(e),
        "on_job_republish":        lambda e: svc["scheduler"].on_job_republish(e),
        "on_publish_discard":      lambda e: svc["scheduler"].on_publish_discard(e),
        "on_schedule_due":         lambda e: svc["scheduler"].on_schedule_due(e),
        "on_monitor_discovered":   lambda e: svc["scheduler"].on_monitor_discovered(e),
        "on_job_finished":         lambda e: svc["scheduler"].on_job_finished(e),
        "on_job_progress":         lambda e: svc["scheduler"].on_job_progress(e),
        "on_publish_finished":     lambda e: svc["scheduler"].on_publish_finished(e),
        "on_channel_targets_edit": lambda e: svc["scheduler"].on_channel_targets_edit(e),
    }
    from .task import header as _task_header
    _task_rt: dict = {}
    task_handlers: Handlers = {
        slot: (lambda s: lambda e: _task_rt["h"][s](e))(slot)
        for slot in set(_task_header.SUBSCRIBES.values())
    }
    engines_handlers: Handlers = {"on_settings_edit": lambda e: svc["engines_settings_edit"](e)}
    platform_adapters_handlers: Handlers = {
        "on_account_changed": lambda e: svc["connections"].on_account_changed(e),
        "on_settings_edit":   lambda e: svc["ingest"].on_settings_edit(e),
    }
    # daemons:监控 + 日报采集(全量,双职责全接)。
    daemons_handlers: Handlers = {
        "on_job_status":           lambda e: svc["monitor"].on_job_status(e),
        "on_job_deleted":          lambda e: svc["monitor"].on_job_deleted(e),
        "on_auth_expired":         lambda e: svc["monitor"].on_auth_expired(e),
        "on_channel_add":          lambda e: svc["monitor"].on_channel_add(e),
        "on_channel_edit":         lambda e: svc["monitor"].on_channel_edit(e),
        "on_channel_remove":       lambda e: svc["monitor"].on_channel_remove(e),
        "on_poll_now":             lambda e: svc["monitor"].on_poll_now(e),
        "on_poll_channel":         lambda e: svc["monitor"].on_poll_channel(e),
        "on_digest_source_add":    lambda e: svc["monitor"].on_digest_source_add(e),
        "on_digest_source_edit":   lambda e: svc["monitor"].on_digest_source_edit(e),
        "on_digest_source_remove": lambda e: svc["monitor"].on_digest_source_remove(e),
        "on_settings_edit":        lambda e: svc["monitor"].on_settings_edit(e),
        # 日报采集入池(video-only 变体不接;全量接 DigestCollector)
        "on_digest_collect":        lambda e: svc["digest_collector"].on_digest_collect(e),
        "on_digest_collect_source": lambda e: svc["digest_collector"].on_digest_collect_source(e),
        "on_pool_edit":             lambda e: svc["digest_collector"].on_pool_edit(e),
        "on_items_used":            lambda e: svc["digest_collector"].on_items_used(e),
    }
    remote_handlers: dict[str, Handlers] = {
        "scheduler": scheduler_handlers, "task": task_handlers,
        "engines": engines_handlers, "platform_adapters": platform_adapters_handlers,
        "daemons": daemons_handlers,
    }
    # media 纯存储域(无 PUBLISHES/SUBSCRIBES)、sentinel 靠内核观察者钩子(SUBSCRIBES 空):
    # 均无需在此接槽位,build_hub 域发现即挂表/建窗。

    # 进程域(publishers,build.py KIND=process):网关转发 + 懒子进程 + 闲时回收(见 docs boot.md 形态 B)
    import sys as _sys
    from atelier_core.boot.composition import domain_kind
    from atelier_core.boot.supervisor import Supervisor
    from atelier_core.core.registry import discover_headers as _disc
    _process_domains = [d for d in _disc(DOMAIN_ROOT) if domain_kind(d, DOMAIN_ROOT) == "process"]
    # 监督器提前构造(child_port 供 ingest 取 POT 口):外部哑边车 + 进程域懒子进程
    _proc_children = [{"name": f"{d}-worker",
                       "cmd": [_sys.executable, "-m", "atelier_core.boot.remote", d, DOMAIN_ROOT],
                       "lazy": True} for d in _process_domains]
    supervisor = Supervisor(list(cfg.supervisor.children) + _proc_children)

    # 网关:进程域 + 外部远程域一并转发;懒进程域登记按需拉起(register_lazy 须在 start() 前)
    gateway = None
    _forward = list(dict.fromkeys(list(cfg.gateway.remote_domains) + _process_domains))
    if cfg.gateway.enabled and _forward:
        from atelier_core.core.gateway.server import GatewayServer
        from atelier_core.core.registry import load_header
        gateway = GatewayServer(host=cfg.gateway.host, port=cfg.gateway.port,
                                token=cfg.gateway.token, ack_timeout=cfg.gateway.ack_timeout)
        remote_handlers.update({d: gateway.remote_handlers(load_header(d, DOMAIN_ROOT)) for d in _forward})
        for d in _process_domains:
            gateway.register_lazy(
                d, ensure=lambda d=d: supervisor.ensure_child(f"{d}-worker"),
                stop=lambda d=d: supervisor.stop_child(f"{d}-worker"), idle_sec=30.0)

    bus, windows = await build_hub(
        remote_handlers, fill_noop=True, journal_db=cfg.paths.journal_db,
        queue_size=cfg.bus.queue_size, max_attempts=cfg.bus.max_attempts,
        put_timeout=cfg.bus.put_timeout, domain_root=DOMAIN_ROOT)
    if gateway is not None:
        gateway.attach(bus, windows)
        await gateway.start()
    store, store_windows = await build_store(cfg.paths.store_db, domain_root=DOMAIN_ROOT)

    from atelier_core.core.log_store import LogStoreHandler
    log_sink = LogStoreHandler(cfg.paths.store_db, batch=cfg.logstore.batch,
                               flush_interval=cfg.logstore.flush_interval, queue_max=cfg.logstore.queue_max)
    logging.getLogger(logger.ROOT).addHandler(log_sink)

    # engines 设置底座
    from atelier_core.core.settings import SettingsStore, edit_handler
    from .engines.schema import EngineSettings
    engines_settings = SettingsStore(store, store_windows["engines"], "engines_settings",
                                     EngineSettings, layer="ENGINES")
    svc["engines_settings_edit"] = edit_handler(engines_settings, "engines")
    await engines_settings.get()

    # task 域:build 聚合(基础 + pipeline-video + pipeline-digest)
    from .task import build as task_build
    ctx = BuildContext(hub=store, stores=store_windows, publish=windows["task"],
                       loop=asyncio.get_running_loop(), cfg=cfg, bus=bus)
    _task_rt["h"] = task_build.build(ctx).handlers

    # scheduler(含发布出闸;task 本地无进程域控制器)
    from .scheduler.impl.publish_service import PublishService
    from .scheduler.impl.service import JobSchedulerService
    svc["scheduler"] = JobSchedulerService(
        windows["scheduler"], store, store_windows["scheduler"],
        publish_service=PublishService(windows["scheduler"], store), task_process=None)

    # platform_adapters(账号 + YouTube 采集,POT 取 bgutil 动态口)
    from .platform_adapters.impl.service import ConnectionService
    svc["connections"] = ConnectionService(windows["platform_adapters"], store, store_windows["platform_adapters"])
    await svc["connections"].sync_engine_connections()   # 启动兜底:从 publishers_accounts 重建 engine_* connections(补离线迁移缺口)
    from .platform_adapters import ingest as pa_ingest
    from .platform_adapters import youtube_token
    svc["ingest"] = pa_ingest.init(store, store_windows["platform_adapters"], pot_port=supervisor.child_port("bgutil"))
    youtube_token.init(store, store_windows["platform_adapters"])

    # daemons:频道监控 + 日报采集 + 闲时排产(全量三职责)
    from .daemons.impl.monitor import MonitorService
    from .daemons.impl.service import SchedulerService, make_jobs_intervals_provider
    svc["monitor"] = MonitorService(windows["daemons"], store, store_windows["daemons"])
    from .daemons import sources as daemons_sources
    daemons_sources.init(store, store_windows["daemons"])   # 采集源门面(域根白名单件)
    await daemons_sources.seed()                            # 启动落种:监控源页首屏可见默认源
    idle_scheduler = SchedulerService(
        windows["daemons"], store, store_windows["daemons"], make_jobs_intervals_provider(store))
    from .daemons.impl.digest_collector import DigestCollector
    svc["digest_collector"] = DigestCollector(store, store_windows["daemons"])
    # 发布账号保活/查态调度(执行侧在 publishers 进程域;到点发 daemons/publishers/* 唤醒)
    from .daemons.impl.publishers_keepalive import PublishersKeepalive
    svc["pub_keepalive"] = PublishersKeepalive(windows["daemons"], store)

    # ── 循环 ────────────────────────────────────────────────────────────────
    tasks = [
        asyncio.create_task(svc["scheduler"].run_worker(), name="scheduler_worker"),
        asyncio.create_task(svc["monitor"].run_loop(), name="monitor_loop"),
        asyncio.create_task(idle_scheduler.run_digest_loop(), name="digest_scheduler"),
        asyncio.create_task(svc["digest_collector"].run_loop(), name="digest_collector"),
        asyncio.create_task(svc["connections"].run_youtube_mirror_loop(), name="yt_mirror"),
        asyncio.create_task(svc["pub_keepalive"].run(), name="pub_keepalive"),
        asyncio.create_task(_housekeeping_loop(bus, store, cfg.retention), name="housekeeping"),
    ]
    await supervisor.start()          # 拉起 bgutil + publish-engine

    # sentinel:哨兵(内核观察者钩子 + 内省;死了系统无感)
    if "sentinel" in windows:
        from .sentinel.impl.service import SentinelService
        sentinel = SentinelService(
            bus, store, windows["sentinel"], store_windows["sentinel"],
            patrol_interval=cfg.sentinel.patrol_interval,
            queue_backlog_threshold=cfg.sentinel.queue_backlog_threshold,
            pending_stale_sec=cfg.sentinel.pending_stale_sec,
            job_stall_sec=cfg.sentinel.job_stall_sec,
            heartbeat_grace=cfg.sentinel.heartbeat_grace,
            error_burst_threshold=cfg.sentinel.error_burst_threshold)
        tasks.append(asyncio.create_task(sentinel.run_loop(), name="sentinel"))

    # web(单端口 + 全功能前端子模块构建产物)
    web_server = web_task = None
    if cfg.web.enabled and "web" in windows:
        import uvicorn

        from .web.impl.service import build_web_app
        web_app = build_web_app(bus, store, windows["web"], dist_dir=cfg.web.dist_dir or None)
        web_server = uvicorn.Server(uvicorn.Config(
            web_app, host=cfg.web.host, port=cfg.web.port, log_config=None, access_log=False))
        web_task = asyncio.create_task(web_server.serve(), name="web")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("atelier-studio 运行中(%d 域,双管线 video+digest,port=%d)。Ctrl-C 停机",
                len(windows), cfg.web.port, layer="CORE", component="Main")
    await stop.wait()

    logger.info("停机中…", layer="CORE", component="Main")
    supervisor.begin_shutdown()
    if web_server is not None:
        web_app.state.close_streams()
        web_server.should_exit = True
        await asyncio.gather(web_task, return_exceptions=True)
    if gateway is not None:
        await gateway.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await supervisor.stop()
    await store.close()
    await bus.close()
    if bus.journal is not None:
        await bus.journal.close()
    log_sink.flush(timeout=3.0)
    logging.getLogger(logger.ROOT).removeHandler(log_sink)
    log_sink.shutdown()
    os._exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="atelier-studio 组装入口")
    parser.add_argument("--check", action="store_true", help="声明自检后退出")
    parser.add_argument("--config", default=None, help="基建配置路径(默认 ./config.toml)")
    args = parser.parse_args()
    asyncio.run(self_test(DOMAIN_ROOT) if args.check else serve(args.config))
