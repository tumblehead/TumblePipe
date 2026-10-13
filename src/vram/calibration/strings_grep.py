import re
import sys

pattern = re.compile(sys.argv[1].encode(), re.IGNORECASE)
for path in sys.argv[2:]:
    data = open(path, "rb").read()
    found = set()
    for m in re.finditer(rb"[\x20-\x7e]{6,}", data):
        s = m.group(0)
        if pattern.search(s):
            found.add(s.decode("ascii", "replace")[:200])
    print(f"== {path}: {len(found)}")
    for s in sorted(found)[:60]:
        print("  ", s)
