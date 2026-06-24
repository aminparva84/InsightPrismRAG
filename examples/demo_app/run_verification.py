#!/usr/bin/env python3
"""Run demo + integration tests; write all events to logs/run_<timestamp>.log."""
from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import time
import traceback
from pathlib import Path

from event_log import init_event_log, log_event

ROOT = Path(__file__).resolve().parent


def _package_info(logger) -> None:
    try:
        ver = importlib.metadata.version("prismrag-patch")
        loc = importlib.metadata.distribution("prismrag-patch").locate_file("")
        log_event(logger, "package.installed", name="prismrag-patch", version=ver, location=str(loc))
    except importlib.metadata.PackageNotFoundError:
        log_event(logger, "package.missing", name="prismrag-patch")
        logger.error("Install first: pip install -r requirements.txt")
        sys.exit(1)


def _run_demo(logger) -> int:
    log_event(logger, "demo.start")
    t0 = time.perf_counter()
    try:
        from demo import main as demo_main

        rc = demo_main(logger=logger)
        elapsed = round(time.perf_counter() - t0, 3)
        log_event(logger, "demo.complete", exit_code=rc, elapsed_sec=elapsed)
        return rc
    except Exception as exc:
        logger.error("demo.failed: %s", exc)
        logger.debug(traceback.format_exc())
        log_event(logger, "demo.error", error=str(exc))
        return 1


def _run_tests(logger, log_path: Path) -> int:
    log_event(logger, "tests.start", target="test_integration.py")
    t0 = time.perf_counter()
    pytest_log = log_path.with_name(log_path.stem + "_pytest.txt")

    cmd = [
        sys.executable, "-m", "pytest",
        "test_integration.py", "-v", "--tb=short",
        f"--log-file={pytest_log}",
        "--log-file-level=DEBUG",
    ]
    logger.info("Running: %s", " ".join(cmd))

    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = round(time.perf_counter() - t0, 3)

    if proc.stdout:
        for line in proc.stdout.splitlines():
            logger.info("PYTEST | %s", line)
    if proc.stderr:
        for line in proc.stderr.splitlines():
            logger.warning("PYTEST STDERR | %s", line)

    if pytest_log.exists():
        logger.info("--- pytest detail log: %s ---", pytest_log)
        for line in pytest_log.read_text(encoding="utf-8").splitlines():
            logger.debug("PYTEST FILE | %s", line)

    log_event(
        logger,
        "tests.complete",
        exit_code=proc.returncode,
        elapsed_sec=elapsed,
        passed=proc.returncode == 0,
    )
    return proc.returncode


def main() -> int:
    logger, log_path = init_event_log("run")
    log_event(logger, "run.start", python=sys.version.split()[0], cwd=str(ROOT))

    _package_info(logger)

    demo_rc = _run_demo(logger)
    test_rc = _run_tests(logger, log_path)

    overall = 0 if demo_rc == 0 and test_rc == 0 else 1
    log_event(
        logger,
        "run.complete",
        demo_exit=demo_rc,
        tests_exit=test_rc,
        overall_exit=overall,
        log_file=str(log_path),
    )
    logger.info("=== All events written to %s ===", log_path)
    print(f"\nFull event log: {log_path}")
    return overall


if __name__ == "__main__":
    sys.exit(main())
