# test basic thread creation (returns valid thread ID)
# This test only verifies k_thread_create() succeeds, not execution
import _thread


def worker():
    pass


# Create thread and check ID is non-zero
tid = _thread.start_new_thread(worker, ())
print(type(tid) == int)
print(tid != 0)
print("done")
