"""Summarise a vkprobe log: live buffers/images by usage, and any names."""
import collections
import sys

BUF_USAGE = {0x1: "xfer_src", 0x2: "xfer_dst", 0x4: "uniform_texel", 0x8: "storage_texel",
             0x10: "uniform", 0x20: "storage", 0x40: "index", 0x80: "vertex",
             0x100: "indirect", 0x20000: "device_address"}
IMG_USAGE = {0x1: "xfer_src", 0x2: "xfer_dst", 0x4: "sampled", 0x8: "storage",
             0x10: "color_att", 0x20: "depth_att", 0x40: "transient", 0x80: "input_att"}


def flags(value, table):
    names = [n for bit, n in table.items() if value & bit]
    return "|".join(names) or hex(value)


FORMAT_BPP = {37: 4, 43: 4, 44: 4, 50: 4, 97: 8, 109: 16, 98: 8, 106: 12, 126: 4, 130: 4,
              9: 1, 13: 1, 16: 2, 76: 2, 100: 4, 104: 8, 103: 4, 129: 4}

buffers, images, names = {}, {}, {}
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    parts = line.rstrip("\n").split(",")
    kind = parts[0]
    if kind == "buffer":
        buffers[parts[1]] = (int(parts[2]), int(parts[3], 16))
    elif kind == "destroy_buffer":
        buffers.pop(parts[1], None)
    elif kind == "image":
        w, h, d, mips, layers, fmt, usage = parts[2:9]
        images[parts[1]] = (int(w), int(h), int(d), int(mips), int(layers), int(fmt), int(usage, 16))
    elif kind == "destroy_image":
        images.pop(parts[1], None)
    elif kind == "name":
        names[parts[2]] = (parts[1], ",".join(parts[3:]))

by = collections.Counter()
count = collections.Counter()
for size, usage in buffers.values():
    key = flags(usage & ~0x3 & ~0x20000, BUF_USAGE) or "transfer"
    by[key] += size
    count[key] += 1
print(f"live buffers: {len(buffers)}, {sum(s for s, _ in buffers.values()) / 2**20:.2f} MiB")
for key, size in by.most_common(12):
    print(f"  {size / 2**20:9.2f} MiB  {count[key]:5d}  {key}")

def img_bytes(w, h, d, mips, layers, fmt):
    bpp = FORMAT_BPP.get(fmt, 4)
    total, ww, hh = 0, w, h
    for _ in range(mips):
        total += ww * hh * max(1, d) * bpp
        ww, hh = max(1, ww // 2), max(1, hh // 2)
    return total * layers

iby = collections.Counter(); icount = collections.Counter()
big = []
for h, (w, hh, d, mips, layers, fmt, usage) in images.items():
    key = flags(usage & ~0x3, IMG_USAGE)
    b = img_bytes(w, hh, d, mips, layers, fmt)
    iby[key] += b; icount[key] += 1
    big.append((b, w, hh, mips, fmt, key))
print(f"live images: {len(images)}, ~{sum(iby.values()) / 2**20:.2f} MiB")
for key, size in iby.most_common(8):
    print(f"  {size / 2**20:9.2f} MiB  {icount[key]:5d}  {key}")
for b, w, hh, mips, fmt, key in sorted(big, reverse=True)[:6]:
    print(f"    {b / 2**20:8.2f} MiB  {w}x{hh} mips={mips} fmt={fmt} {key}")
print(f"named objects: {len(names)}")
for handle, (otype, name) in list(names.items())[:25]:
    print(f"  type={otype} {name}")
