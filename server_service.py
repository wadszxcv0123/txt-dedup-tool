#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import subprocess
import threading
from datetime import datetime

try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager
    HAS_SERVICE = True
except ImportError:
    HAS_SERVICE = False

SERVICE_NAME = "TXT-Dedup-Server"
SERVICE_DISPLAY_NAME = "TXT Dedup Server"
SERVICE_DESCRIPTION = "TXT Hash Deduplication Server"


def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_email_config():
    base_dir = get_base_dir()
    config_file = os.path.join(base_dir, 'server_config.ini')
    import configparser
    config = configparser.ConfigParser()
    if os.path.exists(config_file):
        config.read(config_file, encoding='utf-8')
    return {
        'smtp_server': config.get('Email', 'smtp_server', fallback=''),
        'smtp_port': config.getint('Email', 'smtp_port', fallback=587),
        'smtp_username': config.get('Email', 'smtp_username', fallback=''),
        'smtp_password': config.get('Email', 'smtp_password', fallback=''),
        'from_addr': config.get('Email', 'from_addr', fallback=''),
        'to_addrs': config.get('Email', 'to_addrs', fallback='')
    }


def send_alert_email(title, message):
    try:
        from notifier import email_notification
        config = load_email_config()
        if config.get('smtp_server'):
            email_notification(config, title, message)
    except Exception:
        pass


class ServerService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self._proc = None
        self._stopping = False

    def SvcStop(self):
        self._stopping = True
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        self.ReportServiceStatus(win32service.SERVICE_START_PENDING)

        exe_path = os.path.join(get_base_dir(), 'txt-dedup-server.exe')
        max_restarts = 10
        restart_window = 3600
        restart_times = []
        restart_count = 0

        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, '')
        )

        while not self._stopping:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

            self._proc = subprocess.Popen(
                [exe_path],
                cwd=get_base_dir(),
                startupinfo=startupinfo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            self.ReportServiceStatus(win32service.SERVICE_RUNNING)

            while self._proc.poll() is None:
                try:
                    rc = win32event.WaitForSingleObject(self.hWaitStop, 5000)
                    if rc == win32event.WAIT_OBJECT_0:
                        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
                        self._kill_process()
                        self.ReportServiceStatus(win32service.SERVICE_STOPPED)
                        return
                except Exception:
                    time.sleep(2)

            if self._stopping:
                break

            now = time.time()
            restart_times = [t for t in restart_times if now - t < restart_window]

            if len(restart_times) >= max_restarts:
                servicemanager.LogMsg(
                    servicemanager.EVENTLOG_ERROR_TYPE,
                    0,
                    (self._svc_name_, f"Server crashed {max_restarts} times, stopping")
                )
                send_alert_email(
                    "[Critical] TXT Dedup Server Stopped",
                    f"Server crashed {max_restarts} times in {restart_window}s.\n\nCheck logs manually."
                )
                self.ReportServiceStatus(win32service.SERVICE_STOPPED)
                return

            restart_times.append(now)
            restart_count += 1

            servicemanager.LogMsg(
                servicemanager.EVENTLOG_WARNING_TYPE,
                0,
                (self._svc_name_, f"Server crashed, restarting (#{restart_count})")
            )

            send_alert_email(
                "[Critical] TXT Dedup Server Crash Restart",
                f"Server exited unexpectedly.\n"
                f"Exit code: {self._proc.returncode}\n"
                f"Restart #{restart_count}\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            time.sleep(3)

        self.ReportServiceStatus(win32service.SERVICE_STOPPED)

    def _kill_process(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None


if __name__ == "__main__":
    if HAS_SERVICE:
        win32serviceutil.HandleCommandLine(ServerService)
    else:
        print("Error: pywin32 not installed.")
        sys.exit(1)