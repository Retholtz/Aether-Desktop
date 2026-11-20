import ast
from typing import Dict, List, Optional, Set, Tuple

# Builtins that must NEVER be called OR referenced (blocks `e = eval; e(...)`)
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
    "input",
    "open",
}

# Strict allowlist of approved top-level modules for sandboxed automation/skill scripts
ALLOWED_MODULES: Set[str] = {
    # Pure Python stdlib helpers
    "argparse",
    "base64",
    "calendar",
    "collections",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "difflib",
    "enum",
    "fractions",
    "functools",
    "hashlib",
    "html",
    "itertools",
    "json",
    "math",
    "os",
    "pprint",
    "random",
    "re",
    "shlex",
    "statistics",
    "string",
    "sys",
    "textwrap",
    "time",
    "typing",
    "unicodedata",
    "urllib",
    "uuid",
    "zoneinfo",
    # Approved desktop / document / UI automation packages
    "PIL",
    "bs4",
    "docx",
    "mss",
    "numpy",
    "openpyxl",
    "pandas",
    "pyautogui",
    "pypdf",
    "pyperclip",
    "requests",
    "win32api",
    "win32clipboard",
    "win32com",
    "win32con",
    "win32gui",
}

# Approved internal Aether modules (strictly excludes security.*, core.config_manager, core.oauth_manager, etc.)
ALLOWED_INTERNAL_MODULES: Set[str] = {
    "core.screen_stream",
    "tools.clipboard",
    "tools.gui_primitives",
    "tools.heavy_reasoning",
    "tools.os_controls",
    "tools.screen_vision",
}

# Kept for backwards-compatibility with any callers inspecting these constants
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
    "socket",
    "socketserver",
    "paramiko",
    "pty",
    "tty",
    "termios",
    "code",
    "codeop",
    "runpy",
    "inspect",
    "operator",
    "pathlib",
    "shutil",
    "nt",
    "posix",
    "_winapi",
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
    "security.crypto",
    "security.token_vault",
    "security.ast_gatekeeper",
}

BLOCKED_ATTRIBUTE_ACCESS: Dict[str, Set[str]] = {
    "shutil": {"rmtree", "move", "copy", "copy2", "copytree"},
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
        "fork",
        "open",
        "fdopen",
        "putenv",
        "unsetenv",
        "chmod",
        "chown",
        "symlink",
        "link",
        "replace",
        "rename",
        "_exit",
    },
    "sys": {
        "modules",
        "meta_path",
        "path_hooks",
        "settrace",
        "setprofile",
        "addaudithook",
        "_getframe",
        "_current_frames",
        "audit",
    },
    "win32api": {
        "WinExec",
        "ShellExecute",
        "ShellExecuteEx",
        "CreateProcess",
        "OpenProcess",
        "TerminateProcess",
        "RegOpenKey",
        "RegOpenKeyEx",
        "RegSetValue",
        "RegSetValueEx",
        "RegDeleteKey",
        "RegDeleteValue",
    },
}

# Dangerous attributes blocked on ANY receiver expression (catches chained lookups like `a.b.system` or `exc.__traceback__.tb_frame`)
BLOCKED_GLOBAL_ATTRS: Set[str] = {
    "tb_frame",
    "tb_next",
    "f_globals",
    "f_locals",
    "f_builtins",
    "f_back",
    "f_code",
    "gi_frame",
    "gi_code",
    "cr_frame",
    "cr_code",
    "ag_frame",
    "ag_code",
    "func_globals",
    "func_code",
    "im_func",
    "im_self",
    "ShellExecute",
    "ShellExecuteEx",
    "WinExec",
    "CreateProcess",
    "system",
    "popen",
    "startfile",
    "rmtree",
    "settrace",
    "setprofile",
    "addaudithook",
    "_getframe",
    "_current_frames",
    "_exit",
}

BLOCKED_DUNDER_NAMES: Set[str] = {
    "__builtins__",
    "__subclasses__",
    "__globals__",
    "__code__",
    "__closure__",
    "__bases__",
    "__mro__",
    "__class__",
    "__dict__",
    "__loader__",
    "__spec__",
    "__import__",
    "__getattribute__",
    "__setattr__",
    "__delattr__",
    "__traceback__",
    "__reduce__",
    "__reduce_ex__",
    "__init_subclass__",
}

# Only benign dunders needed for standard script structure (`if __name__ == "__main__"`, etc.)
ALLOWED_DUNDER_NAMES: Set[str] = {
    "__name__",
    "__file__",
    "__doc__",
}

ALLOWED_DUNDER_ATTRS: Set[str] = {
    "__name__",
    "__doc__",
    "__init__",
    "__str__",
    "__repr__",
    "__len__",
    "__iter__",
    "__next__",
    "__enter__",
    "__exit__",
}

BLOCKED_COM_PROGIDS: Set[str] = {
    "wscript.shell",
    "wscript.network",
    "shell.application",
    "scripting.filesystemobject",
    "wbemscripting.swbemlocator",
    "htmlfile",
    "internetexplorer.application",
    "msxml2.xmlhttp",
    "adodb.stream",
    "mmc20.application",
    "schedule.service",
}

COM_DISPATCH_METHODS: Set[str] = {
    "Dispatch",
    "DispatchEx",
    "CreateObject",
    "EnsureDispatch",
    "GetObject",
    "GetActiveObject",
}


def _is_module_allowed(full_name: str) -> bool:
    """Strict allowlist check for an imported module or dotted submodule name."""
    norm = (full_name or "").strip()
    if not norm:
        return False
    lower = norm.lower()
    base = norm.split(".")[0]
    base_lower = base.lower()

    if base_lower in BLOCKED_MODULES or lower in BLOCKED_SUBMODULES:
        return False

    # Check exact internal module allowlist (core.*, tools.*)
    if base_lower in ("core", "tools"):
        return any(lower == m or lower.startswith(m + ".") for m in ALLOWED_INTERNAL_MODULES)

    # Disallow urllib.request.urlopen? Allow urllib.parse only, block urllib.request/ftp/file URL openers if needed?
    # Keep urllib.parse allowed; block urllib.request so arbitrary file:// reads aren't possible.
    if lower == "urllib.request" or lower.startswith("urllib.request."):
        return False

    return base in ALLOWED_MODULES or base_lower in ALLOWED_MODULES


class ASTSecurityGatekeeper(ast.NodeVisitor):
    """Walks the Abstract Syntax Tree of a Python script to enforce strict allowlist safety constraints."""

    def __init__(self):
        self.violations: List[str] = []
        # Maps alias identifier -> canonical base module name (e.g. {"o": "os"})
        self.module_aliases: Dict[str, str] = {}

    @property
    def errors(self) -> List[str]:
        return self.violations

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            full_name = alias.name
            base_module = full_name.split(".")[0].lower()
            local_name = alias.asname or alias.name.split(".")[0]
            self.module_aliases[local_name] = base_module
            if not _is_module_allowed(full_name):
                self.violations.append(f"Import of non-allowlisted module '{alias.name}' is prohibited.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if getattr(node, "level", 0) and node.level > 0:
            self.violations.append("Relative imports are prohibited by safety policy.")
            self.generic_visit(node)
            return

        if not node.module:
            self.violations.append("Unqualified import statement is prohibited.")
            self.generic_visit(node)
            return

        full_mod = node.module
        full_mod_lower = full_mod.lower()
        base_module = full_mod_lower.split(".")[0]

        if not _is_module_allowed(full_mod):
            self.violations.append(f"Import from non-allowlisted module '{node.module}' is prohibited.")

        for alias in node.names:
            if alias.name == "*":
                self.violations.append(f"Wildcard import from '{node.module}' is prohibited by safety policy.")
                continue
            if alias.name.startswith("_"):
                self.violations.append(f"Importing private/dunder symbol '{alias.name}' from '{node.module}' is prohibited.")
                continue

            sub_full = f"{full_mod_lower}.{alias.name.lower()}"
            if sub_full in BLOCKED_SUBMODULES:
                self.violations.append(f"Import of blocked submodule '{sub_full}' is prohibited.")

            if base_module in BLOCKED_ATTRIBUTE_ACCESS and alias.name in BLOCKED_ATTRIBUTE_ACCESS[base_module]:
                self.violations.append(f"Importing '{base_module}.{alias.name}' is prohibited by safety policy.")
                bound_name = alias.asname or alias.name
                self.module_aliases[bound_name] = f"{base_module}.{alias.name}"
            elif alias.name in BLOCKED_GLOBAL_ATTRS or alias.name in BLOCKED_CALLS:
                self.violations.append(f"Importing restricted symbol '{alias.name}' from '{node.module}' is prohibited.")
            else:
                bound_name = alias.asname or alias.name
                if base_module == "win32com" and alias.name == "client":
                    self.module_aliases[bound_name] = "win32com.client"
                elif not _is_module_allowed(f"{full_mod}.{alias.name}") and base_module in ("core", "tools"):
                    # Already checked full_mod against ALLOWED_INTERNAL_MODULES above
                    pass

        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        ident = node.id
        if ident in BLOCKED_CALLS:
            self.violations.append(f"Reference to prohibited builtin '{ident}' is blocked.")
        elif ident in BLOCKED_DUNDER_NAMES:
            self.violations.append(f"Access to restricted dunder identifier '{ident}' is prohibited.")
        elif ident.startswith("__") and ident.endswith("__") and ident not in ALLOWED_DUNDER_NAMES:
            self.violations.append(f"Access to restricted dunder identifier '{ident}' is prohibited.")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        attr = node.attr
        if attr in BLOCKED_DUNDER_NAMES:
            self.violations.append(f"Access to restricted dunder attribute '{attr}' is prohibited.")
        elif attr.startswith("__") and attr.endswith("__") and attr not in ALLOWED_DUNDER_ATTRS:
            self.violations.append(f"Access to restricted dunder attribute '{attr}' is prohibited.")
        elif attr in BLOCKED_GLOBAL_ATTRS:
            self.violations.append(f"Access to restricted attribute '{attr}' is prohibited.")
        elif attr in BLOCKED_CALLS:
            self.violations.append(f"Reference to prohibited attribute '{attr}' is blocked.")

        if isinstance(node.value, ast.Name):
            resolved_mod = self.module_aliases.get(node.value.id, node.value.id)
            if resolved_mod in BLOCKED_ATTRIBUTE_ACCESS and attr in BLOCKED_ATTRIBUTE_ACCESS[resolved_mod]:
                self.violations.append(f"Reference to prohibited attribute '{resolved_mod}.{attr}' is blocked.")
            elif attr.startswith("_") and node.value.id in self.module_aliases:
                self.violations.append(f"Access to private module attribute '{resolved_mod}.{attr}' is prohibited.")
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
            if fn_name in COM_DISPATCH_METHODS:
                self._validate_com_call(fn_name, node)

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

            if attr_name in COM_DISPATCH_METHODS:
                self._validate_com_call(attr_name, node)

        self.generic_visit(node)

    def _validate_com_call(self, method_name: str, node: ast.Call):
        """Requires a static string literal ProgID and blocks dangerous COM shell/WMI objects."""
        target_arg = None
        if node.args:
            target_arg = node.args[0]
        else:
            for kw in node.keywords:
                if kw.arg in ("dispatch", "prog_id", "ProgID", "Class"):
                    target_arg = kw.value
                    break

        if not (isinstance(target_arg, ast.Constant) and isinstance(target_arg.value, str)):
            self.violations.append(
                f"COM call '{method_name}()' requires a static string literal ProgID; dynamic ProgIDs are prohibited."
            )
            return

        prog_id = target_arg.value.strip().lower()
        if prog_id in BLOCKED_COM_PROGIDS or any(prog_id.startswith(b + ".") for b in BLOCKED_COM_PROGIDS):
            self.violations.append(f"COM instantiation of restricted ProgID '{target_arg.value}' is prohibited.")


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
