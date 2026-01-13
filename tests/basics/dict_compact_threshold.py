# Test compaction threshold boundary.
# Compaction triggers when: tombstones > filled / 2

# Start with 30 entries
d = {i: i for i in range(30)}

# Delete entries one by one and verify dict remains consistent
for i in range(25):
    del d[i]
    # Verify remaining entries are correct
    expected = list(range(i + 1, 30))
    if list(d.keys()) != expected:
        print("FAIL at deletion", i)
        break
else:
    print("sequential delete OK")

# Verify values are still correct after deletions (spot check)
d2 = {i: i * 2 for i in range(50)}
for i in range(40):
    del d2[i]

print(list(d2.keys()))
print(d2[40], d2[45], d2[49])

# Test with different deletion patterns (non-sequential)
d3 = {i: i for i in range(100)}
# Delete every other entry
for i in range(0, 100, 2):
    del d3[i]

# Should have odd numbers in order
print(list(d3.keys())[:10])
print(list(d3.keys())[-5:])
