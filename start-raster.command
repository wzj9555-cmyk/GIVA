#!/bin/bash
set -eu
exec "$(dirname "$0")/start.command" "$@"
