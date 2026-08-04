"""纯 Python 实现的 DST 配置文件 Lua 表解析器。

DST 只用一个受限的 Lua 子集：仅 `return { ... }` 这种数据表字面量。
本解析器只处理这个子集，不依赖任何 Lua 运行时。
"""

import re
from enum import Enum, auto
from pathlib import Path
from typing import Any


class LuaParseError(Exception):
    """Lua 解析失败时抛出的异常。"""

    def __init__(self, message: str, line: int = 0, col: int = 0):
        loc = f" at line {line}, col {col}" if line > 0 else ""
        super().__init__(f"{message}{loc}")
        self.line = line
        self.col = col


# ── 词法分析器 ──────────────────────────────────────────────────────────

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
    IDENTIFIER = auto()   # 包含 true/false 关键字
    EOF = auto()


class Token:
    def __init__(self, type_: TokenType, value: Any, line: int, col: int):
        self.type = type_
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.col})"


# Lua 词法单元的正则表达式
_LUA_STRING_RE = re.compile(r'''
    "(?:[^"\\]|\\.)*"           # 双引号字符串
    |'(?:[^'\\]|\\.)*'          # 单引号字符串
    |\[=*\[.*?\]=*\]            # 长方括号字符串（非贪婪匹配以支持嵌套）
''', re.DOTALL | re.VERBOSE)

_LUA_NUMBER_RE = re.compile(r'''
    -?(?:0x[0-9a-fA-F]+         # 十六进制整数
    |\d+\.?\d*(?:[eE][+-]?\d+)? # 十进制数，可带小数部分/指数
    |\.\d+(?:[eE][+-]?\d+)?)    # 以小数点开头的十进制数
''', re.VERBOSE)

_LUA_IDENT_RE = re.compile(r'[a-zA-Z_][a-zA-Z0-9_]*')


class LuaTokenizer:
    """对 Lua 表字面量字符串做词法分析。"""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1
        self.col = 1

    def tokenize(self) -> list[Token]:
        tokens = []
        while self.pos < len(self.text):
            c = self.text[self.pos]

            # 空白字符
            if c in ' \t\r\n':
                if c == '\n':
                    self.line += 1
                    self.col = 1
                else:
                    self.col += 1
                self.pos += 1
                continue

            # 注释：-- 到行尾，或者 --[[ ... ]] 块注释
            if c == '-' and self.pos + 1 < len(self.text) and self.text[self.pos + 1] == '-':
                self._skip_comment()
                continue

            # 字符串
            if c in '"\'':
                token = self._read_string()
                tokens.append(token)
                continue

            # 长方括号字符串 [[...]] 或 [=[...]=]
            if c == '[':
                token = self._read_long_string()
                if token:
                    tokens.append(token)
                    continue
                # 不是长字符串，只是普通的 [
                tokens.append(Token(TokenType.LBRACKET, '[', self.line, self.col))
                self.pos += 1
                self.col += 1
                continue

            # 数字。裸的前导小数点（".01"，不带符号）本身也是合法的 Lua 十进
            # 制字面量，不只是 "-.01" 这种带符号的形式——不加这个分支，"."
            # 会落到下面"未知字符跳过"那段逻辑里被悄悄丢掉，把 ".01" 变成
            # 错误的 "01"。
            if c.isdigit() or \
               (c == '.' and self.pos + 1 < len(self.text) and self.text[self.pos + 1].isdigit()) or \
               (c == '-' and self.pos + 1 < len(self.text) and
                               (self.text[self.pos + 1].isdigit() or self.text[self.pos + 1] == '.')):
                token = self._read_number()
                tokens.append(token)
                continue

            # 标识符和关键字
            if c.isalpha() or c == '_':
                token = self._read_identifier()
                tokens.append(token)
                continue

            # 单字符词法单元
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

            # 未知字符，直接跳过
            self.pos += 1
            self.col += 1

        tokens.append(Token(TokenType.EOF, None, self.line, self.col))
        return tokens

    def _skip_comment(self):
        """跳过 Lua 注释（-- 到行尾，或者 --[[ ... ]] 块注释）。"""
        self.pos += 2  # 跳过 --
        self.col += 2

        # 块注释 --[[ ... ]]
        if self.pos < len(self.text) and self.text[self.pos] == '[':
            # 找匹配的 ]]
            if self.pos + 1 < len(self.text) and self.text[self.pos + 1] == '[':
                # --[[ 风格
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
            # --[=[ ... ]=] 风格
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

        # 行注释：跳到行尾
        while self.pos < len(self.text) and self.text[self.pos] != '\n':
            self.pos += 1
            self.col += 1

    def _read_string(self) -> Token:
        """读取一个带引号的字符串（单引号或双引号）。"""
        quote = self.text[self.pos]
        start_line, start_col = self.line, self.col
        self.pos += 1
        self.col += 1
        chars = []
        while self.pos < len(self.text):
            c = self.text[self.pos]
            if c == '\\' and self.pos + 1 < len(self.text):
                # 转义序列
                next_c = self.text[self.pos + 1]
                escape_map = {
                    'n': '\n', 'r': '\r', 't': '\t', '\\': '\\',
                    '"': '"', "'": "'", 'a': '\a', 'b': '\b',
                    'f': '\f', 'v': '\v',
                }
                if next_c in escape_map:
                    chars.append(escape_map[next_c])
                elif next_c == '\n':
                    # 反斜杠+换行的续行写法
                    self.line += 1
                    self.col = 0
                elif next_c == 'x' and self.pos + 3 < len(self.text):
                    # \xNN 十六进制转义
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
                    # \d{1,3} 十进制转义（简化实现）
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
        """尝试读取一个长方括号字符串 [[...]] 或 [=[...]=]。"""
        start_pos = self.pos
        start_line, start_col = self.line, self.col

        # 数一下有几个等号
        eq_count = 0
        pos = self.pos + 1
        while pos < len(self.text) and self.text[pos] == '=':
            eq_count += 1
            pos += 1

        if pos >= len(self.text) or self.text[pos] != '[':
            return None  # 不是长字符串

        # 这是一个长方括号字符串
        closer = ']' + '=' * eq_count + ']'

        # 跳过开头的方括号序列
        self.pos = pos + 1
        self.col += pos - start_pos + 1

        # 找结束的方括号
        end_idx = self.text.find(closer, self.pos)
        if end_idx == -1:
            raise LuaParseError("Unterminated long string", start_line, start_col)

        content = self.text[self.pos:end_idx]

        # 更新位置（长字符串是原样内容，没有转义）
        newlines = content.count('\n')
        self.line += newlines
        if newlines > 0:
            self.col = len(content.split('\n')[-1]) + len(closer) + 1
        else:
            self.col += len(content) + len(closer)
        self.pos = end_idx + len(closer)

        # 按 Lua 惯例，去掉开头的换行（如果有的话）
        if content.startswith('\n'):
            content = content[1:]
        elif content.startswith('\r\n'):
            content = content[2:]

        return Token(TokenType.STRING, content, start_line, start_col)

    def _read_number(self) -> Token:
        """读取一个数字字面量。"""
        start_line, start_col = self.line, self.col
        match = _LUA_NUMBER_RE.match(self.text, self.pos)
        if not match:
            raise LuaParseError("Invalid number", start_line, start_col)

        num_str = match.group(0)

        # 负数在 Lua 表里是没问题的：{ x=-1 }

        # 解析出实际的值
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
        """读取一个标识符或关键字（true、false、nil、return）。"""
        start_line, start_col = self.line, self.col
        match = _LUA_IDENT_RE.match(self.text, self.pos)
        if not match:
            raise LuaParseError("Invalid identifier", start_line, start_col)

        name = match.group(0)
        self.pos += len(name)
        self.col += len(name)

        # 关键字处理
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


# ── 解析器 ─────────────────────────────────────────────────────────────

class LuaTableParser:
    """Lua 表字面量的递归下降解析器。"""

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
        """解析一个 Lua `return { ... }` 表达式，把表转换成 Python dict 返回。

        同时兼容省略 `return` 关键字、直接以 `{` 开头的文件。
        """
        token = self._peek()

        # 可选的 'return' 关键字
        if token.type == TokenType.RETURN:
            self._advance()

        # 解析表字面量
        result = self._parse_table()
        return result

    def _parse_value(self) -> Any:
        """解析单个值：字符串、数字、布尔值、nil，或者表。"""
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
            # 理论上不该出现在值的位置，兜底处理一下
            return self._parse_table()

        if token.type == TokenType.EOF:
            return {}

        raise LuaParseError(
            f"Unexpected token {token.type.name} ({token.value!r})",
            token.line, token.col
        )

    def _parse_table(self) -> dict:
        """把一个表字面量 { ... } 解析成 Python dict。

        同时处理键值对和数组风格的条目：混合表（既有键值对又有数组）里，
        数组位置的下标会作为字符串形式的整数 key 存进同一个 dict。
        """
        token = self._advance()  # 吃掉 {
        start_line = token.line

        result = {}
        array_idx = 1  # Lua 数组从 1 开始编号

        while True:
            token = self._peek()

            # 空表，或者表结束
            if token.type == TokenType.RBRACE:
                self._advance()
                return result

            if token.type == TokenType.EOF:
                raise LuaParseError("Unterminated table", start_line, 0)

            # 解析一个条目：[key] = value、key = value，或者裸值
            if token.type == TokenType.LBRACKET:
                # ["key"] = value 或者 [expression] = value
                self._advance()  # 吃掉 [
                key = self._parse_value()
                self._expect(TokenType.RBRACKET, "Expected ']' after table key")
                self._expect(TokenType.EQUALS, "Expected '=' after key")
                value = self._parse_value()
                result[str(key)] = value
            elif token.type == TokenType.IDENTIFIER and isinstance(token.value, str):
                # key = value（标识符形式的 key）
                # 往前瞧一个 token：如果下一个是 '='，说明这是键值对
                if (self.idx + 1 < len(self.tokens) and
                        self.tokens[self.idx + 1].type == TokenType.EQUALS):
                    key = token.value
                    self._advance()  # 吃掉 key
                    self._advance()  # 吃掉 =
                    value = self._parse_value()
                    result[key] = value
                else:
                    # 数组风格的值（不带 key）
                    value = self._parse_value()
                    result[str(array_idx)] = value
                    array_idx += 1
            else:
                # 数组风格的值
                value = self._parse_value()
                result[str(array_idx)] = value
                array_idx += 1

            # 每个条目后面应该跟逗号或者 }
            token = self._peek()
            if token.type == TokenType.COMMA:
                self._advance()
                # 允许末尾多一个逗号——瞧瞧下一个是不是 }
                if self._peek().type == TokenType.RBRACE:
                    self._advance()
                    return result
            elif token.type == TokenType.RBRACE:
                self._advance()
                return result
            elif token.type == TokenType.EOF:
                raise LuaParseError("Unterminated table", start_line, 0)
            else:
                # 缺逗号——有些 DST 文件就是这么写的，宽容处理
                pass


# ── 对外的高层 API ─────────────────────────────────────────────────────

def parse_lua_table(text: str, filename: str = "<string>") -> dict:
    """把一个 Lua `return { ... }` 表字面量字符串解析成 Python dict。

    Args:
        text: Lua 源码文本。
        filename: 可选，出错信息里显示的文件名。

    Returns:
        解析出的嵌套 Python dict。

    Raises:
        LuaParseError: 文本无法解析时抛出。
    """
    parser = LuaTableParser(text, filename)
    return parser.parse()


def parse_lua_value(text: str, filename: str = "<value>") -> Any:
    """解析单个 Lua 值表达式：表、字符串、数字、布尔值，或 nil。

    跟要求完整 `return { ... }` 表的 parse_lua_table() 不同，这个函数接受
    任意单个值表达式——用在诸如 mod 配置项的 `default = <value>`、选项的
    `data = <value>` 这类场景，值既可能是裸标量（true、5、"text"），也
    可能是一个表。
    """
    parser = LuaTableParser(text, filename)
    return parser._parse_value()


def parse_lua_file(path: Path) -> dict:
    """解析一个包含 `return { ... }` 表的 Lua 文件。

    Args:
        path: .lua 文件路径。

    Returns:
        解析出的嵌套 Python dict。
    """
    text = path.read_text(encoding="utf-8")
    return parse_lua_table(text, str(path))


# ── Lua 表序列化 ───────────────────────────────────────────────────────

def serialize_lua_table(data: dict, indent: int = 4, _level: int = 0) -> str:
    """把 Python dict 序列化成格式良好的 Lua 表字符串。

    Args:
        data: 要序列化的 dict。
        indent: 每级缩进的空格数。
        _level: 内部用的递归深度计数。

    Returns:
        格式化后的 Lua 表字符串："return {\\n  ...\\n}"
    """
    if not data:
        return "return {}"

    pad = " " * indent
    base_pad = pad * _level
    inner_pad = pad * (_level + 1)

    # 判断是不是纯数组（所有 key 是不是从 "1" 开始的连续整数）
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
            # 嵌套表要去掉 "return " 前缀
            if serialized_val.startswith("return "):
                serialized_val = serialized_val[7:]
            lines.append(f"{inner_pad}[{_lua_key(key)}] = {serialized_val},")
        elif isinstance(value, list):
            # Python list -> Lua 数组表
            items = []
            for item in value:
                items.append(_lua_value(item))
            inner = ", ".join(items)
            lines.append(f"{inner_pad}[{_lua_key(key)}] = {{ {inner} }},")
        elif is_array:
            # 数组风格条目（不带 key）
            lines.append(f"{inner_pad}{_lua_value(value)},")
        else:
            lines.append(f"{inner_pad}[{_lua_key(key)}] = {_lua_value(value)},")

    # 去掉最后一行末尾多余的逗号
    if lines:
        lines[-1] = lines[-1].rstrip(',')

    lines.append(f"{base_pad}}}")
    result = "\n".join(lines)

    if _level == 0:
        result = "return " + result

    return result


def _lua_key(key: Any) -> str:
    """把 key 格式化成 Lua 表语法。"""
    if isinstance(key, str) and key.isdigit():
        return key
    return f'"{key}"'


def _lua_value(value: Any) -> str:
    """把 Python 值格式化成 Lua 字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value)
    elif isinstance(value, str):
        # 转义特殊字符
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


