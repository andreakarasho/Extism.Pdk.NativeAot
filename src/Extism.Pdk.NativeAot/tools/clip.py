"""
Clips a component-encumbered WASM module down to a bare module.

Runs `wasm-tools component unbundle` to strip the Component Model wrapper,
converts to WAT, stubs WASI imports, converts namespaces, and compiles back to WASM.

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


def perform_wasi_stubbing(content: str) -> str:
    """Replace WASI 0.2.0 imports with stub function definitions."""
    stubs = [
        ('wasi:io/error',                   '[resource-drop]error',                     'unreachable'),
        ('wasi:io/poll',                     '[resource-drop]pollable',                  None),
        ('wasi:io/streams',                  '[resource-drop]input-stream',              None),
        ('wasi:io/streams',                  '[resource-drop]output-stream',             None),
        ('wasi:cli/terminal-input',          '[resource-drop]terminal-input',            None),
        ('wasi:cli/terminal-output',         '[resource-drop]terminal-output',           None),
        ('wasi:filesystem/types',            '[resource-drop]descriptor',                None),
        ('wasi:filesystem/types',            '[resource-drop]directory-entry-stream',    None),
        ('wasi:cli/environment',             'get-environment',                          None),
        ('wasi:cli/exit',                    'exit',                                     None),
        ('wasi:io/poll',                     '[method]pollable.block',                   None),
        ('wasi:io/poll',                     'poll',                                     None),
        ('wasi:io/streams',                  '[method]input-stream.subscribe',           'unreachable'),
        ('wasi:io/streams',                  '[method]output-stream.check-write',        None),
        ('wasi:io/streams',                  '[method]output-stream.write',              None),
        ('wasi:io/streams',                  '[method]output-stream.blocking-flush',     None),
        ('wasi:io/streams',                  '[method]output-stream.blocking-write-and-flush', None),
        ('wasi:io/streams',                  '[method]output-stream.subscribe',          'unreachable'),
        ('wasi:cli/stdin',                   'get-stdin',                                'unreachable'),
        ('wasi:cli/stdout',                  'get-stdout',                               'unreachable'),
        ('wasi:cli/stderr',                  'get-stderr',                               'unreachable'),
        ('wasi:cli/terminal-stdin',          'get-terminal-stdin',                       None),
        ('wasi:cli/terminal-stdout',         'get-terminal-stdout',                      None),
        ('wasi:cli/terminal-stderr',         'get-terminal-stderr',                      None),
        ('wasi:clocks/monotonic-clock',      'now',                                      'i64.const 0'),
        ('wasi:clocks/monotonic-clock',      'subscribe-instant',                        'unreachable'),
        ('wasi:clocks/monotonic-clock',      'subscribe-duration',                       'unreachable'),
        ('wasi:clocks/wall-clock',           'now',                                      None),
        ('wasi:filesystem/types',            '[method]descriptor.read-via-stream',       None),
        ('wasi:filesystem/types',            '[method]descriptor.write-via-stream',      None),
        ('wasi:filesystem/types',            '[method]descriptor.append-via-stream',     None),
        ('wasi:filesystem/types',            '[method]descriptor.get-flags',             None),
        ('wasi:filesystem/types',            '[method]descriptor.get-type',              None),
        ('wasi:filesystem/types',            '[method]descriptor.stat',                  None),
        ('wasi:filesystem/types',            '[method]descriptor.metadata-hash',         None),
        ('wasi:filesystem/types',            'filesystem-error-code',                    None),
        ('wasi:filesystem/preopens',         'get-directories',                          None),
        ('wasi:random/random',               'get-random-bytes',                         None),
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

    # Catch-all: stub any wasi_snapshot_preview1 imports (WASI preview 1)
    wasi_p1_pattern = re.compile(r'\(\s*import\s*"(wasi_snapshot_preview1)"\s*"([^"]+)"')
    while True:
        match = wasi_p1_pattern.search(content)
        if not match:
            break
        ns_raw, func_name = match.group(1), match.group(2)
        content = stub_import(content, re.escape(ns_raw), func_name, None, verbose_prefix='[wasi-p1 catch-all] ')

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
        wat_bytes = run_command(['wasm-tools', 'print', unbundled_module])
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

        print('  Performing WASI stubbing...', file=sys.stderr)
        head = perform_wasi_stubbing(head)

        final_wat = head + tail

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
