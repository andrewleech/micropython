# State Machine Library Comparison

Analysis of three Python state machine libraries for code size, RAM usage, and MicroPython compatibility.

Date: 2025-11-12

## Executive Summary

| Library | Code Size | RAM Efficiency | MicroPython Compatible | Best For |
|---------|-----------|----------------|------------------------|----------|
| **statechart** | 105 KB | Moderate | ✓ Yes (minor porting) | Embedded systems, UML statecharts |
| **transitions** | 565 KB | High | ✗ No (major porting) | General CPython, memory-constrained |
| **python-statemachine** | 282 KB | Low | ✗ No (architectural incompatibility) | Modern async Python apps |

**For MicroPython:** statechart is the only viable option.

**For CPython:** transitions offers best RAM efficiency; python-statemachine provides modern features at cost of higher overhead.

---

## Code Size Comparison

### Total Repository Size

| Library | Python Code | Core Module | File Count |
|---------|-------------|-------------|------------|
| **statechart** | 105 KB | runtime.py (~550 lines) | 7 files |
| **python-statemachine** | 282 KB | statemachine.py (~340 lines) | 26 files |
| **transitions** | 565 KB | core.py (~1,100 lines) | 3 core + extensions |

### Module Structure

**statechart** (7 files):
- `__init__.py` - Package initialization
- `display.py` - Visualization
- `event.py` - Event handling
- `pseudostates.py` - Initial/Choice/History states
- `runtime.py` - Core execution engine
- `states.py` - State definitions
- `transitions.py` - Transition logic

**transitions** (3 core + extensions):
- `__init__.py` - Package API
- `core.py` - Main implementation (1,100 lines)
- `version.py` - Version info
- `extensions/` - Optional features (diagrams, async, hierarchical)
- `experimental/` - Experimental features

**python-statemachine** (26 files):
- Core: `statemachine.py`, `state.py`, `event.py`, `transition.py`
- Supporting: `callbacks.py`, `dispatcher.py`, `signature.py`, `factory.py`
- Features: `graph.py`, `registry.py`, `i18n.py`
- Directories: `contrib/`, `engines/`, `locale/`

**Verdict:** statechart has smallest footprint at ~1/5 size of transitions.

---

## RAM Usage Analysis

### **transitions**

**Data structures:**
- `collections.OrderedDict` for state ordering
- `defaultdict(list)` for transitions (lazy allocation)
- `deque()` for event queue (O(1) operations)
- `functools.partial` for dynamic trigger creation
- Callback lists per state/transition

**Allocation strategy:**
- Lazy initialization via defaultdict
- One queue per machine instance
- Simple list storage for callbacks
- No unnecessary object wrapping

**Estimated RAM per instance:** 2-4 KB base + callbacks + (state_count × ~100 bytes)

### **python-statemachine**

**Data structures:**
- Multiple instance dictionaries:
  - `_states_for_instance` - State object cache
  - `_listeners` - Observer registry
  - `_callbacks` - Callback specifications
- Dual execution engines (sync/async)
- Metaclass overhead
- Extensive callback registry system

**Allocation strategy:**
- State object caching per instance
- Observer pattern with dynamic registration
- Callback introspection and binding
- Serialization support (pickle overhead)

**Estimated RAM per instance:** 5-8 KB base + higher dynamic allocation for listeners and callback resolution

### **statechart**

**Data structures:**
- Hierarchical state trees with bidirectional parent-child references
- `asyncio.Task` objects for active states (significant overhead)
- Logger instance per state in hierarchy
- Boolean flags (`active`, `finished`) per state
- List collections (`transitions`, `regions`) per state

**Allocation strategy:**
- Dynamic task creation/cancellation during state entry/exit
- Circular references between parent and child states (GC overhead)
- Recursive state hierarchy traversal

**Estimated RAM per instance:** 3-6 KB base + (task_overhead × active_states) + (state_hierarchy_depth × 500 bytes)

**Note:** asyncio.Task objects are expensive (~1-2 KB each) and one is created per active state with a `do` action.

**Verdict:** transitions most RAM efficient due to simpler data structures and lazy allocation patterns.

---

## MicroPython Compatibility Analysis

### **transitions**

**Blocking dependencies:**
- `collections.OrderedDict` - not in MicroPython
- `collections.defaultdict` - not in MicroPython
- `collections.deque` - not in MicroPython
- `functools.partial` - available but limited
- `inspect.ismethod()` - available in micropython-lib
- `itertools.chain` - limited support
- `six` library - not available, would need removal
- `warnings` module - not available
- `enum.Enum` - available in recent MicroPython

**Porting effort:** Major refactoring required to replace collections usage throughout. Would need custom implementations of OrderedDict/defaultdict/deque.

**Compatibility rating:** Poor - 40+ hours porting effort

### **python-statemachine**

**Blocking dependencies:**
- Complex metaclass usage (`StateMachineMetaclass`)
- `inspect.isawaitable()` - **not available** in MicroPython
- `inspect.Signature` - **not available**
- `inspect.Parameter` - **not available**
- `inspect.BoundArguments` - **not available**
- `inspect.iscoroutinefunction()` - available (aliased)
- `warnings` module - not available
- `functools` - limited
- Pickle support (`__getstate__`/`__setstate__`) - pickle not in MicroPython
- i18n/locale support - not available

**Critical issue:** signature.py (177 lines) depends entirely on Signature/Parameter/BoundArguments classes. These implement Python's function signature introspection system and would require reimplementing ~500+ lines of CPython machinery.

**Porting effort:** Architecturally incompatible. Would require fundamental redesign of callback resolution system.

**Compatibility rating:** Very Poor - not feasible

### **statechart**

**Dependencies:**
- `asyncio` - **available as uasyncio in MicroPython**
- `logging` - **available** (simplified version)
- `typing` - not fully supported but can be stripped

**No external dependencies:** Zero third-party packages required.

**Porting requirements:**
1. Replace `asyncio` imports with `uasyncio`
2. Simplify or stub logging calls if needed
3. Strip type hints (mechanical, can use strip-hints or manual removal)
4. Test circular reference handling with MicroPython's GC

**Estimated porting time:** 2-4 hours development + testing

**Compatibility rating:** Good - only library architecturally compatible with MicroPython

**Verdict:** statechart is the only practical option for MicroPython.

---

## inspect Module Usage Details

### **transitions**

**File:** core.py

**Usage:**
- `inspect.ismethod()` at line 1065
  - Checks if dynamic callbacks (`on_enter_<state>`, `on_exit_<state>`) are bound methods
  - **Available in MicroPython** ✓

**MicroPython status:** Compatible

### **python-statemachine**

**File:** statemachine.py
- `inspect.isawaitable()` at lines 106, 335
  - Determines if event results require async handling
  - **Not available in MicroPython** ✗

**File:** signature.py
- `inspect.BoundArguments` - class used at line 179 - **Not available** ✗
- `inspect.Parameter` - class used lines 76-177 - **Not available** ✗
- `inspect.Signature` - base class extended at line 40 - **Not available** ✗
- `inspect.iscoroutinefunction()` at line 55 - **Available** ✓ (aliased)

**File:** callbacks.py
- `inspect.isawaitable()` at lines 258, 266
  - Checks if callback return values need awaiting
  - **Not available in MicroPython** ✗

**MicroPython status:** Incompatible

### **statechart**

**Usage:** None

**MicroPython status:** No inspect dependencies ✓

### MicroPython inspect Module Contents

Located at: `lib/micropython-lib/python-stdlib/inspect/inspect.py`

**Available functions:**
- ✓ `getmembers(obj, pred=None)`
- ✓ `isfunction(obj)`
- ✓ `isgeneratorfunction(obj)`
- ✓ `isgenerator(obj)`
- ✓ `iscoroutinefunction(obj)` - aliased to isgeneratorfunction (cannot distinguish)
- ✓ `iscoroutine(obj)` - aliased to isgenerator
- ✓ `ismethod(obj)`
- ✓ `isclass(obj)`
- ✓ `ismodule(obj)`

**NOT available:**
- ✗ `isawaitable()` - completely missing
- ✗ `BoundArguments` - completely missing
- ✗ `Parameter` - completely missing
- ✗ `Signature` - completely missing

**Stub implementations (unusable):**
- `getargspec()` - raises NotImplementedError
- `getmodule()`, `getsourcefile()`, `getfile()`, `getsource()` - return None/placeholders
- `currentframe()`, `getframeinfo()` - return None/placeholders

### Workaround Feasibility

**isawaitable() polyfill:**
```python
def isawaitable(obj):
    return hasattr(obj, '__await__')
```
Feasible for simple cases.

**Signature/Parameter/BoundArguments:**
Would require reimplementing CPython's signature introspection system (~500+ lines). Not practical for embedded systems.

---

## Feature Comparison

| Feature | transitions | python-statemachine | statechart |
|---------|-------------|---------------------|------------|
| Hierarchical states | ✓ (extension) | ✗ | ✓ (native) |
| Concurrent regions | ✗ | ✗ | ✓ |
| Pseudostates | Limited | ✗ | ✓ (Initial/Choice/History) |
| History states | ✗ | ✗ | ✓ (shallow) |
| Conditional transitions | ✓ | ✓ | ✓ |
| Guards | ✓ | ✓ | ✓ |
| Entry/exit actions | ✓ | ✓ | ✓ |
| Transition actions | ✓ | ✓ | ✓ |
| Internal transitions | ✓ | ✓ | ✓ |
| Async support | ✓ (extension) | ✓ (native) | ✓ (native) |
| Sync support | ✓ (native) | ✓ (native) | ✗ (async only) |
| Diagram generation | ✓ (optional) | ✓ (optional) | ✗ |
| Observer pattern | ✗ | ✓ | ✗ |
| Django integration | ✓ | ✓ | ✗ |
| UML semantics | Partial | No | ✓ |
| Python 2.7 support | ✓ | ✗ | ✗ |
| Python version | 2.7+ | 3.7+ | 3.7+ |
| Runtime dependencies | six | None | None |
| Maturity | High (6.3k stars) | Medium (1.2k stars) | Low (0 stars) |

---

## Standard Library Dependencies

### **transitions**

**Required:**
- `six` (only external dependency)

**Used:**
- `collections`: OrderedDict, defaultdict, deque
- `functools`: partial
- `inspect`: ismethod
- `itertools`: chain
- `logging`: Logger
- `warnings`: warn
- `enum`: Enum, EnumMeta

### **python-statemachine**

**Required:**
- None

**Used:**
- `inspect`: isawaitable, Signature, Parameter, BoundArguments, iscoroutinefunction
- `warnings`: warn
- `functools`: various
- `typing`: extensive type hints

### **statechart**

**Required:**
- None

**Used:**
- `asyncio`: Task, coroutines
- `logging`: Logger
- `typing`: type hints

---

## Critical Assessment

### For MicroPython Embedded Systems

**statechart is the only practical option.**

The inspect module limitations alone rule out python-statemachine. The collections module dependencies make transitions impractical without significant porting work.

**statechart advantages:**
- Smallest codebase (105 KB)
- No external dependencies
- asyncio → uasyncio migration is straightforward
- UML statechart semantics (hierarchical, concurrent, history states)
- Already authored by user (maintenance control)

**statechart disadvantages:**
- asyncio.Task overhead per active state
- Circular references in state hierarchy (GC pressure)
- Async-only (no sync mode)
- No community adoption yet

**Estimated porting timeline:**
- Replace asyncio → uasyncio: 1-2 hours
- Strip type hints: 30 minutes
- Simplify logging: 30 minutes
- Testing on hardware: 2-4 hours
- **Total: 1 working day**

### For Standard Python (CPython/PyPy)

**transitions** offers best balance for most use cases:
- Mature library with large community (6.3k GitHub stars)
- Most RAM efficient (2-4 KB per instance)
- Good documentation and examples
- Extensions available (async, hierarchical, diagrams)
- Python 2.7+ compatibility if needed

**python-statemachine** targets users wanting:
- Modern async/await patterns as first-class
- Observer pattern integration
- Type safety with mypy
- Django ORM integration
- Introspective callback parameter injection

**Tradeoffs:**
- 2x code size vs transitions (282 KB vs 565 KB)
- Higher RAM usage (5-8 KB vs 2-4 KB)
- More complex architecture
- No hierarchical states support

### For UML Statechart Requirements

**statechart** is the only library implementing full UML statechart semantics:
- Composite states (hierarchical nesting)
- Concurrent regions (orthogonal states)
- History states (shallow)
- Pseudostates (Initial, Choice)

Neither transitions nor python-statemachine support concurrent regions or proper UML history states natively.

---

## Recommendations

### By Use Case

1. **MicroPython embedded projects**
   - Use: **statechart**
   - Rationale: Only architecturally compatible option
   - Action: Port to uasyncio (1 day effort)

2. **Memory-constrained CPython systems**
   - Use: **transitions**
   - Rationale: Most RAM efficient (2-4 KB per instance)
   - Tradeoff: Larger code size but better runtime efficiency

3. **Modern async Python applications**
   - Use: **python-statemachine** if features justify overhead
   - Rationale: Native async, observer pattern, type safety
   - Tradeoff: 2x RAM usage vs transitions

4. **UML statechart compliance required**
   - Use: **statechart**
   - Rationale: Only library with concurrent regions and UML semantics
   - Consideration: Async-only limitation

5. **Python 2.7 legacy systems**
   - Use: **transitions**
   - Rationale: Only library supporting Python 2.7

### Implementation Strategy

**For new MicroPython project:**
1. Fork statechart repository
2. Create `micropython` branch
3. Replace asyncio with uasyncio
4. Strip type hints
5. Add MicroPython-specific examples
6. Validate on target hardware (STM32/ESP32/RP2)

**For CPython project:**
1. Start with transitions for simplicity
2. If hierarchical states needed, evaluate transitions.extensions.nesting
3. If concurrent regions needed, migrate to statechart (CPython asyncio)
4. If observer pattern needed, evaluate python-statemachine

---

## Appendix: File Sizes and Line Counts

### statechart (105 KB total)

```
__init__.py         ~50 lines
display.py          ~100 lines
event.py            ~80 lines
pseudostates.py     ~120 lines
runtime.py          ~550 lines
states.py           ~200 lines
transitions.py      ~150 lines
```

### transitions (565 KB total)

```
core.py             ~1,100 lines
__init__.py         ~30 lines
version.py          ~5 lines
extensions/         (additional features)
experimental/       (experimental features)
```

### python-statemachine (282 KB total)

```
statemachine.py     ~340 lines
callbacks.py        ~270 lines
signature.py        ~180 lines
state.py            ~200 lines
event.py            ~150 lines
transition.py       ~120 lines
(+ 20 other support files)
```

---

## Conclusion

The choice depends on deployment environment:

- **MicroPython:** statechart (only option)
- **CPython memory-constrained:** transitions (most efficient)
- **CPython modern/async:** python-statemachine (feature-rich)
- **UML compliance:** statechart (only compliant implementation)

For your specific case with MicroPython targets, **statechart requires minimal porting effort** and already supports the UML features you likely need (hierarchical states, concurrent regions). The asyncio → uasyncio migration is well-understood and the lack of inspect dependencies eliminates the major compatibility barrier affecting the other two libraries.
