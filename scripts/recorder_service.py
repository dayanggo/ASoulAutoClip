"""Receive completed BililiveRecorder sessions and process a durable queue."""
import argparse
import json
import shutil
import sys
import threading
from pathlib import Path

from core.cli import load_config
from core.highlight_model import ChatModel
from core.highlight_selection import SelectionError
from core.job_lock import job_lock
from core.local_config import load_local_key
from core.paths import PROJECT_ROOT
from core.recorder_ingest import RecorderQueue, load_recorder_config, project_path, run_pipeline
from core.recorder_webhook import make_server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config/local/recorder.json"))
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--check", action="store_true", help="仅检查接入配置、FFmpeg 和模型密钥，不运行录播")
    commands.add_argument("--status", action="store_true", help="显示队列和等待原因")
    commands.add_argument("--retry", metavar="JOB_ID", help="重试指定失败任务；需确认旧进程已停止")
    args = parser.parse_args(argv)
    queue = None
    try:
        config = load_recorder_config(Path(args.config).expanduser().resolve())
        profile = load_config(config["base_config"])
        if args.check or not (args.status or args.retry):
            for tool in ("ffmpeg", "ffprobe"):
                if not shutil.which(tool):
                    raise SelectionError(f"未找到 {tool}，请先配置 FFmpeg")
            load_local_key(profile["llm"]["api_key_env"], PROJECT_ROOT / ".env")
            ChatModel(profile["llm"])  # Validates credentials/configuration without sending a request.
        if args.check:
            print("接入配置检查通过，未下载模型、未调用 API。ASR/GPU 和音画时间线将在真实录制后验证。")
            print(f"录制根目录：{project_path(config['recordings_dir'])}")
            print(f"WebhookV2：http://127.0.0.1:{config['port']}/bililive")
            print(f"弹幕策略：{config['danmaku_policy']}；缺失时额外等待 {config['danmaku_wait_seconds']} 秒。")
            return 0
        queue = RecorderQueue(config, profile)
        if args.status:
            print(json.dumps(queue.status(), ensure_ascii=False, indent=2))
            return 0
        if args.retry:
            queue.retry(args.retry)
            print("任务已重新入队。服务运行时会串行处理；未运行时请启动服务。")
            return 0
        with job_lock(queue.state / "service.lock"):
            queue.recover_interrupted()
            server = make_server(queue, config["port"])
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                print(f"录播接入已启动：http://127.0.0.1:{config['port']}/bililive", flush=True)
                print("请在录制开始前启动服务；等 SessionEnded、关闭事件及文件稳定后串行处理。", flush=True)
                print("不扫描旧录播，不自动投稿；进程详情写入每个任务的 pipeline.log。Ctrl+C 停止。", flush=True)
                print("弹幕策略：" + ("有 XML 则参考，缺失时经过等待后只用视频切片。"
                      if config["danmaku_policy"] == "optional" else "必须有同名 XML 才处理。"), flush=True)
                if queue.status()["queue_paused"]:
                    print("队列暂停：存在中断任务。先确认旧进程停止，再用 --retry ID 恢复。", flush=True)
                stop = threading.Event()
                while True:
                    if queue.refresh():
                        print("新的录播任务已校验并入队；所用视频和弹幕见 recorder-source.json。", flush=True)
                    if not queue.run_next(run_pipeline):
                        stop.wait(config["poll_seconds"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        return 0
    except KeyboardInterrupt:
        print("录播接入已停止，事件、队列和切片缓存保留。")
        return 130
    except (SelectionError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    finally:
        if queue:
            queue.close()


if __name__ == "__main__":
    raise SystemExit(main())
