import ast
from typing import List, Optional, Set, Tuple

BLOCKED_CALLS: Set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
}

BLOCKED_MODULES: Set[str] = {
    "ctypes",
    "winreg",
    "marshal",
    "socketserver",
    "paramiko",
    "pty",
    "tty",
    "termios",
}

BLOCKED_ATTRIBUTE_ACCESS = {
    "shutil": {"rmtree"},  # Prevent blanket directory wiping
    "os": {"system"},      # Force automation through dedicated tools rather than raw cmd shell
}


class ASTSecurityGatekeeper(ast.NodeVisitor):
    """Walks the Abstract Syntax Tree of a Python script to enforce safety constraints."""

    def __init__(self):
        self.violations: List[str] = []

    @property
    def errors(self) -> List[str]:
        return self.violations

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            base_module = alias.name.split(".")[0].lower()
            if base_module in BLOCKED_MODULES:
                self.violations.append(f"Import of blocked module '{base_module}' is prohibited.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            base_module = node.module.split(".")[0].lower()
            if base_module in BLOCKED_MODULES:
                self.violations.append(f"Import from blocked module '{base_module}' is prohibited.")
            if base_module in BLOCKED_ATTRIBUTE_ACCESS:
                blocked_attrs = BLOCKED_ATTRIBUTE_ACCESS[base_module]
                for alias in node.names:
                    if alias.name in blocked_attrs:
                        self.violations.append(f"Importing '{base_module}.{alias.name}' is prohibited by safety policy.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Catch direct calls: eval(...), exec(...)
        if isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_CALLS:
                self.violations.append(f"Call to prohibited builtin function '{node.func.id}()'.")

        # Catch dynamic resolution: getattr(..., 'system')
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in BLOCKED_CALLS:
                self.violations.append(f"Prohibited method invocation '{node.func.attr}()'.")
            elif isinstance(node.func.value, ast.Name):
                obj_name = node.func.value.id
                if obj_name in BLOCKED_ATTRIBUTE_ACCESS and node.func.attr in BLOCKED_ATTRIBUTE_ACCESS[obj_name]:
                    self.violations.append(f"Call to prohibited method '{obj_name}.{node.func.attr}()'.")

        self.generic_visit(node)


# Backward-compatibility alias
SecurityASTVisitor = ASTSecurityGatekeeper


def validate_python_code(code_str: str) -> Tuple[bool, List[str]]:
    """Inspects code syntax tree for security violations."""
    cleaned = (code_str or "").strip()
    if not cleaned:
        return False, ["Script code cannot be empty."]
    try:
        tree = ast.parse(cleaned)
    except SyntaxError as err:
        return False, [f"Syntax error during validation: {err}"]
    except Exception as err:
        return False, [f"Failed to parse script AST: {err}"]

    checker = ASTSecurityGatekeeper()
    checker.visit(tree)
    if checker.violations:
        return False, checker.violations
    return True, []


def validate_python_script(script_code: str) -> Tuple[bool, Optional[str]]:
    """
    Parses Python source code and validates it against security constraints.
    Returns (True, None) if safe, or (False, error_message) if unsafe.
    """
    valid, violations = validate_python_code(script_code)
    if not valid:
        return False, "; ".join(violations) if isinstance(violations, list) else str(violations)
    return True, None



