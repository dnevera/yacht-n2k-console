# Refactoring Rules

## General Principles

1. **No behavior changes** — refactoring must be purely structural.
   Tests must pass before and after each step.

2. **One module per PR** — each extracted module is a self-contained change.
   Never move two modules in the same commit.

3. **Backward compatibility** — all existing imports must continue to work.
   Use `__init__.py` re-exports to preserve import paths.

4. **Shared state via injection** — sub-managers receive locks and shared objects
   via `__init__`, never create their own threading primitives.

5. **Facade keeps all public methods** — `DeviceManager` remains the single
   entry point. Routes don't change. Methods become thin delegates:
   ```python
   def get_info(self): return self._service_mgr.get_info()
   ```

## Threading Rules

6. **Lock ownership** — only the facade creates locks. Sub-managers receive
   references. Document which locks each sub-manager uses.

7. **No cross-sub-manager calls** — sub-managers don't import each other.
   They communicate only through shared state owned by the facade.

8. **Event loop isolation** — `WSStreamHub` may use its own async event loop
   but must not interfere with the bus worker's threading model.

## Testing Rules

9. **Test coverage baseline** — run `pytest --cov` before each phase and after.
   Coverage must not decrease.

10. **Mock at boundaries** — when testing sub-managers, mock the facade's
    shared state, not internal implementations.

11. **Integration tests** — keep existing integration tests as-is. They test
    the facade's public API which must not change.

## Commenting Rules (from .agents/AGENTS.md)

12. **All comments in English** — no Russian in code or docstrings.

13. **Mini-skill level** — explain WHY, not WHAT. Document:
    - Why this approach was chosen
    - Traps and gotchas
    - Architectural context
    - What breaks if changed

14. **Practical Skills** — every public API method gets at least one
    runnable example (curl, Python, bash).

15. **Generic hostnames** — use `<gateway-host>` and `user@gateway-host`.
    Never hardcode real hostnames or usernames.

## File Organization

16. **Module docstring required** — every new `.py` file starts with a module
    docstring explaining its role, dependencies, and thread safety model.

17. **Consistent structure** — each sub-manager file follows:
    ```
    Module docstring
    Imports
    Constants
    Class definition
      Class docstring
      __init__
      Public methods (alphabetical)
      Private methods (alphabetical)
    ```

18. **No circular imports** — dependency graph must be a DAG.
    If A imports B, B must not import A (even indirectly).
