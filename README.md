# Extism PDK for .NET NativeAOT

A Plugin Development Kit (PDK) for building [Extism](https://extism.org/) plugins in C# using .NET NativeAOT, compiled to WebAssembly.

Write your plugin exports as regular C# methods, annotate them with `[ExtismExport]`, and the included source generator + MSBuild targets handle the rest — WIT generation, WASM compilation, component-model unbundling, and optional WASI stubbing.

## How It Works

The project has three main components:

- **Extism.Pdk.NativeAot** — The PDK library. Provides the `Pdk` API (input/output, memory, config, variables, logging, HTTP) and MSBuild integration that automatically configures NativeAOT-to-WASM compilation.
- **Extism.Pdk.SourceGenerator** — A Roslyn source generator that finds `[ExtismExport("name")]` attributes and generates the `UnmanagedCallersOnly` wrapper functions, handling serialization/deserialization of strings, byte arrays, primitives, multi-parameter packing, and FlatBuffers types.
- **clip.py** — A post-publish tool that strips the WASM Component Model wrapper, optionally stubs WASI imports, and converts namespaces so the resulting `.wasm` file is a bare module compatible with the Extism runtime.

## Prerequisites

- [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0)
- **Python 3.10+** — used by `clip.py` during post-publish
- **wasm-tools** — the [Bytecode Alliance `wasm-tools`](https://github.com/bytecodealliance/wasm-tools) CLI (`wasm-tools component unbundle` and `wasm-tools parse` must be on your PATH)

## Building the Sample Plugin

```bash
dotnet publish samples/extism-plugin-dotnet/ExtismPluginExample.csproj -c Release
```

This compiles the plugin to WASM via NativeAOT, then automatically runs `clip.py` to produce the final `ExtismPluginExample_clipped.wasm`.

To keep `wasi:*` imports in the clipped output, set this in your plugin `.csproj`:

```xml
<PropertyGroup>
  <ExtismClipKeepWasiImports>true</ExtismClipKeepWasiImports>
</PropertyGroup>
```

To keep stubbing enabled but bridge key WASI P2 calls through `wasi_snapshot_preview1`
imports (including random/clock/exit, basic stdio handles, and preopen directory
enumeration), set:

```xml
<PropertyGroup>
  <ExtismClipWasiP1Bridge>true</ExtismClipWasiP1Bridge>
</PropertyGroup>
```

## Running the Sample Host

```bash
dotnet run --project samples/extism-host-dotnet/ExtismHostExample.csproj
```

The host loads the clipped WASM plugin and calls every exported function, demonstrating string, numeric, binary, and FlatBuffers round-trips.

## Writing a Plugin

1. Reference `Extism.Pdk.NativeAot` and import its `.props` / `.targets` in your `.csproj`.
2. Write static methods and annotate them with `[ExtismExport("name")]`:

```csharp
using Extism.Pdk;

public static class Plugin
{
    [ExtismExport("greet")]
    public static string Greet(string name) => $"Hello, {name}!";

    [ExtismExport("add")]
    public static int Add(int a, int b) => a + b;
}
```

### Supported Types

| Category | Parameter | Return |
|---|---|---|
| `string` | yes | yes |
| `byte[]` | yes | yes |
| Primitives (`bool`, `byte`, `sbyte`, `short`, `ushort`, `int`, `uint`, `long`, `ulong`, `float`, `double`) | yes | yes |
| `void` | — | yes |
| FlatBuffers (`IFlatbufferObject`) | yes | yes |

Multi-parameter exports are supported when all parameters are primitives (packed as little-endian bytes).

## Acknowledgements

- `clip.py` is based on the original [clip.lua by Pspritechologist](https://gist.github.com/Pspritechologist/08c172aa48cb30a5dee8cfc5004722b6)
- NativeAOT LLVM/WASM backend by [SingleAccretion](https://github.com/SingleAccretion) and [yowl](https://github.com/yowl)
