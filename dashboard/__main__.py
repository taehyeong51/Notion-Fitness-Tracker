"""python -m dashboard: optional setup and one-process local dashboard."""
from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import socket
import sys


def lan_addresses() -> list[str]:
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            value = info[4][0]
            parsed = ipaddress.ip_address(value)
            if parsed.version == 4 and parsed.is_private and not parsed.is_loopback:
                addresses.add(value)
    except OSError:
        pass
    return sorted(addresses)


def main(argv=None):
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Notion 기록을 읽는 개인용 로컬 웹 대시보드")
    parser.add_argument("--config", default=str(root / ".local/dashboard-config.json"))
    parser.add_argument("--cache", default=str(root / ".local/dashboard-cache.json"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--lan", action="store_true", help="같은 LAN 기기에서 접근하도록 실행")
    parser.add_argument("--allow-host", action="append", default=[], help="LAN에서 사용할 추가 로컬 호스트 이름")
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument("--import-config", metavar="PATH", help="기존 Notion 설정을 읽어 웹 설정 생성 후 종료")
    setup.add_argument("--discover", action="store_true", help="Notion 원본을 찾아 웹 설정 생성 후 종료")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("포트는 1–65535여야 합니다.")
    if args.allow_host and not args.lan:
        parser.error("추가 호스트는 --lan과 함께 지정하세요.")

    from .config import ConfigError, discover_config, import_legacy
    if args.import_config or args.discover:
        try:
            result = (
                import_legacy(args.import_config, args.config)
                if args.import_config else discover_config(args.config)
            )
            print(f"웹 설정 저장: {result.get('path', args.config)}")
            for warning in result.get("warnings", []):
                print(warning)
            return 0
        except (ConfigError, ValueError, OSError) as exc:
            print(f"설정 실패: {exc}", file=sys.stderr)
            return 1

    from .server import create_app
    import uvicorn
    hosts = ["127.0.0.1", "localhost", "::1"]
    if args.lan:
        hosts.extend(lan_addresses())
        hosts.extend(args.allow_host)
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with probe:
            probe.bind(("0.0.0.0" if args.lan else "127.0.0.1", args.port))
    except OSError:
        print(f"포트 {args.port}를 사용할 수 없습니다. 실행 중인 앱을 종료하거나 --port로 변경하세요.", file=sys.stderr)
        return 1
    print(f"Fitness Tracker · http://127.0.0.1:{args.port}")
    if args.lan:
        for host in hosts:
            if host not in {"127.0.0.1", "localhost", "::1"}:
                print(f"같은 LAN: http://{host}:{args.port}")
        print("PC가 켜져 있고 앱이 실행 중일 때 같은 LAN에서 사용할 수 있습니다.")
    uvicorn.run(
        create_app(args.config, args.cache, allowed_hosts=hosts),
        host="0.0.0.0" if args.lan else "127.0.0.1", port=args.port,
        proxy_headers=False, access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
