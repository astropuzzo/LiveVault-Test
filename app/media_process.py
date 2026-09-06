"""Launch background media work with CPU headroom, without preexec_fn threads."""
import os
import sys


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Missing media command")
    if hasattr(os, "sched_getaffinity"):
        available = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, available[:max(1, len(available) // 2)])
    if hasattr(os, "nice"):
        os.nice(10)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
