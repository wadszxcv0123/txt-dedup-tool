#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime

VERSION = "1.8.0"
BUILD_TIME = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

APP_NAME = "TXT查重工具"
AUTHOR = "张文龙"
CONTACT = "18053292127"

def get_version():
    return VERSION

def get_build_time():
    return BUILD_TIME

def get_app_info():
    return {
        'name': APP_NAME,
        'version': VERSION,
        'author': AUTHOR,
        'contact': CONTACT,
        'build_time': BUILD_TIME
    }

if __name__ == "__main__":
    print(f"版本: {VERSION}")
    print(f"作者: {AUTHOR}")
    print(f"联系: {CONTACT}")
    print(f"构建时间: {BUILD_TIME}")