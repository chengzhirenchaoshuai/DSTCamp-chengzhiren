"""Pure-Python Lua table parser for DST configuration files.

DST uses a restricted Lua subset: only `return { ... }` data tables.
This parser handles that subset without requiring a Lua runtime dependency.
"""

import re
from enum import Enum, auto
from pathlib import Path
from typing import Any


class LuaParseError(Exception):
    """Error raised when Lua parsing fails."""

    def __init__(self, message: str, line: int = 0, col: int = 0):
        loc = f" at line {line}, col {col}" if line > 0 else ""
        super().__init__(f"{message}{loc}")
        self.line = line
        self.col = col


# ── Tokenizer ──────────────────────────────────────────────────────────

class TokenType(Enum):
    RETURN = auto()
    LBRACE = auto()       # {
    RBRACE = auto()       # }
    LBRACKET = auto()     # [
    RBRACKET = auto()     # ]
    EQUALS = auto()       # =
    COMMA = auto()        # ,
    STRING = auto()
    NUMBER = auto()
    IDENTIFIER = auto()   # Includes true/false keywords
    EOF = auto()


class Token:
    def __init__(self, type_: TokenType, value: Any, line: int, col: int):
        self.type = type_
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.col})"


# Regex patterns for Lua tokens
_LUA_STRING_RE = re.compile(r'''
    "(?:[^"\\]|\\.)*"           # double-quoted string
    |'(?:[^'\\]|\\.)*'          # single-quoted string
    |\[=*\[.*?\]=*\]            # long bracket string (lazy match for nesting)
''', re.DOTALL | re.VERBOSE)

_LUA_NUMBER_RE = re.compile(r'''
    -?(?:0x[0-9a-fA-F]+         # hex integer
    |\d+\.?\d*(?:[eE][+-]?\d+)? # decimal with optional fraction/exponent
    |\.\d+(?:[eE][+-]?\d+)?)    # leading dot decimal
''', re.VERBOSE)

_LUA_IDENT_RE = re.compile(r'[a-zA-Z_][a-zA-Z0-9_]*')


class LuaTokenizer:
    """Tokenize a Lua table literal string."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1
        self.col = 1

    def tokenize(self) -> list[Token]:
        tokens = []
        while self.pos < len(self.text):
            c = self.text[self.pos]

            # Whitespace
            if c in ' \t\r\n':
                if c == '\n':
                    self.line += 1
                    self.col = 1
                else:
                    self.col += 1
                self.pos += 1
                continue

            # Comments: -- to end of line, or --[[ ... ]] block
            if c == '-' and self.pos + 1 < len(self.text) and self.text[self.pos + 1] == '-':
                self._skip_comment()
                continue

            # String
            if c in '"\'':
                token = self._read_string()
                tokens.append(token)
                continue

            # Long bracket string [[...]] or [=[...]=]
            if c == '[':
                token = self._read_long_string()
                if token:
                    tokens.append(token)
                    continue
                # Not a long string, just a regular [
                tokens.append(Token(TokenType.LBRACKET, '[', self.line, self.col))
                self.pos += 1
                self.col += 1
                continue

            # Numbers. A bare leading dot (".01", no sign) is a valid Lua
            # decimal literal too, not just "-.01" -- without this branch
            # the "." falls through to the unknown-character skip below
            # and gets silently dropped, turning ".01" into a bogus "01".
            if c.isdigit() or \
               (c == '.' and self.pos + 1 < len(self.text) and self.text[self.pos + 1].isdigit()) or \
               (c == '-' and self.pos + 1 < len(self.text) and
                               (self.text[self.pos + 1].isdigit() or self.text[self.pos + 1] == '.')):
                token = self._read_number()
                tokens.append(token)
                continue

            # Identifiers and keywords
            if c.isalpha() or c == '_':
                token = self._read_identifier()
                tokens.append(token)
                continue

            # Single-character tokens
            single_map = {
                '{': TokenType.LBRACE,
                '}': TokenType.RBRACE,
                '[': TokenType.LBRACKET,
                ']': TokenType.RBRACKET,
                '=': TokenType.EQUALS,
                ',': TokenType.COMMA,
            }
            if c in single_map:
                tokens.append(Token(single_map[c], c, self.line, self.col))
                self.pos += 1
                self.col += 1
                continue

            # Unknown character - skip with warning
            self.pos += 1
            self.col += 1

        tokens.append(Token(TokenType.EOF, None, self.line, self.col))
        return tokens

    def _skip_comment(self):
        """Skip Lua comment (-- to end of line, or --[[ ... ]] block)."""
        self.pos += 2  # Skip --
        self.col += 2

        # Block comment --[[ ... ]]
        if self.pos < len(self.text) and self.text[self.pos] == '[':
            # Find matching ]]
            depth = 0
            if self.pos + 1 < len(self.text) and self.text[self.pos + 1] == '[':
                # --[[ style
                end_idx = self.text.find(']]', self.pos + 2)
                if end_idx != -1:
                    skipped = self.text[self.pos:end_idx + 2]
                    newlines = skipped.count('\n')
                    self.line += newlines
                    if newlines > 0:
                        self.col = len(skipped.split('\n')[-1]) + 1
                    else:
                        self.col += end_idx + 2 - self.pos
                    self.pos = end_idx + 2
                    return
            # --[=[ ... ]=] style
            eq_count = 0
            while self.pos < len(self.text) and self.text[self.pos] == '=':
                eq_count += 1
                self.pos += 1
            if self.pos < len(self.text) and self.text[self.pos] == '[':
                self.pos += 1
                closer = ']' + '=' * eq_count + ']'
                end_idx = self.text.find(closer, self.pos)
                if end_idx != -1:
                    skipped = self.text[self.pos:end_idx]
                    newlines = skipped.count('\n')
                    self.line += newlines
                    if newlines > 0:
                        self.col = 1
                    else:
                        self.col += end_idx + len(closer) - self.pos
                    self.pos = end_idx + len(closer)
                    return

        # Line comment: skip to end of line
        while self.pos < len(self.text) and self.text[self.pos] != '\n':
            self.pos += 1
            self.col += 1

    def _read_string(self) -> Token:
        """Read a quoted string (single or double quotes)."""
        quote = self.text[self.pos]
        start_line, start_col = self.line, self.col
        self.pos += 1
        self.col += 1
        chars = []
        while self.pos < len(self.text):
            c = self.text[self.pos]
            if c == '\\' and self.pos + 1 < len(self.text):
                # Escape sequence
                next_c = self.text[self.pos + 1]
                escape_map = {
                    'n': '\n', 'r': '\r', 't': '\t', '\\': '\\',
                    '"': '"', "'": "'", 'a': '\a', 'b': '\b',
                    'f': '\f', 'v': '\v',
                }
                if next_c in escape_map:
                    chars.append(escape_map[next_c])
                elif next_c == '\n':
                    # Backslash-newline continuation
                    self.line += 1
                    self.col = 0
                elif next_c == 'x' and self.pos + 3 < len(self.text):
                    # \xNN hex escape
                    hex_str = self.text[self.pos + 2:self.pos + 4]
                    try:
                        chars.append(chr(int(hex_str, 16)))
                        self.pos += 2
                        self.col += 2
                    except ValueError:
                        chars.append('\\')
                        chars.append('x')
                        chars.append(hex_str)
                        self.pos += 2
                        self.col += 2
                elif next_c.isdigit():
                    # \d{1,3} decimal escape (simplified)
                    num_str = ''
                    for i in range(1, 4):
                        if self.pos + i < len(self.text) and self.text[self.pos + i].isdigit():
                            num_str += self.text[self.pos + i]
                    try:
                        chars.append(chr(int(num_str)))
                        self.pos += len(num_str) - 1
                        self.col += len(num_str) - 1
                    except ValueError:
                        chars.append('\\')
                        chars.append(next_c)
                else:
                    chars.append('\\')
                    chars.append(next_c)
                self.pos += 2
                self.col += 2
            elif c == quote:
                self.pos += 1
                self.col += 1
                return Token(TokenType.STRING, ''.join(chars), start_line, start_col)
            elif c == '\n':
                raise LuaParseError("Unterminated string", start_line, start_col)
            else:
                chars.append(c)
                self.pos += 1
                self.col += 1

        raise LuaParseError("Unterminated string", start_line, start_col)

    def _read_long_string(self) -> Token | None:
        """Try to read a long bracket string [[...]] or [=[...]=]."""
        start_pos = self.pos
        start_line, start_col = self.line, self.col

        # Count equals signs
        eq_count = 0
        pos = self.pos + 1
        while pos < len(self.text) and self.text[pos] == '=':
            eq_count += 1
            pos += 1

        if pos >= len(self.text) or self.text[pos] != '[':
            return None  # Not a long string

        # This is a long bracket string
        opener = '[' + '=' * eq_count + '['
        closer = ']' + '=' * eq_count + ']'

        # Skip the opening bracket sequence
        self.pos = pos + 1
        self.col += pos - start_pos + 1

        # Find the closing bracket
        end_idx = self.text.find(closer, self.pos)
        if end_idx == -1:
            raise LuaParseError("Unterminated long string", start_line, start_col)

        content = self.text[self.pos:end_idx]

        # Update position (long strings are raw, no escapes)
        newlines = content.count('\n')
        self.line += newlines
        if newlines > 0:
            self.col = len(content.split('\n')[-1]) + len(closer) + 1
        else:
            self.col += len(content) + len(closer)
        self.pos = end_idx + len(closer)

        # Strip leading newline if present (Lua convention)
        if content.startswith('\n'):
            content = content[1:]
        elif content.startswith('\r\n'):
            content = content[2:]

        return Token(TokenType.STRING, content, start_line, start_col)

    def _read_number(self) -> Token:
        """Read a numeric literal."""
        start_line, start_col = self.line, self.col
        match = _LUA_NUMBER_RE.match(self.text, self.pos)
        if not match:
            raise LuaParseError("Invalid number", start_line, start_col)

        num_str = match.group(0)

        # Handle leading minus as unary operator or part of number
        # In Lua tables, negative numbers are fine: { x=-1 }

        # Parse the value
        if num_str.startswith('0x') or num_str.startswith('0X'):
            value = int(num_str, 16)
        elif '.' in num_str or 'e' in num_str.lower():
            value = float(num_str)
        else:
            value = int(num_str)

        self.pos += len(num_str)
        self.col += len(num_str)
        return Token(TokenType.NUMBER, value, start_line, start_col)

    def _read_identifier(self) -> Token:
        """Read an identifier or keyword (true, false, nil, return)."""
        start_line, start_col = self.line, self.col
        match = _LUA_IDENT_RE.match(self.text, self.pos)
        if not match:
            raise LuaParseError("Invalid identifier", start_line, start_col)

        name = match.group(0)
        self.pos += len(name)
        self.col += len(name)

        # Keyword handling
        if name == 'return':
            return Token(TokenType.RETURN, name, start_line, start_col)
        elif name == 'true':
            return Token(TokenType.IDENTIFIER, True, start_line, start_col)
        elif name == 'false':
            return Token(TokenType.IDENTIFIER, False, start_line, start_col)
        elif name == 'nil':
            return Token(TokenType.IDENTIFIER, None, start_line, start_col)
        else:
            return Token(TokenType.IDENTIFIER, name, start_line, start_col)


# ── Parser ─────────────────────────────────────────────────────────────

class LuaTableParser:
    """Recursive-descent parser for Lua table literals."""

    def __init__(self, text: str, filename: str = "<string>"):
        self.filename = filename
        tokenizer = LuaTokenizer(text)
        self.tokens = tokenizer.tokenize()
        self.idx = 0

    def _peek(self) -> Token:
        return self.tokens[self.idx]

    def _advance(self) -> Token:
        token = self.tokens[self.idx]
        self.idx += 1
        return token

    def _expect(self, type_: TokenType, error_msg: str = "") -> Token:
        token = self._advance()
        if token.type != type_:
            msg = error_msg or f"Expected {type_.name}, got {token.type.name} ({token.value!r})"
            raise LuaParseError(msg, token.line, token.col)
        return token

    def parse(self) -> dict:
        """Parse a Lua `return { ... }` expression and return the table as a Python dict.

        Also handles files that omit the `return` keyword and just start with `{`.
        """
        token = self._peek()

        # Optional 'return' keyword
        if token.type == TokenType.RETURN:
            self._advance()

        # Parse the table literal
        result = self._parse_table()
        return result

    def _parse_value(self) -> Any:
        """Parse a single value: string, number, boolean, nil, or table."""
        token = self._peek()

        if token.type == TokenType.STRING:
            self._advance()
            return token.value

        if token.type == TokenType.NUMBER:
            self._advance()
            return token.value

        if token.type == TokenType.IDENTIFIER:
            # true, false, nil
            self._advance()
            return token.value

        if token.type == TokenType.LBRACE:
            return self._parse_table()

        if token.type == TokenType.LBRACKET:
            # This shouldn't happen at value position, but handle gracefully
            return self._parse_table()

        if token.type == TokenType.EOF:
            return {}

        raise LuaParseError(
            f"Unexpected token {token.type.name} ({token.value!r})",
            token.line, token.col
        )

    def _parse_table(self) -> dict:
        """Parse a table literal { ... } into a Python dict.

        Handles both key-value pairs and array-style entries.
        Mixed tables (both key-value and array) are stored as dict with
        integer keys for array positions.
        """
        token = self._advance()  # Consume {
        start_line = token.line

        result = {}
        array_idx = 1  # Lua arrays are 1-indexed

        while True:
            token = self._peek()

            # Empty table or end of table
            if token.type == TokenType.RBRACE:
                self._advance()
                return result

            if token.type == TokenType.EOF:
                raise LuaParseError("Unterminated table", start_line, 0)

            # Parse entry: [key] = value, key = value, or value
            if token.type == TokenType.LBRACKET:
                # ["key"] = value  or  [expression] = value
                self._advance()  # Consume [
                key = self._parse_value()
                self._expect(TokenType.RBRACKET, "Expected ']' after table key")
                self._expect(TokenType.EQUALS, "Expected '=' after key")
                value = self._parse_value()
                result[str(key)] = value
            elif token.type == TokenType.IDENTIFIER and isinstance(token.value, str):
                # key = value (identifier-style key)
                # Peek ahead: if next token is '=', it's a key-value pair
                if (self.idx + 1 < len(self.tokens) and
                        self.tokens[self.idx + 1].type == TokenType.EQUALS):
                    key = token.value
                    self._advance()  # Consume key
                    self._advance()  # Consume =
                    value = self._parse_value()
                    result[key] = value
                else:
                    # Array-style value (unkeyed)
                    value = self._parse_value()
                    result[str(array_idx)] = value
                    array_idx += 1
            else:
                # Array-style value
                value = self._parse_value()
                result[str(array_idx)] = value
                array_idx += 1

            # After each entry, expect comma or RBRACE
            token = self._peek()
            if token.type == TokenType.COMMA:
                self._advance()
                # Trailing comma is allowed - check if next is }
                if self._peek().type == TokenType.RBRACE:
                    self._advance()
                    return result
            elif token.type == TokenType.RBRACE:
                self._advance()
                return result
            elif token.type == TokenType.EOF:
                raise LuaParseError("Unterminated table", start_line, 0)
            else:
                # Missing comma - some DST files have this, be lenient
                pass


# ── High-level API ─────────────────────────────────────────────────────

def parse_lua_table(text: str, filename: str = "<string>") -> dict:
    """Parse a Lua `return { ... }` table literal string into a Python dict.

    Args:
        text: The Lua source text.
        filename: Optional filename for error messages.

    Returns:
        Parsed table as a nested Python dict.

    Raises:
        LuaParseError: If the text cannot be parsed.
    """
    parser = LuaTableParser(text, filename)
    return parser.parse()


def parse_lua_value(text: str, filename: str = "<value>") -> Any:
    """Parse a single Lua value expression: a table, string, number,
    boolean, or nil.

    Unlike parse_lua_table(), which expects a full `return { ... }` table,
    this accepts any single value expression -- used for things like a mod
    config option's `default = <value>` or a choice's `data = <value>`,
    which can be a bare scalar (true, 5, "text") as well as a table.
    """
    parser = LuaTableParser(text, filename)
    return parser._parse_value()


def parse_lua_file(path: Path) -> dict:
    """Parse a Lua file containing a `return { ... }` table.

    Args:
        path: Path to the .lua file.

    Returns:
        Parsed table as a nested Python dict.
    """
    text = path.read_text(encoding="utf-8")
    return parse_lua_table(text, str(path))


# ── Lua Table Serializer ───────────────────────────────────────────────

def serialize_lua_table(data: dict, indent: int = 4, _level: int = 0) -> str:
    """Serialize a Python dict to a well-formatted Lua table string.

    Args:
        data: The dict to serialize.
        indent: Number of spaces per indentation level.
        _level: Internal recursion depth tracker.

    Returns:
        Formatted Lua table string: "return {\\n  ...\\n}"
    """
    if not data:
        return "return {}"

    pad = " " * indent
    base_pad = pad * _level
    inner_pad = pad * (_level + 1)

    # Check if this is a pure array (all keys are sequential integers starting from "1")
    keys = list(data.keys())
    is_array = all(
        k.isdigit() or (isinstance(k, str) and k.lstrip('-').isdigit())
        for k in keys
    )

    lines = []
    lines.append("{")

    for key, value in data.items():
        if isinstance(value, dict):
            serialized_val = serialize_lua_table(value, indent, _level + 1)
            # Strip the "return " prefix for nested tables
            if serialized_val.startswith("return "):
                serialized_val = serialized_val[7:]
            lines.append(f"{inner_pad}[{_lua_key(key)}] = {serialized_val},")
        elif isinstance(value, list):
            # Python list -> Lua array table
            items = []
            for item in value:
                items.append(_lua_value(item))
            inner = ", ".join(items)
            lines.append(f"{inner_pad}[{_lua_key(key)}] = {{ {inner} }},")
        elif is_array:
            # Array-style entry (no key)
            lines.append(f"{inner_pad}{_lua_value(value)},")
        else:
            lines.append(f"{inner_pad}[{_lua_key(key)}] = {_lua_value(value)},")

    # Remove trailing comma from last line
    if lines:
        lines[-1] = lines[-1].rstrip(',')

    lines.append(f"{base_pad}}}")
    result = "\n".join(lines)

    if _level == 0:
        result = "return " + result

    return result


def _lua_key(key: Any) -> str:
    """Format a key for Lua table syntax."""
    if isinstance(key, str) and key.isdigit():
        return key
    return f'"{key}"'


def _lua_value(value: Any) -> str:
    """Format a Python value as a Lua literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value)
    elif isinstance(value, str):
        # Escape special characters
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    elif value is None:
        return "nil"
    elif isinstance(value, dict):
        return serialize_lua_table(value, 4)
    elif isinstance(value, list):
        items = ", ".join(_lua_value(v) for v in value)
        return f"{{ {items} }}"
    else:
        return f'"{value}"'


def serialize_lua_file(data: dict, path: Path, indent: int = 4) -> None:
    """Serialize a Python dict and write to a Lua file.

    Args:
        data: The dict to serialize.
        path: Destination file path.
        indent: Spaces per indentation level.
    """
    text = serialize_lua_table(data, indent)
    path.write_text(text, encoding="utf-8")
