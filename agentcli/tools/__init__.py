# agentcli/tools/__init__.py

"""
Tools package.

Importing this package ensures that built-in tools
are registered via registry._register_builtins().
"""

# Explicit imports are not required here because
# registry.py auto-imports fs and shell on load.
# This file exists mainly to mark the directory as a package.
