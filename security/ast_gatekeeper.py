import ast
from typing import Dict, List, Optional, Set, Tuple

BLOCKED_CALLS: Set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
}

BLOCKED_MODULES: Set[str] = {
    "ctypes",
    "winreg",
    "subprocess",
    "multiprocessing",
    "importlib",
    "builtins",
    "marshal",
    "pickle",
    "shelve",
    "socketserver",
    "paramiko",
    "pty",
    "tty",
    "termios",
    "code",
    "codeop",
    "runpy",
    "win32process",
    "win32cred",
    "win32crypt",
    "win32security",
    "win32service",
    "security",
}

BLOCKED_SUBMODULES: Set[str] = {
    "core.config_manager",
    "core.oauth_manager",
}

BLOCKED_ATTRIBUTE_ACCESS: Dict[str, Set[str]] = {
    "shutil": {"rmtree"},
    "os": {
        "system",
        "popen",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "startfile",
        "remove",
        "unlink",
        "rmdir",
        "removedirs",
        "kill",
    },
    "win32api": {"WinExec", "ShellExecute"},
}

BLOCKED_DUNDER_NAMES: Set[str] = {
    "__builtins__",
    "__subclasses__",
    "__globals__",
    "__code__",
    "__closure__",
    "__bases__",
    "__mro__",
}

BLOCKED_COM_PROGIDS: Set[str] = {
    "wscript.shell",
    "shell.application",
    "scripting.filesystemobject",
    "wbemscripting.swbemlocator",
}


class ASTSecurityGatekeeper(ast.NodeVisitor):
    """Walks the Abstract Syntax Tree of a Python script to enforce safety constraints."""

    def __init__(self):
        self.violations: List[str] = []
        # Maps alias identifier -> canonical base module name (e.g. {"o": "os"})
        self.module_aliases: Dict[str, str] = {}

    @property
    def errors(self) -> List[str]:
        return self.violations

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            full_name = alias.name.lower()
            base_module = full_name.split(".")[0]
            local_name = alias.asname or alias.name.split(".")[0]
            self.module_aliases[local_name] = base_module
            if base_module in BLOCKED_MODULES or full_name in BLOCKED_SUBMODULES:
                self.violations.append(f"Import of blocked module '{alias.name}' is prohibited.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            full_mod = node.module.lower()
            base_module = full_mod.split(".")[0]
            if base_module in BLOCKED_MODULES or full_mod in BLOCKED_SUBMODULES:
                self.violations.append(f"Import from blocked module '{node.module}' is prohibited.")
            if base_module in BLOCKED_ATTRIBUTE_ACCESS:
                blocked_attrs = BLOCKED_ATTRIBUTE_ACCESS[base_module]
                for alias in node.names:
                    if alias.name == "*" or alias.name in blocked_attrs:
                        self.violations.append(f"Importing '{base_module}.{alias.name}' is prohibited by safety policy.")
            for alias in node.names:
                bound_name = alias.asname or alias.name
                sub_full = f"{full_mod}.{alias.name.lower()}"
                if sub_full in BLOCKED_SUBMODULES:
                    self.violations.append(f"Import of blocked submodule '{sub_full}' is prohibited.")
                if base_module in BLOCKED_ATTRIBUTE_ACCESS and alias.name in BLOCKED_ATTRIBUTE_ACCESS[base_module]:
                    self.module_aliases[bound_name] = f"{base_module}.{alias.name}"
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if node.id in BLOCKED_DUNDER_NAMES:
            self.violations.append(f"Access to restricted dunder identifier '{node.id}' is prohibited.")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr in BLOCKED_DUNDER_NAMES:
            self.violations.append(f"Access to restricted dunder attribute '{node.attr}' is prohibited.")
        if isinstance(node.value, ast.Name):
            resolved_mod = self.module_aliases.get(node.value.id, node.value.id)
            if resolved_mod in BLOCKED_ATTRIBUTE_ACCESS and node.attr in BLOCKED_ATTRIBUTE_ACCESS[resolved_mod]:
                self.violations.append(f"Reference to prohibited attribute '{resolved_mod}.{node.attr}' is blocked.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # 1. Direct function calls: eval(...), exec(...), getattr(...)
        if isinstance(node.func, ast.Name):
            fn_name = node.func.id
            if fn_name in BLOCKED_CALLS:
                self.violations.append(f"Call to prohibited builtin function '{fn_name}()'.")
            resolved = self.module_aliases.get(fn_name, "")
            if "." in resolved:
                mod_part, attr_part = resolved.split(".", 1)
                if mod_part in BLOCKED_ATTRIBUTE_ACCESS and attr_part in BLOCKED_ATTRIBUTE_ACCESS[mod_part]:
                    self.violations.append(f"Call to prohibited function '{resolved}()'.")

        # 2. Attribute method calls: os.system(...), o.system(...), win32com.client.Dispatch(...)
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in BLOCKED_CALLS:
                self.violations.append(f"Prohibited method invocation '{attr_name}()'.")
            elif isinstance(node.func.value, ast.Name):
                raw_obj = node.func.value.id
                resolved_mod = self.module_aliases.get(raw_obj, raw_obj)
                if resolved_mod in BLOCKED_ATTRIBUTE_ACCESS and attr_name in BLOCKED_ATTRIBUTE_ACCESS[resolved_mod]:
                    self.violations.append(f"Call to prohibited method '{resolved_mod}.{attr_name}()'.")

            # Block dangerous COM shell objects in Dispatch / DispatchEx / CreateObject
            if attr_name in ("Dispatch", "DispatchEx", "CreateObject") and node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                    prog_id = first_arg.value.strip().lower()
                    if prog_id in BLOCKED_COM_PROGIDS:
                        self.violations.append(f"COM instantiation of restricted ProgID '{first_arg.value}' is prohibited.")

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
