#!/usr/bin/env python3
"""Container lifecycle and maintenance commands."""
import argparse
import sys
from clash_runtime.runtime import Runtime, healthcheck, log, run
from clash_runtime.settings import ConfigError, Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "update", "healthcheck"))
    args = parser.parse_args()
    try:
        settings = Settings.load()
        if args.command == "run":
            return run(settings)
        if args.command == "update":
            Runtime(settings).update()
        else:
            healthcheck(settings)
        return 0
    except (ConfigError, OSError, ValueError) as error:
        log(str(error) if isinstance(error, ConfigError) else "Operation failed; check service availability and file permissions")
        return 1


if __name__ == "__main__":
    sys.exit(main())
