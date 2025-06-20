# Comprehensive test for sys.settrace() local variable name preservation
# This test provides detailed verification of the local variable name feature

import sys

try:
    sys.settrace
except AttributeError:
    print("SKIP")
    raise SystemExit

def comprehensive_test():
    """Comprehensive test of local variable name preservation."""
    
    # Test data collection
    trace_events = []
    
    def trace_handler(frame, event, arg):
        if frame.f_code.co_name.startswith('test_'):
            locals_dict = frame.f_locals
            if locals_dict:
                real_names = [k for k in locals_dict.keys() if not k.startswith('local_')]
                fallback_names = [k for k in locals_dict.keys() if k.startswith('local_')]
                trace_events.append({
                    'event': event,
                    'function': frame.f_code.co_name,
                    'real_names': real_names,
                    'fallback_names': fallback_names,
                    'total_vars': len(locals_dict)
                })
        return trace_handler
    
    def test_simple():
        name = "Alice"
        age = 25
        active = True
        return name, age, active
    
    def test_complex():
        data = {"key": "value"}
        items = [1, 2, 3]
        count = len(items)
        return data, count
    
    def test_loop():
        results = []
        for i in range(3):
            item = f"item_{i}"
            results.append(item)
        return results
    
    # Run tests
    trace_events.clear()
    sys.settrace(trace_handler)
    
    result1 = test_simple()
    result2 = test_complex()
    result3 = test_loop()
    
    sys.settrace(None)
    
    # Analyze results
    total_events = len(trace_events)
    events_with_real_names = sum(1 for e in trace_events if e['real_names'])
    events_with_fallback = sum(1 for e in trace_events if e['fallback_names'])
    
    print(f"Total events: {total_events}")
    print(f"Events with real names: {events_with_real_names}")
    print(f"Events with fallback names: {events_with_fallback}")
    
    # Check specific variable names
    all_real_names = set()
    for event in trace_events:
        all_real_names.update(event['real_names'])
    
    expected_names = {'name', 'age', 'active', 'data', 'items', 'count', 'results', 'i', 'item'}
    found_expected = expected_names.intersection(all_real_names)
    
    print(f"Expected names found: {len(found_expected)}")
    print(f"Sample real names: {sorted(list(all_real_names))[:10]}")
    
    # Verify results
    print(f"Test results correct: {result1 == ('Alice', 25, True) and result2[1] == 3 and len(result3) == 3}")
    
    return events_with_real_names > 0

if __name__ == "__main__":
    success = comprehensive_test()
    print(f"Comprehensive test passed: {success}")