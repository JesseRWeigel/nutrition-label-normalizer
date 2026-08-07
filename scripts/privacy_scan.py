#!/usr/bin/env python3
"""Look for credentials and machine paths in the tracked files, and prove the scan can see.

Four things here exist because a scan that reads nothing reports exactly like a clean tree:

  A POSITIVE CONTROL. Credential-shaped strings are appended to a real tracked file, the
  whole sweep is run again, and every one of them has to be found. If any is missed, the
  scanner is broken and the clean result it printed a moment earlier meant nothing. The
  file is restored afterwards either way, and the restoration is checked.

  A FLOOR ON THE FILE COUNT. Before the first commit ``git ls-files`` returns nothing at
  all, and a sweep over nothing passes instantly without opening a file.

  ITS OWN READER. Every file is opened as bytes here rather than handed to grep. One NUL
  byte makes git and grep classify a file as binary, and ``grep -I`` then skips it in
  silence, so a secret in that file is invisible. That has happened in this workspace, and
  a real token committed into such a file survived a scan that reported clean.

  NO SELF EXCLUSION. This file is scanned like every other one, and the scan fails if it
  was not. The patterns are assembled from string fragments at run time so this file does
  not contain a complete credential-shaped string for its own sweep to find. The obvious
  alternative, exempting the scanner's own path, buys silence on the pattern list at the
  price of going blind to a real leak in the same file.
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONTROL_FILE = os.path.join("fixtures", "privacy_control.txt")
SELF = os.path.join("scripts", "privacy_scan.py")

# The corpus is 18 label fixtures, a package, a test suite and the scripts. If the count
# drops near zero the repository is not what this scan assumes it is.
MIN_TRACKED = 30

USER = os.environ.get("SUDO_USER") or getpass.getuser()

PATTERNS = [
    ("openai style key", re.compile((r"sk-" + r"[A-Za-z0-9]{20,}").encode())),
    ("github token", re.compile((r"gh" + r"[pousr]_" + r"[A-Za-z0-9]{36}").encode())),
    # AWS key ids are uppercase by definition. A case-insensitive version of this matches
    # ordinary base64 and turns every embedded image into a false alarm.
    ("aws access key id", re.compile((r"AK" + r"IA" + r"[0-9A-Z]{16}").encode())),
    ("slack token", re.compile((r"xox" + r"[baprs]-" + r"[A-Za-z0-9-]{20,}").encode())),
    ("google api key", re.compile((r"AI" + r"za" + r"[0-9A-Za-z_-]{35}").encode())),
    ("private key block", re.compile((r"-----BEGIN [A-Z ]*" + r"PRIVATE KEY-----").encode())),
    ("openrouter key", re.compile((r"sk-or-" + r"v1-" + r"[a-f0-9]{40,}").encode())),
    ("this machine's home directory", re.compile(re.escape(f"/home/{USER}").encode())),
    ("a macos home directory", re.compile(re.escape(f"/Users/{USER}").encode())),
    ("a projects directory shorthand", re.compile((r"~/" + r"Projects").encode())),
]


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", ROOT, "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [p.decode() for p in out.split(b"\0") if p]


def committed_blobs_with_nul(files: list[str]):
    """Which tracked files hold a NUL byte in the object git has, not just on disk.

    Returns None when the objects could not be read, so "could not check" stays a
    different value from "checked, found nothing". Collapsing those two is exactly how an
    advisory tool goes quiet, and this project is entirely advisory.
    """
    if not files:
        return []
    spec = "".join(f":{path}\n" for path in files).encode()
    proc = subprocess.run(
        ["git", "-C", ROOT, "cat-file", "--batch"], input=spec, capture_output=True
    )
    if proc.returncode != 0:
        return None
    out = proc.stdout
    found = []
    pos = 0
    for path in files:
        newline = out.find(b"\n", pos)
        if newline < 0:
            return None
        header = out[pos:newline].split(b" ")
        if len(header) < 3:
            # "<spec> missing", for a path staged for deletion. Nothing to read.
            pos = newline + 1
            continue
        size = int(header[2])
        body = out[newline + 1: newline + 1 + size]
        if b"\0" in body:
            found.append(path)
        pos = newline + 1 + size + 1
    return found


def sweep(files: list[str]):
    hits = []
    opened = 0
    read = 0
    seen = set()
    for rel in files:
        seen.add(rel)
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as error:
            hits.append((rel, "unreadable", str(error)))
            continue
        opened += 1
        read += len(data)
        for label, pattern in PATTERNS:
            match = pattern.search(data)
            if match:
                line = data[: match.start()].count(b"\n") + 1
                hits.append((f"{rel}:{line}", label,
                             match.group(0).decode("utf-8", "replace")[:60]))
    return hits, opened, read, seen


def main() -> int:
    failures = 0

    def bad(message: str) -> None:
        nonlocal failures
        failures += 1
        print(f"  FAIL  {message}")

    files = tracked_files()
    if len(files) < MIN_TRACKED:
        bad(f"only {len(files)} tracked files. Either nothing is committed yet, in which "
            f"case this scan passes without opening anything, or the repository is not "
            f"what it seems.")

    hits, opened, read, seen = sweep(files)
    if hits:
        for where, label, sample in hits:
            bad(f"{label} in {where}: {sample}")
    elif read == 0:
        bad(f"{opened} files were opened and zero bytes came back, so nothing was read")
    else:
        # The byte total is deliberately not printed. This output gets pasted into the
        # README, the README is tracked, and a total that counts it moves every time the
        # paste changes. A number that can never settle cannot be diffed by a reviewer.
        print(f"  ok    {opened} tracked files opened and read in full, "
              f"nothing credential shaped")

    if SELF not in seen:
        bad(f"{SELF} was not among the files scanned, so the scanner exempts itself")
    else:
        print(f"  ok    {SELF} was scanned like every other file, with no self exclusion")

    with_nul = []
    for rel in files:
        path = os.path.join(ROOT, rel)
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                if b"\0" in handle.read():
                    with_nul.append(rel)
    if with_nul:
        bad(f"tracked files containing a NUL byte, invisible to a grep based scan: {with_nul}")
    else:
        print("  ok    no tracked file contains a NUL byte, so a grep based scan would "
              "see them all")

    committed = committed_blobs_with_nul(files)
    if committed is None:
        bad("could not read the committed blobs, so only the working tree was checked "
            "for NUL bytes")
    elif committed:
        bad(f"files whose COMMITTED contents contain a NUL byte, which every grep based "
            f"scan skips even though the working copy looks fine: {committed}")
    else:
        print("  ok    no committed blob contains a NUL byte either, not just the working copy")

    # The page is the artefact that gets published, so it gets its own look. It is built
    # by a script that reads fixtures from paths given on this machine, which is exactly
    # the shape of mistake that puts a home directory into a public file.
    page = os.path.join(ROOT, "docs", "index.html")
    if not os.path.isfile(page):
        bad("docs/index.html does not exist, so the published page could not be checked")
    else:
        with open(page, "rb") as handle:
            data = handle.read()
        leaked = [label for label, pattern in PATTERNS if pattern.search(data)]
        if leaked:
            bad(f"the published page contains: {', '.join(leaked)}")
        else:
            print("  ok    the published page has no machine path or credential in it")
        if re.search(rb"/home/[a-z]", data) or re.search(rb"[A-Z]:\\Users", data):
            bad("the published page contains an absolute home directory path")

    # ---- positive control ---------------------------------------------------------
    control = os.path.join(ROOT, CONTROL_FILE)
    if CONTROL_FILE not in files:
        bad(f"{CONTROL_FILE} is not tracked, so the positive control would not have been "
            f"read by the sweep above and proves nothing")
    else:
        with open(control, "rb") as handle:
            original = handle.read()
        planted = [
            ("github token", "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"),
            ("aws access key id", "AK" + "IA" + "IOSFODNN7EXAMPLE"),
            ("openai style key", "sk-" + "proj" + "A1b2C3d4E5f6G7h8I9j0"),
            ("this machine's home directory", f"/home/{USER}/somewhere/private"),
        ]
        try:
            with open(control, "wb") as handle:
                handle.write(original)
                for _, value in planted:
                    handle.write(f"planted: {value}\n".encode())
            control_hits, _, control_read, _ = sweep(files)
            found = {label for _, label, _ in control_hits}
            missing = [label for label, _ in planted if label not in found]
            if control_read == 0:
                bad("the positive control run read zero bytes")
            elif missing:
                bad("the sweep did NOT find planted " + ", ".join(missing)
                    + f" in {CONTROL_FILE}, so its clean result above is meaningless")
            else:
                print(f"  ok    positive control: {len(planted)} planted strings all found "
                      f"in {CONTROL_FILE}, so the sweep really reads tracked files")
        finally:
            with open(control, "wb") as handle:
                handle.write(original)
        with open(control, "rb") as handle:
            if handle.read() != original:
                bad(f"{CONTROL_FILE} was not restored after the positive control")

    if failures:
        print(f"{failures} privacy check(s) failed")
        return 1
    print("privacy scan clean, and proved able to find what it looks for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
