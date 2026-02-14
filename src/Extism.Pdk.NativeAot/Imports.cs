using System.Runtime.InteropServices;

namespace Extism.Pdk.NativeAot;

internal static class Imports
{
    private const string Module = "extism:host/env";

    // Input
    [DllImport(Module, EntryPoint = "input-length"), WasmImportLinkage]
    internal static extern ulong extism_input_length();

    [DllImport(Module, EntryPoint = "input-load-u8"), WasmImportLinkage]
    internal static extern byte extism_input_load_u8(ulong offset);

    [DllImport(Module, EntryPoint = "input-load-u64"), WasmImportLinkage]
    internal static extern ulong extism_input_load_u64(ulong offset);

    // Output
    [DllImport(Module, EntryPoint = "output-set"), WasmImportLinkage]
    internal static extern void extism_output_set(ulong offset, ulong length);

    // Memory
    [DllImport(Module, EntryPoint = "alloc"), WasmImportLinkage]
    internal static extern ulong extism_alloc(ulong size);

    [DllImport(Module, EntryPoint = "free"), WasmImportLinkage]
    internal static extern void extism_free(ulong offset);

    [DllImport(Module, EntryPoint = "length"), WasmImportLinkage]
    internal static extern ulong extism_length(ulong offset);

    [DllImport(Module, EntryPoint = "load-u8"), WasmImportLinkage]
    internal static extern byte extism_load_u8(ulong offset);

    [DllImport(Module, EntryPoint = "load-u64"), WasmImportLinkage]
    internal static extern ulong extism_load_u64(ulong offset);

    [DllImport(Module, EntryPoint = "store-u8"), WasmImportLinkage]
    internal static extern void extism_store_u8(ulong offset, byte value);

    [DllImport(Module, EntryPoint = "store-u64"), WasmImportLinkage]
    internal static extern void extism_store_u64(ulong offset, ulong value);

    // Config
    [DllImport(Module, EntryPoint = "config-get"), WasmImportLinkage]
    internal static extern ulong extism_config_get(ulong keyOffset);

    // Error
    [DllImport(Module, EntryPoint = "error-set"), WasmImportLinkage]
    internal static extern void extism_error_set(ulong offset);

    // Vars
    [DllImport(Module, EntryPoint = "var-get"), WasmImportLinkage]
    internal static extern ulong extism_var_get(ulong keyOffset);

    [DllImport(Module, EntryPoint = "var-set"), WasmImportLinkage]
    internal static extern void extism_var_set(ulong keyOffset, ulong valueOffset);

    // Logging
    [DllImport(Module, EntryPoint = "log-trace"), WasmImportLinkage]
    internal static extern void extism_log_trace(ulong offset);

    [DllImport(Module, EntryPoint = "log-debug"), WasmImportLinkage]
    internal static extern void extism_log_debug(ulong offset);

    [DllImport(Module, EntryPoint = "log-info"), WasmImportLinkage]
    internal static extern void extism_log_info(ulong offset);

    [DllImport(Module, EntryPoint = "log-warn"), WasmImportLinkage]
    internal static extern void extism_log_warn(ulong offset);

    [DllImport(Module, EntryPoint = "log-error"), WasmImportLinkage]
    internal static extern void extism_log_error(ulong offset);

    [DllImport(Module, EntryPoint = "get-log-level"), WasmImportLinkage]
    internal static extern int extism_get_log_level();

    // HTTP
    [DllImport(Module, EntryPoint = "http-request"), WasmImportLinkage]
    internal static extern ulong extism_http_request(ulong reqOffset, ulong bodyOffset);

    [DllImport(Module, EntryPoint = "http-status-code"), WasmImportLinkage]
    internal static extern int extism_http_status_code();

    [DllImport(Module, EntryPoint = "http-headers"), WasmImportLinkage]
    internal static extern ulong extism_http_headers();
}
