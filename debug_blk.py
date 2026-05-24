#!/usr/bin/env python
"""检查TDXRG.blk文件的十六进制内容"""

from pathlib import Path

blk_file = Path(r"C:\new_hxzq_hc\T0002\blocknew\TDXRG.blk")

with open(blk_file, "rb") as f:
    data = f.read()

print(f"文件大小: {len(data)} 字节")
print(f"\n前200字节（十六进制）：")
print("=" * 80)

for i in range(0, min(200, len(data)), 16):
    hex_str = " ".join(f"{b:02x}" for b in data[i:i+16])
    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i+16])
    print(f"{i:04x}: {hex_str:<48} {ascii_str}")

print("\n" + "=" * 80)
print("\n分析可能的记录结构：")

# 尝试不同的记录长度
for record_size in [6, 7, 8, 9, 10]:
    if len(data) % record_size == 0:
        num_records = len(data) // record_size
        print(f"\n如果每条记录{record_size}字节：共{num_records}条记录")
        print(f"前3条记录：")
        for i in range(3):
            offset = i * record_size
            record = data[offset:offset+record_size]
            hex_str = " ".join(f"{b:02x}" for b in record)
            print(f"  记录{i+1}: {hex_str}")
