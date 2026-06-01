#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time

import serial


# VSCode Debug 时主要改这几个值。
RELAY_PORT = "/dev/ttyACM0"
BAUDRATE = 115200
TIMEOUT = 1.0
TEST_CHANNEL = 1
ON_HOLD_SECONDS = 1.0

START = 0xA0
OP_OFF = 0x00
OP_ON = 0x01


def build_command(channel: int, operation: int) -> bytes:
    checksum = (START + channel + operation) & 0xFF
    return bytes((START, channel, operation, checksum))


def format_hex(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


class UsbRelay:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_connection: serial.Serial | None = None

    def connect(self) -> None:
        if self.serial_connection and self.serial_connection.is_open:
            return

        print(f"connecting relay: port={self.port}, baudrate={self.baudrate}")
        self.serial_connection = serial.Serial(
            self.port,
            self.baudrate,
            bytesize=8,
            timeout=self.timeout,
        )
        self.serial_connection.reset_input_buffer()
        self.serial_connection.reset_output_buffer()
        print("relay connected")

    def disconnect(self) -> None:
        if not self.serial_connection:
            return

        if self.serial_connection.is_open:
            print("disconnecting relay")
            self.serial_connection.close()
            print("relay disconnected")

        self.serial_connection = None

    def send_command(self, command: bytes) -> None:
        if not self.serial_connection or not self.serial_connection.is_open:
            raise RuntimeError("relay is not connected")

        self.serial_connection.write(command)
        self.serial_connection.flush()
        print(f"sent: {format_hex(command)}")

    def open_channel(self, channel: int) -> None:
        command = build_command(channel, OP_ON)
        self.send_command(command)
        print(f"channel {channel} opened")

    def close_channel(self, channel: int) -> None:
        command = build_command(channel, OP_OFF)
        self.send_command(command)
        print(f"channel {channel} closed")


def debug_test() -> None:
    relay = UsbRelay(RELAY_PORT, BAUDRATE, TIMEOUT)

    try:
        relay.connect()
        relay.open_channel(TEST_CHANNEL)
        time.sleep(ON_HOLD_SECONDS)
        relay.close_channel(TEST_CHANNEL)
    finally:
        relay.disconnect()


if __name__ == "__main__":
    debug_test()
