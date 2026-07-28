"""Uniform consumer/gateway wrapper over either the local double or the
real azure-servicebus SDK, so worker.py is agnostic to which backend it is
driving. Auth is always DefaultAzureCredential (managed identity) against
SERVICE_BUS_NAMESPACE — never a connection string (C4/AC-024).
"""

from __future__ import annotations

from typing import Any, Literal

from orchestrator.servicebus.local_double import InMemoryServiceBus, LockedMessage


class AzureServiceBusGateway:
    """Thin uniform-interface wrapper over the real azure-servicebus SDK.
    Lazily creates per-queue sender/receiver objects. Auth is always
    DefaultAzureCredential (managed identity), never a connection string.
    """

    def __init__(self, namespace: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.servicebus import ServiceBusClient

        self._namespace = namespace
        self._client = ServiceBusClient(
            fully_qualified_namespace=namespace, credential=DefaultAzureCredential()
        )
        self._senders: dict[str, Any] = {}
        self._receivers: dict[str, Any] = {}
        self._raw_by_id: dict[str, Any] = {}

    def _sender(self, queue: str) -> Any:
        if queue not in self._senders:
            self._senders[queue] = self._client.get_queue_sender(queue_name=queue)
        return self._senders[queue]

    def _receiver(self, queue: str) -> Any:
        if queue not in self._receivers:
            self._receivers[queue] = self._client.get_queue_receiver(queue_name=queue)
        return self._receivers[queue]

    def send(self, queue: str, body: dict[str, Any]) -> None:
        import json

        from azure.servicebus import ServiceBusMessage

        self._sender(queue).send_messages(ServiceBusMessage(json.dumps(body)))

    def receive(self, queue: str, max_count: int = 10) -> list[LockedMessage]:
        import json

        raw_messages = self._receiver(queue).receive_messages(
            max_message_count=max_count, max_wait_time=5
        )
        results = []
        for raw in raw_messages:
            body = json.loads(str(raw))
            msg_id = str(raw.message_id) or str(raw.sequence_number)
            self._raw_by_id[msg_id] = raw
            results.append(
                LockedMessage(
                    id=msg_id,
                    queue=queue,
                    body=body,
                    delivery_count=raw.delivery_count or 1,
                )
            )
        return results

    def complete(self, msg: LockedMessage) -> None:
        raw = self._raw_by_id.pop(msg.id, None)
        if raw is not None:
            self._receiver(msg.queue).complete_message(raw)

    def abandon(self, msg: LockedMessage) -> None:
        raw = self._raw_by_id.pop(msg.id, None)
        if raw is not None:
            self._receiver(msg.queue).abandon_message(raw)

    def close(self) -> None:
        for sender in self._senders.values():
            sender.close()
        for receiver in self._receivers.values():
            receiver.close()
        self._client.close()


class ServiceBusConsumer:
    """Scoped-to-one-queue view over a shared client (InMemoryServiceBus or
    AzureServiceBusGateway).
    """

    def __init__(
        self,
        queue: Literal["task", "event"],
        use_local_double: bool,
        client: InMemoryServiceBus | AzureServiceBusGateway,
    ) -> None:
        self.queue = queue
        self.use_local_double = use_local_double
        self.client = client

    def receive(self, max_count: int = 10) -> list[LockedMessage]:
        return self.client.receive(self.queue, max_count=max_count)

    def complete(self, msg: LockedMessage) -> None:
        self.client.complete(msg)

    def abandon(self, msg: LockedMessage) -> None:
        self.client.abandon(msg)


def build_client(
    use_local_double: bool,
    local_bus: InMemoryServiceBus | None = None,
    namespace: str | None = None,
) -> InMemoryServiceBus | AzureServiceBusGateway:
    if use_local_double:
        return local_bus if local_bus is not None else InMemoryServiceBus()
    if not namespace:
        raise ValueError("namespace is required when use_local_double is False")
    return AzureServiceBusGateway(namespace)
