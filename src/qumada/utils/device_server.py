import asyncio
import copy
import datetime
import threading
import time
import logging
import json

import websockets
from websockets.asyncio.server import ServerConnection

from qcodes.instrument.parameter import Parameter
from qumada.utils.geometry import gate_list_to_string

logger = logging.getLogger(__name__)


def _get_parameter_data(parameter: Parameter, cache_only: bool = True):
    value = parameter.cache.get(get_if_invalid=not cache_only)
    timestamp = parameter.cache.timestamp

    return {
        "value": value,
        "timestamp": timestamp,
        "name": parameter.full_name,
        "label": parameter.label,
        "unit": parameter.unit,
        "vals": str(parameter.vals),
        "instrument": parameter.instrument.full_name,
        "instrument_class": parameter.instrument.__class__.__name__,
        "root_instrument": parameter.root_instrument.full_name,
        "root_instrument_class": parameter.root_instrument.__class__.__name__,
    }


def _collect_data(*parameters: Parameter, cache_only: bool = True):
    data = []
    for parameter in parameters:
        try:
            parameter_data = _get_parameter_data(parameter, cache_only=cache_only)
        except Exception as e:
            parameter_data = {
                "exception": str(e)
            }
        data.append(parameter_data)

    return {
        "parameters": data,
        "timestamp": time.time(),
    }


class DataCollector:
    def __init__(self, *parameters: Parameter, cache_only: bool = True, minimal_update_delta: float = 1 / 30):
        self.lock = asyncio.Lock()
        self.parameters = parameters
        self.cache_only = cache_only
        self.minimal_update_delta = minimal_update_delta
        self._data = None

    async def get_data(self):
        # although it should be fine to use a blocking lock here,
        # an async lock is the more robust option in case the surrounding code changes.
        async with self.lock:
            if self._data is None:
                delta = float('inf')
            else:
                delta = time.time() - self._data['timestamp']
            if delta >= self.minimal_update_delta:
                self._data = _collect_data(*self.parameters, cache_only=self.cache_only)
            return self._data


class DeviceWebSocket(threading.Thread):
    def __init__(self, collector: DataCollector, ip="127.200.200.9", port=6789):
        super().__init__()
        self.ip = ip
        self.port = port

        self.collector = collector
        self.gate_geometry = None

        self._loop = None
        self._loop_control_future = threading.Event()

    @property
    def address(self):
        return f"ws://{self.ip}:{self.port}"

    def run(self):
        if self._loop is not None:
            raise RuntimeError("Loop already set")

        logger.info(f"Starting websocket server on {self.ip}:{self.port}")
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._serve())
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    def stop(self) -> None:
        """
        May be called from any thread.  It:
          1. signals the serving coroutine to exit,
          2. schedules loop.stop() thread-safely, and
          3. leaves it to the caller to `join()` if they need to block.
        """
        self._loop_control_future.set()
        if self._loop is not None and self._loop.is_running():
            # Wake the event-loop so it notices _stop_event quickly
            self._loop.call_soon_threadsafe(self._loop.stop)

    async def _serve(self) -> None:
        """
        Start the WebSocket server and block until `stop()` is called.
        """
        async with websockets.serve(self._handle_connection, self.ip, self.port) as server:
            logger.info("WebSocket server listening on %s:%d", self.ip, self.port)

            # Poll the threading.Event without blocking the loop forever.
            while not self._loop_control_future.is_set():
                await asyncio.sleep(0.2)

            logger.info("Shutdown requested – closing listener and connections")
            server.close()
            await server.wait_closed()
            logger.info("WebSocket server closed")

    def join(self, timeout = None):
        if self.is_alive():
            self.stop()
        super().join(timeout)

    async def _handle_connection(self, connection: ServerConnection):
        def default_to_json(o):
            if isinstance(o, datetime.datetime):
                return o.timestamp()
            else:
                raise NotImplementedError(f"Cannot serialize {o!r} of type {type(o)}")

        send_geometry = None
        while True:
            data = await self.collector.get_data()

            if self.gate_geometry is not None:
                data["gate_geometry"] = gate_list_to_string(self.gate_geometry)

            serialized = json.dumps(data, default=default_to_json)

            try:
                await connection.send(serialized)
            except websockets.exceptions.ConnectionClosed:
                logger.debug("Connection closed")
                break
            else:
                send_geometry = copy.deepcopy(self.gate_geometry)

            try:
                await asyncio.sleep(self.collector.minimal_update_delta)
            except asyncio.CancelledError:
                logger.debug("Cancelled")
                break
        logger.info("Connection terminated")


