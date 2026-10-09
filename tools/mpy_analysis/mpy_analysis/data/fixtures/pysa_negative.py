def evaluate_literal() -> object:
    return eval("2 + 2", {"__builtins__": {}}, {})
