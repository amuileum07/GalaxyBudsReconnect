#!/usr/bin/env python3
"""Linux Flatpak Galaxy Buds Client 연결 복구. Python 표준 라이브러리만 사용."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

APP = "me.timschneeberger.GalaxyBudsClient"
SETTINGS = Path.home() / ".var/app" / APP / "data/GalaxyBudsClient/settings.json"
MAC_PATTERN = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\Z")


class RecoveryError(Exception):
    pass


def run(*args, timeout=15, required=True):
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=timeout, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RecoveryError(f"{args[0]} 실행 실패: {error}") from error
    if required and result.returncode:
        raise RecoveryError((result.stderr or result.stdout).strip() or
                            f"{args[0]} 종료 코드: {result.returncode}")
    return result


def device_address(explicit=None):
    if explicit:
        address = explicit
    else:
        try:
            settings = json.loads(SETTINGS.read_text())
            address = settings["LastDeviceMac"]
            devices = settings["Devices"]
            if not any(device.get("MacAddress", "").upper() == address.upper()
                       for device in devices):
                raise ValueError("최근 장치가 등록 장치 목록에 없습니다.")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise RecoveryError(f"앱의 장치 설정을 읽지 못했습니다: {error}. --mac으로 주소를 지정하세요.") from error
    if not isinstance(address, str) or not MAC_PATTERN.fullmatch(address):
        raise RecoveryError("올바른 블루투스 주소가 필요합니다. 예: AA:BB:CC:DD:EE:FF")
    return address.upper()


def connected(info):
    return bool(re.search(r"^\s*Connected:\s*yes\s*$", info, re.MULTILINE))


def instances():
    output = run("flatpak", "ps", "--columns=application").stdout
    return sum(line.strip() == APP for line in output.splitlines())


def start_app():
    if instances():
        return
    process = subprocess.Popen(["flatpak", "run", APP], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    for _ in range(10):
        time.sleep(0.5)
        if process.poll() is not None and process.returncode != 0:
            raise RecoveryError("Galaxy Buds Manager 실행에 실패했습니다.")
        if instances():
            print("Galaxy Buds Manager를 다시 실행했습니다.")
            return
    raise RecoveryError("Galaxy Buds Manager 실행을 확인하지 못했습니다.")


def recover(address):
    # 전체 어댑터를 끄지 않는다. 키보드 등 다른 장치의 연결을 보존한다.
    relaunch = False
    try:
        if instances():
            relaunch = True
            run("flatpak", "kill", APP)
            for _ in range(20):
                if not instances():
                    break
                time.sleep(0.25)
            else:
                raise RecoveryError("앱이 종료되지 않아 재연결을 중단했습니다.")
        else:
            relaunch = True
        print("남아 있는 연결 요청을 해제합니다.")
        result = run("bluetoothctl", "disconnect", address, required=False)
        if result.returncode and not re.search(r"NotConnected|not connected", result.stdout + result.stderr, re.I):
            raise RecoveryError("연결 요청 해제 실패: " + (result.stdout + result.stderr).strip())
        print("이어폰을 다시 연결합니다. 최대 35초 걸릴 수 있습니다.")
        result = run("bluetoothctl", "connect", address, timeout=35, required=False)
        # 명령의 성공 문구 대신 시스템의 실제 연결 상태를 확인한다.
        for _ in range(5):
            if connected(run("bluetoothctl", "info", address).stdout):
                print("시스템에서 이어폰 연결을 확인했습니다.")
                return
            time.sleep(1)
        raise RecoveryError("재연결 실패: " + (result.stdout + result.stderr).strip())
    finally:
        if relaunch:
            # 복구 실패나 Ctrl+C 뒤에도 원래 사용하던 앱을 다시 실행한다.
            start_app()


def main():
    parser = argparse.ArgumentParser(description="Galaxy Buds Manager 중복 실행 및 연결 상태 복구")
    parser.add_argument("--check", action="store_true", help="변경 없이 상태만 확인")
    parser.add_argument("--mac", help="앱의 최근 장치 대신 사용할 블루투스 주소")
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise RecoveryError("sudo 없이 데스크톱 사용자 계정으로 실행하세요.")
        for command in ("bluetoothctl", "flatpak"):
            if not shutil.which(command):
                raise RecoveryError(f"필수 명령을 찾을 수 없습니다: {command}")
        address = device_address(args.mac)
        controller = run("bluetoothctl", "show").stdout
        if not re.search(r"^\s*Powered:\s*yes\s*$", controller, re.MULTILINE):
            raise RecoveryError("블루투스가 꺼져 있거나 어댑터를 사용할 수 없습니다. 시스템 설정에서 켜 주세요.")
        info = run("bluetoothctl", "info", address).stdout
        if not re.search(r"^\s*Paired:\s*yes\s*$", info, re.MULTILINE):
            raise RecoveryError("등록된 페어링이 없습니다. 시스템 블루투스 설정에서 먼저 페어링해 주세요.")
        run("flatpak", "info", APP)
        print(f"대상 이어폰: {address}")
        print("실제 연결 상태: " + ("연결됨" if connected(info) else "연결 안 됨"))
        print(f"앱 실행 개수: {instances()}")
        if args.check:
            return 0
        lock_path = Path(__file__).resolve().parent / ".reconnect.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RecoveryError("이미 다른 복구 작업이 실행 중입니다.") from error
            recover(address)
        print("완료: 실제 소리는 짧게 재생해 확인해 주세요.")
        return 0
    except (RecoveryError, OSError) as error:
        print(f"복구를 완료하지 못했습니다: {error}", file=sys.stderr)
        print("이어폰을 케이스에 넣고 뚜껑을 연 채 아래 버튼을 3초 이상 누른 뒤 다시 실행하세요.\n"
              "다른 기기에 연결되어 있다면 먼저 그 연결을 해제하세요. 페어링 정보와 앱 설정은 삭제하지 않았습니다.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("작업을 중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
