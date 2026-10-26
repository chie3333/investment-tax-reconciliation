"""Explicit create/inspect commands; no overwrite or migration command."""
import argparse
import json
import sys
import sqlite3
from .bootstrap import create_database, inspect_database, DEFAULT_PATH


def main():
    parser=argparse.ArgumentParser(description='정규화 앱 DB 생성·읽기 전용 검사. 기존 파일은 덮어쓰지 않습니다.')
    parser.add_argument('command',choices=('create','inspect'))
    parser.add_argument('--path',default=str(DEFAULT_PATH))
    args=parser.parse_args()
    try:
        result=(create_database if args.command=='create' else inspect_database)(args.path)
    except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
