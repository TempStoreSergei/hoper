#!/usr/bin/env python3
"""
Усовершенствованный клиент для SMART Hopper 3
Дата: 2025-03-07
"""

import serial
import serial.tools.list_ports
import sys
import time
import logging
import os
import subprocess

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - TempStoreSergei - %(levelname)s - %(message)s'
)
logger = logging.getLogger("SH3_Client")

# Константы для SMART Hopper 3
VENDOR_ID = 0x191c
PRODUCT_ID = 0x4104

# Константы для протокола SMART Hopper (вариации от стандартного SSP)
CMD_SYNC = 0x11
CMD_SETUP_REQUEST = 0x05
CMD_ENABLE = 0x0A
CMD_DISABLE = 0x09
CMD_POLL = 0x07
CMD_RESET = 0x01

def calculate_crc(data):
    """Вычисляет CRC для SSP пакета"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0x8408
            else:
                crc >>= 1
    return crc

def create_ssp_packet(command, sequence=0x80, data=None):
    """Создает пакет SSP"""
    if data is None:
        data = []

    # Формат пакета: STX(0x7F) + Sequence + Length + Command + Data + CRC
    packet = [0x7F, sequence, len(data) + 1, command] + (data if isinstance(data, list) else list(data))
    crc = calculate_crc(packet)
    packet.extend([crc & 0xFF, (crc >> 8) & 0xFF])

    return bytes(packet)

def test_serial_port(port_name, baud_rates=None):
    """Тестирует последовательный порт с разными настройками"""
    if baud_rates is None:
        baud_rates = [9600, 19200, 38400, 115200]

    # Проверяем права доступа к порту
    try:
        if not os.access(port_name, os.R_OK | os.W_OK):
            logger.error(f"Нет прав доступа к {port_name}. Пробуем добавить текущего пользователя в группу dialout...")
            try:
                # Пробуем исправить права доступа
                subprocess.run(['sudo', 'usermod', '-a', '-G', 'dialout', os.getlogin()], check=True)
                subprocess.run(['sudo', 'chmod', 'a+rw', port_name], check=True)
                logger.info(f"Права доступа к {port_name} обновлены")
            except Exception as e:
                logger.error(f"Не удалось обновить права доступа: {str(e)}")
                logger.info("Попробуйте выполнить: sudo usermod -a -G dialout $USER")
                logger.info("и перезагрузить компьютер или выйти и войти снова")
    except Exception as e:
        logger.warning(f"Не удалось проверить права доступа: {str(e)}")

    # Пробуем разные скорости передачи данных
    for baud in baud_rates:
        logger.info(f"Тестирование {port_name} со скоростью {baud} бод...")

        try:
            # Открываем порт с текущими настройками
            with serial.Serial(
                port=port_name,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=2
            ) as ser:
                # Управление линиями управления
                configurations = [
                    (False, False),  # DTR off, RTS off
                    (True, False),   # DTR on, RTS off
                    (False, True),   # DTR off, RTS on
                    (True, True),    # DTR on, RTS on
                ]

                for dtr, rts in configurations:
                    logger.info(f"  Пробуем DTR={dtr}, RTS={rts}")
                    ser.dtr = dtr
                    ser.rts = rts
                    time.sleep(0.5)

                    # Очистка буферов
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()

                    # Отправка команды SYNC
                    sync_packet = create_ssp_packet(CMD_SYNC)
                    logger.info(f"  Отправка SYNC пакета: {sync_packet.hex()}")
                    ser.write(sync_packet)

                    # Чтение ответа
                    response = ser.read(64)
                    if response:
                        logger.info(f"  !!! ПОЛУЧЕН ОТВЕТ: {response.hex()}")
                        logger.info(f"  Найдены рабочие настройки: скорость={baud}, DTR={dtr}, RTS={rts}")
                        return {
                            'port': port_name,
                            'baudrate': baud,
                            'dtr': dtr,
                            'rts': rts
                        }
                    else:
                        logger.info("  Нет ответа")

                    # Очистка буферов
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()

                    # Пробуем команду RESET
                    reset_packet = create_ssp_packet(CMD_RESET)
                    logger.info(f"  Отправка RESET пакета: {reset_packet.hex()}")
                    ser.write(reset_packet)

                    # Чтение ответа
                    response = ser.read(64)
                    if response:
                        logger.info(f"  !!! ПОЛУЧЕН ОТВЕТ: {response.hex()}")
                        logger.info(f"  Найдены рабочие настройки: скорость={baud}, DTR={dtr}, RTS={rts}")
                        return {
                            'port': port_name,
                            'baudrate': baud,
                            'dtr': dtr,
                            'rts': rts
                        }
                    else:
                        logger.info("  Нет ответа")

        except Exception as e:
            logger.warning(f"  Ошибка при тестировании {port_name} со скоростью {baud}: {str(e)}")

    logger.warning(f"Не удалось установить связь с устройством через порт {port_name}")
    return None

def create_udev_rule():
    """Создает правило udev для устройства"""
    logger.info("Создание правила udev для SMART Hopper 3...")

    rule_content = f'''# Правило udev для SMART Hopper 3
ACTION=="add", SUBSYSTEM=="tty", ATTRS{{idVendor}}=="{VENDOR_ID:04x}", ATTRS{{idProduct}}=="{PRODUCT_ID:04x}", MODE="0666", GROUP="dialout", SYMLINK+="smarthopper"
'''

    rule_file = '/etc/udev/rules.d/99-smarthopper.rules'

    try:
        # Создаем временный файл
        tmp_file = '/tmp/99-smarthopper.rules'
        with open(tmp_file, 'w') as f:
            f.write(rule_content)

        logger.info(f"Временный файл правила создан: {tmp_file}")
        logger.info("Для установки правила выполните:")
        logger.info(f"sudo cp {tmp_file} {rule_file}")
        logger.info("sudo udevadm control --reload-rules && sudo udevadm trigger")

        print("\nДля автоматического создания симлинка /dev/smarthopper и настройки прав доступа:")
        print(f"sudo cp {tmp_file} {rule_file}")
        print("sudo udevadm control --reload-rules && sudo udevadm trigger")

    except Exception as e:
        logger.error(f"Ошибка при создании правила udev: {str(e)}")

def identify_device():
    """Определяет тип устройства и возвращает информацию о нем"""
    # Находим порт устройства SMART Hopper 3
    port = None
    for p in serial.tools.list_ports.comports():
        if p.vid == VENDOR_ID and p.pid == PRODUCT_ID:
            port = p.device
            logger.info(f"Найден порт устройства SMART Hopper 3: {port}")

            # Вывод информации об устройстве
            logger.info(f"Информация об устройстве:")
            logger.info(f"  Производитель: {p.manufacturer}")
            logger.info(f"  Описание: {p.description}")
            logger.info(f"  Серийный номер: {p.serial_number}")
            logger.info(f"  Расположение: {p.location}")
            logger.info(f"  Интерфейс: {p.interface}")
            break

    if not port:
        logger.warning("Порт устройства SMART Hopper 3 не найден")

    return port

def main():
    """Основная функция"""
    logger.info("Запуск клиента SMART Hopper 3")

    # 1. Идентификация устройства
    port = identify_device()
    if not port:
        logger.error("Устройство SMART Hopper 3 не найдено")
        return 1

    # 2. Создание правила udev
    create_udev_rule()

    # 3. Тестирование последовательного порта
    settings = test_serial_port(port)

    if not settings:
        logger.error("Не удалось установить связь с устройством")
        return 1

    # 4. Работа с устройством
    logger.info("Начинаем работу с устройством...")

    try:
        # Открываем порт с найденными настройками
        with serial.Serial(
            port=settings['port'],
            baudrate=settings['baudrate'],
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=2
        ) as ser:
            # Устанавливаем DTR/RTS
            ser.dtr = settings['dtr']
            ser.rts = settings['rts']

            # Очистка буферов
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            # Последовательность команд для инициализации
            sequence = 0x80
            commands = [
                (CMD_SYNC, "SYNC"),
                (CMD_SETUP_REQUEST, "SETUP_REQUEST"),
                (CMD_ENABLE, "ENABLE"),
                (CMD_POLL, "POLL")
            ]

            for cmd, name in commands:
                packet = create_ssp_packet(cmd, sequence)
                logger.info(f"Отправка команды {name} (0x{cmd:02X}): {packet.hex()}")

                ser.write(packet)
                time.sleep(0.5)

                response = ser.read(64)
                if response:
                    logger.info(f"Ответ на {name}: {response.hex()}")

                    # Если успешно получен ответ на POLL, пробуем отправить еще несколько команд опроса
                    if cmd == CMD_POLL:
                        logger.info("Отправка дополнительных команд POLL")
                        for i in range(3):
                            poll_packet = create_ssp_packet(CMD_POLL, 0x80 | ((sequence + i + 1) & 0x7F))
                            logger.info(f"POLL #{i+1}: {poll_packet.hex()}")

                            ser.write(poll_packet)
                            time.sleep(0.5)

                            poll_response = ser.read(64)
                            if poll_response:
                                logger.info(f"Ответ на POLL #{i+1}: {poll_response.hex()}")
                            else:
                                logger.warning(f"Нет ответа на POLL #{i+1}")
                else:
                    logger.warning(f"Нет ответа на {name}")

                # Увеличиваем sequence
                sequence = 0x80 | ((sequence + 1) & 0x7F)

    except Exception as e:
        logger.error(f"Ошибка при работе с устройством: {str(e)}")
        return 1

    logger.info("Работа с устройством завершена")
    return 0

if __name__ == "__main__":
    sys.exit(main())
