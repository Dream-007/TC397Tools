#!/usr/bin/env python
# -*- coding: utf-8 -*-
# coding=utf-8
 
import logging
import sys
import time
import serial
from serial.tools import list_ports
import os
 
class USBRelay():
    """功能通道映射关系
    """
    """查询是否存在DSTUR-T20双通道USB继电器
    """
    
 
    def __init__(self, port: str = None, baudrate: int = 115200, bytesize: int = 8, timeout: float = 0.1):
        ports = serial.tools.list_ports.comports() # 列出所有可用的串口
        self.IsOpen = False
        for port_Info in ports:
            if port_Info.device == port:
                self.port = port
                self.baudrate = baudrate
                self.bytesize = bytesize
                self.timeout = timeout
                self.IsOpen = self.connect()
                break
        else:
            print("未找到指定的串口设备，请检查连接。")

        
    def connect(self):
        if not self.IsOpen:
            self.serial = serial.Serial(self.port, self.baudrate, bytesize=self.bytesize,
                                      timeout=self.timeout)
            self.IsOpen = self.serial.is_open
        return self.IsOpen
    
    def disconnect(self):
        if self.IsOpen:
            self.serial.close()
            self.IsOpen = False
    
    def __write_and_read(self, data, retry=2, delay=0.1):
        """
        发送命令并读取响应
        
        Args:
            data: 字节数据
            retry: 重试次数
            delay: 重试间隔（秒）
        
        Returns:
            响应数据或None
        """
        for attempt in range(retry):
            # 清空缓冲区
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
            
            # 发送
            self.serial.write(data)
            # 等待响应
            time.sleep(delay)
            # 读取
            if self.serial.in_waiting:
                response = self.serial.read(self.serial.in_waiting).decode(errors="replace").strip()
                return response
        return None
    
    def poweron(self,ChnIdx):  # 打开继电器
        OnCmd = [0xA0,ChnIdx,0x01,0xA0 + ChnIdx + 0x01]
        return self.__write_and_read(bytes(OnCmd)).find("ON") != -1 
        
    def poweroff(self,ChnIdx):  # 关闭继电器
        OffCmd = [0xA0,ChnIdx,0x00,0xA0 + ChnIdx + 0x00]
        return self.__write_and_read(bytes(OffCmd)).find("OFF") != -1
    
    
 

if __name__ == '__main__':
    excluded_port = '/dev/ttyACM0'
    Relay = USBRelay(excluded_port)
    for i in range(10):
        Relay.connect()
        time.sleep(1)
        Relay.poweron(1)
        time.sleep(1)
        Relay.poweroff(1)
        time.sleep(1)
        Relay.poweron(2)
        time.sleep(1)
        Relay.poweroff(2)
        time.sleep(1)
        Relay.disconnect()
        time.sleep(1)
    
    

    
    
    
    
 
 