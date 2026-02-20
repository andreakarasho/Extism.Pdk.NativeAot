"""
Clips a component-encumbered WASM module down to a bare module.

Runs `wasm-tools component unbundle` to strip the Component Model wrapper,
converts to WAT, optionally stubs WASI imports, converts namespaces, and compiles back to WASM.

Based on the original clip.lua by Pspritechologist.

Usage:
    python clip.py input.wasm output.wasm
    python clip.py --wat input.wasm output.wat
    python clip.py --pre input.wasm output.wasm
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile


def kebab_to_snake(name: str) -> str:
    return name.replace('-', '_')


def find_balanced_parens(text: str, start: int) -> int:
    """Find the position after the matching closing paren for the opening paren at 'start'."""
    assert text[start] == '('
    depth = 1
    i = start + 1
    while i < len(text) and depth > 0:
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
        i += 1
    return i  # position after the closing paren


def namespace_conversion(content: str, namespaces: list[str]) -> str:
    """Convert function names from kebab-case to snake_case within specified namespaces."""
    for ns in namespaces:
        ns_snake = kebab_to_snake(ns)
        pattern = re.compile(
            r'\(\s*import\s*"' + re.escape(ns) + r'"\s*"([^"]*)"'
        )

        def repl(m, _ns_snake=ns_snake):
            func_name = kebab_to_snake(m.group(1))
            return f'(import "{_ns_snake}" "{func_name}"'

        content = pattern.sub(repl, content)
    return content


def export_name_conversion(content: str) -> str:
    """Convert export function names from kebab-case to snake_case."""
    pattern = re.compile(r'\(\s*export\s*"([^"]*)"')

    def repl(m):
        name = kebab_to_snake(m.group(1))
        return f'(export "{name}"'

    return pattern.sub(repl, content)


def _default_return_instrs(func_decl: str, full_content: str) -> str | None:
    """Generate default return instructions for a function's result types."""
    result_match = re.findall(r'\(result\s+([^)]+)\)', func_decl)

    # If no inline result, try resolving (type N) reference
    if not result_match:
        type_ref = re.search(r'\(type\s+(\d+)\)', func_decl)
        if type_ref:
            type_idx = type_ref.group(1)
            # Find the type definition: (type (;N;) (func ...))
            marker = f'(;{type_idx};)'
            pos = full_content.find(marker)
            if pos >= 0:
                # Walk back to find the opening (type
                line_start = full_content.rfind('(type', max(0, pos - 20), pos)
                if line_start >= 0:
                    type_end = find_balanced_parens(full_content, line_start)
                    type_text = full_content[line_start:type_end]
                    result_match = re.findall(r'\(result\s+([^)]+)\)', type_text)

    if not result_match:
        return None
    # Collect all result types
    result_types = []
    for rm in result_match:
        result_types.extend(rm.split())
    defaults = {'i32': 'i32.const 0', 'i64': 'i64.const 0', 'f32': 'f32.const 0', 'f64': 'f64.const 0'}
    instrs = [defaults.get(t, 'i32.const 0') for t in result_types]
    return '\n    '.join(instrs)


def stub_import(content: str, ns_pattern: str, func_name: str, repl_instr: str | None, verbose_prefix: str = '') -> str:
    """Find an import matching ns_pattern and func_name, replace with stub func definition."""
    search_pat = re.compile(
        r'\(\s*import\s*"' + ns_pattern + r'"\s*"' + re.escape(func_name) + r'"'
    )

    match = search_pat.search(content)
    if not match:
        return content

    import_start = match.start()
    import_end = find_balanced_parens(content, import_start)

    import_text = content[import_start:import_end]
    func_start_rel = import_text.index('(func')
    func_start_abs = import_start + func_start_rel
    func_end = find_balanced_parens(content, func_start_abs)

    func_decl = content[func_start_abs:func_end]

    effective_instr = repl_instr
    if effective_instr is None:
        effective_instr = _default_return_instrs(func_decl, content)

    if effective_instr:
        stub = func_decl[:-1] + '\n    ' + effective_instr + '\n  )'
    else:
        stub = func_decl

    ns_display = match.group(0)[:60]
    print(f'    Stubbed: {ns_display}... -> stub{" (" + (repl_instr or effective_instr or "noop") + ")" }', file=sys.stderr)

    content = content[:import_start] + '  ' + stub + content[import_end:]
    return content


def ensure_func_import(content: str, module: str, name: str, func_decl: str) -> str:
    """Ensure a core wasm import exists; if missing, insert it before table/memory/func declarations."""
    marker = f'(import "{module}" "{name}"'
    if marker in content:
        return content

    insertion = f'  (import "{module}" "{name}" {func_decl})\n'

    # Prefer placing new imports before the first WASI import. Those WASI
    # imports are typically replaced with local shim funcs later in this pass.
    first_wasi_import = re.search(r'^  \(import "wasi:[^"]+"', content, re.MULTILINE)
    if first_wasi_import is not None:
        insert_at = first_wasi_import.start()
        prefix = '\n' if insert_at > 0 and content[insert_at - 1] != '\n' else ''
        return content[:insert_at] + prefix + insertion + content[insert_at:]

    # Otherwise place new imports directly after the top-level import block.
    insert_at = -1
    last_import_start = None
    first_non_type_or_import = None
    decl_pat = re.compile(r'^  \((\w+)\b', re.MULTILINE)
    for match in decl_pat.finditer(content):
        kind = match.group(1)
        if kind == 'type':
            continue
        if kind == 'import':
            last_import_start = match.start() + 2  # skip leading two spaces
            continue
        first_non_type_or_import = match.start()
        break

    if last_import_start is not None:
        insert_at = find_balanced_parens(content, last_import_start)
    elif first_non_type_or_import is not None:
        insert_at = first_non_type_or_import

    if insert_at < 0:
        insert_at = content.find('\n  (table ')
    if insert_at < 0:
        insert_at = content.find('\n  (memory ')
    if insert_at < 0:
        insert_at = content.find('\n  (func ')
    if insert_at < 0:
        raise RuntimeError('Could not find insertion point for extra wasm imports.')

    prefix = '\n' if insert_at > 0 and content[insert_at - 1] != '\n' else ''
    return content[:insert_at] + prefix + insertion + content[insert_at:]


def resolve_cabi_realloc_target(content: str) -> str:
    """Resolve the callable target for cabi_realloc in current WAT text.

    Some modules expose `cabi_realloc` only via an export with a numeric func index,
    without a `$cabi_realloc` symbol. In that case, callers must use `call <index>`.
    """
    if re.search(r'\(func\s+\$cabi_realloc\b', content):
        return '$cabi_realloc'

    named_export = re.search(
        r'\(export\s+"cabi_realloc"\s+\(func\s+(\$[^)\s]+)\)\)',
        content,
    )
    if named_export:
        return named_export.group(1)

    indexed_export = re.search(
        r'\(export\s+"cabi_realloc"\s+\(func\s+(?:\(\;\d+;\)\s*)?(\d+)\)\)',
        content,
    )
    if indexed_export:
        return indexed_export.group(1)

    return '$cabi_realloc'


def normalize_cabi_realloc_calls(content: str) -> str:
    """Rewrite `call $cabi_realloc` to the resolved callable target if needed."""
    target = resolve_cabi_realloc_target(content)
    if target == '$cabi_realloc':
        return content
    return re.sub(r'\bcall\s+\$cabi_realloc\b', f'call {target}', content)


# Bridge instruction for get-random-bytes: func(len: u64) -> list<u8>
# Canonical ABI lowering: (param i64 i32) — len + retptr, writes {ptr:i32, len:i32} to retptr
# Allocates a buffer via cabi_realloc and returns it as a list (bytes are heap-residue,
# sufficient for hash code seeding and other non-cryptographic uses).
_RANDOM_GET_BYTES_BRIDGE = (
    '(local i32 i32)\n'
    '    local.get 0\n'
    '    i32.wrap_i64\n'
    '    local.set 2\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 1\n'
    '    local.get 2\n'
    '    call $cabi_realloc\n'
    '    local.set 3\n'
    '    local.get 1\n'
    '    local.get 3\n'
    '    i32.store\n'
    '    local.get 1\n'
    '    local.get 2\n'
    '    i32.store offset=4'
)


# Bridge get-random-bytes to WASI Preview 1 random_get:
# - allocates an output buffer via cabi_realloc
# - fills it via wasi_snapshot_preview1.random_get
# - returns list ptr/len via canonical ABI outparam
_RANDOM_GET_BYTES_BRIDGE_P1 = (
    '(local i32 i32 i32)\n'
    '    local.get 0\n'
    '    i32.wrap_i64\n'
    '    local.set 2\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 1\n'
    '    local.get 2\n'
    '    call $cabi_realloc\n'
    '    local.set 3\n'
    '    local.get 3\n'
    '    local.get 2\n'
    '    call $__wasi_snapshot_preview1_random_get\n'
    '    local.set 4\n'
    '    local.get 1\n'
    '    local.get 3\n'
    '    i32.store\n'
    '    local.get 1\n'
    '    local.get 2\n'
    '    i32.store offset=4'
)


# Bridge monotonic now() -> wasi_snapshot_preview1.clock_time_get(CLOCK_MONOTONIC=1)
_MONOTONIC_NOW_BRIDGE_P1 = (
    '(local i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 1\n'
    '    i32.const 8\n'
    '    call $cabi_realloc\n'
    '    local.set 0\n'
    '    i32.const 1\n'
    '    i64.const 0\n'
    '    local.get 0\n'
    '    call $__wasi_snapshot_preview1_clock_time_get\n'
    '    drop\n'
    '    local.get 0\n'
    '    i64.load'
)


# Bridge wall-clock now() -> wasi_snapshot_preview1.clock_time_get(CLOCK_REALTIME=0)
# and lower timestamp(ns) to datetime { seconds: u64, nanoseconds: u32 }.
_WALL_CLOCK_NOW_BRIDGE_P1 = (
    '(local i32 i64)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 1\n'
    '    i32.const 8\n'
    '    call $cabi_realloc\n'
    '    local.set 1\n'
    '    i32.const 0\n'
    '    i64.const 0\n'
    '    local.get 1\n'
    '    call $__wasi_snapshot_preview1_clock_time_get\n'
    '    drop\n'
    '    local.get 1\n'
    '    i64.load\n'
    '    local.set 2\n'
    '    local.get 0\n'
    '    local.get 2\n'
    '    i64.const 1000000000\n'
    '    i64.div_u\n'
    '    i64.store\n'
    '    local.get 0\n'
    '    local.get 2\n'
    '    i64.const 1000000000\n'
    '    i64.rem_u\n'
    '    i32.wrap_i64\n'
    '    i32.store offset=8'
)


# Bridge filesystem preopens list via WASI Preview 1:
# enumerate preopened fds and return list<tuple<descriptor, string>>.
_GET_DIRECTORIES_BRIDGE_P1 = (
    '(local i32 i32 i32 i32 i32 i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 1\n'
    '    i32.const 8\n'
    '    call $cabi_realloc\n'
    '    local.set 4\n'
    '    i32.const 3\n'
    '    local.set 3\n'
    '    block\n'
    '      loop\n'
    '        local.get 3\n'
    '        i32.const 64\n'
    '        i32.gt_u\n'
    '        br_if 1\n'
    '        local.get 3\n'
    '        local.get 4\n'
    '        call $__wasi_snapshot_preview1_fd_prestat_get\n'
    '        i32.eqz\n'
    '        if\n'
    '          local.get 4\n'
    '          i32.load offset=4\n'
    '          local.set 5\n'
    '          local.get 5\n'
    '          i32.eqz\n'
    '          if\n'
    '          else\n'
    '            i32.const 0\n'
    '            i32.const 0\n'
    '            i32.const 1\n'
    '            local.get 5\n'
    '            call $cabi_realloc\n'
    '            local.set 6\n'
    '            local.get 3\n'
    '            local.get 6\n'
    '            local.get 5\n'
    '            call $__wasi_snapshot_preview1_fd_prestat_dir_name\n'
    '            i32.eqz\n'
    '            if\n'
    '              local.get 1\n'
    '              local.get 2\n'
    '              i32.const 12\n'
    '              i32.mul\n'
    '              i32.const 4\n'
    '              local.get 2\n'
    '              i32.const 1\n'
    '              i32.add\n'
    '              i32.const 12\n'
    '              i32.mul\n'
    '              call $cabi_realloc\n'
    '              local.set 1\n'
    '              local.get 1\n'
    '              local.get 2\n'
    '              i32.const 12\n'
    '              i32.mul\n'
    '              i32.add\n'
    '              local.set 7\n'
    '              local.get 7\n'
    '              local.get 3\n'
    '              i32.store\n'
    '              local.get 7\n'
    '              local.get 6\n'
    '              i32.store offset=4\n'
    '              local.get 7\n'
    '              local.get 5\n'
    '              i32.store offset=8\n'
    '              local.get 2\n'
    '              i32.const 1\n'
    '              i32.add\n'
    '              local.set 2\n'
    '            else\n'
    '              local.get 6\n'
    '              local.get 5\n'
    '              i32.const 1\n'
    '              i32.const 0\n'
    '              call $cabi_realloc\n'
    '              drop\n'
    '            end\n'
    '          end\n'
    '        end\n'
    '        local.get 3\n'
    '        i32.const 1\n'
    '        i32.add\n'
    '        local.set 3\n'
    '        br 0\n'
    '      end\n'
    '    end\n'
    '    local.get 0\n'
    '    local.get 1\n'
    '    i32.store\n'
    '    local.get 0\n'
    '    local.get 2\n'
    '    i32.store offset=4'
)


# Bridge descriptor.read-directory:
# return success with the directory descriptor itself as stream handle.
_READ_DIRECTORY_BRIDGE_P1 = (
    'local.get 1\n'
    '    i32.const 0\n'
    '    i32.store8\n'
    '    local.get 1\n'
    '    local.get 0\n'
    '    i32.store offset=4'
)


# Bridge directory-entry-stream.read-directory-entry:
# return success with `none`, which indicates end-of-stream.
_READ_DIRECTORY_ENTRY_BRIDGE_P1 = (
    'local.get 1\n'
    '    i32.const 0\n'
    '    i32.store8\n'
    '    local.get 1\n'
    '    i32.const 0\n'
    '    i32.store8 offset=4'
)

# Bridge directory-entry-stream drop to release synthetic stream state.
_DIRECTORY_ENTRY_STREAM_DROP_BRIDGE_P1 = (
    'nop'
)


# Bridge descriptor.open-at to WASI Preview 1 path_open.
_OPEN_AT_BRIDGE_P1 = (
    '(local i32 i32 i64 i64)\n'
    '    local.get 4\n'
    '    i32.const 2\n'
    '    i32.and\n'
    '    if\n'
    '      local.get 6\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 6\n'
    '      local.get 0\n'
    '      i32.store offset=4\n'
    '      return\n'
    '    end\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 4\n'
    '    i32.const 4\n'
    '    call $cabi_realloc\n'
    '    local.set 7\n'
    '    i64.const 2097190\n'
    '    local.set 9\n'
    '    i64.const 2097190\n'
    '    local.set 10\n'
    '    local.get 0\n'
    '    local.get 1\n'
    '    local.get 2\n'
    '    local.get 3\n'
    '    local.get 4\n'
    '    local.get 9\n'
    '    local.get 10\n'
    '    i32.const 0\n'
    '    local.get 7\n'
    '    call $__wasi_snapshot_preview1_path_open\n'
    '    local.set 8\n'
    '    local.get 8\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 6\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 6\n'
    '      local.get 7\n'
    '      i32.load\n'
    '      i32.store offset=4\n'
    '    else\n'
    '      local.get 6\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 6\n'
    '      local.get 8\n'
    '      i32.store8 offset=4\n'
    '    end\n'
    '    local.get 7\n'
    '    i32.const 4\n'
    '    i32.const 4\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge descriptor.get-flags to a deterministic empty-flags success.
_GET_FLAGS_BRIDGE_P1 = (
    'local.get 1\n'
    '    i32.const 0\n'
    '    i32.store8\n'
    '    local.get 1\n'
    '    i32.const 0\n'
    '    i32.store8 offset=1'
)


# Bridge descriptor.get-type to WASI Preview 1 fd_filestat_get.
_GET_TYPE_BRIDGE_P1 = (
    '(local i32 i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 8\n'
    '    i32.const 64\n'
    '    call $cabi_realloc\n'
    '    local.set 2\n'
    '    local.get 0\n'
    '    local.get 2\n'
    '    call $__wasi_snapshot_preview1_fd_filestat_get\n'
    '    local.set 3\n'
    '    local.get 3\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 1\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 2\n'
    '      i32.load8_u offset=16\n'
    '      local.set 4\n'
    '      local.get 4\n'
    '      i32.const 7\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 5\n'
    '        local.set 4\n'
    '      end\n'
    '      local.get 4\n'
    '      i32.const 6\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 7\n'
    '        local.set 4\n'
    '      end\n'
    '      local.get 4\n'
    '      i32.const 5\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 7\n'
    '        local.set 4\n'
    '      end\n'
    '      local.get 4\n'
    '      i32.const 4\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 6\n'
    '        local.set 4\n'
    '      end\n'
    '      local.get 1\n'
    '      local.get 4\n'
    '      i32.store8 offset=1\n'
    '    else\n'
    '      local.get 1\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 1\n'
    '      local.get 3\n'
    '      i32.store8 offset=1\n'
    '    end\n'
    '    local.get 2\n'
    '    i32.const 64\n'
    '    i32.const 8\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge filesystem-error-code(err: borrow<error>) -> option<error-code>.
# Returns Some(err) so filesystem errno-like payloads propagate.
_FILESYSTEM_ERROR_CODE_BRIDGE_P1 = (
    'local.get 1\n'
    '    i32.const 1\n'
    '    i32.store8\n'
    '    local.get 1\n'
    '    local.get 0\n'
    '    i32.store8 offset=1'
)


# Bridge descriptor.read-via-stream to explicit unsupported.
# Signature lowering: (param descriptor:i32 offset:u64 retptr:i32)
_READ_VIA_STREAM_BRIDGE_P1 = (
    '(local i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 8\n'
    '    i32.const 8\n'
    '    call $cabi_realloc\n'
    '    local.set 3\n'
    '    local.get 0\n'
    '    local.get 1\n'
    '    i32.const 0\n'
    '    local.get 3\n'
    '    call $__wasi_snapshot_preview1_fd_seek\n'
    '    local.set 4\n'
    '    local.get 4\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 2\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 2\n'
    '      local.get 0\n'
    '      i32.store offset=4\n'
    '    else\n'
    '      local.get 2\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 2\n'
    '      local.get 4\n'
    '      i32.store8 offset=4\n'
    '    end\n'
    '    local.get 3\n'
    '    i32.const 8\n'
    '    i32.const 8\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge descriptor.write-via-stream to explicit unsupported.
# Signature lowering: (param descriptor:i32 offset:u64 retptr:i32)
_WRITE_VIA_STREAM_BRIDGE_P1 = (
    '(local i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 8\n'
    '    i32.const 8\n'
    '    call $cabi_realloc\n'
    '    local.set 3\n'
    '    local.get 0\n'
    '    local.get 1\n'
    '    i32.const 0\n'
    '    local.get 3\n'
    '    call $__wasi_snapshot_preview1_fd_seek\n'
    '    local.set 4\n'
    '    local.get 4\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 2\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 2\n'
    '      local.get 0\n'
    '      i32.store offset=4\n'
    '    else\n'
    '      local.get 2\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 2\n'
    '      local.get 4\n'
    '      i32.store8 offset=4\n'
    '    end\n'
    '    local.get 3\n'
    '    i32.const 8\n'
    '    i32.const 8\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge descriptor.append-via-stream to explicit unsupported.
# Signature lowering: (param descriptor:i32 retptr:i32)
_APPEND_VIA_STREAM_BRIDGE_P1 = (
    '(local i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 8\n'
    '    i32.const 8\n'
    '    call $cabi_realloc\n'
    '    local.set 2\n'
    '    local.get 0\n'
    '    i64.const 0\n'
    '    i32.const 2\n'
    '    local.get 2\n'
    '    call $__wasi_snapshot_preview1_fd_seek\n'
    '    local.set 3\n'
    '    local.get 3\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 1\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 1\n'
    '      local.get 0\n'
    '      i32.store offset=4\n'
    '    else\n'
    '      local.get 1\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 1\n'
    '      local.get 3\n'
    '      i32.store8 offset=4\n'
    '    end\n'
    '    local.get 2\n'
    '    i32.const 8\n'
    '    i32.const 8\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge input-stream.blocking-read to WASI Preview 1 fd_read.
_INPUT_STREAM_BLOCKING_READ_BRIDGE_P1 = (
    '(local i32 i32 i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 1\n'
    '    local.get 1\n'
    '    i32.wrap_i64\n'
    '    call $cabi_realloc\n'
    '    local.set 3\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 4\n'
    '    i32.const 8\n'
    '    call $cabi_realloc\n'
    '    local.set 4\n'
    '    local.get 4\n'
    '    local.get 3\n'
    '    i32.store\n'
    '    local.get 4\n'
    '    local.get 1\n'
    '    i32.wrap_i64\n'
    '    i32.store offset=4\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 4\n'
    '    i32.const 4\n'
    '    call $cabi_realloc\n'
    '    local.set 5\n'
    '    local.get 0\n'
    '    local.get 4\n'
    '    i32.const 1\n'
    '    local.get 5\n'
    '    call $__wasi_snapshot_preview1_fd_read\n'
    '    local.set 6\n'
    '    local.get 2\n'
    '    i32.const 0\n'
    '    i32.store8\n'
    '    local.get 6\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 2\n'
    '      local.get 3\n'
    '      i32.store offset=4\n'
    '      local.get 2\n'
    '      local.get 5\n'
    '      i32.load\n'
    '      i32.store offset=8\n'
    '    else\n'
    '      local.get 2\n'
    '      i32.const 0\n'
    '      i32.store offset=4\n'
    '      local.get 2\n'
    '      i32.const 0\n'
    '      i32.store offset=8\n'
    '      local.get 3\n'
    '      local.get 1\n'
    '      i32.wrap_i64\n'
    '      i32.const 1\n'
    '      i32.const 0\n'
    '      call $cabi_realloc\n'
    '      drop\n'
    '    end\n'
    '    local.get 4\n'
    '    i32.const 8\n'
    '    i32.const 4\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop\n'
    '    local.get 5\n'
    '    i32.const 4\n'
    '    i32.const 4\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge output-stream.check-write with a small non-zero permit token.
_OUTPUT_STREAM_CHECK_WRITE_BRIDGE_P1 = (
    'local.get 1\n'
    '    i32.const 0\n'
    '    i32.store8\n'
    '    local.get 1\n'
    '    i32.const 1\n'
    '    i32.store8 offset=8\n'
    '    local.get 1\n'
    '    i32.const 0\n'
    '    i32.store offset=12'
)


# Bridge output-stream.write to WASI Preview 1 fd_write.
_OUTPUT_STREAM_WRITE_BRIDGE_P1 = (
    '(local i32 i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 4\n'
    '    i32.const 8\n'
    '    call $cabi_realloc\n'
    '    local.set 4\n'
    '    local.get 4\n'
    '    local.get 1\n'
    '    i32.store\n'
    '    local.get 4\n'
    '    local.get 2\n'
    '    i32.store offset=4\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 4\n'
    '    i32.const 4\n'
    '    call $cabi_realloc\n'
    '    local.set 5\n'
    '    local.get 0\n'
    '    local.get 4\n'
    '    i32.const 1\n'
    '    local.get 5\n'
    '    call $__wasi_snapshot_preview1_fd_write\n'
    '    local.set 6\n'
    '    local.get 3\n'
    '    i32.const 1\n'
    '    i32.store8\n'
    '    local.get 3\n'
    '    i32.const 1\n'
    '    i32.store8 offset=4\n'
    '    local.get 4\n'
    '    i32.const 8\n'
    '    i32.const 4\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop\n'
    '    local.get 5\n'
    '    i32.const 4\n'
    '    i32.const 4\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge output-stream.blocking-flush as immediate completion.
_OUTPUT_STREAM_BLOCKING_FLUSH_BRIDGE_P1 = (
    'local.get 1\n'
    '    i32.const 0\n'
    '    i32.store8\n'
    '    local.get 1\n'
    '    i32.const 0\n'
    '    i32.store8 offset=4'
)


# Bridge descriptor.read to WASI Preview 1 fd_pread.
_READ_BRIDGE_P1 = (
    '(local i32 i32 i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 1\n'
    '    local.get 1\n'
    '    i32.wrap_i64\n'
    '    call $cabi_realloc\n'
    '    local.set 4\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 4\n'
    '    i32.const 8\n'
    '    call $cabi_realloc\n'
    '    local.set 5\n'
    '    local.get 5\n'
    '    local.get 4\n'
    '    i32.store\n'
    '    local.get 5\n'
    '    local.get 1\n'
    '    i32.wrap_i64\n'
    '    i32.store offset=4\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 4\n'
    '    i32.const 4\n'
    '    call $cabi_realloc\n'
    '    local.set 6\n'
    '    local.get 0\n'
    '    local.get 5\n'
    '    i32.const 1\n'
    '    local.get 2\n'
    '    local.get 6\n'
    '    call $__wasi_snapshot_preview1_fd_pread\n'
    '    local.set 7\n'
    '    local.get 7\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 3\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 3\n'
    '      local.get 4\n'
    '      i32.store offset=4\n'
    '      local.get 3\n'
    '      local.get 6\n'
    '      i32.load\n'
    '      i32.store offset=8\n'
    '      local.get 3\n'
    '      i32.const 0\n'
    '      i32.store8 offset=12\n'
    '    else\n'
    '      local.get 3\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 3\n'
    '      local.get 7\n'
    '      i32.store8 offset=4\n'
    '      local.get 4\n'
    '      local.get 1\n'
    '      i32.wrap_i64\n'
    '      i32.const 1\n'
    '      i32.const 0\n'
    '      call $cabi_realloc\n'
    '      drop\n'
    '    end\n'
    '    local.get 5\n'
    '    i32.const 8\n'
    '    i32.const 4\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop\n'
    '    local.get 6\n'
    '    i32.const 4\n'
    '    i32.const 4\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge descriptor.write to WASI Preview 1 fd_pwrite.
_WRITE_BRIDGE_P1 = (
    '(local i32 i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 4\n'
    '    i32.const 8\n'
    '    call $cabi_realloc\n'
    '    local.set 5\n'
    '    local.get 5\n'
    '    local.get 1\n'
    '    i32.store\n'
    '    local.get 5\n'
    '    local.get 2\n'
    '    i32.store offset=4\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 4\n'
    '    i32.const 4\n'
    '    call $cabi_realloc\n'
    '    local.set 6\n'
    '    local.get 0\n'
    '    local.get 5\n'
    '    i32.const 1\n'
    '    local.get 3\n'
    '    local.get 6\n'
    '    call $__wasi_snapshot_preview1_fd_pwrite\n'
    '    local.set 7\n'
    '    local.get 7\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 4\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 4\n'
    '      local.get 6\n'
    '      i32.load\n'
    '      i64.extend_i32_u\n'
    '      i64.store offset=8\n'
    '    else\n'
    '      local.get 4\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 4\n'
    '      local.get 7\n'
    '      i32.store8 offset=8\n'
    '    end\n'
    '    local.get 5\n'
    '    i32.const 8\n'
    '    i32.const 4\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop\n'
    '    local.get 6\n'
    '    i32.const 4\n'
    '    i32.const 4\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge descriptor.metadata-hash to WASI Preview 1 fd_filestat_get.
_METADATA_HASH_BRIDGE_P1 = (
    '(local i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 8\n'
    '    i32.const 64\n'
    '    call $cabi_realloc\n'
    '    local.set 2\n'
    '    local.get 0\n'
    '    local.get 2\n'
    '    call $__wasi_snapshot_preview1_fd_filestat_get\n'
    '    local.set 3\n'
    '    local.get 3\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 1\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 1\n'
    '      local.get 2\n'
    '      i64.load\n'
    '      i64.store offset=8\n'
    '      local.get 1\n'
    '      local.get 2\n'
    '      i64.load offset=8\n'
    '      i64.store offset=16\n'
    '    else\n'
    '      local.get 1\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 1\n'
    '      local.get 3\n'
    '      i32.store8 offset=8\n'
    '    end\n'
    '    local.get 2\n'
    '    i32.const 64\n'
    '    i32.const 8\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge descriptor.metadata-hash-at to WASI Preview 1 path_filestat_get.
_METADATA_HASH_AT_BRIDGE_P1 = (
    '(local i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 8\n'
    '    i32.const 64\n'
    '    call $cabi_realloc\n'
    '    local.set 5\n'
    '    local.get 0\n'
    '    local.get 1\n'
    '    local.get 2\n'
    '    local.get 3\n'
    '    local.get 5\n'
    '    call $__wasi_snapshot_preview1_path_filestat_get\n'
    '    local.set 6\n'
    '    local.get 6\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 4\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 4\n'
    '      local.get 5\n'
    '      i64.load\n'
    '      i64.store offset=8\n'
    '      local.get 4\n'
    '      local.get 5\n'
    '      i64.load offset=8\n'
    '      i64.store offset=16\n'
    '    else\n'
    '      local.get 4\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 4\n'
    '      local.get 6\n'
    '      i32.store8 offset=8\n'
    '    end\n'
    '    local.get 5\n'
    '    i32.const 64\n'
    '    i32.const 8\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge descriptor.stat to WASI Preview 1 fd_filestat_get.
_STAT_BRIDGE_P1 = (
    '(local i32 i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 8\n'
    '    i32.const 64\n'
    '    call $cabi_realloc\n'
    '    local.set 2\n'
    '    local.get 0\n'
    '    local.get 2\n'
    '    call $__wasi_snapshot_preview1_fd_filestat_get\n'
    '    local.set 3\n'
    '    local.get 3\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 1\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 1\n'
    '      local.get 2\n'
    '      i32.load8_u offset=16\n'
    '      local.set 4\n'
    '      local.get 4\n'
    '      i32.const 7\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 5\n'
    '        local.set 4\n'
    '      end\n'
    '      local.get 4\n'
    '      i32.const 6\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 7\n'
    '        local.set 4\n'
    '      end\n'
    '      local.get 4\n'
    '      i32.const 5\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 7\n'
    '        local.set 4\n'
    '      end\n'
    '      local.get 4\n'
    '      i32.const 4\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 6\n'
    '        local.set 4\n'
    '      end\n'
    '      local.get 4\n'
    '      i32.store8 offset=8\n'
    '      local.get 1\n'
    '      local.get 2\n'
    '      i64.load offset=24\n'
    '      i64.store offset=16\n'
    '      local.get 1\n'
    '      local.get 2\n'
    '      i64.load offset=32\n'
    '      i64.store offset=24\n'
    '      local.get 1\n'
    '      i32.const 0\n'
    '      i32.store8 offset=32\n'
    '      local.get 1\n'
    '      i32.const 0\n'
    '      i32.store8 offset=56\n'
    '      local.get 1\n'
    '      i32.const 0\n'
    '      i32.store8 offset=80\n'
    '    else\n'
    '      local.get 1\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 1\n'
    '      local.get 3\n'
    '      i32.store8 offset=16\n'
    '    end\n'
    '    local.get 2\n'
    '    i32.const 64\n'
    '    i32.const 8\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge descriptor.stat-at to WASI Preview 1 path_filestat_get.
_STAT_AT_BRIDGE_P1 = (
    '(local i32 i32 i32)\n'
    '    i32.const 0\n'
    '    i32.const 0\n'
    '    i32.const 8\n'
    '    i32.const 64\n'
    '    call $cabi_realloc\n'
    '    local.set 5\n'
    '    local.get 0\n'
    '    local.get 1\n'
    '    local.get 2\n'
    '    local.get 3\n'
    '    local.get 5\n'
    '    call $__wasi_snapshot_preview1_path_filestat_get\n'
    '    local.set 6\n'
    '    local.get 6\n'
    '    i32.eqz\n'
    '    if\n'
    '      local.get 4\n'
    '      i32.const 0\n'
    '      i32.store8\n'
    '      local.get 4\n'
    '      local.get 5\n'
    '      i32.load8_u offset=16\n'
    '      local.set 7\n'
    '      local.get 7\n'
    '      i32.const 7\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 5\n'
    '        local.set 7\n'
    '      end\n'
    '      local.get 7\n'
    '      i32.const 6\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 7\n'
    '        local.set 7\n'
    '      end\n'
    '      local.get 7\n'
    '      i32.const 5\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 7\n'
    '        local.set 7\n'
    '      end\n'
    '      local.get 7\n'
    '      i32.const 4\n'
    '      i32.eq\n'
    '      if\n'
    '        i32.const 6\n'
    '        local.set 7\n'
    '      end\n'
    '      local.get 7\n'
    '      i32.store8 offset=8\n'
    '      local.get 4\n'
    '      local.get 5\n'
    '      i64.load offset=24\n'
    '      i64.store offset=16\n'
    '      local.get 4\n'
    '      local.get 5\n'
    '      i64.load offset=32\n'
    '      i64.store offset=24\n'
    '      local.get 4\n'
    '      i32.const 0\n'
    '      i32.store8 offset=32\n'
    '      local.get 4\n'
    '      i32.const 0\n'
    '      i32.store8 offset=56\n'
    '      local.get 4\n'
    '      i32.const 0\n'
    '      i32.store8 offset=80\n'
    '    else\n'
    '      local.get 4\n'
    '      i32.const 1\n'
    '      i32.store8\n'
    '      local.get 4\n'
    '      local.get 6\n'
    '      i32.store8 offset=16\n'
    '    end\n'
    '    local.get 5\n'
    '    i32.const 64\n'
    '    i32.const 8\n'
    '    i32.const 0\n'
    '    call $cabi_realloc\n'
    '    drop'
)


# Bridge wasi:cli/exit exit() -> wasi_snapshot_preview1.proc_exit()
_EXIT_BRIDGE_P1 = (
    'local.get 0\n'
    '    call $__wasi_snapshot_preview1_proc_exit\n'
    '    unreachable'
)


def perform_wasi_stubbing(
    content: str,
    stub_wasi: bool = True,
    stub_env: bool = True,
    use_wasi_p1_bridge: bool = False,
) -> str:
    """Replace selected imports with stub function definitions.

    If `stub_wasi` is true, WASI 0.2.0 imports are stubbed with safe defaults.
    Special cases:
    - get-random-bytes: bridges via cabi_realloc to return a valid list<u8>
      (prevents crashes in .NET runtime hash code / ArrayPool initialization)
    - exit: uses unreachable (exit should never return)

    If `stub_env` is true, remaining `env` imports are also stubbed.
    If `use_wasi_p1_bridge` is true, selected WASI P2 shims call
    `wasi_snapshot_preview1` functions instead of no-op/unreachable stubs.
    """
    if not stub_wasi:
        if not stub_env:
            return content

        env_pattern = re.compile(r'\(\s*import\s*"(env)"\s*"([^"]+)"')
        while True:
            match = env_pattern.search(content)
            if not match:
                break
            ns_raw, func_name = match.group(1), match.group(2)
            content = stub_import(content, re.escape(ns_raw), func_name, None, verbose_prefix='[env catch-all] ')
        return content

    io_error_drop_instr = 'unreachable'
    exit_instr = 'unreachable'
    stdin_get_instr = 'unreachable'
    stdout_get_instr = 'unreachable'
    stderr_get_instr = 'unreachable'
    output_stream_subscribe_instr = 'unreachable'
    monotonic_now_instr = 'i64.const 0'
    monotonic_subscribe_instr = 'unreachable'
    wall_clock_now_instr = None
    get_directories_instr = None
    directory_entry_stream_drop_instr = None
    read_via_stream_instr = None
    write_via_stream_instr = None
    append_via_stream_instr = None
    input_stream_blocking_read_instr = None
    output_stream_check_write_instr = None
    output_stream_write_instr = None
    output_stream_blocking_flush_instr = None
    read_instr = None
    write_instr = None
    get_type_instr = None
    filesystem_error_code_instr = None
    read_directory_instr = None
    read_directory_entry_instr = None
    open_at_instr = None
    get_flags_instr = None
    stat_instr = None
    stat_at_instr = None
    metadata_hash_instr = None
    metadata_hash_at_instr = None
    random_bytes_instr = _RANDOM_GET_BYTES_BRIDGE

    if use_wasi_p1_bridge:
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'random_get',
            '(func $__wasi_snapshot_preview1_random_get (param i32 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'clock_time_get',
            '(func $__wasi_snapshot_preview1_clock_time_get (param i32 i64 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'proc_exit',
            '(func $__wasi_snapshot_preview1_proc_exit (param i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'fd_prestat_get',
            '(func $__wasi_snapshot_preview1_fd_prestat_get (param i32 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'fd_prestat_dir_name',
            '(func $__wasi_snapshot_preview1_fd_prestat_dir_name (param i32 i32 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'fd_readdir',
            '(func $__wasi_snapshot_preview1_fd_readdir (param i32 i32 i32 i64 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'fd_read',
            '(func $__wasi_snapshot_preview1_fd_read (param i32 i32 i32 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'fd_write',
            '(func $__wasi_snapshot_preview1_fd_write (param i32 i32 i32 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'fd_pread',
            '(func $__wasi_snapshot_preview1_fd_pread (param i32 i32 i32 i64 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'fd_pwrite',
            '(func $__wasi_snapshot_preview1_fd_pwrite (param i32 i32 i32 i64 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'fd_seek',
            '(func $__wasi_snapshot_preview1_fd_seek (param i32 i64 i32 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'path_open',
            '(func $__wasi_snapshot_preview1_path_open (param i32 i32 i32 i32 i32 i64 i64 i32 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'fd_filestat_get',
            '(func $__wasi_snapshot_preview1_fd_filestat_get (param i32 i32) (result i32))',
        )
        content = ensure_func_import(
            content,
            'wasi_snapshot_preview1',
            'path_filestat_get',
            '(func $__wasi_snapshot_preview1_path_filestat_get (param i32 i32 i32 i32 i32) (result i32))',
        )
        io_error_drop_instr = 'nop'
        exit_instr = _EXIT_BRIDGE_P1
        stdin_get_instr = 'i32.const 0'
        stdout_get_instr = 'i32.const 1'
        stderr_get_instr = 'i32.const 2'
        output_stream_subscribe_instr = 'i32.const 0'
        monotonic_now_instr = _MONOTONIC_NOW_BRIDGE_P1
        monotonic_subscribe_instr = 'i32.const 0'
        wall_clock_now_instr = _WALL_CLOCK_NOW_BRIDGE_P1
        get_directories_instr = _GET_DIRECTORIES_BRIDGE_P1
        directory_entry_stream_drop_instr = _DIRECTORY_ENTRY_STREAM_DROP_BRIDGE_P1
        read_via_stream_instr = _READ_VIA_STREAM_BRIDGE_P1
        write_via_stream_instr = _WRITE_VIA_STREAM_BRIDGE_P1
        append_via_stream_instr = _APPEND_VIA_STREAM_BRIDGE_P1
        input_stream_blocking_read_instr = _INPUT_STREAM_BLOCKING_READ_BRIDGE_P1
        output_stream_check_write_instr = _OUTPUT_STREAM_CHECK_WRITE_BRIDGE_P1
        output_stream_write_instr = _OUTPUT_STREAM_WRITE_BRIDGE_P1
        output_stream_blocking_flush_instr = _OUTPUT_STREAM_BLOCKING_FLUSH_BRIDGE_P1
        read_instr = _READ_BRIDGE_P1
        write_instr = _WRITE_BRIDGE_P1
        get_type_instr = _GET_TYPE_BRIDGE_P1
        filesystem_error_code_instr = _FILESYSTEM_ERROR_CODE_BRIDGE_P1
        read_directory_instr = _READ_DIRECTORY_BRIDGE_P1
        read_directory_entry_instr = _READ_DIRECTORY_ENTRY_BRIDGE_P1
        open_at_instr = _OPEN_AT_BRIDGE_P1
        get_flags_instr = _GET_FLAGS_BRIDGE_P1
        stat_instr = _STAT_BRIDGE_P1
        stat_at_instr = _STAT_AT_BRIDGE_P1
        metadata_hash_instr = _METADATA_HASH_BRIDGE_P1
        metadata_hash_at_instr = _METADATA_HASH_AT_BRIDGE_P1
        random_bytes_instr = _RANDOM_GET_BYTES_BRIDGE_P1

    stubs = [
        ('wasi:io/error',                   '[resource-drop]error',                     io_error_drop_instr),
        ('wasi:io/poll',                     '[resource-drop]pollable',                  None),
        ('wasi:io/streams',                  '[resource-drop]input-stream',              None),
        ('wasi:io/streams',                  '[resource-drop]output-stream',             None),
        ('wasi:cli/terminal-input',          '[resource-drop]terminal-input',            None),
        ('wasi:cli/terminal-output',         '[resource-drop]terminal-output',           None),
        ('wasi:filesystem/types',            '[resource-drop]descriptor',                None),
        ('wasi:filesystem/types',            '[resource-drop]directory-entry-stream',    directory_entry_stream_drop_instr),
        ('wasi:cli/environment',             'get-environment',                          None),
        ('wasi:cli/exit',                    'exit',                                     exit_instr),
        ('wasi:io/poll',                     '[method]pollable.block',                   None),
        ('wasi:io/poll',                     'poll',                                     None),
        ('wasi:io/streams',                  '[method]input-stream.subscribe',           'unreachable'),
        ('wasi:io/streams',                  '[method]input-stream.blocking-read',       input_stream_blocking_read_instr),
        ('wasi:io/streams',                  '[method]output-stream.check-write',        output_stream_check_write_instr),
        ('wasi:io/streams',                  '[method]output-stream.write',              output_stream_write_instr),
        ('wasi:io/streams',                  '[method]output-stream.blocking-flush',     output_stream_blocking_flush_instr),
        ('wasi:io/streams',                  '[method]output-stream.blocking-write-and-flush', None),
        ('wasi:io/streams',                  '[method]output-stream.subscribe',          output_stream_subscribe_instr),
        ('wasi:cli/stdin',                   'get-stdin',                                stdin_get_instr),
        ('wasi:cli/stdout',                  'get-stdout',                               stdout_get_instr),
        ('wasi:cli/stderr',                  'get-stderr',                               stderr_get_instr),
        ('wasi:cli/terminal-stdin',          'get-terminal-stdin',                       None),
        ('wasi:cli/terminal-stdout',         'get-terminal-stdout',                      None),
        ('wasi:cli/terminal-stderr',         'get-terminal-stderr',                      None),
        ('wasi:clocks/monotonic-clock',      'now',                                      monotonic_now_instr),
        ('wasi:clocks/monotonic-clock',      'subscribe-instant',                        monotonic_subscribe_instr),
        ('wasi:clocks/monotonic-clock',      'subscribe-duration',                       monotonic_subscribe_instr),
        ('wasi:clocks/wall-clock',           'now',                                      wall_clock_now_instr),
        ('wasi:filesystem/types',            '[method]descriptor.read-via-stream',       read_via_stream_instr),
        ('wasi:filesystem/types',            '[method]descriptor.write-via-stream',      write_via_stream_instr),
        ('wasi:filesystem/types',            '[method]descriptor.append-via-stream',     append_via_stream_instr),
        ('wasi:filesystem/types',            '[method]descriptor.read',                  read_instr),
        ('wasi:filesystem/types',            '[method]descriptor.write',                 write_instr),
        ('wasi:filesystem/types',            '[method]descriptor.get-flags',             get_flags_instr),
        ('wasi:filesystem/types',            '[method]descriptor.read-directory',        read_directory_instr),
        ('wasi:filesystem/types',            '[method]descriptor.get-type',              get_type_instr),
        ('wasi:filesystem/types',            '[method]descriptor.stat',                  stat_instr),
        ('wasi:filesystem/types',            '[method]descriptor.stat-at',               stat_at_instr),
        ('wasi:filesystem/types',            '[method]descriptor.open-at',               open_at_instr),
        ('wasi:filesystem/types',            '[method]descriptor.metadata-hash',         metadata_hash_instr),
        ('wasi:filesystem/types',            '[method]descriptor.metadata-hash-at',      metadata_hash_at_instr),
        ('wasi:filesystem/types',            '[method]directory-entry-stream.read-directory-entry', read_directory_entry_instr),
        ('wasi:filesystem/types',            'filesystem-error-code',                    filesystem_error_code_instr),
        ('wasi:filesystem/preopens',         'get-directories',                          get_directories_instr),
        ('wasi:random/random',               'get-random-bytes',                         random_bytes_instr),
    ]

    for ns, func, repl_instr in stubs:
        ns_pat = re.escape(ns) + r'@\d+\.\d+\.\d+'
        content = stub_import(content, ns_pat, func, repl_instr)

    # Catch-all: stub any remaining wasi: imports not covered by the explicit list
    wasi_pattern = re.compile(r'\(\s*import\s*"(wasi:[^"]+)"\s*"([^"]+)"')
    while True:
        match = wasi_pattern.search(content)
        if not match:
            break
        ns_raw, func_name = match.group(1), match.group(2)
        content = stub_import(content, re.escape(ns_raw), func_name, None, verbose_prefix='[wasi catch-all] ')

    # NOTE: wasi_snapshot_preview1 imports are NOT stubbed — Extism provides them natively
    # when the plugin is loaded with withWasi: true (random_get, fd_write, clock_time_get, etc.)

    if not stub_env:
        return content

    # Catch-all: stub any remaining "env" imports (pthread, etc. from NativeAOT runtime)
    env_pattern = re.compile(r'\(\s*import\s*"(env)"\s*"([^"]+)"')
    while True:
        match = env_pattern.search(content)
        if not match:
            break
        ns_raw, func_name = match.group(1), match.group(2)
        content = stub_import(content, re.escape(ns_raw), func_name, None, verbose_prefix='[env catch-all] ')

    return content


def fix_undefined_stubs(content: str) -> str:
    """Replace `unreachable` in linker-generated undefined_stub functions with safe defaults.

    NativeAOT emits local `undefined_stub` functions for unresolved symbols (e.g. pthread).
    These contain `unreachable` which traps at runtime. Replace with default return values.
    """
    # Match both $undefined_stub and $"#funcN undefined_stub" patterns
    pattern = re.compile(r'\(func\s+\$(?:undefined_stub|"[^"]*undefined_stub[^"]*")')

    offset = 0
    while True:
        match = pattern.search(content, offset)
        if not match:
            break

        func_start = match.start()
        func_end = find_balanced_parens(content, func_start)
        func_text = content[func_start:func_end]

        # Get the return instructions based on the function's result type
        ret_instrs = _default_return_instrs(func_text, content)

        if ret_instrs:
            new_func = func_text.replace('unreachable', ret_instrs, 1)
        else:
            new_func = func_text.replace('unreachable', 'nop', 1)

        if new_func != func_text:
            name_end = min(60, func_text.find('\n') if '\n' in func_text else 60)
            print(f'    Fixed undefined_stub: {func_text[:name_end].strip()} -> {ret_instrs or "nop"}', file=sys.stderr)

        content = content[:func_start] + new_func + content[func_end:]
        offset = func_start + len(new_func)

    return content


def run_command(cmd: list[str], input_data: bytes | None = None) -> bytes:
    """Run a command, return stdout bytes. Raises on failure."""
    result = subprocess.run(cmd, input=input_data, capture_output=True)
    if result.returncode != 0:
        stderr_text = result.stderr.decode('utf-8', errors='replace')
        raise RuntimeError(f'Command {" ".join(cmd)!r} failed (exit {result.returncode}):\n{stderr_text}')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(
        description='Clips a component-encumbered WASM module down to a bare module.'
    )
    parser.add_argument('input', help='Input WASM file (component model)')
    parser.add_argument('output', help='Output WASM file (bare module)')
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='Print progress updates to stderr.',
    )
    parser.add_argument(
        '-t', '--wat', action='store_true',
        help='Output the modified WAT instead of compiling back to WASM.',
    )
    parser.add_argument(
        '--pre', action='store_true',
        help='Unbundle and output that alone before performing transformations. '
             'If combined with --wat, outputs the unbundled WAT.',
    )
    parser.add_argument(
        '--view', type=int, default=200,
        help='Lookahead multiplier for WAT processing (default: 200). '
             'Multiplied by 100 to get character count. 0 = process entire file.',
    )
    parser.add_argument(
        '--tmp-dir', default=None,
        help='Directory for temporary artifacts. Cleaned up automatically unless specified.',
    )
    parser.add_argument(
        '--namespaces',
        default='env,debug',
        help='Comma-separated list of namespaces to convert from kebab-case to snake_case (default: env,debug)',
    )
    parser.add_argument(
        '--keep-wasi-imports', action='store_true',
        help='Keep wasi:* imports instead of replacing them with stubs.',
    )
    parser.add_argument(
        '--wasi-p1-bridge', action='store_true',
        help='When stubbing WASI P2 imports, bridge key calls via wasi_snapshot_preview1 imports.',
    )
    args = parser.parse_args()

    convert_namespaces = [ns.strip() for ns in args.namespaces.split(',') if ns.strip()]

    custom_tmp_dir = args.tmp_dir is not None
    tmp_dir = os.path.abspath(args.tmp_dir) if custom_tmp_dir else tempfile.mkdtemp(prefix='clip_wasm_')

    if custom_tmp_dir:
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        os.makedirs(tmp_dir, exist_ok=True)

    try:
        # Step 1: Unbundle the component model WASM to get the bare module
        print('  Unbundling component...', file=sys.stderr)
        run_command([
            'wasm-tools', 'component', 'unbundle',
            '--module-dir', tmp_dir,
            args.input,
        ])

        unbundled_module = os.path.join(tmp_dir, 'unbundled-module0.wasm')
        if not os.path.exists(unbundled_module):
            # Try to find whatever module was produced
            wasm_files = [f for f in os.listdir(tmp_dir) if f.endswith('.wasm')]
            if not wasm_files:
                raise RuntimeError(f'No WASM modules found in {tmp_dir} after unbundle')
            unbundled_module = os.path.join(tmp_dir, wasm_files[0])
            print(f'    Using unbundled module: {wasm_files[0]}', file=sys.stderr)

        if args.pre and not args.wat:
            print('  Writing unbundled WASM to output...', file=sys.stderr)
            with open(unbundled_module, 'rb') as f:
                data = f.read()
            with open(args.output, 'wb') as f:
                f.write(data)
            return

        # Step 2: Convert to WAT
        print('  Converting to WAT...', file=sys.stderr)
        # Prefer naming unnamed items so later text edits are robust even when
        # the module was built with aggressive stripping (numeric-only refs).
        try:
            wat_bytes = run_command(['wasm-tools', 'print', '--name-unnamed', unbundled_module])
        except RuntimeError as exc:
            err = str(exc)
            if '--name-unnamed' in err and ('unexpected argument' in err or 'Found argument' in err):
                wat_bytes = run_command(['wasm-tools', 'print', unbundled_module])
            else:
                raise
        wat = wat_bytes.decode('utf-8')

        if args.pre:
            print('  Writing unbundled WAT to output...', file=sys.stderr)
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(wat)
            return

        # Step 3: Process the WAT (only the head for efficiency)
        view = (args.view or 0) * 100
        if view > 0:
            head = wat[:view]
            tail = wat[view:]
        else:
            head = wat
            tail = ''

        print(f'  Performing namespace conversion ({", ".join(convert_namespaces)})...', file=sys.stderr)
        head = namespace_conversion(head, convert_namespaces)

        print('  Performing export name conversion...', file=sys.stderr)
        head = export_name_conversion(head)

        if args.keep_wasi_imports:
            print('  Preserving WASI imports (--keep-wasi-imports)...', file=sys.stderr)
        else:
            print('  Performing WASI stubbing...', file=sys.stderr)
        if args.keep_wasi_imports and args.wasi_p1_bridge:
            print('  Note: --wasi-p1-bridge ignored because --keep-wasi-imports is enabled.', file=sys.stderr)
        head = perform_wasi_stubbing(
            head,
            stub_wasi=not args.keep_wasi_imports,
            use_wasi_p1_bridge=args.wasi_p1_bridge,
        )

        final_wat = head + tail
        final_wat = normalize_cabi_realloc_calls(final_wat)

        print('  Fixing undefined_stub functions...', file=sys.stderr)
        final_wat = fix_undefined_stubs(final_wat)

        if args.wat:
            print('  Writing modified WAT to output...', file=sys.stderr)
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(final_wat)
            return

        # Step 4: Compile WAT back to WASM
        print('  Compiling WAT to final WASM module...', file=sys.stderr)
        final_wasm = run_command(['wasm-tools', 'parse'], input_data=final_wat.encode('utf-8'))

        print('  Writing final module to output...', file=sys.stderr)
        with open(args.output, 'wb') as f:
            f.write(final_wasm)

        print('  Done.', file=sys.stderr)

    finally:
        if not custom_tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == '__main__':
    main()
