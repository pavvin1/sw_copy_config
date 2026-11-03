#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Таке running-config с D-Link DGS-1510 (CLI в стиле Cisco) по telnet.
Тaken from variables DLINK_USER / DLINK_PASSWORD.

"""

import argparse
import os
import re
import socket
import sys
import time

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class TelnetSession:
    def __init__(self, host, port, timeout):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._carry = b""
        self.text = ""
        self.match = None

    def _strip(self, data):
        data = self._carry + data
        self._carry = b""
        out = bytearray()
        i = 0
        while i < len(data):
            byte = data[i]
            if byte != IAC:
                out.append(byte)
                i += 1
                continue
            if i + 1 >= len(data):
                self._carry = data[i:]
                break
            cmd = data[i + 1]
            if cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(data):
                    self._carry = data[i:]
                    break
                option = data[i + 2]
                if cmd == DO:
                    self.sock.sendall(bytes([IAC, WONT, option]))
                elif cmd == WILL:
                    self.sock.sendall(bytes([IAC, DONT, option]))
                i += 3
            elif cmd == SB:
                end = data.find(bytes([IAC, SE]), i)
                if end == -1:
                    self._carry = data[i:]
                    break
                i = end + 2
            else:
                i += 2
        return bytes(out).decode("utf-8", "replace")

    def read_until(self, patterns, timeout):
        deadline = time.time() + timeout
        while True:
            for index, pattern in enumerate(patterns):
                found = pattern.search(self.text)
                if found:
                    self.match = found
                    return index
            if time.time() > deadline:
                raise TimeoutError("no responce %s с" % timeout)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                raise TimeoutError("timeout wait responce from sw")
            if not chunk:
                raise ConnectionError("sw is close telnet-session")
            self.text += self._strip(chunk)

    def send(self, line):
        self.sock.sendall(line.encode("utf-8") + b"\r\n")
        self.text = ""

    def send_raw(self, data):
        self.sock.sendall(data)
        self.text = ""

    def flush(self):
        data, self.text = self.text, ""
        return data

    def close(self):
        try:
            self.sock.sendall(b"exit\r\n")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=23)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    user = os.environ.get("DLINK_USER")
    password = os.environ.get("DLINK_PASSWORD")
    if not user or password is None:
        raise RuntimeError("the variables DLINK_USER / DLINK_PASSWORD")

    login_re = re.compile(r"(?i)user\s*name\s*:|login\s*:")
    password_re = re.compile(r"(?i)pass\s*word\s*:")
    error_re = re.compile(r"(?i)(incorrect|access denied|login invalid|fail)")
    any_prompt_re = re.compile(r"^([\w.\-]+)#[ \t]*$", re.M)
    pager_re = re.compile(r"(CTRL\+C[^\r\n]*a\s+All|--\s*More\s*--)")

    session = TelnetSession(args.host, args.port, args.timeout)
    collected = ""
    try:
        session.read_until([login_re], args.timeout)
        session.send(user)
        session.read_until([password_re], args.timeout)
        session.send(password)

        if session.read_until([any_prompt_re, error_re], args.timeout) != 0:
            raise RuntimeError("cant access: wronng login or pass")

        prompt = session.match.group(1) + "#"
        prompt_re = re.compile(re.escape(prompt) + r"[ \t]*$", re.M)

        session.send("terminal length 0")
        session.read_until([prompt_re], args.timeout)

        session.send("show running-config")
        while True:
            matched = session.read_until([prompt_re, pager_re], args.timeout)
            collected += session.flush()
            if matched == 0:
                break
            session.send_raw(b"a")
    finally:
        session.close()

    collected = ANSI.sub("", collected.replace("\r\n", "\n").replace("\r", "\n"))
    collected = re.sub(r"CTRL\+C[^\n]*", "", collected)
    collected = re.sub(r"--\s*More\s*--[^\n]*", "", collected)

    start = collected.find("Building configuration")
    if start == -1:
        start = collected.find("Current configuration")
    if start == -1:
        raise RuntimeError("in responce no show running-config")
    body = collected[start:]
    body = re.sub(re.escape(prompt) + r"[ \t]*$", "", body, flags=re.M)
    body = "\n".join(line.rstrip() for line in body.splitlines()).strip() + "\n"

    if len(body.splitlines()) < 10:
        raise RuntimeError("config is to short")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    print("OK %d lines" % len(body.splitlines()))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(1)
