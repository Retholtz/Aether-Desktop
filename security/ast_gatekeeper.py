"""
Aether Desktop - AST Security Gatekeeper
Parses and inspects dynamically generated Python scripts using the Python AST
to block unauthorized imports, arbitrary shell injections, or dangerous system calls.
"""

import ast
from typing import Optional, Set, Tuple

# Disallowed modules that should never be imported by dynamic automation scripts
BLOCKED_MODULES: Set[str] = {
    "socketserver",
    "paramiko",
    "pty",
    "tty",
    "termios",
}

# Blocked built-in functions or calls
BLOCKED_CALLS: Set[str] = {
    "breakpoint",
}

# Blocked attributes on specific modules
BLOCKED_ATTRIBUTE_ACCESS = {
    "shutil": {"rmtree"},  # Prevent blanket directory wiping
    "os": {"system"},      # Force automation through dedicated tools rather than raw cmd shell
}


class SecurityASTVisitor(ast.NodeVisitor):
    """Walks the Abstract Syntax Tree of a Python script to enforce safety constraints."""

    def __init__(self):
        self.errors = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            base_mod = alias.name.split(".")[0].lower()
            if base_mod in BLOCKED_MODULES:
                self.errors.append(f"Import of blocked module '{alias.name}' is prohibited.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            base_mod = node.module.split(".")[0].lower()
            if base_mod in BLOCKED_MODULES:
                self.errors.append(f"Import from blocked module '{node.module}' is prohibited.")
            if base_mod in BLOCKED_ATTRIBUTE_ACCESS:
                blocked_attrs = BLOCKED_ATTRIBUTE_ACCESS[base_mod]
                for alias in node.names:
                    if alias.name in blocked_attrs:
                        self.errors.append(f"Importing '{base_mod}.{alias.name}' is prohibited by safety policy.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Check direct function calls by name
        if isinstance(node.func, ast.Name):
            fn_name = node.func.id
            if fn_name in BLOCKED_CALLS:
                self.errors.append(f"Direct call to '{fn_name}()' is prohibited.")

        # Check attribute calls (e.g. os.system)
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if isinstance(node.func.value, ast.Name):
                obj_name = node.func.value.id
                if obj_name in BLOCKED_ATTRIBUTE_ACCESS and attr_name in BLOCKED_ATTRIBUTE_ACCESS[obj_name]:
                    self.errors.append(f"Call to '{obj_name}.{attr_name}()' is prohibited by safety policy.")

        self.generic_visit(node)


def validate_python_script(script_code: str) -> Tuple[bool, Optional[str]]:
    """
    Parses Python source code and validates it against security constraints.

    Returns:
        (True, None) if safe.
        (False, error_message) if unsafe or if syntax is invalid.
    """
    cleaned = (script_code or "").strip()
    if not cleaned:
        return False, "Script code cannot be empty."

    try:
        tree = ast.parse(cleaned)
    except SyntaxError as e:
        return False, f"Python SyntaxError at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Failed to parse script AST: {e}"

    visitor = SecurityASTVisitor()
    visitor.visit(tree)

    if visitor.errors:
        return False, "; ".join(visitor.errors)

    return True, None

