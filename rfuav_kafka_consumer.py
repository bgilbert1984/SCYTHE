"""
rfuav_kafka_consumer.py

Kafka consumer for RFUAV detections. Reuses the canonical RFUAV event schema and
hands events to an injected SCYTHE ingest handler so streaming and HTTP paths
share the same audited write flow.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional
import json
import logging
import threading


logger = logging.getLogger(__name__)


class RFUAVKafkaConsumer:
    def __init__(
        self,
        *,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
        bootstrap_servers: Any = "localhost:9092",
        topic: str = "rf.uav.detections",
        group_id: str = "scythe-rfuav",
        max_poll_records: int = 500,
        auto_offset_reset: str = "latest",
        consumer: Optional[Any] = None,
    ) -> None:
        self.handler = handler
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.group_id = group_id
        self.max_poll_records = max(1, int(max_poll_records))
        self.auto_offset_reset = auto_offset_reset
        self._consumer = consumer if consumer is not None else self._build_consumer()
        self._thread: Optional[threading.Thread] = None

    def _build_consumer(self) -> Any:
        try:
            from kafka import KafkaConsumer
        except ImportError as exc:
            raise RuntimeError("Kafka support requires kafka-python to be installed") from exc

        return KafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset=self.auto_offset_reset,
            max_poll_records=self.max_poll_records,
        )

    def handle(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(event, dict):
            raise ValueError("RFUAV Kafka event must be a dict")
        return self.handler(event)

    def run(self) -> None:
        for message in self._consumer:
            try:
                self.handle(message.value)
            except Exception as exc:
                logger.warning("RFUAV Kafka consumer failed to handle message: %s", exc)

    def start_background(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._thread = threading.Thread(target=self.run, name="rfuav-kafka-consumer", daemon=True)
        self._thread.start()
        return self._thread
