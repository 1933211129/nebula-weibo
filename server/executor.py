"""Explicit collection process. Never initialize or migrate the database here."""

import argparse
import signal


def run_worker(task_ids=None, seconds=None):
    from server.collector.worker import WorkerPool

    pool = WorkerPool(task_ids=task_ids)
    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, lambda *_: pool.stop.set())
        pool.start()
        pool.stop.wait(seconds)
    finally:
        pool.shutdown()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', action='append', help='只执行指定任务，可重复传入')
    parser.add_argument('--seconds', type=int, help='运行秒数；省略则持续运行至收到退出信号')
    args = parser.parse_args()
    if args.seconds is not None and args.seconds <= 0:
        parser.error('--seconds 必须大于 0')
    run_worker(args.task, args.seconds)


if __name__ == '__main__':
    main()
