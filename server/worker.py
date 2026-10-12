"""Compatibility imports. New collection code lives in server.collector."""

from server.collector.transport import API, RemoteError, fetch, httpx
from server.collector.records import endpoint_for, process_result, save_comment
from server.collector.worker import WorkerPool, pool
