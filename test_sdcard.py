import os
import random
from machine import SDCard
from time import ticks_us, sleep_ms
import gc

sdcard = SDCard(1)


def run(size_mult=5):
    gc.collect()
    size = random.randint(1, 1 << 16)
    data = bytearray(size)
    for i in range(size):
        data[i] = i & 0xFF
    print("size = ", size)

    os.chdir("/sdcard")
    print(os.getcwd())
    print(os.listdir())

    ticks_start_us = ticks_us()
    mode = "wb"
    print("Write")
    f = open("test_file.txt", mode)
    for _ in range(size_mult):
        print("loop", _)
        f.write(data)
    f.close()
    ticks_end_us = ticks_us()

    data_size_mb = (len(data) * size_mult) / (1024.0 * 1024.0)
    duration = (ticks_end_us - ticks_start_us) / 1000000.0
    data_rate = data_size_mb / duration

    print("Read back")
    f = open("test_file.txt", "rb")
    for _ in range(size_mult):
        print("loop", _)
        data = f.read(size)  # scan up
        # for i in range(size - 1, -1, -1):  # scan down
        for i in range(size):
            if data[i] != i & 0xFF:
                offset = _ * size + i
                print("data mismatch at 0x%x, %d %d" % (offset, data[i], i & 0xFF))
                break
    f.close()

    print()
    print("Block Length    :", len(data))
    print("Total duration  : {:.1f} s".format(duration))
    print("Transmitted data: {:.1f} Mb".format(data_size_mb))
    print("Data Rate       : {:.1f} MB/s".format(data_rate))

    return data_rate


run(20)
