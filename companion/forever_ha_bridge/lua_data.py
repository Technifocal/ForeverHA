"""Parse only literal Lua assignments. Never evaluate a SavedVariables file."""
import math
import re
import base64

IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
LONG = re.compile(r"\[(=*)\[")
FIELD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\s*=")
NUMBER = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)")


class LuaDataError(ValueError):
    pass


class Parser:
    def __init__(self, text: str):
        if len(text) > 64 * 1024 * 1024:
            raise LuaDataError("SavedVariables exceeds 64 MiB")
        self.text, self.pos, self.values = text.lstrip("\ufeff"), 0, 0

    def skip(self):
        while self.pos < len(self.text):
            if self.text[self.pos].isspace():
                self.pos += 1
            elif self.text.startswith("--", self.pos):
                self.pos += 2
                if self.long_string(peek=True):
                    self.long_string()
                else:
                    end = self.text.find("\n", self.pos)
                    self.pos = len(self.text) if end < 0 else end + 1
            else:
                break

    def take(self, s):
        self.skip()
        if self.text.startswith(s, self.pos):
            self.pos += len(s)
            return True
        return False

    def expect(self, s):
        if not self.take(s):
            raise LuaDataError(f"Expected {s!r} at offset {self.pos}")

    def ident(self):
        self.skip()
        match = IDENT.match(self.text, self.pos)
        if not match:
            raise LuaDataError(f"Expected identifier at offset {self.pos}")
        self.pos += len(match[0])
        return match[0]

    def long_string(self, peek=False):
        match = LONG.match(self.text, self.pos)
        if not match:
            return False if peek else None
        if peek:
            return True
        start = self.pos + len(match[0])
        end = self.text.find("]" + match[1] + "]", start)
        if end < 0:
            raise LuaDataError("Incomplete long string")
        self.pos = end + len(match[1]) + 2
        value = self.text[start:end]
        return value[1:] if value.startswith("\n") else value

    def string(self):
        quote = self.text[self.pos]
        self.pos += 1
        out = bytearray()
        escapes = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
        while self.pos < len(self.text):
            c = self.text[self.pos]
            self.pos += 1
            if c == quote:
                try:
                    return out.decode("utf-8")
                except UnicodeDecodeError:
                    return {"__lua_bytes_base64": base64.b64encode(out).decode("ascii")}
            if c in "\r\n":
                raise LuaDataError("Unescaped newline in quoted string")
            if c != "\\":
                out.extend(c.encode("utf-8"))
                continue
            if self.pos >= len(self.text):
                break
            c = self.text[self.pos]
            self.pos += 1
            if c.isdigit() and c.isascii():
                digits = c
                while len(digits) < 3 and self.pos < len(self.text) and self.text[self.pos] in "0123456789":
                    digits += self.text[self.pos]
                    self.pos += 1
                if int(digits) > 255:
                    raise LuaDataError("Invalid decimal escape")
                out.append(int(digits))
            elif c == "x":
                digits = self.text[self.pos:self.pos + 2]
                if not re.fullmatch(r"[0-9a-fA-F]{2}", digits):
                    raise LuaDataError("Invalid hex escape")
                self.pos += 2
                out.append(int(digits, 16))
            elif c == "z":
                while self.pos < len(self.text) and self.text[self.pos].isspace():
                    self.pos += 1
            elif c in escapes:
                out.extend(escapes[c].encode())
            elif c in "\\\"'\n":
                out.extend(c.encode())
            else:
                raise LuaDataError("Unsupported string escape")
        raise LuaDataError("Incomplete string")

    def value(self, depth=0):
        self.values += 1
        if depth > 60 or self.values > 2_000_000:
            raise LuaDataError("Lua literal resource limit exceeded")
        self.skip()
        if self.pos >= len(self.text):
            raise LuaDataError("Incomplete value")
        c = self.text[self.pos]
        if c in "\"'":
            return self.string()
        if self.long_string(peek=True):
            return self.long_string()
        if c == "{":
            self.pos += 1
            table, index = {}, 1
            while not self.take("}"):
                self.skip()
                if self.take("["):
                    key = self.value(depth + 1)
                    if type(key) not in (str, int, float):
                        raise LuaDataError("Unsupported table key")
                    self.expect("]")
                    self.expect("=")
                    item = self.value(depth + 1)
                else:
                    save = self.pos
                    match = FIELD.match(self.text, self.pos)
                    if match:
                        key = self.ident()
                        self.expect("=")
                        item = self.value(depth + 1)
                    else:
                        self.pos = save
                        key, item = index, self.value(depth + 1)
                        index += 1
                table[key] = item
                if not self.take(",") and not self.take(";"):
                    self.expect("}")
                    break
            if table and set(table) == set(range(1, len(table) + 1)):
                return [table[i] for i in range(1, len(table) + 1)]
            return {str(k): v for k, v in table.items()}
        match = NUMBER.match(self.text, self.pos)
        if match:
            self.pos += len(match[0])
            token = match[0]
            value = int(token, 16) if "x" in token.lower() else float(token)
            if not math.isfinite(value):
                raise LuaDataError("Non-finite number")
            return int(value) if value == int(value) else value
        name = self.ident()
        if name in ("true", "false", "nil"):
            return {"true": True, "false": False, "nil": None}[name]
        raise LuaDataError("Executable Lua or unsupported literal")

    def parse(self):
        assignments = {}
        self.skip()
        while self.pos < len(self.text):
            name = self.ident()
            if name != "ForeverHADB":
                raise LuaDataError("Unexpected global assignment")
            self.expect("=")
            if name in assignments:
                raise LuaDataError("Duplicate global assignment")
            assignments[name] = self.value()
            self.take(";")
            self.skip()
        db = assignments.get("ForeverHADB")
        if not isinstance(db, dict) or db.get("schema") != 1:
            raise LuaDataError("Missing or unsupported ForeverHADB schema")
        return db


def loads(text):
    return Parser(text).parse()
