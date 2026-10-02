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
    return pattern.sub(lambda match: _blank(match.group()) if strings or match.group().startswith(("//", "/*")) else match.group(), text)


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


def _test_markers(path: str, text: str) -> dict[str, list[_Occurrence]]:
    if not _is_test(path):
        return {}
    if path.endswith(_JS_EXTENSIONS):
        masked = _mask_js(text)
        return {
            "test-skip": _matches(text, masked, r"(?<![\w$.])(?:(?:it|test|describe)\s*\.\s*skip\b|(?:xit|xtest|xdescribe)\b\s*(?=\())"),
            "test-focus": _matches(text, masked, r"(?<![\w$.])(?:it|test|describe)\s*\.\s*only\b"),
        }
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
