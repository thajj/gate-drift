"""Find concrete changes that weaken common automated quality gates.

The rules deliberately inspect a small set of recognizable constructs. An empty
result means no supported pattern was found, not that a patch is safe.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import difflib
import io
import json
import re
import tokenize
from typing import Iterable


@dataclass
class FileChange:
    path: str
    before: str
    after: str


@dataclass
class Finding:
    rule_id: str
    severity: str
    title: str
    path: str
    line: int
    side: str
    before: str
    after: str
    explanation: str


@dataclass
class _Occurrence:
    signature: str
    line: int
    text: str


_JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
_SOURCE_EXTENSIONS = _JS_EXTENSIONS + (".py", ".go")
_METRICS = {"lines", "statements", "branches", "functions"}


def _is_test(path: str) -> bool:
    path = path.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    if not name.endswith(_SOURCE_EXTENSIONS):
        return False
    return (
        any(part in {"test", "tests", "__tests__"} for part in path.split("/")[:-1])
        or name.startswith("test_")
        or bool(re.search(r"(?:[._-](?:test|spec))\.[^.]+$", name))
        or name.endswith("_test.go")
    )


def _workflow(path: str) -> bool:
    return bool(re.search(r"(?:^|/)\.github/workflows/[^/]+\.ya?ml$", path))


def _config(path: str) -> bool:
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return (
        name in {"package.json", "pyproject.toml", ".coveragerc", "setup.cfg", "tox.ini", ".nycrc", ".nycrc.json"}
        or bool(re.search(r"(?:jest|vitest|nyc|coverage|tsconfig|jsconfig).*\.(?:[cm]?[jt]s|json|jsonc|toml|ini|cfg)$", name))
    )


def _blank(text: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in text)


def _mask_js(text: str, strings: bool = True) -> str:
    """Mask comments and optionally literals while preserving line positions."""
    pattern = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/|\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'|`(?:\\[\s\S]|[^`\\])*`")
    identifier_pattern = re.compile(r"[A-Za-z_$][\w$]*")
    output = list(text)
    index = 0
    previous = ""
    regex_prefixes = {"", "=", "(", "[", "{", ":", ",", ";", "!", "?", "&", "|", "+", "-", "*", "%", "~", "^", "<", ">", "/", "return", "throw", "case", "yield", "await"}
    while index < len(text):
        char = text[index]
        token = pattern.match(text, index) if char in "/\"'`" else None
        if token:
            comment = token.group().startswith(("//", "/*"))
            if strings or comment:
                output[index:token.end()] = _blank(token.group())
            if not comment:
                previous = "literal"
            index = token.end()
            continue
        if char == "/" and previous in regex_prefixes:
            cursor = index + 1
            in_class = False
            while cursor < len(text) and text[cursor] not in "\r\n":
                if text[cursor] == "\\":
                    cursor += 2
                    continue
                if text[cursor] == "[":
                    in_class = True
                elif text[cursor] == "]":
                    in_class = False
                elif text[cursor] == "/" and not in_class:
                    cursor += 1
                    while cursor < len(text) and text[cursor].isalpha():
                        cursor += 1
                    output[index:cursor] = _blank(text[index:cursor])
                    previous = "literal"
                    index = cursor
                    break
                cursor += 1
            else:
                previous = char
                index += 1
            continue
        identifier = identifier_pattern.match(text, index) if char.isalpha() or char in "_$" else None
        if identifier:
            previous = identifier.group()
            index = identifier.end()
        else:
            if not char.isspace():
                previous = char
            index += 1
    return "".join(output)


def _mask_python(text: str) -> str:
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    output = list(text)
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type not in {tokenize.COMMENT, tokenize.STRING}:
                continue
            start = offsets[token.start[0] - 1] + token.start[1]
            end = offsets[token.end[0] - 1] + token.end[1]
            output[start:end] = _blank(text[start:end])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A diff can contain incomplete source; retain the masking completed so far.
        pass
    return "".join(output)


def _mask_yaml_comment(line: str) -> str:
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and quote == '"':
            escaped = True
        elif char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == "#" and quote is None and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _line_text(text: str, number: int) -> str:
    lines = text.splitlines()
    return lines[number - 1].strip() if 0 < number <= len(lines) else ""


def _replacement_lines(before: str, after: str) -> dict[int, str]:
    """Pair lines within replacement blocks, leaving pure additions unpaired."""
    old_lines, new_lines = before.splitlines(), after.splitlines()
    result = {}
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "replace":
            for offset in range(min(old_end - old_start, new_end - new_start)):
                result[new_start + offset + 1] = old_lines[old_start + offset].strip()
    return result


def _matches(raw: str, masked: str, pattern: str) -> list[_Occurrence]:
    result = []
    for match in re.finditer(pattern, masked, re.MULTILINE):
        marker = list(re.finditer(r"\b(?:skipif|skipIf|skipUnless|skipTest|SkipNow|Skipf|Skip|skip|only|xfail|xit|xtest|xdescribe)\b", match.group()))
        offset = match.start() + (marker[-1].start() if marker else 0)
        number = _line(masked, offset)
        result.append(_Occurrence(re.sub(r"\s+", "", match.group()), number, _line_text(raw, number)))
    return result


def _added(old: Iterable[_Occurrence], new: Iterable[_Occurrence]) -> list[_Occurrence]:
    counts = Counter(item.signature for item in old)
    added = []
    for item in new:
        if counts[item.signature]:
            counts[item.signature] -= 1
        else:
            added.append(item)
    return added


def _vitest_imports(text: str, masked: str) -> dict[str, str]:
    """Resolve only static named test/it/describe imports from literal vitest."""
    imports = {}
    comments_masked = _mask_js(text, strings=False)
    pattern = r"\bimport\s*\{([^{}]*)\}\s*from\s*(['\"])vitest\2"
    for match in re.finditer(pattern, comments_masked):
        # Keeping module strings enables parsing imports, but an import-looking
        # fixture string must never establish a real framework binding.
        if masked[match.start():match.start() + 6] != "import":
            continue
        for specifier in match.group(1).split(","):
            binding = re.fullmatch(r"\s*(test|it|describe)(?:\s+as\s+([A-Za-z_$][\w$]*))?\s*", specifier)
            if binding:
                original, alias = binding.groups()
                imports[alias or original] = original
    return imports


def _delimiter_pairs(masked: str) -> dict[int, int]:
    """Index balanced code delimiters; literals/comments have already vanished."""
    pairs = {}
    stack = []
    closing = {")": "(", "]": "[", "}": "{"}
    for index, char in enumerate(masked):
        if char in "([{":
            stack.append((char, index))
        elif char in closing:
            if stack and stack[-1][0] == closing[char]:
                _, start = stack.pop()
                pairs[start] = index
            else:
                # A malformed outer construct must not leak a callback scope.
                stack.clear()
    return pairs


def _argument_spans(masked: str, start: int, end: int, pairs: dict[int, int]) -> list[tuple[int, int]]:
    """Split a balanced call at top-level commas only."""
    arguments = []
    argument_start = start + 1
    index = argument_start
    while index < end:
        if masked[index] in "([{":
            closing = pairs.get(index)
            if closing is None or closing >= end:
                return []
            index = closing + 1
        elif masked[index] == ",":
            arguments.append((argument_start, index))
            argument_start = index + 1
            index += 1
        else:
            index += 1
    arguments.append((argument_start, end))
    return arguments


def _nested_function_spans(masked: str, start: int, end: int, pairs: dict[int, int]) -> list[tuple[int, int]]:
    """Exclude simple nested function bodies from a test context's scope."""
    spans = []
    for match in re.finditer(r"=>|\bfunction(?:\s+[A-Za-z_$][\w$]*)?\s*(\()", masked[start:end]):
        cursor = start + match.end()
        if match.group(1):
            parameter_end = pairs.get(start + match.start(1))
            if parameter_end is None:
                continue
            cursor = parameter_end + 1
        while cursor < end and masked[cursor].isspace():
            cursor += 1
        if cursor < end and masked[cursor] == "{":
            body_end = pairs.get(cursor)
            if body_end is not None and body_end < end:
                spans.append((cursor, body_end + 1))
        elif not match.group(1):
            expression_start = cursor
            while cursor < end:
                if masked[cursor] in "([{" and cursor in pairs:
                    cursor = pairs[cursor] + 1
                elif masked[cursor] in ",;)}]":
                    break
                else:
                    cursor += 1
            spans.append((expression_start, cursor))
    return spans


def _parameter_scopes(masked: str, pairs: dict[int, int]):
    """Recognize simple function/method/arrow parameter lists and bodies."""
    controls = {"if", "for", "while", "switch", "catch", "with"}
    for match in re.finditer(r"\b([A-Za-z_$][\w$]*)\s*(\()", masked):
        if match.group(1) in controls:
            continue
        parameter_start = match.start(2)
        parameter_end = pairs.get(parameter_start)
        if parameter_end is None:
            continue
        cursor = parameter_end + 1
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if cursor < len(masked) and masked[cursor] == "{" and cursor in pairs:
            yield masked[parameter_start + 1:parameter_end], cursor, pairs[cursor] + 1
    arrow = r"(?:\(([^()]*)\)|([A-Za-z_$][\w$]*))\s*=>\s*(\{)"
    for match in re.finditer(arrow, masked):
        body_start = match.start(3)
        if body_start in pairs:
            yield match.group(1) or match.group(2) or "", body_start, pairs[body_start] + 1


def _shadowed_import_scopes(masked: str, imports: dict[str, str], pairs: dict[int, int]):
    scopes = []
    for parameters, start, end in _parameter_scopes(masked, pairs):
        names = {parameter.strip() for parameter in parameters.split(",") if re.fullmatch(r"\s*[A-Za-z_$][\w$]*\s*", parameter)}
        shadowed = names & imports.keys()
        if shadowed:
            scopes.append((start, end, shadowed))
    return scopes


def _vitest_context_skips(text: str, masked: str, imports: dict[str, str]) -> list[_Occurrence]:
    # Context.skip is a Vitest feature. Require evidence that the factory comes
    # from Vitest instead of guessing from an object called ctx or test.
    factories = sorted(alias for alias, original in imports.items() if original in {"test", "it"})
    if not factories:
        return []
    names = "|".join(re.escape(name) for name in factories)
    calls = re.finditer(rf"(?<![\w$.])(?P<factory>{names})(?:\s*\.\s*(?:skip|only))?\s*(\()", masked)
    pairs = _delimiter_pairs(masked)
    shadowed_imports = _shadowed_import_scopes(masked, imports, pairs)
    method_scopes = list(_parameter_scopes(masked, pairs))
    result = []
    seen = set()
    callback = re.compile(r"\s*(?:async\s+)?(?:\(\s*([A-Za-z_$][\w$]*)\s*\)|([A-Za-z_$][\w$]*))\s*=>\s*(\{)")
    for call in calls:
        if any(start <= call.start() < end and call.group("factory") in names for start, end, names in shadowed_imports):
            continue
        # The unnamed call parenthesis follows the named factory group.
        call_start = call.start(2)
        call_end = pairs.get(call_start)
        if call_end is None:
            continue
        for start, end in _argument_spans(masked, call_start, call_end, pairs)[1:]:
            arrow = callback.match(masked, start, end)
            if not arrow:
                continue
            context = arrow.group(1) or arrow.group(2)
            body_start = arrow.start(3)
            body_end = pairs.get(body_start)
            if body_end is None or body_end >= end or masked[body_end + 1:end].strip():
                continue
            body = masked[body_start + 1:body_end]
            # Resolve the direct callback context only. Nested helper callbacks
            # can shadow its name; they are not evidence about this binding.
            nested = _nested_function_spans(masked, body_start + 1, body_end, pairs)
            nested.extend((start, end) for _, start, end in method_scopes if body_start < start < end <= body_end)
            if re.search(rf"\b(?:const|let|var|class)\s+{re.escape(context)}\b|\b{re.escape(context)}\s*=(?!=|>)", body):
                continue
            for skip in re.finditer(rf"(?<![\w$.]){re.escape(context)}\s*\.\s*(skip)\s*(?=\()", body):
                offset = body_start + 1 + skip.start(1)
                if offset in seen or any(start <= offset < end for start, end in nested):
                    continue
                seen.add(offset)
                number = _line(text, offset)
                # Renaming a callback parameter or moving it does not create a
                # new skip. Count the established context operation per file.
                result.append(_Occurrence("vitest-context.skip", number, _line_text(text, number)))
    return result


def _js_test_markers(text: str) -> dict[str, list[_Occurrence]]:
    masked = _mask_js(text)
    imports = _vitest_imports(text, masked)
    shadowed_imports = _shadowed_import_scopes(masked, imports, _delimiter_pairs(masked)) if imports else []
    names = "|".join(re.escape(name) for name in sorted({"it", "test", "describe"} | set(imports)))
    result = {
        "test-skip": _matches(text, masked, r"(?<![\w$.])(?:xit|xtest|xdescribe)\b\s*(?=\()"),
        "test-focus": [],
    }
    for match in re.finditer(rf"(?<![\w$.])(?P<name>{names})\s*\.\s*(?P<marker>skipIf|runIf|skip|only)\b", masked):
        if any(start <= match.start() < end and match.group("name") in names for start, end, names in shadowed_imports):
            continue
        marker = match.group("marker")
        canonical = imports.get(match.group("name"), match.group("name"))
        number = _line(text, match.start("marker"))
        occurrence = _Occurrence(f"{canonical}.{marker}", number, _line_text(text, number))
        result["test-focus" if marker == "only" else "test-skip"].append(occurrence)
    result["test-skip"].extend(_vitest_context_skips(text, masked, imports))
    for occurrences in result.values():
        occurrences.sort(key=lambda item: (item.line, item.signature))
    return result


def _test_markers(path: str, text: str) -> dict[str, list[_Occurrence]]:
    if not _is_test(path):
        return {}
    if path.endswith(_JS_EXTENSIONS):
        return _js_test_markers(text)
    if path.endswith(".py"):
        return {"test-skip": _matches(text, _mask_python(text), r"\b(?:pytest\s*\.\s*(?:mark\s*\.\s*(?:skipif|skip|xfail)|skip|xfail)|unittest\s*\.\s*(?:skipIf|skipUnless|skip)|self\s*\.\s*skipTest)\b")}
    if path.endswith(".go"):
        return {"test-skip": _matches(text, _mask_js(text), r"\b(?:t|tb)\s*\.\s*(?:SkipNow|Skipf|Skip)\s*(?=\()")}
    return {}


def _directives(path: str, text: str) -> dict[str, list[_Occurrence]]:
    if not path.endswith(_JS_EXTENSIONS):
        return {}
    # Inspect actual comment tokens, so strings containing directive examples do
    # not become findings. ESLint and TypeScript directives are comments by design.
    token_pattern = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/|\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'|`(?:\\[\s\S]|[^`\\])*`")
    result = {"typescript-suppression": [], "eslint-suppression": []}
    for token in token_pattern.finditer(text):
        if not token.group().startswith(("//", "/*")):
            continue
        comment = token.group()
        if comment.startswith("//"):
            directive = re.match(r"^///?\s*(@ts-(?:nocheck|ignore)\b)", comment)
            directive_offset = 0
        else:
            # TypeScript's scanner recognizes ignore on the last block-comment
            # line. File-level nocheck is a leading single-line pragma.
            directive_offset = comment.rfind("\n") + 1
            directive = re.match(r"^\s*(?:/|\*)*\s*(@ts-ignore\b)", comment[directive_offset:])
        if directive:
            marker = directive.group(1)
            executable_prefix = _mask_js(text[:token.start()], strings=False).lstrip("\ufeff")
            executable_prefix = re.sub(r"^#![^\n]*(?:\n|$)", "", executable_prefix)
            if marker != "@ts-nocheck" or not executable_prefix.strip():
                number = _line(text, token.start() + directive_offset + directive.start(1))
                result["typescript-suppression"].append(_Occurrence(marker, number, _line_text(text, number)))
        for rule_id, pattern in (
            ("eslint-suppression", r"^(?://|/\*)\s*(eslint-disable(?:-next-line|-line)?\b)"),
        ):
            for match in re.finditer(pattern, token.group()):
                offset = token.start() + match.start(1)
                number = _line(text, offset)
                signature = match.group(1)
                rules = token.group()[match.end():].split("--", 1)[0].split("*/", 1)[0].strip()
                signature += ":" + ",".join(sorted(rule.strip() for rule in rules.split(",") if rule.strip()))
                result[rule_id].append(_Occurrence(signature, number, _line_text(text, number)))
    return result


def _workflow_values(text: str) -> tuple[list[_Occurrence], list[tuple[int, str]]]:
    flags = []
    commands = []
    block_indent = None
    block_key = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = _mask_yaml_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if block_indent is not None:
            if indent > block_indent:
                if block_key == "run":
                    commands.append((number, line.strip()))
                continue
            block_indent = block_key = None
        match = re.match(r"\s*(?:-\s*)?([\w-]+)\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        if re.match(r"[|>]", value):
            block_indent, block_key = indent, key
            continue
        if key == "continue-on-error" and value.strip("\"'").lower() == "true":
            flags.append(_Occurrence("continue-on-error:true", number, raw.strip()))
        if key == "run":
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            commands.append((number, value))
    return flags, commands


def _package_scripts(text: str) -> list[tuple[int, str, str]]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("scripts"), dict):
        return []
    scripts_match = re.search(r'"scripts"\s*:\s*\{', text)
    cursor = scripts_match.end() if scripts_match else 0
    result = []
    for key, value in data["scripts"].items():
        if not isinstance(value, str):
            continue
        # Locate the key/value pair in the source to preserve the exact JSON line.
        key_match = re.search(re.escape(json.dumps(key)) + r"\s*:\s*", text[cursor:])
        number = _line(text, cursor + key_match.start()) if key_match else 1
        if key_match:
            cursor += key_match.end()
        result.append((number, key, value))
    return result


def _check_command(command: str) -> bool:
    # Quoted messages such as echo "pytest || true" are not executed checks.
    masked = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', lambda match: " " * len(match.group()), command)
    for segment in re.split(r"&&|\|\||[;\n]", masked):
        segment = re.sub(r"^\s*(?:[A-Za-z_]\w*=\S+\s+)*", "", segment).strip()
        segment = re.sub(r"^(?:npx(?:\s+--[\w=-]+)*|uv\s+run)\s+", "", segment)
        if re.match(r"(?:pytest|jest|vitest|eslint|tsc|mypy|pyright|unittest)(?:\s|$)", segment):
            return True
        if re.match(r"(?:python(?:3(?:\.\d+)?)?\s+-m\s+(?:pytest|unittest)|go\s+test|cargo\s+test|ruff\s+check)\b", segment):
            return True
        if re.match(r"(?:npm|pnpm|yarn|bun|make)\s+(?:(?:run|exec)\s+)?(?:test(?::[\w-]+)?|lint(?::[\w-]+)?|type-?check(?::[\w-]+)?|tsc|eslint|vitest|jest)\b", segment):
            return True
    return False


def _swallowed(path: str, text: str) -> list[_Occurrence]:
    if _workflow(path):
        commands = [(number, "", command) for number, command in _workflow_values(text)[1]]
    elif path.rsplit("/", 1)[-1] == "package.json":
        commands = _package_scripts(text)
    else:
        return []
    result = []
    for number, name, command in commands:
        masked = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', lambda match: " " * len(match.group()), command)
        suffix = re.search(r"\|\|\s*true\b|;\s*exit\s+0\b", masked)
        named_check = bool(re.match(r"^(?:test|lint|type-?check)(?:$|:)", name))
        if suffix and (named_check or _check_command(command[:suffix.start()])):
            signature = re.sub(r"\s+", " ", command.strip())
            result.append(_Occurrence(signature, number, _line_text(text, number)))
    return result


def _object_values(text: str):
    """Yield simple scalar object properties with their containing key path."""
    masked = _mask_js(text, strings=False)
    token_re = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_$][\w$-]*|-?\d+(?:\.\d+)?|[{}:\[\],=]')
    tokens = list(token_re.finditer(masked))
    stack = []
    for index, token in enumerate(tokens):
        value = token.group()
        if value == "{":
            label = ""
            if index >= 2 and tokens[index - 1].group() in {":", "="}:
                label = tokens[index - 2].group().strip("\"'")
            stack.append(label)
        elif value == "}":
            if stack:
                stack.pop()
        elif index >= 2 and tokens[index - 1].group() == ":" and value not in {"[", "]", ",", "="}:
            key = tokens[index - 2].group().strip("\"'")
            yield tuple(stack), key, value, token.start()


def _ts_values(path: str, text: str) -> dict[str, list[_Occurrence]]:
    if not re.search(r"(?:^|/)(?:tsconfig|jsconfig)[^/]*\.jsonc?$", path):
        return {}
    result = defaultdict(list)
    for stack, key, value, offset in _object_values(text):
        if stack and stack[-1] == "compilerOptions" and key in {"strict", "noCheck"} and value in {"true", "false"}:
            number = _line(text, offset)
            result[key].append(_Occurrence(value, number, _line_text(text, number)))
    return dict(result)


def _coverage_values(path: str, text: str) -> dict[str, list[tuple[float, _Occurrence]]]:
    if not (_config(path) or _workflow(path)):
        return {}
    result = defaultdict(list)
    name = path.rsplit("/", 1)[-1].lower()
    # CLI thresholds belong to executable commands or pytest option sections,
    # never package descriptions or unrelated strings.
    commands = []
    if _workflow(path):
        commands = [(number, command) for number, command in _workflow_values(text)[1] if _check_command(command)]
    elif name == "package.json":
        commands = [(number, command) for number, _, command in _package_scripts(text) if _check_command(command)]
    elif name in {"pyproject.toml", "setup.cfg", "tox.ini"}:
        section = ""
        for number, raw in enumerate(text.splitlines(), 1):
            line = _mask_yaml_comment(raw).strip()
            header = re.match(r"\[([^]]+)\]", line)
            if header:
                section = header.group(1).lower()
            if section in {"tool.pytest.ini_options", "pytest", "tool:pytest"} or (name == "tox.ini" and section.startswith("testenv")):
                commands.append((number, line))
    for number, command in commands:
        for match in re.finditer(r"--cov-fail-under(?:\s*=\s*|\s+)(\d+(?:\.\d+)?)", command):
            result["cov-fail-under"].append((float(match.group(1)), _Occurrence("cov-fail-under", number, _line_text(text, number))))
    if name in {"pyproject.toml", ".coveragerc", "setup.cfg", "tox.ini"}:
        section = ""
        for number, raw in enumerate(text.splitlines(), 1):
            line = _mask_yaml_comment(raw).strip()
            header = re.match(r"\[([^]]+)\]", line)
            if header:
                section = header.group(1).lower()
            valid_section = section in {"tool.coverage.report", "coverage:report"} or (name == ".coveragerc" and section == "report")
            match = re.match(r"fail_under\s*=\s*(\d+(?:\.\d+)?)\s*$", line)
            if valid_section and match:
                result[section + ".fail_under"].append((float(match.group(1)), _Occurrence("fail_under", number, raw.strip())))
        return dict(result)
    for stack, key, value, offset in _object_values(text):
        if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            threshold_scope = "coverageThreshold" in stack or ("coverage" in stack and "thresholds" in stack)
            nyc_scope = name in {".nycrc", ".nycrc.json"} or "nyc" in stack
            if key in _METRICS and (threshold_scope or nyc_scope):
                number = _line(text, offset)
                signature = ".".join(part for part in stack + (key,) if part)
                result[signature].append((float(value), _Occurrence(signature, number, _line_text(text, number))))
    return dict(result)


_RULES = {
    "test-skip": ("medium", "Test skipping added", "A newly added skip or expected-failure marker can stop a test from blocking a failing change."),
    "test-focus": ("high", "Focused test selection added", "An only marker can prevent the rest of this test suite from running."),
    "continue-on-error": ("high", "Workflow failure allowed", "This workflow step or job can now fail without making the workflow fail."),
    "swallowed-check": ("high", "Check failure swallowed", "A test, lint, or type-check command now explicitly converts failure into a successful exit."),
    "typescript-suppression": ("medium", "TypeScript suppression added", "A newly added TypeScript directive suppresses checking for a line or file."),
    "eslint-suppression": ("medium", "ESLint suppression added", "A newly added ESLint directive disables one or more lint rules."),
}


def analyze(changes: list[FileChange]) -> list[Finding]:
    """Return deterministic, line-addressed findings for supported gate changes."""
    findings = []
    for change in changes:
        path, before, after = change.path.replace("\\", "/"), change.before, change.after
        if before and not after and _is_test(path):
            findings.append(Finding("deleted-test-file", "high", "Test file deleted", path, 1, "left", _line_text(before, 1), "", "A test source file was removed. Review whether its coverage was preserved elsewhere."))
            continue
        replaced_lines = _replacement_lines(before, after)
        old_rules = _test_markers(path, before)
        new_rules = _test_markers(path, after)
        old_rules.update(_directives(path, before))
        new_rules.update(_directives(path, after))
        if _workflow(path):
            old_rules["continue-on-error"] = _workflow_values(before)[0]
            new_rules["continue-on-error"] = _workflow_values(after)[0]
        old_rules["swallowed-check"] = _swallowed(path, before)
        new_rules["swallowed-check"] = _swallowed(path, after)
        for rule_id, occurrences in new_rules.items():
            for item in _added(old_rules.get(rule_id, []), occurrences):
                severity, title, explanation = _RULES[rule_id]
                findings.append(Finding(rule_id, severity, title, path, item.line, "right", replaced_lines.get(item.line, ""), item.text, explanation))

        old_ts, new_ts = _ts_values(path, before), _ts_values(path, after)
        for key, bad_value, rule_id, title in (
            ("strict", "false", "typescript-strict-disabled", "TypeScript strict checking disabled"),
            ("noCheck", "true", "typescript-nocheck-enabled", "TypeScript checking disabled"),
        ):
            old_bad = [item for item in old_ts.get(key, []) if item.signature == bad_value]
            new_bad = [item for item in new_ts.get(key, []) if item.signature == bad_value]
            # strict=false is a weakening only when explicit strict=true existed.
            if key == "strict" and not any(item.signature == "true" for item in old_ts.get(key, [])):
                continue
            old_text = next((item.text for item in old_ts.get(key, []) if item.signature != bad_value), "")
            for item in _added(old_bad, new_bad):
                findings.append(Finding(rule_id, "high", title, path, item.line, "right", old_text, item.text, "This compiler option now disables TypeScript checks that previously applied."))

        old_coverage, new_coverage = _coverage_values(path, before), _coverage_values(path, after)
        for key, values in new_coverage.items():
            old_values = list(old_coverage.get(key, []))
            remaining_new = []
            # Match unchanged values first so moving a threshold does not count.
            for value, occurrence in values:
                same = next((index for index, old in enumerate(old_values) if old[0] == value), None)
                if same is None:
                    remaining_new.append((value, occurrence))
                else:
                    old_values.pop(same)
            for (old_value, old_item), (new_value, new_item) in zip(old_values, remaining_new):
                if new_value < old_value:
                    findings.append(Finding("coverage-lowered", "high", "Coverage threshold lowered", path, new_item.line, "right", old_item.text, new_item.text, f"The configured {key} threshold decreased from {old_value:g} to {new_value:g}."))
    return sorted(findings, key=lambda item: (item.path, item.line, item.rule_id, item.before, item.after))
