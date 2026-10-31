import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicehub.rediarize import rediarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--id', required=True)
    parser.add_argument('--data', default=str(Path(__file__).resolve().parents[1]/'data'))
    parser.add_argument('--speakers', type=int, default=2)
    parser.add_argument('--wait', action='store_true')
    args = parser.parse_args()
    try:
        result = asyncio.run(rediarize(args.id, args.data, args.speakers, args.wait))
        print(result)
    except Exception as exc:
        print(type(exc).__name__, str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
