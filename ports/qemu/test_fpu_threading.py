# Test FPU context switching and register preservation
# This test verifies that FPU registers are correctly saved/restored
# during thread context switches on ARM Cortex-M4 with FPU

import _thread
import time

# Global results dictionary for thread outputs
results = {}
lock = _thread.allocate_lock()


def fpu_intensive_thread(thread_id, iterations):
    """
    Perform FPU-intensive operations to stress test context switching.
    Uses multiple FPU registers and performs calculations that would
    be corrupted if FPU state is not properly preserved.
    """
    # Each thread uses different starting values
    base = thread_id * 1000.0

    # Perform repeated FPU operations with yields to force context switches
    for i in range(iterations):
        # Use different FPU operations: add, mul, div, sqrt approximation
        a = base + float(i)
        b = a * 1.5
        c = b / 2.0
        d = c + a * 0.25

        # Force context switch by sleeping
        time.sleep_ms(1)

        # Continue calculation after context switch
        e = d * 2.0 - a
        f = e / 3.0 + b

        # Store intermediate result
        result = f

    # Store final result
    with lock:
        results[thread_id] = result
        print(f"Thread {thread_id}: final result = {result:.6f}")


def main():
    print("FPU Threading Test")
    print("=" * 50)

    # Test parameters
    num_threads = 3
    iterations = 10

    print(f"Creating {num_threads} threads, each performing {iterations} FPU iterations")

    # Create threads
    threads = []
    for i in range(num_threads):
        tid = _thread.start_new_thread(fpu_intensive_thread, (i, iterations))
        threads.append(tid)
        print(f"Started thread {i}")

    # Wait for all threads to complete
    time.sleep(2)

    # Verify results
    print("\n" + "=" * 50)
    print("Results verification:")

    # Calculate expected values (same calculation as in thread)
    expected = {}
    for thread_id in range(num_threads):
        base = thread_id * 1000.0
        for i in range(iterations):
            a = base + float(i)
            b = a * 1.5
            c = b / 2.0
            d = c + a * 0.25
            e = d * 2.0 - a
            f = e / 3.0 + b
            result = f
        expected[thread_id] = result

    # Check if all results match expected values
    all_pass = True
    for thread_id in range(num_threads):
        if thread_id in results:
            actual = results[thread_id]
            exp = expected[thread_id]
            # Allow small floating point tolerance
            diff = abs(actual - exp)
            tolerance = 0.001

            if diff < tolerance:
                print(f"Thread {thread_id}: PASS (result={actual:.6f}, expected={exp:.6f})")
            else:
                print(
                    f"Thread {thread_id}: FAIL (result={actual:.6f}, expected={exp:.6f}, diff={diff:.6f})"
                )
                all_pass = False
        else:
            print(f"Thread {thread_id}: FAIL (no result)")
            all_pass = False

    print("=" * 50)
    if all_pass:
        print("FPU THREADING TEST: PASS")
        print("All threads completed with correct FPU calculations")
        print("FPU registers preserved correctly across context switches")
    else:
        print("FPU THREADING TEST: FAIL")
        print("FPU state corruption detected!")

    return 0 if all_pass else 1


if __name__ == "__main__":
    main()
