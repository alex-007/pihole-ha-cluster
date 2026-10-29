#!/bin/bash
# Convenience entry point; tests use only the Python standard library.
exec python3 "$(dirname "$0")/test_lease_sync_integration.py" "$@"
