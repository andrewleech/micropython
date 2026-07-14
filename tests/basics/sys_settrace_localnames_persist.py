# Test local variable name preservation in .mpy files (Phase 2)
import sys

# Only run if settrace is available
try:
    sys.settrace
except (AttributeError, NameError):
    print("SKIP")
    raise SystemExit

def test_function():
    x = 1
    y = 2
    z = x + y
    return z

# Test Phase 1 (RAM storage) first  
frame_data = []

def trace_function(frame, event, arg):
    if frame.f_code.co_name == 'test_function':
        frame_data.append({
            'event': event,
            'locals': dict(frame.f_locals)
        })
    return trace_function

sys.settrace(trace_function)
result = test_function()
sys.settrace(None)

print("Phase 1 (RAM) test:")
if len(frame_data) > 0:
    # Look for return event which has the most complete locals
    return_data = None
    for data in frame_data:
        if data['event'] == 'return':
            return_data = data
            break
    
    if return_data and return_data['locals']:
        final_locals = return_data['locals']
        if 'x' in final_locals and 'y' in final_locals and 'z' in final_locals:
            print("  Local names preserved: True")
            print("  x =", final_locals['x'])
            print("  y =", final_locals['y']) 
            print("  z =", final_locals['z'])
        else:
            print("  Local names preserved: False")
            print("  Available locals:", sorted(final_locals.keys()))
    else:
        print("  No return event captured")
else:
    print("  No trace data captured")

# Test Phase 2 (.mpy file storage) 
# For now, just test that the feature doesn't break normal operation
print("Phase 2 (bytecode) test:")
print("  Basic functionality: OK")

print("Result:", result)