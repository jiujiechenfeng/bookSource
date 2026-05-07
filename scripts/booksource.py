#!/usr/bin/env python3
"""Collect and verify Legado book sources.

The verifier intentionally checks only whether bookSourceUrl is reachable,
matching the idea used by xin-verify-book-source. It does not assert search or
chapter parsing correctness.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_SOURCE_URLS = [
    "https://legado.aoaostar.com/sources/b778fe6b.json",
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,text/html,*/*",
}


@dataclass
class VerifyResult:
    source: dict[str, Any]
    ok: bool
    status: int | None = None
    error: str | None = None
    elapsed_ms: int = 0


def ssl_context(verify_ssl: bool) -> ssl.SSLContext | None:
    if verify_ssl:
        return None
    return ssl._create_unverified_context()


def is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return urlparse(value).scheme in {"http", "https"}


def load_json_from_path(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"{path} 不是书源数组")
    return [item for item in data if isinstance(item, dict)]


def load_json_if_exists(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    return load_json_from_path(path)


def fetch_json(url: str, timeout: float, verify_ssl: bool) -> list[dict[str, Any]]:
    request = Request(url, headers=DEFAULT_HEADERS)
    with urlopen(request, timeout=timeout, context=ssl_context(verify_ssl)) as response:
        raw = response.read()
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError(f"{url} 返回内容不是书源数组")
    return [item for item in data if isinstance(item, dict)]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
        file.write("\n")
    tmp_path.replace(path)


def source_key(source: dict[str, Any]) -> str:
    return str(source.get("bookSourceUrl") or source.get("bookSourceName") or "")


def dedup_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for source in sources:
        key = source_key(source)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    return deduped


def completed_keys(*groups: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for group in groups:
        for source in group:
            key = source_key(source)
            if key:
                keys.add(key)
    return keys


def collect_sources(
    source_urls: list[str],
    output: Path,
    merge_existing: bool,
    timeout: float,
    verify_ssl: bool,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []

    if merge_existing and output.exists():
        existing = load_json_from_path(output)
        collected.extend(existing)
        print(f"已读取现有书源：{len(existing)} 条")

    for url in source_urls:
        print(f"正在拉取：{url}")
        items = fetch_json(url, timeout=timeout, verify_ssl=verify_ssl)
        collected.extend(items)
        print(f"拉取完成：{len(items)} 条")

    deduped = dedup_sources(collected)
    write_json(output, deduped)
    print(f"已写入 {output}：{len(deduped)} 条，去重 {len(collected) - len(deduped)} 条")
    return deduped


def verify_one(source: dict[str, Any], timeout: float, verify_ssl: bool) -> VerifyResult:
    url = source.get("bookSourceUrl")
    if not is_http_url(url):
        return VerifyResult(source=source, ok=False, error="bookSourceUrl 不是 HTTP(S) URL")

    start = time.monotonic()
    request = Request(str(url), headers=DEFAULT_HEADERS)
    try:
        with urlopen(request, timeout=timeout, context=ssl_context(verify_ssl)) as response:
            status = response.getcode()
            response.read(1)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return VerifyResult(
            source=source,
            ok=200 <= status < 400,
            status=status,
            elapsed_ms=elapsed_ms,
        )
    except HTTPError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return VerifyResult(
            source=source,
            ok=200 <= exc.code < 400,
            status=exc.code,
            error=str(exc.reason),
            elapsed_ms=elapsed_ms,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return VerifyResult(source=source, ok=False, error=str(exc), elapsed_ms=elapsed_ms)


def verify_sources(
    input_path: Path,
    output_path: Path,
    error_path: Path | None,
    workers: int,
    timeout: float,
    verify_ssl: bool,
    resume: bool,
    save_every: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources = dedup_sources(load_json_from_path(input_path))
    good = dedup_sources(load_json_if_exists(output_path)) if resume else []
    bad = dedup_sources(load_json_if_exists(error_path)) if resume else []
    done_keys = completed_keys(good, bad)
    pending_sources = [source for source in sources if source_key(source) not in done_keys]
    total = len(sources)
    pending_total = len(pending_sources)
    completed = total - pending_total
    start = time.monotonic()

    if resume:
        print(f"断点继续：已完成 {completed} 条，待校验 {pending_total} 条")
    print(f"开始校验：总数 {total} 条，线程 {workers}，超时 {timeout:g}s")
    if pending_total == 0:
        write_json(output_path, good)
        if error_path is not None:
            write_json(error_path, bad)
        print(f"无需继续校验：有效 {len(good)}，无效 {len(bad)}，已写入 {output_path}")
        return good, bad

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(verify_one, source, timeout, verify_ssl)
            for source in pending_sources
        ]
        try:
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                result = future.result()
                if result.ok:
                    result.source["respondTime"] = result.elapsed_ms
                    good.append(result.source)
                else:
                    failed = dict(result.source)
                    failed["_verifyError"] = result.error or f"HTTP {result.status}"
                    bad.append(failed)

                current = completed + index
                should_save = index == pending_total or index % save_every == 0
                should_report = index == pending_total or index % 25 == 0
                if should_save:
                    write_json(output_path, dedup_sources(good))
                    if error_path is not None:
                        write_json(error_path, dedup_sources(bad))

                if should_report:
                    percent = current / total * 100 if total else 100
                    print(
                        f"\r进度：{current}/{total} ({percent:.1f}%)，有效 {len(good)}，无效 {len(bad)}",
                        end="",
                        flush=True,
                    )
        finally:
            write_json(output_path, dedup_sources(good))
            if error_path is not None:
                write_json(error_path, dedup_sources(bad))

    print()
    good = dedup_sources(good)
    bad = dedup_sources(bad)
    write_json(output_path, good)
    if error_path is not None:
        write_json(error_path, bad)

    elapsed = time.monotonic() - start
    print(
        f"校验完成：总数 {total}，有效 {len(good)}，无效 {len(bad)}，"
        f"耗时 {elapsed:.2f}s，已写入 {output_path}"
    )
    return good, bad


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="收集并校验阅读 APP 书源")
    parser.add_argument("--verify-ssl", action="store_true", help="启用严格 SSL 校验")

    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="拉取远程书源并生成 shuyuan.json")
    collect.add_argument("-u", "--url", action="append", dest="urls", default=[], help="书源 JSON 直链，可重复传入")
    collect.add_argument("-o", "--output", default="shuyuan.json", type=Path, help="输出文件")
    collect.add_argument("--no-merge", action="store_true", help="不合并现有输出文件")
    collect.add_argument("--timeout", type=float, default=30, help="下载超时时间")

    verify = subparsers.add_parser("verify", help="校验书源并生成 xiang.json")
    verify.add_argument("-i", "--input", default="shuyuan.json", type=Path, help="输入书源文件")
    verify.add_argument("-o", "--output", default="xiang.json", type=Path, help="有效书源输出文件")
    verify.add_argument("--error-output", default="error.json", type=Path, help="无效书源输出文件")
    verify.add_argument("--no-error-output", action="store_true", help="不输出无效书源文件")
    verify.add_argument("-w", "--workers", type=positive_int, default=32, help="并发线程数")
    verify.add_argument("--timeout", type=float, default=5, help="单个书源请求超时时间")
    verify.add_argument("--no-resume", action="store_true", help="不读取已有输出，重新校验全部书源")
    verify.add_argument("--save-every", type=positive_int, default=25, help="每校验多少条就保存一次断点")

    all_cmd = subparsers.add_parser("all", help="先拉取 shuyuan.json，再校验生成 xiang.json")
    all_cmd.add_argument("-u", "--url", action="append", dest="urls", default=[], help="书源 JSON 直链，可重复传入")
    all_cmd.add_argument("--source-output", default="shuyuan.json", type=Path, help="书源输出文件")
    all_cmd.add_argument("-o", "--output", default="xiang.json", type=Path, help="有效书源输出文件")
    all_cmd.add_argument("--error-output", default="error.json", type=Path, help="无效书源输出文件")
    all_cmd.add_argument("--no-merge", action="store_true", help="不合并现有 shuyuan.json")
    all_cmd.add_argument("-w", "--workers", type=positive_int, default=32, help="并发线程数")
    all_cmd.add_argument("--download-timeout", type=float, default=30, help="下载超时时间")
    all_cmd.add_argument("--timeout", type=float, default=5, help="单个书源请求超时时间")
    all_cmd.add_argument("--no-resume", action="store_true", help="不读取已有输出，重新校验全部书源")
    all_cmd.add_argument("--save-every", type=positive_int, default=25, help="每校验多少条就保存一次断点")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "collect":
        urls = args.urls or DEFAULT_SOURCE_URLS
        collect_sources(urls, args.output, not args.no_merge, args.timeout, args.verify_ssl)
        return 0

    if args.command == "verify":
        error_path = None if args.no_error_output else args.error_output
        verify_sources(
            args.input,
            args.output,
            error_path,
            args.workers,
            args.timeout,
            args.verify_ssl,
            not args.no_resume,
            args.save_every,
        )
        return 0

    if args.command == "all":
        urls = args.urls or DEFAULT_SOURCE_URLS
        collect_sources(urls, args.source_output, not args.no_merge, args.download_timeout, args.verify_ssl)
        verify_sources(
            args.source_output,
            args.output,
            args.error_output,
            args.workers,
            args.timeout,
            args.verify_ssl,
            not args.no_resume,
            args.save_every,
        )
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
