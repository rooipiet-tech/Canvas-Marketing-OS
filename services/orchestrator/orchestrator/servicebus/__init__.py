"""Service Bus abstraction: a local/emulated double plus a thin real
azure-servicebus-backed gateway, sharing one uniform interface
(send/receive/complete/abandon) so orchestrator/worker.py is agnostic to
which backend it's driving (C4).
"""
