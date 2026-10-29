"""Windows command-line companion for qmt_clipboard.py (standard library only)."""

import argparse
import ctypes
import datetime as dt
import json
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from qmt_clipboard import Clipboard, MAX_REQUEST_CHARS, PROTOCOL, SHANGHAI, TERMINAL, _identifier, dumps


def exchange(clipboard, request, timeout=10, interval=1, clock=time.monotonic, sleep=time.sleep,
             pending_requests=()):
    """Retry the identical request, never a regenerated trade or session."""
    if timeout <= 0 or interval <= 0:
        raise ValueError("timeout and interval must be positive")
    wire = dumps(request)
    authorized_pending = {dumps(item) for item in pending_requests if item.get("type") == "trade"}
    if len(wire) > MAX_REQUEST_CHARS:
        raise ValueError("request exceeds clipboard limit")
    deadline = clock() + timeout
    expected_session = request.get("session")
    published = False
    last_seen = "unread"
    clipboard_error = ""
    while clock() < deadline:
        try:
            current = clipboard.read()
            try:
                message = json.loads(current)
            except (ValueError, TypeError):
                message = None
            # Report only the kind of clipboard content, never the user's copied text.
            last_seen = "non-protocol content" if current else "empty clipboard"
            if isinstance(message, dict) and message.get("protocol") == PROTOCOL:
                last_seen = "pending request" if current == wire else "other protocol message"
                if message.get("type") == "response":
                    last_seen = "response for another request"
                    if expected_session and message.get("session") != expected_session:
                        raise RuntimeError("QMT session changed; reconcile manually; old trades were not rebound")
                    if published and message.get("request_id") == request["request_id"]:
                        return message
                elif message.get("type") == "trade" and message != request:
                    if request.get("type") in ("poll", "snapshot") and dumps(message) in authorized_pending:
                        # This exact request was durably exposed before a lost
                        # response. Do not overwrite or resubmit it: let QMT
                        # finish handling it before sending our read-only poll.
                        sleep(min(interval, max(0, deadline - clock())))
                        continue
                    raise RuntimeError("another request is pending; do not run multiple relays")
                # Abandoned poll/snapshot requests have no financial side effects.
                # The process mutex permits safely replacing these read-only requests.
            if current != wire:
                published = clipboard.replace(current, wire) or published
            else:
                published = True
        except OSError as exc:
            clipboard_error = str(exc)[:300]
        sleep(min(interval, max(0, deadline - clock())))
    message = "clipboard response timeout (request=%s, published=%s, last_seen=%s)" % (
        request.get("type"),
        "yes" if published else "no",
        last_seen,
    )
    if clipboard_error:
        message += "; last clipboard error: " + clipboard_error
    if request.get("type") in ("poll", "snapshot"):
        message += "; read-only request; no order was submitted by this request"
    else:
        message += "; trade outcome unknown; do not retry with a new order ID"
    raise TimeoutError(message)


def save_response(response, output_dir):
    """External-only file I/O. Snapshot replacement and log IDs survive relay restart."""
    session = _identifier(response.get("session"), "response session")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / (session + ".result.json")
    temporary = output / (session + ".result.tmp")
    temporary.write_text(json.dumps(response, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    logfile = output / (session + ".log.jsonl")
    last_sequence = 0
    if logfile.exists():
        for line in logfile.read_text(encoding="utf-8").splitlines():
            try:
                last_sequence = max(last_sequence, int(json.loads(line)["seq"]))
            except (ValueError, KeyError, TypeError):
                continue
    with logfile.open("a", encoding="utf-8") as handle:
        for item in response.get("logs", []):
            if item["seq"] > last_sequence:
                handle.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
                last_sequence = item["seq"]
    return destination


def finished(response, order_ids):
    states = {item["id"]: item["status"] for item in response.get("orders", [])}
    return not response.get("query_error") and all(states.get(key) in TERMINAL for key in order_ids)


@contextmanager
def single_client():
    """One relay per Windows login session, including across independent CLI invocations."""
    if sys.platform != "win32":
        raise RuntimeError("run the relay in the same Windows desktop session as QMT")
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
    kernel.CreateMutexW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.CreateMutexW(None, False, "Local\\qlib_clipboard_relay")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            raise RuntimeError("another clipboard relay is already running")
        yield
    finally:
        kernel.CloseHandle(handle)


def _poll(session=None):
    return dict(protocol=PROTOCOL, type="poll", request_id=uuid.uuid4().hex, session=session)


def _show(response, output_dir):
    path = save_response(response, output_dir)
    statuses = (
        ", ".join(
            "%s:%s:%s/%s" % (item["id"], item["status"], item["filled_quantity"], item["quantity"])
            for item in response.get("orders", [])
        )
        or "no orders"
    )
    print("session=%s %s -> %s" % (response["session"], statuses, path))
    print("source_version=%s capabilities=%s" % (
        response.get("source_version", "unknown"),
        ",".join(response.get("capabilities", [])),
    ))
    if response.get("query_error"):
        print("broker query error: " + response["query_error"], file=sys.stderr)
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "QMT rejected the request")


def run_automatic(args, clipboard, now=None, sleep=time.sleep):
    from clipboard_auto import AutoRelay, atomic_write

    now = now or (lambda: dt.datetime.now(SHANGHAI))
    output = Path(args.output_dir).resolve()
    root = Path(args.bridge_root).resolve()
    state = Path(args.state_dir).resolve() if args.state_dir else output / "state"
    if not root.is_dir() or not (root / "inbox").is_dir():
        raise ValueError("bridge-root must be the existing Windows shared directory containing inbox")

    def communicate(request):
        if args.check:
            print(
                "[check] waiting for %s response (timeout=%gs)" % (request["type"], args.timeout),
                file=sys.stderr,
                flush=True,
            )
        response = exchange(clipboard, request, args.timeout, args.interval,
                            pending_requests=pending_trade_requests(runner.journal))
        if args.check:
            print(
                "[check] received %s response; ok=%s" % (request["type"], response.get("ok")),
                file=sys.stderr,
                flush=True,
            )
        save_response(response, output)
        save_response(response, root / "logs" / "clipboard")
        return response

    runner = AutoRelay(root, args.strategy, state, communicate, now=now)
    if args.check:
        print(json.dumps(runner.check(), ensure_ascii=False, indent=2))
        return 0

    status_file = output / "automation.status.json"
    last_error = None
    print("automatic relay: day=%s strategy=%s bridge=%s" % (runner.day, args.strategy, root), flush=True)
    while True:
        stamp = now().astimezone(SHANGHAI)
        error = ""
        fatal = False
        try:
            runner.step()
        except RuntimeError as exc:
            error, fatal = str(exc), True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            error = str(exc)
        if error and error != last_error:
            print(error, file=sys.stderr, flush=True)
        last_error = error
        ended = now().astimezone(SHANGHAI).strftime("%H:%M:%S") >= "17:01:00"
        status = "ERROR" if error else "RUNNING"
        if ended and not error:
            status = "NO_SIGNAL" if runner.journal is None else "COMPLETE" if runner.complete() else "UNRESOLVED"
        payload = dict(
            day=runner.day,
            ts=stamp.isoformat(),
            status=status,
            error=error,
            session=runner.journal.get("session") if runner.journal else None,
            batch_id=runner.journal["batch"]["header"]["batch_id"] if runner.journal else None,
        )
        atomic_write(status_file, dumps(payload) + "\n")
        atomic_write(root / "logs" / "clipboard" / (runner.day + "_automation.status.json"), dumps(payload) + "\n")
        if fatal or ended:
            return 1 if error else 0 if runner.complete() else 2
        sleep(args.interval)


def pending_trade_requests(journal):
    """Return exact persisted requests; observing them never authorizes a new send."""
    result = []
    for phase in (journal or {}).get("phases", {}).values():
        if phase.get("request"):
            result.append(phase["request"])
        result.extend(child["request"] for child in phase.get("requests", [])
                      if child.get("attempted") and child.get("request"))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    for name in ("status", "send", "watch", "auto"):
        command = commands.add_parser(name)
        command.add_argument("--output-dir", default="clipboard_output")
        command.add_argument("--timeout", type=float, default=10)
        command.add_argument("--interval", type=float, default=1)
        if name == "send":
            command.add_argument("file", help="UTF-8 trade JSON with an explicit QMT session")
            command.add_argument("--watch-seconds", type=float, default=300)
        elif name == "watch":
            command.add_argument("--session", required=True)
            command.add_argument("--watch-seconds", type=float, default=300)
        elif name == "auto":
            command.set_defaults(output_dir=str(Path(__file__).resolve().parent / "output"))
            command.add_argument("--bridge-root", required=True, help="Windows directory shared to the Mac")
            command.add_argument("--strategy", default="alla_v4_ladder_k1h5_postclose_real")
            command.add_argument("--state-dir", help="local durable request journal; never delete to retry")
            command.add_argument("--check", action="store_true", help="read-only deployment check; never submits")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    try:
        if args.timeout <= 0 or args.interval <= 0 or getattr(args, "watch_seconds", 0) < 0:
            raise ValueError("invalid timeout, interval or watch duration")
        if args.command == "auto":
            with single_client():
                return run_automatic(args, Clipboard())
        if args.command == "send":
            source = Path(args.file)
            if source.stat().st_size > MAX_REQUEST_CHARS:
                raise ValueError("trade file exceeds size limit")
            request = json.loads(source.read_text(encoding="utf-8-sig"))
            if not isinstance(request, dict) or request.get("protocol") != PROTOCOL or request.get("type") != "trade":
                raise ValueError("file must contain a qlib-clipboard-v1 trade request")
            _identifier(request.get("session"), "session (copy from status; never bind automatically)")
            _identifier(request.get("request_id"), "request_id")
        else:
            request = _poll(getattr(args, "session", None))
        with single_client():
            clipboard = Clipboard()
            response = exchange(clipboard, request, args.timeout, args.interval)
            _show(response, args.output_dir)
            if args.command == "status":
                return 0
            session = request["session"]
            order_ids = (
                [item["id"] for item in request["orders"]]
                if args.command == "send"
                else [item["id"] for item in response["orders"]]
            )
            deadline = time.monotonic() + args.watch_seconds
            if args.watch_seconds == 0:
                return 0
            while not finished(response, order_ids) and time.monotonic() < deadline:
                time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
                response = exchange(clipboard, _poll(session), args.timeout, args.interval)
                _show(response, args.output_dir)
            if not finished(response, order_ids):
                print("watch ended with unresolved orders; no cancellation or resubmission performed", file=sys.stderr)
                return 2
            return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("relay stopped; submitted broker orders remain active", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
