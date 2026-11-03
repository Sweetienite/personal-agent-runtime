"""The MCP bridge (plan P5): a thin stdio server through which a coordinator dispatches tasks to the Kernel.

It validates arguments and calls the Kernel. It holds no model credentials, copies no conversation and edits no
configuration; the person's own decisions are not reachable from it. See docs/MCP.md.
"""
