#!/usr/bin/env python3
"""One-line summary of the script.

    ./thing.py INPUT -o out.json    what this does

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import sys


def run(args):
    result = {"input": args.input}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    print("wrote %s" % args.out)
    # print an explicit `note:` line for anything partial or degraded


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input")
    ap.add_argument("-o", "--out", default="out.json")
    args = ap.parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()
