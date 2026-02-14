using System.Buffers.Binary;
using System.Text;
using Extism.Sdk;
using Google.FlatBuffers;
using ExamplePlugin;

var wasmPath = args.Length > 0
    ? args[0]
    : Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "extism-plugin-dotnet", "bin", "Release", "net10.0", "wasi-wasm", "native", "ExtismPluginExample_clipped.wasm");

if (!File.Exists(wasmPath))
{
    Console.WriteLine($"WASM file not found: {wasmPath}");
    Console.WriteLine("Build the guest plugin first:");
    Console.WriteLine("  dotnet publish examples/extism-plugin-dotnet/ExtismPluginExample.csproj -c Release");
    return 1;
}

Console.WriteLine($"Loading plugin from: {wasmPath}");
Console.WriteLine();

var wasmBytes = File.ReadAllBytes(wasmPath);
var manifest = new Manifest(new ByteArrayWasmSource(wasmBytes, "plugin"));
using var plugin = new Plugin(manifest, [], withWasi: true);

// --- String → String ---
{
    var output = plugin.Call("greet", Encoding.UTF8.GetBytes("World"));
    Console.WriteLine($"greet(\"World\") = \"{Encoding.UTF8.GetString(output)}\"");
}

// --- No input → String ---
{
    var output = plugin.Call("version", []);
    Console.WriteLine($"version() = \"{Encoding.UTF8.GetString(output)}\"");
}

// --- String → Void ---
{
    plugin.Call("log", Encoding.UTF8.GetBytes("hello from host"));
    Console.WriteLine($"log(\"hello from host\") = (void)");
}

// --- Two ints → int (little-endian packed) ---
{
    var input = new byte[8];
    BinaryPrimitives.WriteInt32LittleEndian(input.AsSpan(0), 17);
    BinaryPrimitives.WriteInt32LittleEndian(input.AsSpan(4), 25);
    var output = plugin.Call("add", input);
    var result = BinaryPrimitives.ReadInt32LittleEndian(output);
    Console.WriteLine($"add(17, 25) = {result}");
}

// --- Three doubles → double ---
{
    var input = new byte[24];
    BinaryPrimitives.WriteDoubleLittleEndian(input.AsSpan(0), 80.0);
    BinaryPrimitives.WriteDoubleLittleEndian(input.AsSpan(8), 0.7);
    BinaryPrimitives.WriteDoubleLittleEndian(input.AsSpan(16), 1.0);
    var output = plugin.Call("weighted_avg", input);
    var result = BinaryPrimitives.ReadDoubleLittleEndian(output);
    Console.WriteLine($"weighted-avg(80.0, 0.7, 1.0) = {result}");
}

// --- int → bool ---
{
    var input = new byte[4];
    BinaryPrimitives.WriteInt32LittleEndian(input, 42);
    var output = plugin.Call("is_even", input);
    var result = output[0] != 0;
    Console.WriteLine($"is-even(42) = {result}");
}

// --- Two floats → float ---
{
    var input = new byte[8];
    BinaryPrimitives.WriteSingleLittleEndian(input.AsSpan(0), 3.0f);
    BinaryPrimitives.WriteSingleLittleEndian(input.AsSpan(4), 4.0f);
    var output = plugin.Call("distance", input);
    var result = BinaryPrimitives.ReadSingleLittleEndian(output);
    Console.WriteLine($"distance(3.0, 4.0) = {result}");
}

// --- long → long ---
{
    var input = new byte[8];
    BinaryPrimitives.WriteInt64LittleEndian(input, 10);
    var output = plugin.Call("factorial", input);
    var result = BinaryPrimitives.ReadInt64LittleEndian(output);
    Console.WriteLine($"factorial(10) = {result}");
}

// --- byte[] → byte[] ---
{
    var input = new byte[] { 0x00, 0x0F, 0xF0, 0xFF };
    var output = plugin.Call("xor_bytes", input).ToArray();
    Console.WriteLine($"xor-bytes([{FormatBytes(input)}]) = [{FormatBytes(output)}]");
}

// --- Mixed primitives: int(4) + byte(1) + bool(1) → int ---
{
    var input = new byte[6];
    BinaryPrimitives.WriteInt32LittleEndian(input.AsSpan(0), 100);
    input[4] = 30;
    input[5] = 0;
    var output = plugin.Call("cond_add", input);
    var result = BinaryPrimitives.ReadInt32LittleEndian(output);
    Console.WriteLine($"cond-add(100, 30, false) = {result}");
}

{
    var input = new byte[6];
    BinaryPrimitives.WriteInt32LittleEndian(input.AsSpan(0), 100);
    input[4] = 30;
    input[5] = 1;
    var output = plugin.Call("cond_add", input);
    var result = BinaryPrimitives.ReadInt32LittleEndian(output);
    Console.WriteLine($"cond-add(100, 30, true) = {result}");
}

// --- Complex type via FlatBuffers ---
{
    var builder = new FlatBufferBuilder(256);
    var nameOffset = builder.CreateString("Alice");
    var inv = new[]
    {
        builder.CreateString("sword"),
        builder.CreateString("shield"),
    };
    var invVector = PlayerData.CreateInventoryVector(builder, inv);
    var root = PlayerData.CreatePlayerData(builder, nameOffset, 50, invVector);
    builder.Finish(root.Value);

    var input = builder.SizedByteArray();
    var output = plugin.Call("level_up", input).ToArray();

    var resultBuf = new ByteBuffer(output);
    var result = PlayerData.GetRootAsPlayerData(resultBuf);
    var inventory = new List<string>();
    for (int i = 0; i < result.InventoryLength; i++)
        inventory.Add(result.Inventory(i));
    Console.WriteLine($"level-up(Alice, 50, [sword, shield]) = ({result.Name}, {result.Score}, [{string.Join(", ", inventory)}])");
}

Console.WriteLine();
Console.WriteLine("All exports executed successfully!");
return 0;

static string FormatBytes(byte[] bytes) =>
    string.Join(", ", bytes.Select(b => $"0x{b:X2}"));
