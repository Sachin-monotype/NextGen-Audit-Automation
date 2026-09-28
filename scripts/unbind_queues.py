#!/usr/bin/env python3
"""Unbind automation queues to stop them from accumulating messages."""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "python"))

from audit_validator.config import load_config
from audit_validator.rabbitmq.connection import url_parameters
from audit_validator.rabbitmq.enriched_routing_keys import ENRICHED_ROUTING_KEYS_LIST
import pika

def main():
    config = load_config()
    rmq = config.rabbitmq

    print(f"Connecting to RabbitMQ to unbind queues...")
    params = url_parameters(rmq.url)
    try:
        connection = pika.BlockingConnection(params)
    except Exception as e:
        print(f"Failed to connect to RabbitMQ: {e}")
        return

    channel = connection.channel()

    print(f"\nUnbinding raw queue: '{rmq.raw_queue}' from exchange: '{rmq.raw_exchange}'")
    try:
        channel.queue_unbind(queue=rmq.raw_queue, exchange=rmq.raw_exchange, routing_key="#")
        print("  -> Successfully unbound raw queue.")
    except Exception as e:
        print(f"  -> Note: {e}")

    print(f"\nUnbinding enriched queue: '{rmq.enriched_queue}' from exchange: '{rmq.enriched_exchange}'")
    if rmq.enriched_use_wildcard_bind:
        try:
            channel.queue_unbind(queue=rmq.enriched_queue, exchange=rmq.enriched_exchange, routing_key="#")
            print("  -> Successfully unbound enriched queue (wildcard).")
        except Exception as e:
            print(f"  -> Note: {e}")
    else:
        success_count = 0
        for rk in ENRICHED_ROUTING_KEYS_LIST:
            try:
                channel.queue_unbind(queue=rmq.enriched_queue, exchange=rmq.enriched_exchange, routing_key=rk)
                success_count += 1
            except Exception as e:
                pass
        print(f"  -> Successfully unbound {success_count} routing keys for enriched queue.")

    if rmq.consume_dead_letter_queue:
        print(f"\nUnbinding dead letter queue: '{rmq.dead_letter_queue}' from exchange: '{rmq.dead_letter_exchange}'")
        try:
            channel.queue_unbind(queue=rmq.dead_letter_queue, exchange=rmq.dead_letter_exchange, routing_key="dl")
            print("  -> Successfully unbound dead letter queue.")
        except Exception as e:
            print(f"  -> Note: {e}")

    connection.close()
    print("\nDone. The queues are now unbound and will stop accumulating new messages from the exchange.")

if __name__ == "__main__":
    main()
